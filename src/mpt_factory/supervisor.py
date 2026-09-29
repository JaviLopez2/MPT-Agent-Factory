from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from mpt_factory.agents import TechnicalEvaluator
from mpt_factory.artifacts import collect
from mpt_factory.common import FileLock, atomic_json, now, read_json
from mpt_factory.db import Database, TERMINAL
from mpt_factory.processes import alive, detached_options, find_runner, identify, terminate_tree
from mpt_factory.services import Services
from mpt_factory.worktrees import Worktrees, git


class MPTVideoAgent:
    def __init__(self, config):
        self.cfg = config
        self.children = {}

    def launch(self, job, request):
        runner = Path(__file__).with_name("runner.py")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
               "PYTHONDONTWRITEBYTECODE": "1"}
        with (request.parent / "worker.log").open("ab") as output:
            process = subprocess.Popen([str(self.cfg.mpt_python), "-B", "-u", str(runner), str(request)],
                cwd=request.parent, stdout=output, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env, **detached_options())
        self.children[process.pid] = process
        return process.pid, identify(process.pid) or now()

    def reap(self):
        for pid, process in list(self.children.items()):
            if process.poll() is not None:
                del self.children[pid]


class Supervisor:
    def __init__(self, config, video_agent=None, evaluator=None):
        self.cfg = config
        self.db = Database(config.db)
        self.services = Services(config, self.db)
        self.worktrees = Worktrees(config, self.db)
        self.video = video_agent or MPTVideoAgent(config)
        self.evaluator = evaluator or TechnicalEvaluator(config.ffprobe)

    def defer(self, job, reason):
        attempts = job["preflight_attempts"] + 1
        if attempts >= self.cfg.service_attempts:
            self.db.transition(job["id"], "failed", error=reason, preflight_attempts=attempts)
        else:
            self.db.transition(job["id"], "queued", error=reason, preflight_attempts=attempts,
                next_run=now() + self.cfg.service_retry_seconds)

    def prepare(self, job):
        if job["cancel_requested"]:
            self.db.transition(job["id"], "cancelled")
            return
        spec = json.loads(job["spec"])
        try:
            root = self.worktrees.root(job["experiment_id"])
            if not self.cfg.mpt_python.is_file():
                raise ValueError(f"MPT interpreter not found: {self.cfg.mpt_python}")
            if not (root / "config.toml").is_file():
                raise ValueError(f"MPT config missing: {root / 'config.toml'}")
            health = self.services.ensure(spec["required_services"])
            self.db.event(job["id"], "health", services=health)
            if any(not s["healthy"] for s in health):
                self.defer(job, "Required services unavailable; see health events")
                return
            if "comfyui" in spec["required_services"] and not self.services.gpu_idle():
                self.defer(job, "ComfyUI queue busy or unreadable")
                return
            try:
                with FileLock(self.cfg.gpu_lock):
                    pass
            except RuntimeError:
                self.defer(job, "GPU lease held by another Factory worker/check")
                return
            folder = self.cfg.data / "runs" / job["id"]
            folder.mkdir(parents=True, exist_ok=True)
            request = folder / "request.json"
            # Preparing cannot have spawned: RUNNING is committed before Popen.
            if request.exists():
                raise RuntimeError("Attempt already exists; manual retry must create a new job ID")
            atomic_json(request, {"job_id": job["id"], "spec": spec, "created": now(),
                "mpt_root": str(root), "stable_root": str(self.cfg.mpt_root),
                "mpt_commit": git(root, "rev-parse", "HEAD"),
                "experiment_id": job["experiment_id"], "gpu_lock": str(self.cfg.gpu_lock)})
            self.db.transition(job["id"], "running", attempt_dir=str(folder), error=None)
            try:
                pid, started = self.video.launch(job, request)
                self.db.update(job["id"], pid=pid, process_started=started)
                self.db.event(job["id"], "worker_started", pid=pid)
            except Exception as exc:
                # May have spawned just before DB failure; recover by argv next tick.
                self.db.event(job["id"], "launch_error", error=str(exc))
        except Exception as exc:
            current = self.db.get(job["id"])
            if current["state"] == "preparing":
                self.db.transition(job["id"], "failed", error=f"Preflight: {exc}")
            else:
                raise

    def running(self, job):
        folder = Path(job["attempt_dir"])
        request = folder / "request.json"
        progress = read_json(folder / "progress.json", {})
        if progress and progress.get("progress") != job["progress"]:
            self.db.update(job["id"], progress=int(progress.get("progress", 0)))
        pid, started = job["pid"], job["process_started"]
        if not alive(pid, started):
            recovered = find_runner(request)
            if recovered:
                pid, started = recovered
                self.db.update(job["id"], pid=pid, process_started=started)
                self.db.event(job["id"], "worker_recovered", pid=pid)
        live = alive(pid, started)
        result = read_json(folder / "result.json")
        if result and result.get("job_id") != job["id"]:
            raise RuntimeError("Worker result belongs to a different job")
        if result and not live:
            outcome = "succeeded" if result.get("status") == "succeeded" else "failed"
            self.db.transition(job["id"], "collecting", outcome=outcome, error=result.get("error"))
            return
        metadata = read_json(request, {})
        age = now() - metadata.get("created", job["created"])
        if job["cancel_requested"] or age > self.cfg.timeout_seconds:
            if live:
                terminate_tree(pid, started)
            self.db.transition(job["id"], "collecting",
                outcome="cancelled" if job["cancel_requested"] else "failed",
                error="Cancelled by operator" if job["cancel_requested"] else "Generation timeout")
        elif not live and age > 10:
            # Handles a crash before spawn, dead workers and machines rebooting.
            self.db.transition(job["id"], "collecting", outcome="interrupted",
                error="Worker disappeared without a durable result; no automatic rerun")

    def finish(self, job):
        folder = Path(job["attempt_dir"])
        if job["state"] == "collecting":
            items = collect(folder)
            self.db.save_artifacts(job["id"], items)
            self.db.event(job["id"], "artifacts_collected", count=len(items))
            if job["outcome"] != "succeeded":
                self.db.transition(job["id"], job["outcome"] or "failed")
                return
            self.db.transition(job["id"], "evaluating")
        result = self.evaluator.evaluate(folder, self.db.artifacts(job["id"]), json.loads(job["spec"]))
        self.db.transition(job["id"], "succeeded" if result["technical_pass"] else "failed",
            result=json.dumps(result, ensure_ascii=False), progress=100,
            error=None if result["technical_pass"] else "; ".join(result["failures"]))

    def export_events(self):
        folder = self.cfg.data / "logs"
        folder.mkdir(exist_ok=True)
        cursor_file = folder / "cursor.json"
        cursor = read_json(cursor_file, {"seq": 0})["seq"]
        events = self.db.events(after=cursor)
        if events:
            with (folder / "factory.jsonl").open("a", encoding="utf-8") as output:
                for event in events:
                    event["payload"] = json.loads(event["payload"])
                    output.write(json.dumps(event, ensure_ascii=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
            atomic_json(cursor_file, {"seq": events[-1]["seq"]})

    def tick(self):
        if hasattr(self.video, "reap"):
            self.video.reap()
        active = self.db.active()
        if not active:
            claimed = self.db.claim()
            active = [self.db.get(claimed)] if claimed else []
        for job in active:
            try:
                if job["state"] == "preparing":
                    self.prepare(job)
                elif job["state"] == "running":
                    self.running(job)
                elif job["state"] in {"collecting", "evaluating"}:
                    self.finish(job)
            except Exception as exc:
                # Fail closed for unknown process/OS errors. Keep active, blocking
                # further claims, until the operator resolves the underlying cause.
                self.db.event(job["id"], "supervisor_error", error=f"{type(exc).__name__}: {exc}")
                self.db.update(job["id"], error=f"Supervisor needs attention: {exc}")
        self.export_events()
        atomic_json(self.cfg.data / "supervisor.json", {"pid": os.getpid(), "time": now(), "status": "running"})

    def run(self, once=False, until_idle=False):
        with FileLock(self.cfg.data / "supervisor.lock"):
            first = None
            try:
                while True:
                    self.tick()
                    if once:
                        active = self.db.active()
                        if first is None and active:
                            first = active[0]["id"]
                        if first and self.db.get(first)["state"] in TERMINAL:
                            return
                        if not active and not any(j["state"] == "queued" for j in self.db.jobs(100000)):
                            return
                    if until_idle and not self.db.active() and not any(j["state"] == "queued" for j in self.db.jobs(100000)):
                        return
                    time.sleep(self.cfg.poll_seconds)
            finally:
                self.export_events()
                atomic_json(self.cfg.data / "supervisor.json", {"pid": os.getpid(), "time": now(), "status": "stopped"})
