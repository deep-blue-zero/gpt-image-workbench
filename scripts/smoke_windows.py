"""Install, run, upgrade, and uninstall isolated release copies. Never call OpenAI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def run(executable, arguments, environment, report=None):
    process = subprocess.run(
        [str(executable), *arguments], env=environment, cwd=executable.parent, timeout=240
    )
    if process.returncode:
        detail = report.read_text("utf-8") if report and report.exists() else "No self-test report"
        raise RuntimeError(f"{executable.name} exited {process.returncode}: {detail}")


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
