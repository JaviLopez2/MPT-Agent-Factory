from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from mpt_factory.common import FileLock
from mpt_factory.processes import alive, detached_options, identify, terminate_tree


class Services:
    def __init__(self, config, db):
        self.cfg, self.db = config, db

    def item(self, name):
        return next(s for s in self.cfg.services if s["name"] == name)

    def health(self, item):
        url = urlparse(item["url"])
        result = {"name": item["name"], "url": item["url"], "required": item.get("required", False),
                  "healthy": False, "port_open": False, "detail": ""}
        try:
            with socket.create_connection((url.hostname, url.port or 80), timeout=1):
                result["port_open"] = True
            request = urllib.request.Request(item["url"])
            if item.get("token_env"):
                token = os.environ.get(item["token_env"])
                if token:
                    request.add_header("Authorization", f"Bearer {token}")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(request, timeout=2) as response:
                    code, body = response.status, response.read(2 * 1024 * 1024)
            except urllib.error.HTTPError as exc:
                code, body = exc.code, exc.read(1024)
            result["http_status"] = code
            result["healthy"] = code in item.get("expected_status", [200])
            if result["healthy"] and item.get("json_key"):
                payload = json.loads(body)
                result["healthy"] = isinstance(payload, dict) and item["json_key"] in payload
            result["detail"] = "ready" if result["healthy"] else f"Unexpected response: HTTP {code}"
            if item.get("readiness") == "liveness_only" and result["healthy"]:
                result["detail"] = "HTTP alive; workflow/model readiness checked by generation"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            result["detail"] = type(exc).__name__
        return result

    def all_health(self):
        return [self.health(s) for s in self.cfg.services]

    def start(self, name):
        item = self.item(name)
        # Serialize dashboard/CLI/supervisor management across processes.
        with FileLock(self.cfg.data / "services.lock"):
            health = self.health(item)
            if health["healthy"]:
                return health
            if health["port_open"]:
                raise RuntimeError(f"Port occupied by an unhealthy/external process: {name}")
            with self.db.connect() as db:
                owned = db.execute("SELECT * FROM service_processes WHERE name=?", (name,)).fetchone()
            if owned and alive(owned["pid"], owned["started"]):
                return health  # Still starting; don't spawn duplicate servers.
            command = item.get("command") or []
            if not command:
                raise RuntimeError(f"No launch command configured for {name}; start your existing launcher")
            cwd = Path(os.path.expandvars(item.get("cwd", str(self.cfg.file.parent)))).resolve()
            log = self.cfg.data / "logs" / f"service-{name}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("ab") as stream:
                process = subprocess.Popen([os.path.expandvars(p) for p in command], cwd=cwd,
                    stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    **detached_options())
            started = identify(process.pid)
            if started is None:
                raise RuntimeError(f"Service exited on startup: {name}; inspect {log}")
            with self.db.connect() as db:
                db.execute("INSERT OR REPLACE INTO service_processes VALUES(?,?,?)", (name, process.pid, started))
            self.db.event(None, "service_started", name=name, pid=process.pid)
            return self.health(item)

    def stop(self, name):
        with FileLock(self.cfg.data / "services.lock"):
            if self.db.active():
                raise RuntimeError("Cannot stop services while a Factory job is active")
            with self.db.connect() as db:
                row = db.execute("SELECT * FROM service_processes WHERE name=?", (name,)).fetchone()
            if not row:
                raise RuntimeError("Service not started by Factory; refusing to stop an external process")
            terminate_tree(row["pid"], row["started"])
            with self.db.connect() as db:
                db.execute("DELETE FROM service_processes WHERE name=?", (name,))
            self.db.event(None, "service_stopped", name=name)

    def ensure(self, required):
        names = {s["name"] for s in self.cfg.services}
        if not set(required) <= names:
            raise ValueError(f"Unknown required services: {set(required) - names}")
        results = []
        for item in self.cfg.services:
            if item["name"] not in required:
                continue
            state = self.health(item)
            if not state["healthy"] and item.get("auto_start", False):
                try:
                    state = self.start(item["name"])
                except (OSError, RuntimeError) as exc:
                    state["detail"] = str(exc)
            results.append(state)
        return results

    def gpu_idle(self):
        """ComfyUI queue is shared with external UIs; don't overlap existing work."""
        item = next((s for s in self.cfg.services if s["name"] == "comfyui"), None)
        if not item:
            return True
        parsed = urlparse(item["url"])
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"{parsed.scheme}://{parsed.netloc}/queue", timeout=2) as response:
                queue = json.load(response)
            return not queue.get("queue_running") and not queue.get("queue_pending")
        except (OSError, ValueError):
            return False
