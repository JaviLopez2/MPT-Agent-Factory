from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from mpt_factory.common import atomic_json, digest
from mpt_factory.worktrees import Worktrees

ROLES = {"identity", "detail", "internal", "context", "other"}


def create_job(cfg, db, spec, base: Path, priority=0, experiment=None):
    """Validate/snapshot inputs at submission. No deferred references to mutable files."""
    if not isinstance(spec, dict) or set(spec) - {"params", "references", "reference_mode", "profile", "required_services"}:
        raise ValueError("Job fields: params, references, reference_mode, profile, required_services")
    spec = json.loads(json.dumps(spec))
    params = spec.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("video_subject"), str) or not params["video_subject"].strip():
        raise ValueError("params.video_subject must be a nonempty string")
    if params.get("video_source") not in {"openai_image", "local"}:
        raise ValueError("v0.1 supports openai_image and local sources")
    if params.get("bgm_type", "") not in {"", "random", "custom"}:
        raise ValueError("v0.1 supports local background music only")
    if spec.get("profile", "balanced") not in {"fast", "balanced", "quality"}:
        raise ValueError("profile must be fast, balanced or quality")
    if spec.get("reference_mode", "user_first") not in {"auto_only", "user_first", "user_only"}:
        raise ValueError("Invalid reference_mode")
    if len(spec.get("references", [])) > 20:
        raise ValueError("At most 20 manual references")
    if experiment:
        Worktrees(cfg, db).root(experiment)
    job_id = str(uuid.uuid4())
    folder = cfg.data / "inputs" / job_id
    folder.mkdir(parents=True)

    def copy(raw, name, extensions=None):
        source = Path(raw).expanduser()
        source = (base / source).resolve() if not source.is_absolute() else source.resolve()
        if not source.is_file() or (extensions and source.suffix.lower() not in extensions):
            raise ValueError(f"Missing/unsupported input: {source}")
        target = folder / f"{name}{source.suffix.lower()}"
        shutil.copy2(source, target)
        return target

    try:
        references = []
        for index, item in enumerate(spec.get("references", []), 1):
            role = item.get("role", "identity")
            if role not in ROLES:
                raise ValueError(f"Unsupported manual role {role}; continuity is planner metadata")
            target = copy(item["path"], f"reference-{index:02d}", {".png", ".jpg", ".jpeg", ".webp"})
            references.append({"path": str(target), "original": Path(item["path"]).name, "role": role,
                "description": str(item.get("description", ""))[:240], "anchor": bool(item.get("anchor", False)),
                "sha256": digest(target)})
        spec["references"] = references
        if params.get("custom_audio_file"):
            params["custom_audio_file"] = str(copy(params["custom_audio_file"], "audio"))
        if params.get("video_source") == "local":
            if not params.get("video_materials"):
                raise ValueError("Local jobs require video_materials")
            for index, material in enumerate(params["video_materials"]):
                material["url"] = str(copy(material["url"], f"material-{index:02d}"))
                material["provider"] = "local"
        spec.setdefault("profile", "balanced")
        spec.setdefault("reference_mode", "user_first")
        required = spec.setdefault("required_services", ["llama", "bridge", "comfyui"] if params["video_source"] == "openai_image" else [])
        if not isinstance(required, list) or not set(required) <= {s["name"] for s in cfg.services}:
            raise ValueError("Unknown required_services")
        atomic_json(folder / "job.json", spec)
        return db.create(spec, job_id, priority, experiment)
    except Exception:
        shutil.rmtree(folder)
        raise
