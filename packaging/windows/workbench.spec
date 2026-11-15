# Build from the repository root using scripts/build_windows.py.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent.parent
datas = collect_data_files("app")
# Gradio inspects component sources and serves its packaged JS/CSS at runtime.
datas += collect_data_files("gradio", include_py_files=True)
datas += collect_data_files("gradio_client", include_py_files=True)
datas += collect_data_files("safehttpx")
datas += collect_data_files("groovy")
datas += copy_metadata("gradio", recursive=True)
datas += copy_metadata("keyring", recursive=True)
hidden = collect_submodules("gradio") + collect_submodules("uvicorn")
hidden += ["keyring.backends.Windows", "PIL._tkinter_finder"]
a = Analysis(
    [str(root / "packaging/windows/entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "ruff", "IPython", "notebook", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="GPTImageWorkbench", debug=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GPTImageWorkbench")
