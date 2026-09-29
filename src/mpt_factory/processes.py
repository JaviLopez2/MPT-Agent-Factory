from __future__ import annotations

import os
import subprocess
import signal
import time
from pathlib import Path

import psutil


def _translated_procfs():
    # Some containers mount the host /proc inside a child PID namespace. Never
    # inspect/signals a coincidentally equal host PID. Windows uses psutil directly.
    return sys_platform_linux() and int(os.readlink('/proc/self')) != os.getpid()


def sys_platform_linux():
    import sys
    return sys.platform == 'linux'


def _visible_pid(process):
    if not _translated_procfs():
        return process.pid
    base = Path('/proc') / str(process.pid)
    if (base / 'ns/pid').stat().st_ino != Path('/proc/self/ns/pid').stat().st_ino:
        raise psutil.NoSuchProcess(process.pid)
    for line in (base / 'status').read_text().splitlines():
        if line.startswith('NSpid:'):
            return int(line.split()[-1])
    raise psutil.NoSuchProcess(process.pid)


def _process(pid):
    if not _translated_procfs():
        return psutil.Process(pid)
    for process in psutil.process_iter():
        try:
            if _visible_pid(process) == pid:
                return process
        except (OSError, psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    raise psutil.NoSuchProcess(pid)


def detached_options():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def identify(pid):
    try:
        process = _process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process.create_time()
    except psutil.NoSuchProcess:
        return None


def alive(pid, started):
    if not pid or not started:
        return False
    stamp = identify(pid)
    return stamp is not None and stamp == started


def find_runner(request: Path):
    """Recover the spawn/SQLite update crash window using an exact argv element."""
    for process in psutil.process_iter(["pid", "cmdline", "create_time", "status"]):
        try:
            args = process.info["cmdline"] or []
            if str(request) in args and any(Path(a).name == "runner.py" for a in args):
                if process.info["status"] != psutil.STATUS_ZOMBIE:
                    return _visible_pid(process), process.info["create_time"]
        except (OSError, psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _wait_stopped(targets, timeout):
    """Do not call waitpid(host_pid) when /proc uses an outer PID namespace.

    A zombie has already released its resources. Reap only our children using
    the PID visible to this process; recovered/orphan workers may not be children.
    """
    deadline = time.monotonic() + timeout
    pending = list(targets)
    while pending:
        survivors = []
        for process in pending:
            try:
                if not process.is_running():
                    continue
                if process.status() == psutil.STATUS_ZOMBIE:
                    if os.name != "nt":
                        try:
                            os.waitpid(_visible_pid(process), os.WNOHANG)
                        except (ChildProcessError, ProcessLookupError):
                            pass
                    continue
                survivors.append(process)
            except (FileNotFoundError, ProcessLookupError, psutil.NoSuchProcess):
                continue
        pending = survivors
        if not pending or time.monotonic() >= deadline:
            return pending
        time.sleep(0.05)
    return []


def terminate_tree(pid, started):
    """Only signal an owned identity, including its descendants; never kill by port."""
    if not alive(pid, started):
        return
    try:
        parent = _process(pid)
        # The PID may have been recycled between alive() and this second lookup.
        if parent.create_time() != started or not parent.is_running():
            return
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    targets = [*reversed(children), parent]
    for process in targets:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                continue
            if _translated_procfs():
                os.kill(_visible_pid(process), signal.SIGTERM)
            else:
                process.terminate()
        except (ProcessLookupError, psutil.NoSuchProcess):
            pass
    survivors = _wait_stopped(targets, timeout=3)
    for process in survivors:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                continue
            if _translated_procfs():
                os.kill(_visible_pid(process), signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, psutil.NoSuchProcess):
            pass
    survivors = _wait_stopped(survivors, timeout=3)
    if survivors:
        raise RuntimeError("Could not stop owned process tree; queue remains blocked")
