"""Small native launcher; the full workbench continues to run in the user's browser."""

from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.config import Config
from app.desktop_server import AlreadyRunning, DesktopServer
from app.paths import application_root, desktop_home


def saved_library(root: Path) -> Path | None:
    path = desktop_home(root) / "launcher.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text("utf-8"))["library"]
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_dir():
            raise ValueError
        return Path(value)
    except (ValueError, KeyError, TypeError):
        raise ValueError(
            "The saved library is unavailable. Reconnect its drive or choose a library."
        ) from None


def save_library(root: Path, library: Path):
    path = desktop_home(root) / "launcher.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"library": str(library.resolve())}, indent=2), "utf-8")
    tmp.replace(path)


class Launcher:
    def __init__(self, args):
        self.args = args
        self.root_path = application_root()
        self.server = None
        self.busy = False
        self.messages = queue.Queue()
        self.window = tk.Tk()
        self.window.title("GPT Image Workbench")
        self.window.geometry("540x315")
        self.window.minsize(480, 300)
        self.window.protocol("WM_DELETE_WINDOW", self.quit)
        frame = ttk.Frame(self.window, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="GPT Image Workbench", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Your image studio opens in your browser.", padding=(0, 8)).pack(anchor="w")
        self.status = tk.StringVar(value="Starting Workbench…")
        ttk.Label(frame, textvariable=self.status, wraplength=480).pack(anchor="w", pady=10)
        self.library = tk.StringVar()
        ttk.Label(frame, textvariable=self.library, wraplength=480).pack(anchor="w", pady=5)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=12)
        self.open_button = ttk.Button(
            buttons, text="Open Workbench", command=self.open_browser, state="disabled"
        )
        self.open_button.pack(side="left", padx=(0, 8))
        self.choose_button = ttk.Button(buttons, text="Choose library…", command=self.choose_library)
        self.choose_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Quit Workbench", command=self.quit).pack(side="right")
        ttk.Button(
            frame,
            text="Downloads and updates",
            command=lambda: webbrowser.open(
                "https://github.com/deep-blue-zero/gpt-image-workbench/releases/latest"
            ),
        ).pack(anchor="w")
        try:
            self.start(args.data_dir or saved_library(self.root_path))
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
        self.window.after(150, self.poll)

    def background(self, action, success):
        self.busy = True
        self.choose_button.config(state="disabled")

        def run():
            try:
                action()
                self.messages.put((success, None))
            except AlreadyRunning as exc:
                self.messages.put(("existing", exc))
            except Exception:
                # Never put arbitrary exception text (which may include credentials) in diagnostics.
                self.messages.put(
                    (
                        "error",
                        "Could not start Workbench. Check library permissions and free disk space. "
                        "Choose another library or restart. See workbench.jsonl in the library.",
                    )
                )

        threading.Thread(target=run, daemon=False).start()

    def start(self, library):
        config = Config(root=self.root_path, data_dir=library, demo=self.args.demo)
        self.server = DesktopServer(config, self.args.port)
        self.library.set(f"Library: {config.data_dir}")
        self.background(self.server.start, "ready")

    def poll(self):
        try:
            event, value = self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.choose_button.config(state="normal")
            if event == "ready":
                self.status.set(
                    "Running. Keep this launcher open. Closing the browser does not stop requests."
                )
                self.open_button.config(state="normal")
                if not self.args.no_browser:
                    self.open_browser()
            elif event == "existing":
                if value.url:
                    webbrowser.open(value.url)
                    self.window.destroy()
                    return
                self.status.set(str(value))
            elif event == "quit":
                self.window.destroy()
                return
            elif event == "switch":
                self.start(self.next_library)
            else:
                self.status.set(value)
        self.window.after(150, self.poll)

    def open_browser(self):
        if self.server and self.server.url:
            webbrowser.open(self.server.url)

    def choose_library(self):
        if self.busy:
            return
        if self.server and self.server.workbench:
            job = self.server.workbench.job
            if job and job.finished is None:
                messagebox.showinfo(
                    "Request active", "Let the active request finish before changing libraries."
                )
                return
        folder = filedialog.askdirectory(title="Choose the library folder (containing workbench.sqlite3)")
        if not folder:
            return
        self.next_library = Path(folder)
        try:
            save_library(self.root_path, self.next_library)
        except OSError:
            messagebox.showerror(
                "Could not save", "Could not save the library preference. Check folder permissions."
            )
            return
        self.open_button.config(state="disabled")
        self.status.set("Switching libraries… Existing files will be preserved.")
        self.background(self.server.stop if self.server else lambda: None, "switch")

    def quit(self):
        if self.busy:
            messagebox.showinfo(
                "Please wait", "Workbench is starting or stopping. Please wait for it to finish."
            )
            return
        if self.server and self.server.workbench:
            job = self.server.workbench.job
            if (
                job
                and job.finished is None
                and not messagebox.askokcancel(
                    "Finish request and quit?",
                    "The current request will finish and its result will be saved. Remaining queued requests will be "
                    "cancelled. Submitted remote Batch jobs continue at OpenAI. Quit when saving finishes?",
                )
            ):
                return
        self.status.set("Finishing any active request and saving results before quitting…")
        self.open_button.config(state="disabled")
        self.background(self.server.stop if self.server else lambda: None, "quit")


