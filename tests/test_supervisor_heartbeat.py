"""Regressions for Windows supervisor heartbeat and atomic JSON resilience."""
from __future__ import annotations

from pathlib import Path

import pytest

from mpt_factory import common
from mpt_factory.common import atomic_json, read_json
from mpt_factory.supervisor import Supervisor


def windows_lock_error(code: int = 5) -> OSError:
    exc = PermissionError("simulated Windows sharing/access lock")
    exc.winerror = code
    return exc


def test_atomic_json_retries_transient_windows_replace_and_cleans_tmp(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    real_replace = common.os.replace
    calls = []
    sleeps = []

    def flaky_replace(source, destination):
        calls.append((Path(source), Path(destination)))
        if len(calls) < 3:
            raise windows_lock_error(5)
        return real_replace(source, destination)

    monkeypatch.setattr(common.os, "replace", flaky_replace)
    monkeypatch.setattr(common.time, "sleep", lambda delay: sleeps.append(delay))

    atomic_json(target, {"status": "running"})

    assert read_json(target) == {"status": "running"}
    assert len(calls) == 3
    assert sleeps == list(common._ATOMIC_REPLACE_RETRY_DELAYS[:2])
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_json_does_not_retry_nontransient_replace_error(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    calls = []

    def broken_replace(source, destination):
        calls.append((source, destination))
        exc = OSError("non-transient replace failure")
        exc.winerror = 87
        raise exc

    monkeypatch.setattr(common.os, "replace", broken_replace)
    monkeypatch.setattr(common.time, "sleep", lambda _: pytest.fail("non-transient error retried"))

    with pytest.raises(OSError, match="non-transient"):
        atomic_json(target, {"status": "running"})

    assert len(calls) == 1
    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_supervisor_tick_survives_transient_status_publish_failure(stack, monkeypatch):
    cfg, _ = stack
    supervisor = Supervisor(cfg)
    calls = []

    def locked_atomic(path, value):
        calls.append((Path(path), value))
        if Path(path).name == "supervisor.json":
            raise windows_lock_error(32)
        return common.atomic_json(path, value)

    monkeypatch.setattr("mpt_factory.supervisor.atomic_json", locked_atomic)

    supervisor.tick()
    supervisor.tick()

    assert sum(path.name == "supervisor.json" for path, _ in calls) == 2


def test_supervisor_tick_survives_transient_event_export_failure(stack, monkeypatch):
    cfg, _ = stack
    supervisor = Supervisor(cfg)
    exports = []

    def locked_export():
        exports.append(True)
        raise windows_lock_error(33)

    monkeypatch.setattr(supervisor, "export_events", locked_export)
    monkeypatch.setattr(supervisor, "_publish_supervisor_status", lambda status: True)

    supervisor.tick()
    supervisor.tick()

    assert len(exports) == 2


def test_supervisor_tick_does_not_hide_nontransient_housekeeping_failure(stack, monkeypatch):
    cfg, _ = stack
    supervisor = Supervisor(cfg)

    def broken_atomic(path, value):
        exc = OSError("disk or programming failure")
        exc.winerror = 87
        raise exc

    monkeypatch.setattr("mpt_factory.supervisor.atomic_json", broken_atomic)

    with pytest.raises(OSError, match="disk or programming"):
        supervisor.tick()


def test_shutdown_cleanup_does_not_mask_original_supervisor_exception(stack, monkeypatch):
    cfg, _ = stack
    supervisor = Supervisor(cfg)

    def original_failure():
        raise ValueError("original supervisor failure")

    def cleanup_failure():
        raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(supervisor, "tick", original_failure)
    monkeypatch.setattr(supervisor, "_export_events_resilient", cleanup_failure)
    monkeypatch.setattr(
        supervisor,
        "_publish_supervisor_status",
        lambda status: (_ for _ in ()).throw(RuntimeError("status cleanup failure")),
    )

    with pytest.raises(ValueError, match="original supervisor failure"):
        supervisor.run()
