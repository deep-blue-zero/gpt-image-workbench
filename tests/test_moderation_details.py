from __future__ import annotations

import csv
import json
import threading
import time

import httpx
import pytest
from openai import BadRequestError, OpenAI

from app.api.client import ImageAPI
from app.api.errors import WorkbenchError, classify_error
from app.config import Settings
from app.experiments import Run
from app.models.spec import GenerationSpec
from app.service import Workbench
from app.storage.repository import Repository
from app.ui.application import build_ui
from app.ui.views import moderation_view

DETAILS = {"moderation_stage": "output", "categories": ["sexual", "future-public-category"]}
PROVIDER = {
    "code": "moderation_blocked",
    "type": "image_generation_user_error",
    "message": "The generated result was blocked by a safety check.",
    "moderation_details": DETAILS,
}


def sdk(handler):
    return OpenAI(
        api_key="unit-test-placeholder",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("operation", ["generate", "edit"])
def test_http_moderation_diagnostics_preserved_without_automatic_retry(
    registry, tmp_path, png_bytes, operation
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": PROVIDER}, headers={"x-request-id": "req_details"})

    reference = tmp_path / "source.png"
    reference.write_bytes(png_bytes)
    spec = GenerationSpec(
        prompt="Preserve the supplied image.",
        operation=operation,
        reference_ids=("source",) if operation == "edit" else (),
        moderation="low",
    )
    with sdk(handler) as client:
        with pytest.raises(WorkbenchError) as caught:
            ImageAPI("unused", registry, Settings(max_retries=3), client).run(
                spec,
                [reference] if spec.reference_ids else [],
                threading.Event(),
                lambda _: None,
                lambda _: None,
            )
    error = caught.value.to_dict()
    assert len(calls) == 1 and error["category"] == "moderation"
    assert error["provider_message"] == PROVIDER["message"]
    assert error["provider_type"] == PROVIDER["type"]
    assert error["moderation_details"] == DETAILS
    assert error["request_id"] == "req_details" and error["status_code"] == 400
    assert error["retryable"] is False and error["uncertain"] is False


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("details", [None, {}, {"moderation_stage": "unknown", "categories": []}, "invalid"])
def test_optional_details_and_sdk_body_variants(wrapped, details):
    provider = {**PROVIDER, "moderation_details": details}
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/images/edits"))
    error = classify_error(
        BadRequestError("blocked", response=response, body={"error": provider} if wrapped else provider)
    )
    assert error.moderation_details == (details if isinstance(details, dict) else None)
    assert error.category == "moderation"


@pytest.mark.parametrize("nested", [False, True])
def test_streamed_refusal_retains_provider_details(registry, nested):
    event = (
        {"type": "image_generation.failed", "error": PROVIDER} if nested else {**PROVIDER, "type": "error"}
    )
    content = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
    with sdk(
        lambda _: httpx.Response(
            200,
            text=content,
            headers={"content-type": "text/event-stream", "x-request-id": "req_stream_refusal"},
        )
    ) as client:
        with pytest.raises(WorkbenchError) as caught:
            ImageAPI("unused", registry, Settings(), client).run(
                GenerationSpec(prompt="Test", stream=True),
                [],
                threading.Event(),
                lambda _: None,
                lambda _: None,
            )
    assert caught.value.category == "moderation"
    assert caught.value.moderation_details == DETAILS
    assert caught.value.provider_message == PROVIDER["message"]
    assert caught.value.request_id == "req_stream_refusal"


def test_diagnostics_survive_storage_exports_and_ui(config, monkeypatch):
    secret = "unit-test-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    provider = {
        **PROVIDER,
        "message": f"{PROVIDER['message']} {secret} <script>alert(1)</script>",
        "moderation_details": {**DETAILS, "api_key": secret, "future_context": "Retain unknown fields."},
    }
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": provider}, headers={"x-request-id": "req_persisted"})

    def factory(key, registry, settings):
        api = ImageAPI(key, registry, settings, sdk(handler))
        api.owns_client = True
        return api

    workbench = Workbench(config, factory)
    try:
        spec = GenerationSpec(prompt="Diagnostic preservation test", moderation="low")
        plan = workbench.prepare("Diagnostics", [Run(spec, 1, 1), Run(spec, 1, 2)])
        exp = workbench.submit(plan.id, True)
        deadline = time.monotonic() + 10
        while workbench.snapshot()["status"] in ("queued", "running"):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert len(calls) == 1  # Existing stop-on-failure default stays in force.
        records = workbench.repo.requests(exp)
        assert len(records) == 1 and records[0]["status"] == "refused"
        record = records[0]
        error = record["error"]
        assert error["moderation_details"]["future_context"] == "Retain unknown fields."
        assert "api_key" not in error["moderation_details"]
        assert secret not in json.dumps(record)
        assert workbench.snapshot()["error"] == error
        ui = build_ui(workbench)
        inspect = next(fn.fn for fn in ui.fns.values() if fn.fn and fn.fn.__name__ == "inspect_request")
        shown_record, shown_html = inspect(record["id"])
        assert shown_record == record
        assert "output" in shown_html and "future-public-category" in shown_html
        assert "&lt;script&gt;" in shown_html and "<script>" not in shown_html
        poll = next(fn.fn for fn in ui.fns.values() if fn.fn and fn.fn.__name__ == "poll")
        assert "req_persisted" in poll(None, None, None)[4]
        json_path, csv_path = workbench.repo.export(exp)
    finally:
        workbench.close()
    reopened = Repository(config.data_dir, config.timezone).request(record["id"])
    assert reopened["error"] == error
    assert json.loads(json_path.read_text("utf-8"))["requests"][0]["error"] == error
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["moderation_stage"] == "output"
    assert json.loads(row["moderation_categories"]) == DETAILS["categories"]
    assert json.loads(row["moderation_details"]) == error["moderation_details"]
    assert row["provider_error_message"] == error["provider_message"]
    assert row["provider_error_type"] == PROVIDER["type"]
    assert secret not in csv_path.read_text("utf-8-sig")


def test_ui_distinguishes_legacy_absent_and_explicit_unknown_details():
    assert "older record" in moderation_view({"category": "moderation"})
    assert "did not return" in moderation_view({"category": "moderation", "moderation_details": None})
    shown = moderation_view(
        {"category": "moderation", "moderation_details": {"moderation_stage": "unknown", "categories": []}}
    )
    assert "unknown" in shown and "[]" in shown and "did not return" not in shown
