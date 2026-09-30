"""Windows redirector regressions, runnable on Windows and Linux without MPT models.

Mocked process tables cover PID ambiguity; one test uses an actual forwarding
launcher and runner. Running these on Linux is not native Windows validation.
"""
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from mpt_factory import processes
from mpt_factory.common import FileLock, atomic_json, read_json
from mpt_factory.jobs import create_job
from mpt_factory.supervisor import MPTVideoAgent, Supervisor


def fake_process(pid, started, request):
    process = Mock(pid=pid)
    process.create_time.return_value = started
    process.cmdline.return_value = [sys.executable, 'runner.py', str(request)]
    process.status.return_value = 'running'
    process.is_running.return_value = True
    return process


def test_recovery_prefers_published_worker_over_equivalent_launcher(tmp_path, monkeypatch):
    request = tmp_path / 'request.json'
    worker = fake_process(200, 100.0, request)
    launcher = fake_process(100, 101.0, request)
    atomic_json(tmp_path / 'worker.json', {'pid': 200, 'time': 102.0})
    monkeypatch.setattr(processes, '_process', lambda pid: {100: launcher, 200: worker}[pid])
    monkeypatch.setattr(processes.psutil, 'process_iter', lambda *args: [launcher, worker])
    monkeypatch.setattr(processes, '_visible_pid', lambda p: p.pid)
    assert processes.find_runner(request) == (200, 100.0)


@pytest.mark.parametrize('bad_identity', ['other-request', 'other-program', 'zombie'])
def test_recovery_rejects_published_pid_for_wrong_worker(tmp_path, monkeypatch, bad_identity):
    request = tmp_path / 'request.json'
    unrelated = fake_process(123, 100.0, request)
    if bad_identity == 'other-request':
        unrelated.cmdline.return_value[-1] = str(tmp_path / 'other-request.json')
    elif bad_identity == 'other-program':
        unrelated.cmdline.return_value[1] = 'other.py'
    else:
        unrelated.status.return_value = processes.psutil.STATUS_ZOMBIE
    atomic_json(tmp_path / 'worker.json', {'pid': 123})
    monkeypatch.setattr(processes, '_process', lambda pid: unrelated)
    monkeypatch.setattr(processes.psutil, 'process_iter', lambda *args: [unrelated])
    assert processes.find_runner(request) is None


@pytest.mark.parametrize('contents', ['{', 'null', '[]', '{"pid": "not-a-pid"}'])
def test_invalid_handshake_allows_exact_argv_recovery(tmp_path, monkeypatch, contents):
    request = tmp_path / 'request.json'
    (tmp_path / 'worker.json').write_text(contents)
    launcher = fake_process(100, 100.0, request)
    worker = fake_process(200, 101.0, request)
    monkeypatch.setattr(processes.psutil, 'process_iter', lambda *args: [launcher, worker])
    monkeypatch.setattr(processes, '_visible_pid', lambda p: p.pid)
    assert processes.find_runner(request) == (200, 101.0)


def test_recovery_process_disappearing_during_identity_lookup(tmp_path, monkeypatch):
    request = tmp_path / 'request.json'
    gone = fake_process(100, 100.0, request)
    gone.create_time.side_effect = processes.psutil.NoSuchProcess(100)
    live = fake_process(200, 101.0, request)
    monkeypatch.setattr(processes.psutil, 'process_iter', lambda *args: [gone, live])
    monkeypatch.setattr(processes, '_visible_pid', lambda p: p.pid)
    assert processes.find_runner(request) == (200, 101.0)


def test_launch_does_not_trust_unrelated_handshake_pid(stack, monkeypatch):
    cfg, _ = stack
    request = cfg.data / 'request.json'
    unrelated = fake_process(123, 100.0, cfg.data / 'different.json')
    atomic_json(cfg.data / 'worker.json', {'pid': 123})
    launcher = Mock(pid=100)
    launcher.poll.return_value = 0
    monkeypatch.setattr('mpt_factory.supervisor.subprocess.Popen', lambda *a, **k: launcher)
    monkeypatch.setattr(processes, '_process', lambda pid: unrelated)
    monkeypatch.setattr(processes.psutil, 'process_iter', lambda *args: [unrelated])
    # Advance the bounded handshake wait without sleeping.
    clock = iter([0.0, 0.1, 3.0])
    monkeypatch.setattr('mpt_factory.supervisor.time.monotonic', lambda: next(clock))
    monkeypatch.setattr('mpt_factory.supervisor.time.sleep', lambda _: None)
    pid, birth = MPTVideoAgent(cfg).launch({}, request)
    assert pid != 123
    assert birth is None


def test_supervisor_will_not_cancel_live_pid_belonging_to_other_job(stack, spec, monkeypatch):
    cfg, db = stack
    job = create_job(cfg, db, spec, cfg.file.parent)
    db.transition(job, 'queued')
    db.claim()
    folder = cfg.data / 'runs' / job
    atomic_json(folder / 'request.json', {'job_id': job, 'created': time.time()})
    db.transition(job, 'running', attempt_dir=str(folder), pid=123, process_started=100.0)
    db.cancel(job)
    monkeypatch.setattr('mpt_factory.supervisor.alive', lambda *args: True)
    monkeypatch.setattr('mpt_factory.supervisor.find_runner', lambda request: None)
    kill = Mock()
    monkeypatch.setattr('mpt_factory.supervisor.terminate_tree', kill)
    Supervisor(cfg).running(db.get(job))
    kill.assert_not_called()


def test_real_forwarding_launcher_handshake_recovery_cancel_and_gpu_release(stack, spec, monkeypatch):
    cfg, db = stack
    spec['params']['video_subject'] = 'sleep'
    job = create_job(cfg, db, spec, cfg.file.parent)
    db.transition(job, 'queued')
    popen = subprocess.Popen
    launchers = []

    def forward(argv, **kwargs):
        launcher = popen([sys.executable, '-c',
            'import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))', *argv], **kwargs)
        launchers.append(launcher)
        return launcher

    first = Supervisor(cfg)
    row = None
    try:
        # Popen deliberately returns a different PID from the interpreter running runner.py.
        with monkeypatch.context() as m:
            m.setattr('mpt_factory.supervisor.subprocess.Popen', forward)
            first.tick()
        row = db.get(job)
        worker = read_json(Path(row['attempt_dir']) / 'worker.json')
        assert worker['pid'] == row['pid']
        assert row['pid'] != launchers[0].pid
        assert processes.alive(row['pid'], row['process_started'])
        # Do not cancel until the real fixture worker holds the GPU lease.
        deadline = time.monotonic() + 10
        while not (Path(row['attempt_dir']) / 'progress.json').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (Path(row['attempt_dir']) / 'progress.json').exists()
        with pytest.raises(RuntimeError):
            with FileLock(cfg.gpu_lock):
                pass
        db.update(job, pid=None, process_started=None)
        second = Supervisor(cfg)
        second.tick()
        assert db.get(job)['pid'] == worker['pid']
        db.cancel(job)
        second.tick()
        second.tick()
        assert db.get(job)['state'] == 'cancelled'
        assert not processes.alive(row['pid'], row['process_started'])
        with FileLock(cfg.gpu_lock):
            pass
        assert sum(e['kind'] == 'worker_started' for e in db.events(job)) == 1
    finally:
        if row:
            processes.terminate_tree(row['pid'], row['process_started'])
        for launcher in launchers:
            if launcher.poll() is None:
                processes.terminate_tree(launcher.pid, processes.identify(launcher.pid))
            launcher.wait(timeout=10)
