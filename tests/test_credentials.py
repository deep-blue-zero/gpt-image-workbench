import json
import sys
from uuid import uuid4

import httpx
import pytest
from openai import OpenAI

from app.credentials import CredentialError, Credentials, WindowsCredentialStore
from app.credentials import test_connection as check_connection
from app.experiments import Run
from app.models.spec import GenerationSpec
from app.service import Job, Workbench
from app.storage.security import sanitize
from app.ui.application import build_ui

KEY = "offline-placeholder-key-for-credential-tests"


class MemoryStore:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self):
        self.value = None


def test_saved_and_session_only_keys_do_not_go_into_library(config, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = MemoryStore()
    credentials = Credentials(config.data_dir, store=store)
    credentials.set(KEY, True)
    assert Credentials(config.data_dir, store=store).key == KEY
    credentials.set(KEY + "-session", False)
    assert credentials.key == KEY + "-session"
    assert Credentials(config.data_dir, store=store).key == KEY
    assert sanitize({"message": KEY, "api_key": KEY}) == {"message": "[REDACTED]"}
    assert list(config.data_dir.iterdir()) == []


def test_failed_secure_save_does_not_fall_back_to_plaintext(config):
    class BrokenStore(MemoryStore):
        def set(self, value):
            raise OSError(value)

    credentials = Credentials(config.data_dir, store=BrokenStore())
    with pytest.raises(CredentialError) as caught:
        credentials.set(KEY, True)
    assert KEY not in str(caught.value)
    assert credentials.key is None
    assert list(config.data_dir.iterdir()) == []
    credentials.set(KEY, False)
    assert credentials.key == KEY


def test_gui_override_and_disconnect_do_not_rewrite_environment(config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-placeholder")
    credentials = Credentials(config.data_dir, store=MemoryStore())
    assert credentials.key == "environment-placeholder"
    credentials.set(KEY, True)
    assert credentials.key == KEY
    credentials.disconnect()
    assert credentials.key is None
    assert Credentials(config.data_dir, store=MemoryStore()).key == "environment-placeholder"


def test_connection_uses_only_read_only_models_endpoint():
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"object": "list", "data": []})

    def client(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    assert "No image was generated" in check_connection(KEY, client_factory=client)
    assert requests == [("GET", "/v1/models")]


def test_connection_refusal_does_not_echo_provider_body_or_key():
    def client(**kwargs):
        return OpenAI(
            **kwargs,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": {"message": KEY}}))
            ),
        )

    with pytest.raises(CredentialError) as caught:
        check_connection(KEY, client_factory=client)
    assert KEY not in str(caught.value) and "rejected" in str(caught.value)


def test_api_key_ui_clears_field_and_invalidates_previews(config):
    config.credentials = Credentials(config.data_dir, store=MemoryStore())
    workbench = Workbench(config)
    try:
        ui = build_ui(workbench)
        spec = GenerationSpec(prompt="offline setup test")
        workbench.prepare("old account", [Run(spec, 1, 1)])
        callback = next(f.fn for f in ui.fns.values() if f.fn and f.fn.__name__ == "save_key")
        response = callback(KEY, True)
        assert response[0] == "" and KEY not in json.dumps(response)
        assert config.api_key == KEY and workbench.plans == {}
        assert workbench.repo.requests() == []
        key_input = next(b for b in ui.blocks.values() if getattr(b, "label", None) == "OpenAI API key")
        assert key_input.type == "password" and key_input.value == ""
    finally:
        workbench.close()


def test_credentials_cannot_change_during_active_request_or_demo(config):
    config.credentials = Credentials(config.data_dir, store=MemoryStore())
    workbench = Workbench(config)
    try:
        workbench.job = Job("active", 1)
        with pytest.raises(ValueError, match="active request"):
            workbench.change_credentials(KEY, False)
        workbench.job = None
        config.demo = True
        with pytest.raises(ValueError, match="API access disabled"):
            workbench.change_credentials(KEY, False)
        assert config.api_key is None
    finally:
        workbench.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows credential vault")
def test_windows_credential_manager_round_trip_in_isolated_namespace(tmp_path):
    store = WindowsCredentialStore(tmp_path)
    store.service = "GPT Image Workbench offline test " + uuid4().hex
    try:
        store.set(KEY)
        assert store.get() == KEY
        store.delete()
        assert store.get() is None
    finally:
        store.delete()
