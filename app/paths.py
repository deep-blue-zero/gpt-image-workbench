"""Separate shipped resources from writable libraries in desktop distributions."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def application_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd().resolve()


def desktop_home(root: Path | None = None) -> Path:
    root = root or application_root()
    if (root / "portable.flag").is_file():
        return root
    return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "GPT Image Workbench"


def default_data_dir(root: Path) -> Path:
    if getattr(sys, "frozen", False):
        return desktop_home(root) / "data"
    return root / "data"
