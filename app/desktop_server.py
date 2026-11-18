"""Loopback server shared by the source and desktop launchers."""

from __future__ import annotations

import json
import os
import secrets
import socket
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from app.config import Config, configure_logging
from app.lifecycle import RestartTarget
from app.main import InstanceLock


class AlreadyRunning(RuntimeError):
    def __init__(self, url: str | None):
        self.url = url
        super().__init__(
            "This library is already open. Close its other Workbench before changing launch options."
        )


def existing_url(data_dir: Path) -> str | None:
    try:
        record = json.loads((data_dir / ".desktop-instance.json").read_text("utf-8"))
        url = record["url"]
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            return None
        with urllib.request.urlopen(url.rstrip("/") + "/workbench/instance", timeout=2) as response:
            return url if json.load(response).get("instance") == record["instance"] else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def available_port(preferred: int = 7865) -> int:
    for port in range(preferred, min(preferred + 100, 65536)):
        with socket.socket() as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError("No local port is available. Close unused Workbench instances and try again.")


class DesktopServer:
    def __init__(self, config: Config, port: int | None = None, *, restarted_from: str | None = None):
        self.config = config
        self.port = port
        self.instance = secrets.token_hex(24)
        self.restarted_from = restarted_from
        self.restart_requested = threading.Event()
        self.lock = InstanceLock(config.data_dir / ".instance.lock")
        self.owns_lock = False
        self.workbench = None
        self.ui = None
        self.url = None

    def start(self) -> str:
        try:
            self.lock.__enter__()
        except RuntimeError:
            raise AlreadyRunning(existing_url(self.config.data_dir)) from None
        self.owns_lock = True
        try:
            os.environ["GRADIO_TEMP_DIR"] = str(self.config.data_dir / "gradio-cache")
            configure_logging(self.config.data_dir)
            from app.service import Workbench
            from app.ui.application import build_ui, launch_options

            self.workbench = Workbench(self.config)
            self.workbench.repo.recover_interrupted()
            self.ui = build_ui(self.workbench, instance_id=self.instance)
            self.ui.queue(default_concurrency_limit=4, max_size=64)
            self.ui.launch(
                server_port=self.port or available_port(),
                inbrowser=False,
                prevent_thread_lock=True,
                quiet=True,
                **launch_options(self.workbench),
            )
            self.url = self.ui.local_url.rstrip("/")
            self.register_lifecycle_routes()

            path = self.config.data_dir / ".desktop-instance.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"url": self.url, "instance": self.instance, "pid": os.getpid()}), "utf-8"
            )
            temporary.replace(path)
            return self.url
        except Exception:
            self.stop()
            raise

    def restart_target(self) -> RestartTarget:
        return RestartTarget(self.config.data_dir, urlsplit(self.url).port, self.instance)

    def register_lifecycle_routes(self):
        port = urlsplit(self.url).port
        local_origins = {self.url, f"http://localhost:{port}"}

        @self.ui.app.get("/workbench/instance", include_in_schema=False)
        def identity():
            return JSONResponse(
                {"instance": self.instance, "restarted_from": self.restarted_from},
                headers={"Cache-Control": "no-store"},
            )

        @self.ui.app.post("/workbench/restart", include_in_schema=False)
        def restart(request: Request):
            origin = request.headers.get("origin")
            if origin not in local_origins or origin != "http://" + request.headers.get("host", ""):
                raise HTTPException(403, "Open Settings in the local Workbench to restart it.")
            if request.headers.get("x-workbench-instance") != self.instance:
                raise HTTPException(409, "This tab is out of date. Reload it before restarting Workbench.")
            try:
                self.workbench.begin_restart()
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return JSONResponse(
                {"status": "restarting"},
                status_code=202,
                background=BackgroundTask(self.restart_requested.set),
            )

    def stop(self):
        if not self.owns_lock:
            return
        try:
            if self.workbench:
                # Stop new submissions before waiting for an in-flight paid result to save.
                with self.workbench.lock:
                    self.workbench.closing = True
                self.workbench.close()
            if self.ui:
                self.ui.close()
        finally:
            (self.config.data_dir / ".desktop-instance.json").unlink(missing_ok=True)
            self.lock.__exit__(None, None, None)
            self.owns_lock = False
