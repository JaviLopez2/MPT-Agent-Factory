from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path

from mpt_factory.common import FileLock, inside, now


def git(root: Path, *args) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                   encoding="utf-8", errors="replace", stderr=subprocess.PIPE).strip()


def tracked_clean(root):
    return not git(root, "status", "--porcelain", "--untracked-files=no")


class Worktrees:
    def __init__(self, config, db):
        self.cfg, self.db = config, db

    def stable(self):
        root = self.cfg.mpt_root
        if git(root, "branch", "--show-current") != self.cfg.branch:
            raise ValueError(f"Stable MPT must be on branch {self.cfg.branch}")
        if not tracked_clean(root):
            raise ValueError("Stable MPT has tracked modifications; commit/stash intentionally before running")
        return git(root, "rev-parse", "HEAD")

    def create(self, label):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", label):
            raise ValueError("Experiment label: lowercase letters, digits and hyphens, max 40")
        with FileLock(self.cfg.data / "worktrees.lock"):
            base = self.stable()
            experiment = f"exp-{label}-{uuid.uuid4().hex[:8]}"
            branch = f"factory/{experiment}"
            target = inside(self.cfg.worktrees / experiment, self.cfg.worktrees)
            target.parent.mkdir(parents=True, exist_ok=True)
            git(self.cfg.mpt_root, "worktree", "add", "-b", branch, str(target), base)
            # Configuration is private runtime data, only copied if Git ignores it.
            runtime_config = self.cfg.mpt_root / "config.toml"
            if runtime_config.is_file():
                try:
                    git(target, "check-ignore", "config.toml")
                except subprocess.CalledProcessError:
                    raise ValueError(f"config.toml is not ignored in {target}; provision it manually")
                shutil.copy2(runtime_config, target / "config.toml")
            with self.db.connect() as db:
                db.execute("INSERT INTO experiments(id,path,branch,base_commit,created) VALUES(?,?,?,?,?)",
                           (experiment, str(target), branch, base, now()))
            self.db.event(None, "experiment_created", id=experiment, branch=branch, base_commit=base)
            return self.get(experiment)

    def get(self, experiment):
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM experiments WHERE id=?", (experiment,)).fetchone()
        if not row:
            raise ValueError("Experiment is not registered")
        return dict(row)

    def root(self, experiment=None):
        if not experiment:
            self.stable()
            return self.cfg.mpt_root
        record = self.get(experiment)
        root = inside(Path(record["path"]), self.cfg.worktrees)
        if root == self.cfg.mpt_root or git(root, "branch", "--show-current") != record["branch"]:
            raise ValueError("Experiment branch/path mismatch")
        common = lambda p: Path(git(p, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        if common(root) != common(self.cfg.mpt_root):
            raise ValueError("Experiment must be a worktree of the configured MPT repository")
        return root

    def check(self, experiment):
        """Fixed checks, no arbitrary shell commands and no stable target."""
        root = self.root(experiment)
        log_dir = self.cfg.data / "experiments" / experiment
        log_dir.mkdir(parents=True, exist_ok=True)
        commands = [
            [str(self.cfg.mpt_python), "-m", "compileall", "-q", "app", "cli.py"],
            [str(self.cfg.mpt_python), "-m", "pytest", "-q"],
        ]
        results = []
        with FileLock(self.cfg.gpu_lock):
            for index, command in enumerate(commands):
                log = log_dir / f"check-{index}.log"
                with log.open("wb") as stream:
                    try:
                        run = subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT, timeout=3600)
                        code = run.returncode
                    except subprocess.TimeoutExpired:
                        code = 124
                results.append({"command": command[1:], "exit_code": code, "log": str(log)})
                if code:
                    break
        self.db.event(None, "experiment_checks", experiment_id=experiment, results=results)
        return results
