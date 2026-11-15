# Windows distribution

Windows x64 is the packaged target. The installer and ZIP include Python, Gradio,
Pillow, SQLite, the OpenAI SDK, Tk, and the shipped registries/UI assets. Source
installation remains supported. Other OS/CPU packages are deferred.

## Installation and library storage

- **Installer:** run the `-setup.exe` release asset. It installs per user, creates
  a Start menu shortcut, and does not require a Python installation.
- **Portable:** extract the whole `-portable.zip` into a writable directory, then
  open `GPTImageWorkbench.exe`. Running directly inside a ZIP is unsupported.
- The installer defaults to `%LOCALAPPDATA%\Programs\GPT Image Workbench` for code
  and `%LOCALAPPDATA%\GPT Image Workbench\data` for the library.
- A `portable.flag` beside the executable selects `data` beside the executable.
  Portable mode never stores API keys in files; saved keys stay in the Windows vault.
- **Choose library** selects an existing data directory (the folder containing
  `workbench.sqlite3`), or a new empty folder. This preserves existing source-checkout
  libraries without copying or rewriting their images. Credentials are scoped to
  the selected library path. Moving a library requires reconnecting its key.
- A missing selected library produces an error instead of silently making a new one.
- A duplicate launch opens the existing browser instance when it is a managed
  desktop instance. A source CLI instance must be closed before desktop startup.
- Closing the browser leaves the application running. Quit using the launcher;
  an in-flight call finishes and saves its result before exit. Remote Batch jobs
  continue at OpenAI after the desktop app closes.

## Updates and uninstall

Use **Downloads and updates** in the launcher to open the current GitHub Release.
Automatic background updating is not implemented.

Quit Workbench, then run the new installer. Program files are updated separately
from the library. The installer refuses to replace a running packaged application.
The uninstaller removes installed program files and preserves libraries and vault
credentials. Use **Disconnect and forget saved key** first if you want to remove
the credential too. Back up libraries with the application stopped.

For a portable update, extract the new ZIP to a fresh folder and use **Choose
library** to open the old `data` directory. Do not replace a folder while it is running.

## Building locally

From a Windows source checkout with Python 3.12:

```powershell
python -m pip install -r requirements-lock.txt -r requirements-build.txt
python -m pip install --no-deps -e .
$compiler = ./scripts/install_inno.ps1 -ToolsDir "$env:TEMP/workbench-build-tools"
python scripts/build_windows.py --iscc "$compiler"
python scripts/smoke_windows.py
```

Inno Setup 7.1.1 is downloaded from its official GitHub release and verified with
a pinned SHA-256 before installation. PyInstaller and build hooks are pinned.
Build outputs are restricted to `build/` and `dist/`. Only explicit application
resources and dependencies enter the bundle; local `.env`, libraries, and private
development receipts are excluded. Third-party license files accompany the runtime.

The package smoke test launches both distributions with Python removed from the
child process PATH. It checks the embedded interpreter, Tk, Credential Manager,
HTTP UI, local image storage, upgrade preservation, and uninstall preservation.
All sample images are generated locally; no API key or paid request is used.

## Automated GitHub Releases

**Actions → Windows release → Run workflow** builds both packages. Leave Publish
unchecked for validation/downloadable workflow artifacts only. Check Publish on
`main` to create the versioned GitHub Release after all checks pass.

Alternatively, pushing a `vX.Y.Z` tag triggers automatic publication. The tag must
match `project.version` in `pyproject.toml`. Before each release, update that version,
the changelog, and `packaging/windows/RELEASE-NOTES.md`. Existing releases are never
overwritten automatically; use a new version when changing distributed files.

The workflow tests offline, builds on Windows, tests the actual installer and ZIP,
and uploads them with `SHA256SUMS.txt`. Test reports are retained as workflow artifacts.
It uses GitHub's repository token for publishing and does not need an OpenAI API key.

## Signing

These initial packages are **unsigned**. The release notes clearly disclose this;
Windows may display SmartScreen or an unknown-publisher warning. A signing identity
has not been configured. Signing can be added to the workflow once the maintainer
has a certificate or signing-service account; checksums are not a publisher signature.
