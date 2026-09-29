from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from mpt_factory.artifacts import collect, evaluate
from mpt_factory.common import FileLock, atomic_json, digest, read_json
from mpt_factory.db import Database, TERMINAL
from mpt_factory.jobs import create_job
from mpt_factory.processes import alive, identify, terminate_tree
from mpt_factory.services import Services
from mpt_factory.supervisor import Supervisor
from mpt_factory.worktrees import Worktrees, git


def submit(stack, spec):
    cfg, db = stack
    job = create_job(cfg, db, spec, cfg.file.parent)
    db.transition(job, "queued")
    return job


def finish(supervisor, job, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        supervisor.tick()
        row = supervisor.db.get(job)
        if row["state"] in TERMINAL:
            return row
        time.sleep(0.05)
    row = supervisor.db.get(job)
    if row["pid"]:
        terminate_tree(row["pid"], row["process_started"])
    pytest.fail(f"Job stalled: {row}")


def test_vertical_real_process_media_sqlite(stack, spec):
    cfg, db = stack
    before = git(cfg.mpt_root, "status", "--porcelain")
    config_hash = digest(cfg.mpt_root / "config.toml")
    job = submit(stack, spec)
    row = finish(Supervisor(cfg), job)
    assert row["state"] == "succeeded", row["error"]
    assert json.loads(row["result"])["visual_quality"] == "not_evaluated"
    assert {a["kind"] for a in db.artifacts(job)} >= {"final_video", "image", "diagnostics", "log"}
    assert config_hash == digest(cfg.mpt_root / "config.toml")
    assert git(cfg.mpt_root, "status", "--porcelain") == before
    states = [json.loads(e["payload"])["to"] for e in db.events(job) if e["kind"] == "state"]
    assert states == ["created", "queued", "preparing", "running", "collecting", "evaluating", "succeeded"]
    assert (cfg.data / "logs/factory.jsonl").is_file()


def test_claim_atomic_and_single_gpu_slot(stack, spec):
    cfg, db = stack
    for _ in range(8):
        submit(stack, spec)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: Database(cfg.db).claim(), range(8)))
    assert sum(x is not None for x in results) == 1


def test_illegal_transition(stack, spec):
    cfg, db = stack
    job = create_job(cfg, db, spec, cfg.file.parent)
    with pytest.raises(ValueError, match="Illegal transition"):
        db.transition(job, "succeeded")


def test_priority_fifo(stack, spec):
    cfg, db = stack
    low = submit(stack, spec)
    high = create_job(cfg, db, spec, cfg.file.parent, priority=5)
    db.transition(high, "queued")
    assert db.claim() == high


def test_queued_cancel(stack, spec):
    cfg, db = stack
    job = submit(stack, spec)
    db.cancel(job)
    assert db.get(job)["state"] == "cancelled"
    assert db.claim() is None


@pytest.mark.parametrize("topic,expected", [("fail", "failed"), ("write-stable", "failed")])
def test_failure_and_stable_write_guard(stack, spec, topic, expected):
    cfg, db = stack
    spec["params"]["video_subject"] = topic
    job = submit(stack, spec)
    row = finish(Supervisor(cfg), job)
    assert row["state"] == expected
    assert not (cfg.mpt_root / "forbidden.txt").exists()
    assert not db.claim()


def test_supervisor_recovery_live_worker_and_cancel(stack, spec):
    cfg, db = stack
    spec["params"]["video_subject"] = "sleep"
    job = submit(stack, spec)
    first = Supervisor(cfg)
    first.tick()
    original = db.get(job)
    assert original["state"] == "running"
    assert alive(original["pid"], original["process_started"])
    # Simulate crash between spawn and recording PID. Restart locates exact argv.
    db.update(job, pid=None, process_started=None)
    second = Supervisor(cfg)
    second.tick()
    assert db.get(job)["pid"] == original["pid"]
    db.cancel(job)
    assert finish(second, job)["state"] == "cancelled"
    assert not alive(original["pid"], original["process_started"])
    first.video.reap()


def test_timeout_stops_owned_process(stack, spec):
    cfg, db = stack
    cfg = replace(cfg, timeout_seconds=0.1)
    spec["params"]["video_subject"] = "sleep"
    job = submit((cfg, db), spec)
    row = finish(Supervisor(cfg), job)
    assert row["state"] == "failed"
    assert row["error"] == "Generation timeout"
    assert not alive(row["pid"], row["process_started"])


def test_dead_worker_is_interrupted_never_retried(stack, spec):
    cfg, db = stack
    job = submit(stack, spec)
    db.claim()
    folder = cfg.data / "runs" / job
    atomic_json(folder / "request.json", {"created": time.time() - 30})
    db.transition(job, "running", attempt_dir=str(folder))
    assert finish(Supervisor(cfg), job)["state"] == "interrupted"
    assert not db.claim()


