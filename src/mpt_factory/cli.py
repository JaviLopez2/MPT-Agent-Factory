from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from mpt_factory.config import load_config
from mpt_factory.db import Database
from mpt_factory.jobs import create_job
from mpt_factory.services import Services
from mpt_factory.supervisor import Supervisor
from mpt_factory.worktrees import Worktrees


def parser():
    p = argparse.ArgumentParser(description="MPT Agent Factory v0.1 — local deterministic supervisor")
    p.add_argument("--config", default="factory.toml")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--probe-mpt", action="store_true", help="Import MPT dependencies without generating")
    create = sub.add_parser("create")
    create.add_argument("file", type=Path)
    create.add_argument("--queue", action="store_true")
    create.add_argument("--priority", type=int, default=0)
    create.add_argument("--experiment")
    for cmd in ("enqueue", "show", "cancel", "retry"):
        sub.add_parser(cmd).add_argument("id")
    sub.add_parser("list")
    sup = sub.add_parser("supervisor")
    mode = sup.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--until-idle", action="store_true")
    services = sub.add_parser("services")
    services.add_argument("action", choices=("status", "start", "stop"))
    services.add_argument("name", nargs="?")
    exp = sub.add_parser("experiment")
    exp.add_argument("action", choices=("create", "show", "check"))
    exp.add_argument("name")
    dash = sub.add_parser("dashboard")
    dash.add_argument("--port", type=int, default=8600)
    return p


def doctor(cfg, db, probe=False):
    report = {"python_exists": cfg.mpt_python.is_file(),
              "mpt_exists": (cfg.mpt_root / "app/services/task.py").is_file(),
              "config_exists": (cfg.mpt_root / "config.toml").is_file(),
              "services": Services(cfg, db).all_health(), "database": str(cfg.db)}
    try:
        report["mpt_commit"] = Worktrees(cfg, db).stable()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        report["git_error"] = str(exc)
    try:
        report["ffprobe"] = subprocess.check_output([cfg.ffprobe, "-version"], text=True, timeout=10).splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        report["ffprobe"] = "MISSING — configure factory.ffprobe"
    if probe and report["python_exists"] and report["config_exists"]:
        runner_dir = Path(__file__).parent
        # A read-only import probe; no pipeline, background jobs or UI.
        script = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from runner import protect_sources; protect_sources([sys.argv[2]]); sys.path.insert(0,sys.argv[2]); from app.config import config; config.app['upload_post_auto_upload']=False; config.app['enable_redis']=False; from app.services.task import start; from app.models.schema import VideoParams; print('MPT_IMPORT_OK')"
        result = subprocess.run([str(cfg.mpt_python), "-B", "-c", script, str(runner_dir), str(cfg.mpt_root)],
            cwd=cfg.data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=120,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        report["mpt_import_ok"] = result.returncode == 0
        if result.returncode:
            report["mpt_import_error"] = result.stderr[-2500:]
    return report


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        db = Database(cfg.db)
        result = None
        if args.command == "init":
            result = {"database": str(cfg.db), "schema_version": 1}
        elif args.command == "doctor":
            result = doctor(cfg, db, args.probe_mpt)
        elif args.command == "create":
            file = args.file.resolve()
            spec = json.loads(file.read_text(encoding="utf-8-sig"))
            result = create_job(cfg, db, spec, file.parent, args.priority, args.experiment)
            if args.queue:
                db.transition(result, "queued")
        elif args.command == "enqueue":
            db.transition(args.id, "queued")
            result = args.id
        elif args.command == "cancel":
            db.cancel(args.id)
            result = db.get(args.id)
        elif args.command == "retry":
            old = db.get(args.id)
            if old["state"] not in {"failed", "interrupted", "cancelled"}:
                raise ValueError("Only failed/interrupted/cancelled jobs can be manually retried")
            result = create_job(cfg, db, json.loads(old["spec"]), cfg.data, old["priority"], old["experiment_id"])
            db.transition(result, "queued")
            db.event(result, "manual_retry", previous_job=args.id)
        elif args.command == "show":
            result = {"job": db.get(args.id), "artifacts": db.artifacts(args.id), "events": db.events(args.id)}
        elif args.command == "list":
            result = [{k: row[k] for k in ("id", "title", "state", "progress", "error")} for row in db.jobs()]
        elif args.command == "supervisor":
            Supervisor(cfg).run(args.once, args.until_idle)
        elif args.command == "services":
            manager = Services(cfg, db)
            if args.action == "status":
                result = manager.all_health()
            elif not args.name:
                raise ValueError("A service name is required")
            else:
                result = getattr(manager, args.action)(args.name)
        elif args.command == "experiment":
            manager = Worktrees(cfg, db)
            result = {"create": manager.create, "show": manager.get, "check": manager.check}[args.action](args.name)
        elif args.command == "dashboard":
            env = {**os.environ, "MPT_FACTORY_CONFIG": str(cfg.file)}
            return subprocess.call([sys.executable, "-m", "streamlit", "run",
                str(Path(__file__).with_name("dashboard.py")), "--server.port", str(args.port),
                "--server.address", "127.0.0.1", "--browser.gatherUsageStats", "false"], env=env)
        if result is not None:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    except KeyboardInterrupt:
        print("Supervisor stopped. Active worker continues; restart supervisor to recover it.")
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
