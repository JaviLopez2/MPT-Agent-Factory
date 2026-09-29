import socket
import sys
import time
from dataclasses import replace

from mpt_factory.services import Services


def test_manage_real_http_service_and_recover_ownership(stack):
    cfg, db = stack
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    item = {"name": "test-http", "url": f"http://127.0.0.1:{port}/",
            "command": [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            "cwd": str(cfg.data), "auto_start": True}
    cfg = replace(cfg, services=(item,))
    manager = Services(cfg, db)
    try:
        manager.start("test-http")
        deadline = time.monotonic() + 10
        while not manager.health(item)["healthy"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert manager.health(item)["healthy"]
        with db.connect() as connection:
            initial = dict(connection.execute("SELECT * FROM service_processes").fetchone())
        # New manager reuses the persisted identity, not a second port owner.
        restarted = Services(cfg, db)
        restarted.start("test-http")
        with db.connect() as connection:
            assert dict(connection.execute("SELECT * FROM service_processes").fetchone()) == initial
        restarted.stop("test-http")
        assert not restarted.health(item)["port_open"]
    finally:
        with db.connect() as connection:
            owned = connection.execute("SELECT 1 FROM service_processes").fetchone()
        if owned:
            manager.stop("test-http")
