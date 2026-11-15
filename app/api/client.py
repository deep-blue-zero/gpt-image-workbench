from __future__ import annotations

import logging
import random
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import OpenAI

from app.api.errors import WorkbenchError, classify_error, provider_error_body, provider_error_fields
from app.api.payload import build_payload
from app.config import Settings
from app.models.capabilities import Registry
from app.models.spec import GenerationSpec

logger = logging.getLogger("workbench")


@dataclass
class APIResult:
    body: dict[str, Any]
    request_id: str | None
    attempts: int


class ImageAPI:
    def __init__(self, api_key: str, registry: Registry, settings: Settings, client: OpenAI | None = None):
        self.registry, self.settings = registry, settings
        # Explicit official endpoint: no third-party service or environment base-URL override.
        self.client = client or OpenAI(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=20),
        )
        self.owns_client = client is None

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def run(
        self,
        spec: GenerationSpec,
        reference_paths: list[Path],
        cancel: threading.Event,
        on_attempt: Callable[[int], None],
        on_preview: Callable[[str], None],
    ) -> APIResult:
        payload = build_payload(spec, self.registry)
        if len(reference_paths) != len(spec.reference_ids):
            raise ValueError("Reference IDs and files do not match.")
        for attempt in range(1, self.settings.max_retries + 2):
            if cancel.is_set():
                raise WorkbenchError("cancelled", "Locally queued request cancelled before submission.")
            request_id = None
            try:
                with ExitStack() as stack:
                    kwargs = dict(payload)
                    # Documented 2.6 field, passed through the SDK's public extension
                    # until its typed signatures include complexity (absent in 2.54).
                    extra_body = {}
                    if "complexity" in kwargs:
                        extra_body["complexity"] = kwargs.pop("complexity")
                    if spec.operation == "edit":
                        extra_body["moderation"] = kwargs.pop("moderation")
                    if extra_body:
                        kwargs["extra_body"] = extra_body
                    if spec.operation == "edit":
                        try:
                            kwargs["image"] = [stack.enter_context(p.open("rb")) for p in reference_paths]
                        except OSError as exc:
                            raise WorkbenchError(
                                "filesystem",
                                "Could not read an edit reference. Check its file and permissions. No request was submitted.",
                            ) from exc
                        # Public SDK extension, because 2.54's edit signature lacks documented moderation.
                        # New quality values are serialized as ordinary strings; no SDK types are patched.
                        on_attempt(attempt)
                        response = self.client.images.with_raw_response.edit(**kwargs)
                    else:
                        on_attempt(attempt)
                        response = self.client.images.with_raw_response.generate(**kwargs)
                    request_id = response.headers.get("x-request-id")
                    parsed = response.parse()
                    if not spec.stream:
                        return APIResult(
                            parsed.model_dump(mode="json", exclude_none=True), request_id, attempt
                        )
                    completed: list[dict] = []
                    metadata: dict[str, Any] = {}
                    with parsed as events:
                        for event in events:
                            data = event.model_dump(mode="json", exclude_none=True)
                            event_type = data.get("type", "")
                            if event_type.endswith(".partial_image"):
                                on_preview(data.get("b64_json", ""))
                            elif event_type.endswith(".completed"):
                                completed.append({"b64_json": data.get("b64_json", "")})
                                metadata.update(
                                    {k: v for k, v in data.items() if k not in ("b64_json", "type")}
                                )
                            elif event_type == "error" or event_type.endswith(".failed"):
                                provider = provider_error_body(data)
                                code = provider.get("code") or data.get("code")
                                diagnostics = provider_error_fields(provider)
                                if code in (
                                    "moderation_blocked",
                                    "content_policy_violation",
                                    "safety_violation",
                                ):
                                    raise WorkbenchError(
                                        "moderation",
                                        "OpenAI declined this streamed image under its content policies.",
                                        request_id=request_id,
                                        code=code,
                                        **diagnostics,
                                    )
                                raise WorkbenchError(
                                    "stream",
                                    "The stream failed after submission. Review usage before rerunning.",
                                    uncertain=True,
                                    request_id=request_id,
                                    code=code,
                                    **diagnostics,
                                )
                    if not completed:
                        raise WorkbenchError(
                            "stream",
                            "The stream ended without a final image. Its charge may be unknown.",
                            uncertain=True,
                            request_id=request_id,
                        )
                    return APIResult({**metadata, "data": completed}, request_id, attempt)
            except Exception as exc:
                error = classify_error(exc)
                # The SDK may raise a streamed error before yielding its event,
                # without copying the request ID from the original HTTP headers.
                if error.request_id is None:
                    error.request_id = request_id
                logger.warning(
                    "api_attempt_failed",
                    extra={
                        "error_type": error.category,
                        "request_id": error.request_id,
                        "status_code": error.status_code,
                    },
                )
                if not error.retryable or attempt > self.settings.max_retries:
                    raise error from exc
                # Only explicit rate-limit rejection / connect-before-send failure is replayed.
                delay = min(30.0, 2**attempt + random.uniform(0, 1))
                response = getattr(exc, "response", None)
                try:
                    delay = (
                        min(60.0, max(delay, float(response.headers.get("retry-after", 0))))
                        if response
                        else delay
                    )
                except (TypeError, ValueError):
                    pass
                if cancel.wait(delay):
                    raise WorkbenchError("cancelled", "Cancelled before the next retry.") from exc
        raise AssertionError("Unreachable retry state")
