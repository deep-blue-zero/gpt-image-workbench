from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx
import openai

from app.storage.security import sanitize


@dataclass
class WorkbenchError(Exception):
    category: str
    message: str
    uncertain: bool = False
    retryable: bool = False
    status_code: int | None = None
    request_id: str | None = None
    code: str | None = None
    provider_message: str | None = None
    provider_type: str | None = None
    moderation_details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


def provider_error_body(body: Any) -> dict[str, Any]:
    """Accept both the HTTP envelope and the SDK's unwrapped error body."""
    if not isinstance(body, dict):
        return {}
    nested = body.get("error")
    return nested if isinstance(nested, dict) else body


def provider_error_fields(body: Any) -> dict[str, Any]:
    """Retain optional provider diagnostics, without inventing missing fields."""
    error = sanitize(provider_error_body(body))
    return {
        "provider_message": error.get("message") if isinstance(error.get("message"), str) else None,
        "provider_type": error.get("type") if isinstance(error.get("type"), str) else None,
        "moderation_details": (
            error["moderation_details"] if isinstance(error.get("moderation_details"), dict) else None
        ),
    }


def classify_error(exc: Exception) -> WorkbenchError:
    if isinstance(exc, WorkbenchError):
        return exc
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error = provider_error_body(body)
    code = str(error.get("code") or getattr(exc, "code", "") or "")
    request_id = getattr(exc, "request_id", None)
    base = {
        "status_code": status,
        "request_id": request_id,
        "code": code or None,
        **provider_error_fields(error),
    }
    if code in {
        "moderation_blocked",
        "content_policy_violation",
        "safety_violation",
        "image_generation_user_error",
    }:
        # The last generic code is not itself a moderation refusal; inspect the explicit type.
        if code != "image_generation_user_error" or error.get("type") == "moderation_blocked":
            return WorkbenchError(
                "moderation",
                "OpenAI declined this image request under its content policies. "
                "Revise the prompt or references; low moderation still applies those policies.",
                **base,
            )
    if status in (401, 403):
        return WorkbenchError(
            "authentication",
            "OpenAI rejected authentication or project/model access. "
            "Check the API key, project permissions and any required organization verification.",
            **base,
        )
    if code in {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}:
        return WorkbenchError(
            "credits", "OpenAI reports insufficient credits or a billing limit. Check API billing.", **base
        )
    if status == 429:
        return WorkbenchError(
            "rate_limit",
            "OpenAI rate limit reached. The workbench will retry within its bounded retry limit.",
            retryable=True,
            **base,
        )
    if status == 404 or code == "model_not_found":
        return WorkbenchError(
            "model",
            "The selected model is unavailable to this project. Choose an accessible model explicitly.",
            **base,
        )
    if status in (400, 422):
        return WorkbenchError(
            "parameters",
            "OpenAI rejected a parameter or reference image. Check the selected model's "
            "documented controls and image limits. The selected settings were preserved.",
            **base,
        )
    if isinstance(exc, (openai.APITimeoutError, httpx.TimeoutException)):
        return WorkbenchError(
            "timeout",
            "The request timed out after submission. Its server outcome and charge may be unknown. "
            "No automatic resubmission was made; inspect usage before rerunning.",
            uncertain=True,
            **base,
        )
    if isinstance(exc, openai.APIConnectionError):
        cause = exc.__cause__
        if isinstance(cause, httpx.ConnectError):
            return WorkbenchError(
                "connection", "Could not connect to OpenAI. Check network access.", retryable=True, **base
            )
        return WorkbenchError(
            "network",
            "The connection failed. The server outcome may be unknown; no automatic resubmission.",
            uncertain=True,
            **base,
        )
    if status is not None and status >= 500:
        return WorkbenchError(
            "server",
            "OpenAI returned a server error. The outcome may be uncertain; automatic resubmission "
            "is disabled to avoid duplicate charges. Review usage before rerunning.",
            uncertain=True,
            **base,
        )
    return WorkbenchError(
        "unexpected",
        "An unexpected response or local error occurred. Review the request record and "
        "structured log. No automatic resubmission was made.",
        uncertain=True,
        **base,
    )
