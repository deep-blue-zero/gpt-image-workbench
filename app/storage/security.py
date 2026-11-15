from __future__ import annotations

import os
import re
from typing import Any

SECRET_KEYS = {"api_key", "authorization", "access_token", "refresh_token", "password", "secret"}
KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_SESSION_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    if value:
        _SESSION_SECRETS.add(value)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: sanitize(v)
            for k, v in value.items()
            if k.lower() not in SECRET_KEYS and k not in {"b64_json", "partial_image_b64"}
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        key = os.getenv("OPENAI_API_KEY", "")
        value = value.replace(key, "[REDACTED]") if key else value
        for secret in tuple(_SESSION_SECRETS):
            value = value.replace(secret, "[REDACTED]")
        return KEY_PATTERN.sub("[REDACTED]", value)
    return value
