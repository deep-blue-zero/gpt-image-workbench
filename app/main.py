from __future__ import annotations

import argparse
import os
import webbrowser
from pathlib import Path

# Must precede Gradio imports. No analytics, Hub telemetry, or remote themes/fonts.
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

from app.config import Config  # noqa: E402
from app.lifecycle import supervise  # noqa: E402


class InstanceLock:
    """An OS-held lock prevents two apps from recovering each other's running jobs."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                "Another workbench is already using this data folder. Open its browser window instead."
            ) from exc
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()


def _serve(args, stopping):
    from app.desktop_server import DesktopServer

    config = Config(data_dir=args.data_dir, demo=args.demo)
    port = args.port or int(os.getenv("WORKBENCH_PORT", "7865"))
    server = DesktopServer(config, port, restarted_from=args.restarted_from)
    try:
        url = server.start()
        print(f"GPT Image Workbench: {url}", flush=True)
        print(
            "API disabled (demo mode)."
            if not config.api_key
            else "API key detected. Paid calls require UI confirmation.",
            flush=True,
        )
        if not args.no_browser and not args.restarted_from:
            webbrowser.open(url)
        while not stopping():
            if server.restart_requested.wait(0.25):
                return server.restart_target()
    finally:
        server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local GPT Image Workbench")
    parser.add_argument("--demo", action="store_true", help="Disable paid API access even if a key exists")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", type=Path)
    supervise(_serve, parser.parse_args())


if __name__ == "__main__":
    main()
