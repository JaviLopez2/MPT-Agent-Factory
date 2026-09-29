"""Standalone stdlib bootstrap launched with MPT's own Python, never Factory's venv.

The isolated process owns the GPU lock and survives a supervisor restart.
Its only supported external input is an immutable request.json created by Factory.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

# This script is also executed directly by an unrelated portable interpreter.
from common import FileLock, atomic_json, digest, inside, now, read_json, redact


def protect_sources(roots):
    """Catch accidental Python writes to source trees; not a hostile-code sandbox."""
    roots = [Path(root).resolve() for root in roots]

    def protected(value):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        candidate = Path(os.fsdecode(value)).resolve()
        return any(candidate == root or candidate.is_relative_to(root) for root in roots)

    def audit(event, args):
        targets = []
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                targets = [path]
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime"}:
            targets = args[:1]
        elif event in {"os.rename", "os.link", "os.symlink"}:
            targets = args[:2]
        if any(protected(p) for p in targets):
            raise PermissionError("Factory generation cannot write inside an MPT source tree")
    sys.addaudithook(audit)


def run_mpt(request, folder):
    root = Path(request["mpt_root"]).resolve()
    if not (root / "config.toml").is_file():
        raise ValueError("MPT config.toml missing; configure the existing MPT installation first")
    protect_sources({str(root), request["stable_root"]})
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from app.config import config

    spec = request["spec"]
    # Enforce before importing services; cannot be overridden by a job.
    config.app.update({"upload_post_auto_upload": False, "enable_redis": False,
                       "material_directory": "task", "openai_image_performance_profile": spec["profile"],
                       "openai_image_manual_reference_mode": spec["reference_mode"]})

    def deny(*args, **kwargs):
        raise PermissionError("Publishing/config writes are disabled in MPT Agent Factory v0.1")
    config.save_config = deny

    from loguru import logger
    secrets = []

    def collect_secrets(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if any(word in key.lower() for word in ("api_key", "password", "secret", "token")):
                    if isinstance(item, str) and len(item) > 3:
                        secrets.append(item)
                    elif isinstance(item, list):
                        secrets.extend(v for v in item if isinstance(v, str) and len(v) > 3)
                else:
                    collect_secrets(item)
    collect_secrets(config._cfg)

    def sink(message):
        record = message.record
        text = record["message"]
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        with (folder / "mpt.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": now(), "level": record["level"].name,
                                     "message": redact(text)}, ensure_ascii=False) + "\n")
    logger.remove()
    logger.add(sink, level="INFO", diagnose=False, backtrace=False)

    from app.utils import utils
    original_storage = utils.storage_dir
    storage = folder / "storage"

    def isolated_storage(sub_dir="", create=False):
        # Existing assets (fonts are resource_dir, BGM/local inputs are read-only)
        # remain available; all generated caches/tasks go to the attempt directory.
        if sub_dir == "bgm":
            return original_storage(sub_dir, create=False)
        target = inside(storage / sub_dir, storage)
        if create:
            target.mkdir(parents=True, exist_ok=True)
        return str(target)
    utils.storage_dir = isolated_storage

    import cli
    from app.models.schema import VideoParams
    raw_params = spec["params"]
    unknown = set(raw_params) - set(VideoParams.model_fields)
    if unknown:
        raise ValueError(f"Unknown VideoParams fields: {sorted(unknown)}")
    cli_args = ["--video-subject", raw_params["video_subject"],
                "--video-source", raw_params["video_source"], "--stop-at", "video"]
    if raw_params["video_source"] == "local":
        cli_args += ["--video-materials", raw_params["video_materials"][0]["url"]]
    args = cli.parse_args(cli_args)
    defaults = cli.build_video_params(args).model_dump()
    params = VideoParams.model_validate({**defaults, **raw_params})
    # Reuse MPT validation and its local-material security boundary. Copies are
    # written to attempt storage, never the stable installation's local_videos.
    cli.prepare_cli_files(params, stop_at="video")

    task_id = request["job_id"]
    task_dir = Path(utils.task_dir(task_id))
    references = task_dir / "user_references"
    references.mkdir(exist_ok=True)
    manifest = {"schema_version": 4, "mode": spec["reference_mode"],
                "model": "qwen-image-2.1-precision", "max_images": 20, "files": []}
    for index, item in enumerate(spec["references"], 1):
        source = Path(item["path"])
        if digest(source) != item["sha256"]:
            raise ValueError("Snapshotted reference changed after submission")
        target = references / f"reference-{index:02d}{source.suffix}"
        shutil.copy2(source, target)
        manifest["files"].append({"stored": target.name, "original": item["original"],
            "slot": index, "role": item["role"], "description": item["description"],
            "anchor": item["anchor"] if item["role"] == "identity" else False, "sha256": item["sha256"]})
    atomic_json(references / "manifest.json", manifest)
    atomic_json(folder / "effective_params.json", redact(params.model_dump(mode="json")))
    atomic_json(folder / "provenance.json", {"mpt_commit": request["mpt_commit"],
        "mpt_root": str(root), "config_sha256": digest(root / "config.toml"),
        "python": sys.version, "executable": sys.executable, "publishing": False,
        "profile": spec["profile"], "experiment_id": request.get("experiment_id")})

    from app.services import state as sm
    from app.services import task
    task._schedule_cross_post = deny
    task.upload_post.cross_post_video = deny

    class PersistentState(sm.MemoryState):
        def update_task(self, *args, **kwargs):
            super().update_task(*args, **kwargs)
            current = self.get_task(task_id) or {}
            atomic_json(folder / "progress.json", {
                "time": now(), "progress": current.get("progress", 0),
                "state": current.get("state"), "failed_stage": current.get("failed_stage"),
            })
    sm.state = PersistentState()
    result = task.start(task_id=task_id, params=params, stop_at="video", allow_server_file_input=True)
    if not isinstance(result, dict) or not result or result.get("state") == task.const.TASK_STATE_FAILED:
        raise RuntimeError(str((result or {}).get("error", "MPT returned no result")))
    if not result.get("videos"):
        raise RuntimeError("MPT returned no final videos")
    return redact(result)


def main():
    request_path = Path(sys.argv[1]).resolve()
    request = read_json(request_path)
    folder = request_path.parent
    done = threading.Event()
    result = {"job_id": request["job_id"], "started": now(), "status": "failed"}
    # Publish the identity of the actual interpreter before taking the GPU lock.
    # This is distinct from the PID returned by Popen on Windows when a venv
    # launcher/redirector process sits in front of the real Python process.
    atomic_json(folder / "worker.json", {"pid": os.getpid(), "time": now()})

    def heartbeat():
        while not done.is_set():
            atomic_json(folder / "heartbeat.json", {"pid": os.getpid(), "time": now()})
            done.wait(2)
    try:
        with FileLock(Path(request["gpu_lock"])):
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            result["mpt_result"] = run_mpt(request, folder)
            result["status"] = "succeeded"
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        # Full traceback goes to local worker.log; no config/request dumps.
        traceback.print_exc()
    finally:
        done.set()
        result["finished"] = now()
        atomic_json(folder / "result.json", result)
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
