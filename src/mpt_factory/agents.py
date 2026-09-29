"""Narrow extension contracts; no LLM or automatic code promotion in v0.1."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mpt_factory import artifacts


class VideoAgent(Protocol):
    def launch(self, job: dict, request: Path) -> tuple[int, float]: ...


class EvaluatorAgent(Protocol):
    def evaluate(self, folder: Path, items: list, spec: dict) -> dict: ...


class EngineerAgent(Protocol):
    def propose_experiment(self, finding: dict) -> dict:
        """Return a proposal; implementation must target a registered isolated worktree."""
        ...


class TechnicalEvaluator:
    def __init__(self, ffprobe):
        self.ffprobe = ffprobe

    def evaluate(self, folder, items, spec):
        return artifacts.evaluate(folder, items, spec, self.ffprobe)