def self_test(args):
    """Exercise the bundled runtime, Tk, server, assets and storage without API access."""
    window = tk.Tk()
    window.withdraw()
    window.update()
    config = Config(data_dir=args.data_dir, demo=True)
    server = DesktopServer(config, args.port)
    try:
        from uuid import uuid4

        from app.credentials import WindowsCredentialStore

        vault = WindowsCredentialStore(config.data_dir)
        vault.service = "GPT Image Workbench package test " + uuid4().hex
        try:
            vault.set("offline-packaged-credential-fixture")
            assert vault.get() == "offline-packaged-credential-fixture"
        finally:
            vault.delete()
        url = server.start()
        with urllib.request.urlopen(url, timeout=30) as response:
            page = response.read().decode("utf-8")
        assets = re.findall(r'(?:src|href)="(\./assets/[^"\s]+\.(?:js|css))"', page)
        assert assets, "The packaged frontend must provide its JavaScript and stylesheets."
        for asset in assets:
            with urllib.request.urlopen(url + "/" + asset.removeprefix("./"), timeout=30) as response:
                assert response.status == 200 and response.read(64)
        with urllib.request.urlopen(url + "/config", timeout=30) as response:
            ui = json.load(response)
        identity = server.workbench.demo_image()
        image_path = server.workbench.repo.image_path(identity)
        assert image_path.is_file()
        assert config.api_key is None
        assert any(c.get("props", {}).get("label") == "OpenAI API key" for c in ui["components"])
        result = {
            "status": "passed",
            "frozen": bool(getattr(sys, "frozen", False)),
            "api_access": False,
            "data_dir": str(config.data_dir),
            "image_id": identity,
            "components": len(ui["components"]),
            "tk": tk.TkVersion,
            "credential_manager": "passed",
            "frontend_assets": len(assets),
        }
    finally:
        server.stop()
        window.destroy()
    args.self_test.parent.mkdir(parents=True, exist_ok=True)
    args.self_test.write_text(json.dumps(result, indent=2), "utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--self-test", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        try:
            self_test(args)
        except Exception:
            import traceback

            from app.storage.security import sanitize

            args.self_test.parent.mkdir(parents=True, exist_ok=True)
            args.self_test.write_text(
                json.dumps({"status": "failed", "error": sanitize(traceback.format_exc())}), "utf-8"
            )
            raise SystemExit(1) from None
    else:
        Launcher(args).window.mainloop()


if __name__ == "__main__":
    main()
