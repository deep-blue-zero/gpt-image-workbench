"""Install, run, upgrade, and uninstall isolated release copies. Never call OpenAI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def run(executable, arguments, environment, report=None):
    process = subprocess.run(
        [str(executable), *arguments], env=environment, cwd=executable.parent, timeout=240
    )
    if process.returncode:
        detail = report.read_text("utf-8") if report and report.exists() else "No self-test report"
        raise RuntimeError(f"{executable.name} exited {process.returncode}: {detail}")


def check_restart(command, library, env=None):
    """Launch an isolated copy, request a restart, and verify the replacement and its library."""
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    marker = library / ".desktop-instance.json"
    settings = library / "settings.json"
    process = subprocess.Popen(
        [*command, "--demo", "--no-browser", "--data-dir", str(library), "--port", str(port)], env=env
    )
    worker_handle = None

    def wait_for_instance(previous):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Launcher exited early with code {process.returncode}")
            try:
                record = json.loads(marker.read_text("utf-8"))
                with urllib.request.urlopen(url + "/workbench/instance", timeout=2) as response:
                    identity = json.load(response)
                if record["instance"] == identity["instance"] and identity["restarted_from"] == previous:
                    assert record["url"] == url
                    return record
            except (OSError, ValueError):
                pass  # The previous worker releases the library before its replacement starts.
            time.sleep(0.2)
        raise TimeoutError("The expected Workbench instance did not become ready.")

    try:
        original = wait_for_instance(None)
        saved = json.loads(settings.read_text("utf-8")) if settings.exists() else {}
        saved["default_moderation"] = "auto" if saved.get("default_moderation", "auto") == "low" else "low"
        settings.write_text(json.dumps(saved), "utf-8")
        request = urllib.request.Request(
            url + "/workbench/restart",
            method="POST",
            headers={"Origin": url, "X-Workbench-Instance": original["instance"]},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 202
        replacement = wait_for_instance(original["instance"])
        assert replacement["pid"] != original["pid"] and replacement["instance"] != original["instance"]
        if os.name == "nt":
            import _winapi

            worker_handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, replacement["pid"])
        with urllib.request.urlopen(url + "/config", timeout=10) as response:
            components = [component["props"] for component in json.load(response)["components"]]
        assert any(
            c.get("label") == "Default moderation for new sessions"
            and c["value"] == saved["default_moderation"]
            for c in components
        )
        assert any(c.get("elem_id") == "restart-workbench" for c in components)
    finally:
        # Closing the test supervisor's pipe lets its worker perform normal shutdown.
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
        if worker_handle is not None:
            try:
                # The native launcher can still hold its executable after releasing the library.
                assert _winapi.WaitForSingleObject(worker_handle, 30000) == _winapi.WAIT_OBJECT_0
            finally:
                _winapi.CloseHandle(worker_handle)
        deadline = time.monotonic() + 30
        while marker.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not marker.exists(), "The test worker must release its library."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, default=Path("dist/releases"))
    parser.add_argument("--report", type=Path, default=Path("build/windows-smoke.json"))
    args = parser.parse_args()
    (installer,) = args.assets.resolve().glob("*-setup.exe")
    (archive,) = args.assets.resolve().glob("*-portable.zip")
    # TemporaryDirectory owns only this new isolated tree; it cannot delete user libraries.
    with tempfile.TemporaryDirectory(prefix="workbench-release-") as directory:
        scratch = Path(directory)
        env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "PYTHONHOME", "PYTHONPATH", "WORKBENCH_DATA_DIR", "WORKBENCH_RUN_LIVE"):
            env.pop(key, None)
        env["LOCALAPPDATA"] = str(scratch / "profile")
        # The executable must operate without any Python or development tools on PATH.
        system_root = os.environ["SYSTEMROOT"]
        env["PATH"] = os.pathsep.join([str(Path(system_root) / "System32"), system_root])
        portable = scratch / "Portable café"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(portable)
        portable_root = portable / "GPT Image Workbench"
        portable_report = scratch / "portable.json"
        run(
            portable_root / "GPTImageWorkbench.exe",
            ["--self-test", str(portable_report)],
            env,
            portable_report,
        )
        portable_result = json.loads(portable_report.read_text())
        # Windows TEMP can use an 8.3 alias while the application resolves its full path.
        # Compare the actual directory, not two different spellings of the same location.
        assert Path(portable_result["data_dir"]).samefile(portable_root / "data"), (
            f"Portable library {portable_result['data_dir']} is not {portable_root / 'data'}"
        )
        assert portable_result["frozen"] and portable_result["api_access"] is False
        check_restart([portable_root / "GPTImageWorkbench.exe"], portable_root / "data", env)

        install_root = scratch / "Installed app"
        switches = [
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOICONS",
            "/SMOKETEST=1",
            f"/DIR={install_root}",
            f"/LOG={scratch / 'setup.log'}",
        ]
        run(installer, switches, env)
        installed_report = scratch / "installed.json"
        try:
            run(
                install_root / "GPTImageWorkbench.exe",
                ["--self-test", str(installed_report)],
                env,
                installed_report,
            )
            result = json.loads(installed_report.read_text())
            library = scratch / "profile/GPT Image Workbench/data"
            assert Path(result["data_dir"]).samefile(library), (
                f"Installed library {result['data_dir']} is not {library}"
            )
            assert result["frozen"]
            assert not (install_root / "data").exists()
            check_restart([install_root / "GPTImageWorkbench.exe"], library, env)
            marker = library / "upgrade-preservation.txt"
            marker.write_text("keep my library", "utf-8")
            settings = library / "settings.json"
            settings.write_text('{"default_moderation":"low"}', "utf-8")
            original = next((library / "images").glob("*.png"))
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            run(installer, switches, env)
            run(
                install_root / "GPTImageWorkbench.exe",
                ["--self-test", str(installed_report)],
                env,
                installed_report,
            )
            assert marker.read_text() == "keep my library"
            assert json.loads(settings.read_text())["default_moderation"] == "low"
            assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
        finally:
            uninstallers = list(install_root.glob("unins*.exe"))
            if uninstallers:
                run(uninstallers[0], ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], env)
        assert not (install_root / "GPTImageWorkbench.exe").exists()
        assert marker.exists() and original.exists() and settings.exists()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "portable": portable_result["status"],
                    "installed": result["status"],
                    "portable_webp_reference": portable_result["webp_reference"],
                    "installed_webp_reference": result["webp_reference"],
                    "portable_restart": "passed",
                    "installed_restart": "passed",
                    "upgrade_preserved_library": True,
                    "uninstall_preserved_library": True,
                    "python_on_child_path": False,
                    "paid_api_requests": 0,
                },
                indent=2,
            ),
            "utf-8",
        )
        print(args.report.read_text())


if __name__ == "__main__":
    main()
