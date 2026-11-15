from __future__ import annotations

import argparse
import os
from pathlib import Path

# Must precede Gradio imports. No analytics, Hub telemetry, or remote themes/fonts.
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

from app.config import Config, configure_logging  # noqa: E402


class InstanceLock:
    """An OS-held lock prevents two apps from recovering each other's running jobs."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if self.handle.read(1) == b"":
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Local GPT Image Workbench")
    parser.add_argument("--demo", action="store_true", help="Disable paid API access even if a key exists")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    config = Config(data_dir=args.data_dir, demo=args.demo)
    port = args.port or int(os.getenv("WORKBENCH_PORT", "7865"))
    os.environ["GRADIO_TEMP_DIR"] = str(config.data_dir / "gradio-cache")
    configure_logging(config.data_dir)
    from app.service import Workbench
    from app.ui.application import build_ui, launch_options

    with InstanceLock(config.data_dir / ".instance.lock"):
        workbench = Workbench(config)
        workbench.repo.recover_interrupted()
        try:
            demo = build_ui(workbench)
            demo.queue(default_concurrency_limit=4, max_size=64)
            print(f"GPT Image Workbench: http://127.0.0.1:{port}")
            print(
                "API disabled (demo mode)."
                if not config.api_key
                else "API key detected. Paid calls require UI confirmation."
            )
            demo.launch(server_port=port, inbrowser=not args.no_browser, **launch_options(workbench))
        finally:
            workbench.close()


if __name__ == "__main__":
    main()
