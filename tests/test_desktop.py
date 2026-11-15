import json
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from app.config import Config
from app.desktop_launcher import save_library, saved_library
from app.desktop_server import AlreadyRunning, DesktopServer, available_port, existing_url
from app.paths import application_root, default_data_dir
from app.service import Workbench


def test_installed_and_portable_defaults_ignore_working_directory(tmp_path, monkeypatch):
    root = tmp_path / "program"
    root.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / "GPTImageWorkbench.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    assert application_root() == root
    assert default_data_dir(root) == tmp_path / "profile/GPT Image Workbench/data"
    (root / "portable.flag").touch()
    assert default_data_dir(root) == root / "data"


def test_source_library_location_and_explicit_override_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert Config(root=tmp_path).data_dir == tmp_path / "data"
    assert Config(root=tmp_path, data_dir=tmp_path / "existing").data_dir == tmp_path / "existing"


def test_missing_selected_library_is_not_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    library = tmp_path / "external"
    library.mkdir()
    save_library(tmp_path, library)
    assert saved_library(tmp_path) == library
    library.rmdir()
    with pytest.raises(ValueError, match="unavailable"):
        saved_library(tmp_path)


def test_occupied_port_is_skipped():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        assert available_port(port) != port


def test_untrusted_instance_url_is_never_opened(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A non-local instance URL must never be requested")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    for url in ("https://example.com", "http://localhost.evil.test:7865", "file:///secret"):
        (tmp_path / ".desktop-instance.json").write_text(json.dumps({"url": url, "instance": "x"}))
        assert existing_url(tmp_path) is None


def test_desktop_reopens_same_library_and_releases_lock(config):
    config.demo = True
    server = DesktopServer(config, available_port(17900))
    try:
        url = server.start()
        assert existing_url(config.data_dir) == url
        other = DesktopServer(config)
        with pytest.raises(AlreadyRunning) as caught:
            other.start()
        assert caught.value.url == url
        other.stop()
        assert existing_url(config.data_dir) == url
    finally:
        server.stop()
    assert not (config.data_dir / ".desktop-instance.json").exists()


def test_shutdown_waits_for_worker_to_save(config):
    workbench = Workbench(config)
    release = threading.Event()
    saved = config.data_dir / "worker-saved.txt"

    def worker():
        release.wait(5)
        saved.write_text("saved")

    workbench.executor.submit(worker)
    shutdown = threading.Thread(target=workbench.close)
    shutdown.start()
    try:
        shutdown.join(0.1)
        assert shutdown.is_alive() and workbench.closing
    finally:
        release.set()
        shutdown.join(5)
    assert saved.read_text() == "saved" and not shutdown.is_alive()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows graphical launcher")
def test_graphical_launcher_starts_and_quits_without_browser(config, monkeypatch):
    from app.desktop_launcher import Launcher

    monkeypatch.setattr("app.desktop_launcher.application_root", lambda: config.root)
    monkeypatch.setattr("webbrowser.open", lambda _: pytest.fail("No browser should open in this test"))
    launcher = Launcher(
        SimpleNamespace(data_dir=config.data_dir, demo=True, no_browser=True, port=available_port(18010))
    )
    launcher.window.withdraw()
    try:
        deadline = time.monotonic() + 30
        while launcher.busy and time.monotonic() < deadline:
            launcher.window.update()
            time.sleep(0.05)
        assert not launcher.busy and launcher.server.url
        assert str(launcher.open_button["state"]) == "normal"
        assert "Running" in launcher.status.get()
        launcher.quit()
        while launcher.busy and time.monotonic() < deadline:
            launcher.window.update()
            time.sleep(0.05)
        assert not launcher.busy and not launcher.server.owns_lock
    finally:
        launcher.server.stop()
        try:
            launcher.window.destroy()
        except Exception:
            pass
