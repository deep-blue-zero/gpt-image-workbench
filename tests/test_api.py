from __future__ import annotations

import json
import threading

import httpx
import pytest
from openai import OpenAI

from app.api.client import ImageAPI
from app.api.errors import WorkbenchError
from app.config import Settings
from app.models.spec import GenerationSpec


def sdk(handler):
    return OpenAI(
        api_key="unit-test-placeholder",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("moderation", ["auto", "low"])
@pytest.mark.parametrize("operation", ["generate", "edit"])
def test_sdk_wire_contract_moderation_quality_size(
    registry, tmp_path, png_bytes, api_body, moderation, operation
):
    captured = []

    def handler(request):
        captured.append((request.url.path, request.headers, request.read()))
        return httpx.Response(200, json=api_body, headers={"x-request-id": "req_wire"})

    client = sdk(handler)
    api = ImageAPI("unused", registry, Settings(), client=client)
    reference = tmp_path / "reference.png"
    reference.write_bytes(png_bytes)
    spec = GenerationSpec(
        prompt="青い空 — 日本語",
        quality="max",
        width=2160,
        height=3840,
        moderation=moderation,
        operation=operation,
        reference_ids=("reference",) if operation == "edit" else (),
    )
    attempts = []
    result = api.run(
        spec, [reference] if operation == "edit" else [], threading.Event(), attempts.append, lambda _: None
    )
    path, headers, raw = captured[0]
    assert path.endswith("/edits" if operation == "edit" else "/generations")
    assert result.request_id == "req_wire" and result.body["usage"]["output_tokens"] == 100
    assert attempts == [1]
    if operation == "generate":
        payload = json.loads(raw)
        assert payload["moderation"] == moderation and payload["quality"] == "max"
        assert payload["size"] == "2160x3840" and payload["model"] == spec.model
        assert payload["prompt"] == spec.prompt
    else:
        assert b"multipart/form-data" in headers["content-type"].encode()
        assert f'name="moderation"\r\n\r\n{moderation}'.encode() in raw
        assert b'name="quality"\r\n\r\nmax' in raw
        assert b'name="size"\r\n\r\n2160x3840' in raw
        assert png_bytes in raw and spec.prompt.encode("utf-8") in raw
        assert b'name="image[]"' in raw
        assert b"input_fidelity" not in raw
    client.close()


def test_streaming_partial_and_completed(registry, api_body):
    events = [
        {
            "type": "image_generation.partial_image",
            "partial_image_index": 0,
            "b64_json": api_body["data"][0]["b64_json"],
        },
        {
            "type": "image_generation.completed",
            "b64_json": api_body["data"][0]["b64_json"],
            "usage": api_body["usage"],
        },
    ]
    content = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    client = sdk(
        lambda r: httpx.Response(
            200, text=content, headers={"content-type": "text/event-stream", "x-request-id": "req_stream"}
        )
    )
    previews = []
    result = ImageAPI("unused", registry, Settings(), client).run(
        GenerationSpec(prompt="test", stream=True, partial_images=1),
        [],
        threading.Event(),
        lambda _: None,
        previews.append,
    )
    assert len(previews) == 1 and len(result.body["data"]) == 1
    assert result.body["usage"] == api_body["usage"] and result.request_id == "req_stream"
    client.close()


def test_unreadable_edit_reference_never_submitted(registry, tmp_path):
    calls, attempts = [], []
    client = sdk(lambda request: calls.append(request))
    try:
        with pytest.raises(WorkbenchError) as caught:
            ImageAPI("unused", registry, Settings(), client).run(
                GenerationSpec(prompt="edit", operation="edit", reference_ids=("missing",)),
                [tmp_path / "missing.png"],
                threading.Event(),
                attempts.append,
                lambda _: None,
            )
        assert caught.value.category == "filesystem" and not caught.value.uncertain
        assert calls == [] and attempts == []
    finally:
        client.close()


@pytest.mark.parametrize(
    "status,code,category",
    [
        (401, "invalid_api_key", "authentication"),
        (403, "forbidden", "authentication"),
        (429, "insufficient_quota", "credits"),
        (429, "rate_limit_exceeded", "rate_limit"),
        (404, "model_not_found", "model"),
        (400, "invalid_parameter", "parameters"),
        (400, "moderation_blocked", "moderation"),
        (400, "content_policy_violation", "moderation"),
        (503, "server_error", "server"),
    ],
)
def test_error_categories(registry, status, code, category):
    client = sdk(
        lambda r: httpx.Response(
            status,
            json={"error": {"code": code, "message": "sensitive prompt text", "type": "error"}},
            headers={"x-request-id": "req_error"},
        )
    )
    with pytest.raises(WorkbenchError) as caught:
        ImageAPI("unused", registry, Settings(max_retries=0), client).run(
            GenerationSpec(prompt="test"), [], threading.Event(), lambda _: None, lambda _: None
        )
    assert caught.value.category == category and "sensitive" not in str(caught.value)
    assert caught.value.request_id == "req_error"
    client.close()


class NoWaitEvent:
    def is_set(self):
        return False

    def wait(self, delay):
        assert 0 < delay <= 60
        return False


def test_bounded_rate_limit_retry(registry):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "wait"}})

    client = sdk(handler)
    with pytest.raises(WorkbenchError):
        ImageAPI("unused", registry, Settings(max_retries=2), client).run(
            GenerationSpec(prompt="test"), [], NoWaitEvent(), lambda _: None, lambda _: None
        )
    assert len(calls) == 3
    client.close()


@pytest.mark.parametrize("kind", ["timeout", "server"])
def test_ambiguous_failures_never_replayed(registry, kind):
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("test", request=request)
        return httpx.Response(500, json={"error": {"message": "internal", "code": "server_error"}})

    client = sdk(handler)
    with pytest.raises(WorkbenchError) as caught:
        ImageAPI("unused", registry, Settings(max_retries=3), client).run(
            GenerationSpec(prompt="test"), [], threading.Event(), lambda _: None, lambda _: None
        )
    assert caught.value.uncertain and len(calls) == 1
    client.close()
