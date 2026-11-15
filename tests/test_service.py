from __future__ import annotations

import threading
import time

import pytest

from app.api.client import APIResult
from app.config import Settings
from app.experiments import Run
from app.models.spec import GenerationSpec
from app.service import Workbench
from app.ui.views import native_view, usage_dashboard


def wait_until_done(workbench):
    deadline = time.monotonic() + 10
    while workbench.snapshot()["status"] in ("running", "queued"):
        assert time.monotonic() < deadline, workbench.snapshot()
        time.sleep(0.01)


def factory_for(body, started=None, release=None, calls=None):
    class FakeAPI:
        def __init__(self, *args):
            pass

        def run(self, spec, paths, cancel, on_attempt, on_preview):
            if calls is not None:
                calls.append(spec)
            on_attempt(1)
            if started:
                started.set()
            if release:
                assert release.wait(5)
            return APIResult(body, "req_test", 1)

        def close(self):
            pass

    return FakeAPI


def test_no_key_and_no_confirmation(config, api_body):
    workbench = Workbench(config, factory_for(api_body))
    try:
        plan = workbench.prepare("demo", [Run(GenerationSpec(prompt="test"), 1, 1)])
        with pytest.raises(ValueError, match="explicitly confirm"):
            workbench.submit(plan.id, False)
        with pytest.raises(ValueError, match="Demo mode"):
            workbench.submit(plan.id, True)
        assert workbench.repo.requests() == []
    finally:
        workbench.close()


def test_complete_request_metadata_original_dimensions_and_no_double_count(
    config, api_body, monkeypatch, png_bytes
):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    body = {**api_body, "data": api_body["data"] * 2}
    workbench = Workbench(config, factory_for(body))
    try:
        spec = GenerationSpec(
            prompt="日本語",
            n=2,
            moderation="low",
            quality="max",
            width=2160,
            height=3840,
            model="gpt-image-2.5-flare-2026-09-08",
        )
        plan = workbench.prepare("two outputs", [Run(spec, 1, 1)])
        workbench.submit(plan.id, True)
        wait_until_done(workbench)
        assert workbench.snapshot()["status"] == "completed", workbench.snapshot()
        records = workbench.repo.requests()
        assert len(records) == 1 and records[0]["output_count"] == 2
        assert records[0]["calculated_cost"] == pytest.approx(0.00313)
        images = workbench.repo.images()
        assert len(images) == 2
        for image in images:
            assert workbench.repo.image_path(image["id"]).read_bytes() == png_bytes
            detail = workbench.repo.image(image["id"])
            assert detail["metadata"]["snapshot"] == "2026-09-08"
            assert detail["metadata"]["actual_size"] == "48x32"
            assert detail["metadata"]["requested_size"] == "2160x3840"
            assert detail["request"]["api_request_id"] == "req_test"
            assert detail["request"]["spec"]["moderation"] == "low"
        assert "$0.00313" in usage_dashboard(workbench)[0]
        assert "width:48.0px" in native_view(workbench, images[0]["id"], "100%")
        with pytest.raises(ValueError, match="only once"):
            workbench.submit(plan.id, True)
    finally:
        workbench.close()


def test_cancel_queued_preserves_submitted_output(config, api_body, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    started, release, calls = threading.Event(), threading.Event(), []
    workbench = Workbench(config, factory_for(api_body, started, release, calls))
    try:
        spec = GenerationSpec(prompt="test")
        plan = workbench.prepare("cancel", [Run(spec, 1, 1), Run(spec, 1, 2)])
        workbench.submit(plan.id, True)
        assert started.wait(5)
        with pytest.raises(ValueError, match="already running"):
            workbench.submit(plan.id, True)
        assert "already submitted" in workbench.cancel()
        release.set()
        wait_until_done(workbench)
        assert len(calls) == 1 and len(workbench.repo.images()) == 1
        assert workbench.snapshot()["status"] == "cancelled"
    finally:
        release.set()
        workbench.close()


def test_usage_preserved_when_image_corrupt(config, api_body, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    body = {**api_body, "data": [{"b64_json": "bad"}]}
    workbench = Workbench(config, factory_for(body))
    try:
        plan = workbench.prepare("bad image", [Run(GenerationSpec(prompt="test"), 1, 1)])
        workbench.submit(plan.id, True)
        wait_until_done(workbench)
        record = workbench.repo.requests()[0]
        assert record["error"]["category"] == "image_data"
        assert record["calculated_cost"] == pytest.approx(0.00313)
        assert record["response"]["usage"] == api_body["usage"]
        assert workbench.snapshot()["status"] == "failed"
    finally:
        workbench.close()


def test_edit_parent_and_reference_bytes(config, api_body, monkeypatch, tmp_path, png_bytes):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    workbench = Workbench(config, factory_for(api_body))
    try:
        path = tmp_path / "original.png"
        path.write_bytes(png_bytes)
        references = workbench.import_references([str(path)], "gpt-image-2.5-sunburst")
        spec = GenerationSpec(
            prompt="Edit instruction", operation="edit", reference_ids=references, moderation="low"
        )
        plan = workbench.prepare("edit", [Run(spec, 1, 1)])
        workbench.submit(plan.id, True)
        wait_until_done(workbench)
        result = next(i for i in workbench.repo.images() if i["kind"] == "generated")
        assert result["parent_image_id"] == references[0]
        assert path.read_bytes() == png_bytes
    finally:
        workbench.close()


def test_guard_blocks_without_submission(config, api_body, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    config.save_settings(Settings(monthly_budget_usd=0.00001, spending_guard=True))
    calls = []
    workbench = Workbench(config, factory_for(api_body, calls=calls))
    try:
        plan = workbench.prepare("budget", [Run(GenerationSpec(prompt="test"), 1, 1)])
        workbench.submit(plan.id, True)
        wait_until_done(workbench)
        assert workbench.snapshot()["status"] == "blocked"
        assert calls == [] and workbench.repo.requests() == []
    finally:
        workbench.close()


def test_partial_response_stops_remaining_requests(config, api_body, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    calls = []
    workbench = Workbench(config, factory_for(api_body, calls=calls))
    try:
        spec = GenerationSpec(prompt="test", n=2)
        plan = workbench.prepare("partial", [Run(spec, 1, 1), Run(spec, 1, 2)])
        workbench.submit(plan.id, True)
        wait_until_done(workbench)
        assert workbench.snapshot()["status"] == "failed"
        assert len(calls) == 1 and len(workbench.repo.images()) == 1
        assert workbench.repo.requests()[0]["status"] == "partial"
        assert workbench.repo.requests()[0]["calculated_cost"] == pytest.approx(0.00313)
    finally:
        workbench.close()


def test_moderation_refusal_remains_identifiable_and_retains_cost_estimate(config, monkeypatch):
    from app.api.errors import WorkbenchError

    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")

    class RefusingAPI:
        def __init__(self, *args):
            pass

        def run(self, spec, paths, cancel, attempt, preview):
            attempt(1)
            raise WorkbenchError("moderation", "Content-policy refusal", request_id="req_refusal")

        def close(self):
            pass

    workbench = Workbench(config, RefusingAPI)
    try:
        plan = workbench.prepare("refused", [Run(GenerationSpec(prompt="test", moderation="low"), 1, 1)])
        workbench.submit(plan.id, True)
        wait_until_done(workbench)
        record = workbench.repo.requests()[0]
        assert record["status"] == "refused"
        assert record["error"]["category"] == "moderation"
        assert record["calculated_cost"] is None and record["estimated_cost"] > 0
        assert record["spec"]["moderation"] == "low"
    finally:
        workbench.close()
