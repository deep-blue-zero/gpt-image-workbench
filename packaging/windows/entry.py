"""PyInstaller entry point: no terminal window, and no dependency on the working directory."""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Uvicorn and dependencies expect streams even in a Windows GUI executable.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        # The installer checks this mutex instead of terminating a running paid request.
        _installer_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\GPTImageWorkbench")
    from app.desktop_launcher import main

    main()
