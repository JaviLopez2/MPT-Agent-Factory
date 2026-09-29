"""Regression tests for process identity, cancellation and actual supervisor restart."""
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

from mpt_factory import processes
from mpt_factory.jobs import create_job
from mpt_factory.db import TERMINAL


def test_reused_pid_between_probe_and_signal_is_not_killed(monkeypatch):
    # alive() can succeed just before the OS recycles the PID. The second
    # lookup must independently verify birth time before walking/signalling.
    reused = Mock()
    reused.create_time.return_value = 999.0
    reused.children.return_value = []
    monkeypatch.setattr(processes, "alive", lambda *args: True)
    monkeypatch.setattr(processes, "_process", lambda pid: reused)
    monkeypatch.setattr(processes, "_translated_procfs", lambda: False)
    monkeypatch.setattr(processes.psutil, "wait_procs", lambda *a, **kw: ([], []))
    processes.terminate_tree(42, 123.0)
    reused.terminate.assert_not_called()
    reused.children.assert_not_called()


def test_birth_times_are_not_rounded_together(monkeypatch):
    monkeypatch.setattr(processes, "identify", lambda pid: 100.02)
    assert not processes.alive(42, 100.01)


def test_supervisor_process_restart_and_child_cancellation(stack, spec):
    cfg, db = stack
    # Make the fixture task spawn a real subprocess, like an FFmpeg child.
    task = cfg.mpt_root / "app/services/task.py"
    source = task.read_text()
    source = source.replace(
        "if params.video_subject == 'sleep': time.sleep(60)",
        """if params.video_subject == 'sleep':
        import sys
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        (root/'child.json').write_text(json.dumps({'pid': child.pid}))
        time.sleep(60)""")
    task.write_text(source)
    subprocess.run(["git", "-C", str(cfg.mpt_root), "add", "app/services/task.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(cfg.mpt_root), "-c", "user.name=Factory Test", "-c",
                    "user.email=factory-test@example.invalid", "commit", "-m", "Child fixture"], check=True, capture_output=True)
    # TOML supports literal strings containing Windows backslashes.
    cfg.file.write_text(f"[factory]\ndata_dir='{cfg.data}'\npoll_seconds=0.05\n"
        f"[mpt]\nroot='{cfg.mpt_root}'\npython='{cfg.mpt_python}'\nbranch='stable'\n"
        f"worktrees_dir='{cfg.worktrees}'\n", encoding="utf-8")
    spec["params"]["video_subject"] = "sleep"
    job = create_job(cfg, db, spec, cfg.file.parent)
    db.transition(job, "queued")
    cmd = [str(cfg.mpt_python), "-m", "mpt_factory", "--config", str(cfg.file), "supervisor"]
    first = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    second = None
    row = child = None
    try:
        deadline = time.monotonic() + 20
        child_file = cfg.data / "runs" / job / "storage/tasks" / job / "child.json"
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child_file.exists(), db.get(job)
        row = db.get(job)
        child_pid = json.loads(child_file.read_text())["pid"]
        child = (child_pid, processes.identify(child_pid))
        assert child[1] is not None
        first.terminate()
        first.wait(timeout=10)
        assert processes.alive(row["pid"], row["process_started"])
        assert processes.alive(*child)
        # Lose persisted identity too: recovery must use exact request argv.
        db.update(job, pid=None, process_started=None)
        db.cancel(job)
        second = subprocess.Popen(cmd + ["--until-idle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        second.wait(timeout=25)
        assert second.returncode == 0
        assert db.get(job)["state"] == "cancelled", db.get(job)
        assert not processes.alive(row["pid"], row["process_started"])
        assert not processes.alive(*child)
        assert len([e for e in db.events(job) if e["kind"] == "worker_started"]) == 1
    finally:
        for proc in (first, second):
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        if child:
            processes.terminate_tree(*child)
        if row:
            processes.terminate_tree(row["pid"], row["process_started"])
