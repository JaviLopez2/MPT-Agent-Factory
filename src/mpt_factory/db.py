from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from mpt_factory.common import now, redact

ACTIVE = ("preparing", "running", "collecting", "evaluating")
TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
TRANSITIONS = {
    "created": {"queued", "cancelled"},
    "queued": {"preparing", "cancelled"},
    "preparing": {"queued", "running", "failed", "cancelled", "interrupted"},
    "running": {"collecting", "interrupted"},
    "collecting": {"evaluating", "failed", "cancelled", "interrupted"},
    "evaluating": {"succeeded", "failed", "interrupted"},
    **{state: set() for state in TERMINAL},
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES('schema_version','1');
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, state TEXT NOT NULL,
                    spec TEXT NOT NULL, experiment_id TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0, next_run REAL NOT NULL DEFAULT 0,
                    preflight_attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    attempt_dir TEXT, pid INTEGER, process_started REAL, progress INTEGER DEFAULT 0,
                    outcome TEXT, error TEXT, result TEXT);
                CREATE INDEX IF NOT EXISTS queue_order ON jobs(state,next_run,priority,created);
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL,
                    job_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts(
                    job_id TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(job_id,path), FOREIGN KEY(job_id) REFERENCES jobs(id));
                CREATE TABLE IF NOT EXISTS experiments(
                    id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,branch TEXT UNIQUE NOT NULL,
                    base_commit TEXT NOT NULL,created REAL NOT NULL,status TEXT NOT NULL DEFAULT 'isolated');
                CREATE TABLE IF NOT EXISTS service_processes(
                    name TEXT PRIMARY KEY,pid INTEGER NOT NULL,started REAL NOT NULL);
            """)
            if db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] != "1":
                raise RuntimeError("Unsupported database schema; do not downgrade Factory")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db, job, kind, payload):
        db.execute("INSERT INTO events(time,job_id,kind,payload) VALUES(?,?,?,?)",
                   (now(), job, kind, json.dumps(redact(payload), ensure_ascii=False)))

    def event(self, job, kind, **payload):
        with self.connect() as db:
            self._event(db, job, kind, payload)

    def create(self, spec, job_id=None, priority=0, experiment_id=None):
        job_id = job_id or str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO jobs(id,title,state,spec,experiment_id,created,updated,priority) VALUES(?,?,'created',?,?,?,?,?)",
                       (job_id, spec["params"]["video_subject"], json.dumps(spec, ensure_ascii=False),
                        experiment_id, now(), now(), priority))
            self._event(db, job_id, "state", {"from": None, "to": "created"})
        return job_id

    def get(self, job):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown job: {job}")
        return dict(row)

    def jobs(self, limit=200):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,))]

    def active(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs WHERE state IN ('preparing','running','collecting','evaluating') ORDER BY created")]

    def _transition(self, db, job, target, **updates):
        current = db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()
        if not current or target not in TRANSITIONS[current[0]]:
            raise ValueError(f"Illegal transition: {current[0] if current else None} -> {target}")
        self._update(db, job, state=target, updated=now(), **updates)
        self._event(db, job, "state", {"from": current[0], "to": target, **updates})

    def transition(self, job, target, **updates):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._transition(db, job, target, **updates)

    @staticmethod
    def _update(db, job, **updates):
        allowed = {"state", "updated", "next_run", "preflight_attempts", "cancel_requested", "attempt_dir", "pid",
                   "process_started", "progress", "outcome", "error", "result"}
        if not set(updates) <= allowed:
            raise ValueError("Unknown job field")
        db.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in updates)} WHERE id=?", (*updates.values(), job))

    def update(self, job, **updates):
        with self.connect() as db:
            self._update(db, job, updated=now(), **updates)

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','running','collecting','evaluating')").fetchone():
                return None
            row = db.execute("SELECT id FROM jobs WHERE state='queued' AND next_run<=? ORDER BY priority DESC,created LIMIT 1", (now(),)).fetchone()
            if not row:
                return None
            self._transition(db, row[0], "preparing")
            return row[0]

    def cancel(self, job):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()
            if not state:
                raise ValueError("Unknown job")
            if state[0] in {"created", "queued"}:
                self._transition(db, job, "cancelled")
            elif state[0] not in TERMINAL:
                self._update(db, job, cancel_requested=1)
                self._event(db, job, "cancel_requested", {})

    def artifacts(self, job):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM artifacts WHERE job_id=? ORDER BY path", (job,))]

    def save_artifacts(self, job, artifacts):
        with self.connect() as db:
            db.execute("DELETE FROM artifacts WHERE job_id=?", (job,))
            db.executemany("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                           [(job, a["path"], a["kind"], a["size"], a["sha256"]) for a in artifacts])

    def events(self, job=None, after=0):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE seq>? AND (? IS NULL OR job_id=?) ORDER BY seq", (after, job, job))]
