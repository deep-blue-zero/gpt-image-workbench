"""Build only application/runtime files, then installer and portable release assets."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--skip-freeze", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Windows packages must be built on Windows.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    output = (ROOT / "dist/releases").resolve()
    bundle = (ROOT / "dist/frozen/GPTImageWorkbench").resolve()
    # All overwrite targets are fixed, verified children of this repository's build directories.
    assert output.is_relative_to(ROOT / "dist") and bundle.is_relative_to(ROOT / "dist")
    output.mkdir(parents=True, exist_ok=True)
    if not args.skip_freeze:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(bundle.parent),
                "--workpath",
                str(ROOT / "build/pyinstaller"),
                str(ROOT / "packaging/windows/workbench.spec"),
            ],
            cwd=ROOT,
            check=True,
        )
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE.txt")
    shutil.copy2(ROOT / "packaging/windows/README-WINDOWS.txt", bundle / "README-WINDOWS.txt")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", bundle / "Python-LICENSE.txt")
    forbidden = {".env", "workbench.sqlite3", "portable.flag", "launcher.json", "settings.json"}
    assert not any(p.name in forbidden for p in bundle.rglob("*")), "Private/runtime file in bundle."
    subprocess.run(
        [
            str(args.iscc.resolve()),
            f"/DAppVersion={version}",
            f"/DBundleDir={bundle}",
            f"/DOutputDir={output}",
            str(ROOT / "packaging/windows/workbench.iss"),
        ],
        check=True,
    )
    archive = output / f"gpt-image-workbench-{version}-windows-x64-portable.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                target.write(path, "GPT Image Workbench/" + path.relative_to(bundle).as_posix())
        target.writestr("GPT Image Workbench/portable.flag", "Keep the library alongside this executable.\n")
    installer = output / f"gpt-image-workbench-{version}-windows-x64-setup.exe"
    checksums = []
    for path in (installer, archive):
        with path.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    (output / "SHA256SUMS.txt").write_text("".join(checksums), "utf-8")
    print(f"Built Windows {version}: {installer.name}, {archive.name}")


if __name__ == "__main__":
    main()
