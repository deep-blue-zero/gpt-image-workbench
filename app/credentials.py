"""Credentials stay in memory or Windows Credential Manager, never in the library."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from app.storage.security import register_secret


class CredentialError(ValueError):
    pass


class WindowsCredentialStore:
    def __init__(self, library: Path):
        # Separate libraries (including disposable test libraries) cannot borrow credentials.
        self.account = hashlib.sha256(str(library.resolve()).casefold().encode()).hexdigest()
        self.service = "GPT Image Workbench / OpenAI"

    def backend(self):
        if sys.platform != "win32":
            raise CredentialError("Remembering keys is available on Windows. Choose session-only storage.")
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()

    def get(self):
        return self.backend().get_password(self.service, self.account)

    def set(self, key):
        self.backend().set_password(self.service, self.account, key)

    def delete(self):
        backend = self.backend()
        if backend.get_password(self.service, self.account):
            backend.delete_password(self.service, self.account)


class Credentials:
    def __init__(self, library: Path, *, store=None):
        self.store = store if store is not None else WindowsCredentialStore(library)
        self.override: str | None = None
        self.saved: str | None = None
        self.loaded = False
        self.notice = ""

    @property
    def key(self) -> str | None:
        if self.override is not None:
            value = self.override
        elif value := os.getenv("OPENAI_API_KEY", "").strip():
            pass
        else:
            if not self.loaded:
                try:
                    self.saved = self.store.get()
                except Exception:
                    self.notice = "Saved credentials could not be read. Enter a key to continue."
                self.loaded = True
            value = self.saved
        if value:
            register_secret(value)
        return value or None

    @property
    def source(self) -> str:
        if not self.key:
            return "not connected"
        if self.override is not None:
            return "Windows Credential Manager" if self.override == self.saved else "this session"
        return (
            "environment / .env" if os.getenv("OPENAI_API_KEY", "").strip() else "Windows Credential Manager"
        )

    def set(self, key: str, remember: bool) -> None:
        key = key.strip()
        if not 20 <= len(key) <= 2048 or not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key):
            raise CredentialError("Paste the complete API key, with no spaces or line breaks.")
        register_secret(key)
        if remember:
            try:
                self.store.set(key)
            except Exception:
                raise CredentialError(
                    "Windows could not save the key securely. Uncheck Remember to use it for this session only."
                ) from None
            self.saved = key
            self.loaded = True
        self.override = key
        self.notice = ""

    def disconnect(self) -> None:
        try:
            self.store.delete()
        except Exception:
            raise CredentialError(
                "Could not remove the saved key. Please retry; it has not been forgotten."
            ) from None
        self.override = ""
        self.saved = None
        self.loaded = True


def test_connection(key: str | None, *, client_factory=None) -> str:
    """A read-only models request; it never generates an image or proves billing access."""
    if not key:
        raise CredentialError("Save a key first, then test the connection.")
    from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI

    try:
        with (client_factory or OpenAI)(api_key=key, timeout=15, max_retries=0) as client:
            client.models.list()
    except AuthenticationError:
        raise CredentialError(
            "OpenAI rejected this key. Check that it is active and copied completely."
        ) from None
    except APIConnectionError:
        raise CredentialError("Could not reach OpenAI. Check your internet connection and retry.") from None
    except APIStatusError as exc:
        raise CredentialError(
            f"OpenAI returned HTTP {exc.status_code}. Check project permissions or retry later. "
            "A restricted key may allow image requests without allowing this connection test."
        ) from None
    except Exception:
        raise CredentialError("Connection test failed. No image request was sent.") from None
    return (
        "Connected to OpenAI. No image was generated. Model access and billing are checked when you submit."
    )