def test_resume_collection_without_regeneration(stack, spec):
    cfg, db = stack
    job = submit(stack, spec)
    supervisor = Supervisor(cfg)
    supervisor.tick()
    folder = Path(db.get(job)["attempt_dir"])
    end = time.monotonic() + 15
    while not (folder / "result.json").exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert read_json(folder / "result.json")["status"] == "succeeded"
    supervisor.video.reap()
    row = finish(Supervisor(cfg), job)
    assert row["state"] == "succeeded"
    assert len([e for e in db.events(job) if e["kind"] == "worker_started"]) == 1


def test_missing_services_backoff_bounded(stack, spec):
    cfg, db = stack
    service = {"name": "llama", "url": "http://127.0.0.1:1/health", "required": True}
    cfg = replace(cfg, services=(service,))
    spec["required_services"] = ["llama"]
    job = submit((cfg, db), spec)
    row = finish(Supervisor(cfg), job)
    assert row["state"] == "failed"
    assert row["preflight_attempts"] == 3
    assert not row["pid"]


def test_lock_excludes_second_process(tmp_path):
    lock = tmp_path / "lock"
    with FileLock(lock):
        with pytest.raises(RuntimeError):
            FileLock(lock).acquire()
    with FileLock(lock):
        pass


def test_reference_snapshot_and_role_validation(stack, spec, tmp_path):
    cfg, db = stack
    image = tmp_path / "ref.png"
    image.write_bytes(b"reference")
    spec["references"] = [{"path": str(image), "role": "identity", "anchor": True}]
    job = create_job(cfg, db, spec, tmp_path)
    image.write_bytes(b"changed")
    saved = json.loads(db.get(job)["spec"])["references"][0]
    assert Path(saved["path"]).read_bytes() == b"reference"
    spec["references"][0]["role"] = "continuity"
    with pytest.raises(ValueError, match="Unsupported manual role"):
        create_job(cfg, db, spec, tmp_path)


def test_worktree_isolation_and_stable_untouched(stack):
    cfg, db = stack
    manager = Worktrees(cfg, db)
    head = git(cfg.mpt_root, "rev-parse", "HEAD")
    experiment = manager.create("trial")
    root = manager.root(experiment["id"])
    (root / "cli.py").write_text("# isolated edit\n")
    assert "parse_args" in (cfg.mpt_root / "cli.py").read_text()
    assert git(cfg.mpt_root, "rev-parse", "HEAD") == head
    assert git(cfg.mpt_root, "branch", "--show-current") == "stable"
    assert (root / "config.toml").exists()
    assert not git(root, "ls-files", "config.toml")


def test_dirty_stable_blocked_untracked_allowed(stack):
    cfg, db = stack
    (cfg.mpt_root / "backup.bak").write_text("user backup")
    Worktrees(cfg, db).stable()
    (cfg.mpt_root / "cli.py").write_text("changed")
    with pytest.raises(ValueError, match="tracked modifications"):
        Worktrees(cfg, db).stable()


def test_unregistered_experiment_rejected(stack):
    cfg, db = stack
    with pytest.raises(ValueError, match="not registered"):
        Worktrees(cfg, db).root("../../stable")


def test_external_service_stop_refused(stack):
    cfg, db = stack
    with pytest.raises(RuntimeError, match="external"):
        Services(cfg, db).stop("unknown")


def test_pid_reuse_identity_guard():
    import os
    stamp = identify(os.getpid())
    assert alive(os.getpid(), stamp)
    assert not alive(os.getpid(), stamp - 60)
    terminate_tree(os.getpid(), stamp - 60)  # Must not signal the current process.


def test_invalid_final_mp4_fails_evaluation(tmp_path, spec):
    (tmp_path / "final-1.mp4").write_bytes(b"invalid")
    result = evaluate(tmp_path, collect(tmp_path), spec, "ffprobe")
    assert not result["technical_pass"]
    assert result["visual_quality"] == "not_evaluated"


def test_artifact_symlink_escape_ignored(tmp_path):
    elsewhere = tmp_path.parent / "outside.json"
    elsewhere.write_text('{}')
    try:
        (tmp_path / "escape.json").symlink_to(elsewhere)
    except OSError:
        pytest.skip("Symlink permission not available")
    assert collect(tmp_path) == []


def test_dashboard_renders_completed_job(stack, spec, monkeypatch):
    from streamlit.testing.v1 import AppTest
    cfg, db = stack
    job = submit(stack, spec)
    assert finish(Supervisor(cfg), job)["state"] == "succeeded"
    monkeypatch.setattr("mpt_factory.dashboard.load_config", lambda: cfg)
    # Import entry directly so the patched load_config is used by the app.
    app = AppTest.from_string("from mpt_factory.dashboard import main\nmain()")
    app.run(timeout=20)
    assert not app.exception
    assert "MPT Agent Factory" in app.title[0].value
    assert app.selectbox[0].value == job
    assert any("Completados" == metric.label and metric.value == "1" for metric in app.metric)


def test_unknown_param_fails_at_real_boundary(stack, spec):
    cfg, db = stack
    spec["params"]["made_up_option"] = True
    job = submit(stack, spec)
    row = finish(Supervisor(cfg), job)
    assert row["state"] == "failed"
    assert "Unknown VideoParams" in row["error"]
