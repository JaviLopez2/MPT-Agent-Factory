from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mpt_factory.common import atomic_json, digest, inside, read_json


def collect(folder: Path):
    artifacts = []
    for file in sorted(folder.rglob("*")):
        if not file.is_file() or file.is_symlink() or file.name.endswith(".tmp"):
            continue
        inside(file, folder)
        suffix = file.suffix.lower()
        if suffix not in {".mp4", ".png", ".jpg", ".jpeg", ".webp", ".json", ".jsonl", ".log", ".srt", ".mp3", ".wav"}:
            continue
        if file.name in {"artifact_manifest.json", "evaluation.json", "heartbeat.json", "request.json"}:
            continue
        kind = "metadata"
        if suffix == ".mp4":
            kind = "final_video" if file.name.startswith("final-") else "scene_video"
        elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            kind = "reference" if "user_references" in file.parts else "image"
        elif file.name == "precision_diagnostics.json":
            kind = "diagnostics"
        elif suffix in {".log", ".jsonl"}:
            kind = "log"
        artifacts.append({"path": str(file.resolve()), "kind": kind,
                          "size": file.stat().st_size, "sha256": digest(file)})
    atomic_json(folder / "artifact_manifest.json", artifacts)
    return artifacts


def evaluate(folder, artifacts, spec, ffprobe):
    """Technical checks only. A success is never a claim of visual/factual quality."""
    failures, warnings, videos, diagnostics = [], [], [], []
    for item in artifacts:
        if item["kind"] == "final_video":
            try:
                output = subprocess.check_output([ffprobe, "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type,width,height", "-of", "json", item["path"]],
                    stderr=subprocess.STDOUT, timeout=30)
                info = json.loads(output)
                streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
                if not streams or float(info.get("format", {}).get("duration", 0)) <= 0:
                    failures.append("Invalid video stream or zero duration")
                videos.append({"path": item["path"], **info})
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                failures.append(f"ffprobe failed: {type(exc).__name__}")
        elif item["kind"] == "diagnostics":
            diagnostic = read_json(Path(item["path"]))
            if not isinstance(diagnostic, dict):
                failures.append("Invalid precision_diagnostics.json")
                continue
            scenes = diagnostic.get("plan_scenes", [])
            if not isinstance(scenes, list):
                failures.append("Malformed plan_scenes")
                scenes = []
            summary = {k: diagnostic.get(k) for k in ("schema_version", "status", "scene_count", "generated_scene_count")}
            summary["precision_scenes"] = sum(s.get("route") == "precision" for s in scenes if isinstance(s, dict))
            diagnostics.append(summary)
            if diagnostic.get("status") != "completed":
                failures.append("Image generation diagnostics are not completed")
            if diagnostic.get("schema_version") != 3:
                warnings.append("Unknown diagnostics schema; semantic evaluation unavailable")
            for scene in scenes:
                if not isinstance(scene, dict):
                    failures.append("Malformed diagnostic scene")
                    continue
                if scene.get("coverage_status") in {"unsupported", "missing", "uncovered"}:
                    warnings.append(f"Scene {scene.get('scene')}: evidence coverage requires review")
    if not videos:
        failures.append("No final MP4")
    if spec["params"]["video_source"] == "openai_image":
        if not diagnostics:
            failures.append("Missing precision_diagnostics.json")
        if not any(a["kind"] == "image" for a in artifacts):
            failures.append("Missing generated scene images")
    result = {"technical_pass": not failures, "failures": failures, "warnings": warnings,
              "visual_quality": "not_evaluated", "human_review_required": True,
              "videos": videos, "diagnostics": diagnostics}
    atomic_json(folder / "evaluation.json", result)
    return result
