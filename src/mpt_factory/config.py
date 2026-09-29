from __future__ import annotations

import hashlib
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    file: Path
    data: Path
    mpt_root: Path
    mpt_python: Path
    branch: str
    worktrees: Path
    poll_seconds: float
    timeout_seconds: float
    service_retry_seconds: float
    service_attempts: int
    ffprobe: str
    services: tuple[dict, ...]

    @property
    def db(self):
        return self.data / "factory.sqlite3"

    @property
    def gpu_lock(self):
        # One lock per local machine/user, including distinct Factory data roots.
        return Path(tempfile.gettempdir()) / "mpt-agent-factory-gpu.lock"


def load_config(path: str | Path | None = None) -> Config:
    file = Path(path or os.environ.get("MPT_FACTORY_CONFIG", "factory.toml")).resolve()
    raw = tomllib.loads(file.read_text(encoding="utf-8-sig"))
    factory, mpt = raw.get("factory", {}), raw["mpt"]

    def local(value):
        result = Path(os.path.expandvars(str(value))).expanduser()
        return (file.parent / result).resolve() if not result.is_absolute() else result.resolve()

    data = local(factory.get("data_dir", "data"))
    root = local(mpt["root"])
    worktrees = local(mpt.get("worktrees_dir", "../MPT-worktrees"))
    if data.is_relative_to(root) or worktrees.is_relative_to(root) or root.is_relative_to(worktrees):
        raise ValueError("Factory data and worktrees must be separate from the stable MPT tree")
    services = tuple(raw.get("services", []))
    names = set()
    for item in services:
        if item["name"] in names:
            raise ValueError("Duplicate service name")
        names.add(item["name"])
        url = urlparse(item["url"])
        if url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.scheme != "http":
            raise ValueError("v0.1 service management is restricted to local HTTP endpoints")
        if item.get("command") and not isinstance(item["command"], list):
            raise ValueError("Service command must be an argv array, never a shell string")
    cfg = Config(file, data, root, local(mpt["python"]),
                 mpt.get("branch", "moneyprinter_qwen21_quality_v3_1"), worktrees,
                 float(factory.get("poll_seconds", 2)),
                 float(factory.get("job_timeout_seconds", 14400)),
                 float(factory.get("service_retry_seconds", 30)),
                 int(factory.get("service_attempts", 120)),
                 factory.get("ffprobe", "ffprobe"), services)
    if min(cfg.poll_seconds, cfg.timeout_seconds, cfg.service_retry_seconds, cfg.service_attempts) <= 0:
        raise ValueError("Intervals and attempt limits must be positive")
    data.mkdir(parents=True, exist_ok=True)
    return cfg
