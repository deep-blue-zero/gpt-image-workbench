from __future__ import annotations

import copy
import csv
import json
from dataclasses import replace
from email import policy
from email.parser import BytesParser

import gradio as gr
import httpx
import pytest
from openai import OpenAI

from app.api.batch import BatchAPI
from app.config import Settings
from app.experiments import Run
from app.models.spec import GenerationSpec
from app.service import Workbench
from app.storage.repository import BudgetError
from app.ui.application import build_ui
from app.ui.components import form_values
from app.ui.views import usage_dashboard


class BatchHarness:
    """The real SDK talks only to this in-process HTTP transport."""

    def __init__(self):
        self.calls, self.uploads = [], []
        self.files, self.jobs = {}, {}
        self.timeout_create = False
        self.client = OpenAI(
            api_key="unit-test-only",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handle)),
        )

    def factory(self, key, settings):
        return BatchAPI(key, settings, client=self.client)

    def handle(self, request):
        path = request.url.path.removeprefix("/v1")
        self.calls.append((request.method, path))
        if request.method == "POST" and path == "/files":
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n".encode()
                + request.read()
            )
            fields = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()
            }
            identity = f"file-input-{len(self.uploads)}"
            self.uploads.append(fields)
            self.files[identity] = fields["file"]
            return httpx.Response(
                200,
                json={
                    "id": identity,
                    "object": "file",
                    "bytes": len(fields["file"]),
                    "created_at": 1793400000,
                    "filename": "input",
                    "purpose": fields["purpose"].decode(),
                },
            )
        if request.method == "POST" and path == "/batches":
            body = json.loads(request.read())
            assert body["completion_window"] == "24h"
            identity = f"batch_test_{len(self.jobs)}"
            job = {
                "id": identity,
                "object": "batch",
                "status": "validating",
                "created_at": 1793400000,
                "completion_window": "24h",
                "endpoint": body["endpoint"],
                "input_file_id": body["input_file_id"],
                "metadata": body["metadata"],
                "request_counts": {
                    "total": len(self.lines(body["input_file_id"])),
                    "completed": 0,
                    "failed": 0,
                },
            }
            self.jobs[identity] = job
            if self.timeout_create:
                self.timeout_create = False
                raise httpx.ReadTimeout("Response lost after server accepted the Batch", request=request)
            return httpx.Response(200, json=job)
        if request.method == "GET" and path == "/batches":
            return httpx.Response(
                200, json={"object": "list", "data": list(self.jobs.values()), "has_more": False}
            )
        if request.method == "GET" and path.startswith("/batches/"):
            return httpx.Response(200, json=self.jobs[path.rsplit("/", 1)[-1]])
        if request.method == "POST" and path.endswith("/cancel"):
            job = self.jobs[path.split("/")[-2]]
            job["status"] = "cancelling"
            return httpx.Response(200, json=job)
        if request.method == "GET" and path.endswith("/content"):
            return httpx.Response(
                200,
                content=self.files[path.split("/")[-2]],
                headers={"content-type": "application/octet-stream"},
            )
        raise AssertionError(f"Unexpected HTTP call: {request.method} {path}")

    def lines(self, file_id):
        return [json.loads(line) for line in self.files[file_id].splitlines()]

    def finish(self, remote_id, outputs=(), errors=(), status="completed"):
        job = self.jobs[remote_id]
        job["status"] = status
        job["request_counts"]["completed"] = len(outputs)
        job["request_counts"]["failed"] = len(errors)
        for kind, rows in (("output", outputs), ("error", errors)):
            if rows:
                file_id = f"file-{kind}-{remote_id}"
                self.files[file_id] = (
                    b"\n".join(json.dumps(row, ensure_ascii=False).encode() for row in rows) + b"\n"
                )
                job[f"{kind}_file_id"] = file_id


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-only")
    transport = BatchHarness()
    yield transport
    transport.client.close()


def submit(wb, runs):
    plan = wb.prepare("Batch test", runs, "batch")
    identity = wb.submit(plan.id, True)
    wb.executor.submit(lambda: None).result(timeout=15)
    return identity, wb.repo.batch_jobs(identity)


def ok(custom, body, suffix=""):
    return {
        "id": "batch_req_" + custom,
        "custom_id": custom,
        "response": {"status_code": 200, "request_id": "req_" + custom + suffix, "body": body},
        "error": None,
    }


def refused(custom):
    return {
        "custom_id": custom,
        "response": {
            "status_code": 400,
            "request_id": "req_refused",
            "body": {
                "error": {
                    "type": "image_generation_user_error",
                    "code": "moderation_blocked",
                    "message": "Provider refused this output.",
                    "moderation_details": {"moderation_stage": "output", "categories": ["sexual"]},
                }
            },
        },
        "error": None,
    }


@pytest.mark.parametrize("operation", ["generate", "edit"])
@pytest.mark.parametrize("moderation", ["auto", "low"])
def test_real_sdk_batch_payload_and_edit_references(
    config, harness, png_bytes, api_body, operation, moderation
):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        references = ()
        if operation == "edit":
            source = config.root / "source.png"
            source.write_bytes(png_bytes)
            references = wb.import_references([str(source)], "gpt-image-2.5-sunburst")
        spec = GenerationSpec(
            prompt="日本語\nPreserve detail.",
            quality="max",
            moderation=moderation,
            operation=operation,
            reference_ids=references,
        )
        plan = wb.prepare("preview", [Run(spec, 1, 1)], "batch")
        assert harness.calls == []
        standard = wb.costs.estimate(spec, config.settings)
        assert plan.total == pytest.approx(standard.usd / 2)
        exp, jobs = submit(wb, [Run(spec, 1, 1), Run(spec, 1, 2)])
        assert len(jobs) == 1 and jobs[0]["status"] == "validating"
        job = jobs[0]
        lines = harness.lines(job["input_file_id"])
        assert len(lines) == 2 and len({r["custom_id"] for r in lines}) == 2
        assert {r["custom_id"] for r in lines} == {r["id"] for r in wb.repo.requests(exp)}
        for line in lines:
            assert line["url"] == ("/v1/images/edits" if operation == "edit" else "/v1/images/generations")
            assert line["method"] == "POST"
            assert line["body"]["prompt"] == spec.prompt
            assert line["body"]["moderation"] == moderation
            assert line["body"]["quality"] == "max" and "stream" not in line["body"]
            if operation == "edit":
                assert line["body"]["images"] == [{"file_id": job["reference_files"][references[0]]}]
                assert "image" not in line["body"]
        purposes = [item["purpose"] for item in harness.uploads]
        assert purposes == ([b"vision", b"batch"] if operation == "edit" else [b"batch"])
        assert all(item["expires_after[seconds]"] == b"2592000" for item in harness.uploads)
        assert all(not path.startswith("/images/") for method, path in harness.calls)
        harness.finish(job["remote_id"], [ok(line["custom_id"], api_body) for line in lines])
        wb.batches.import_results(job["id"])
        images = wb.repo.images(experiment=exp)
        assert len(images) == 2
        for output in images:
            assert output["parent_image_id"] == (references[0] if references else None)
            assert output["reference_ids"] == list(references)
            assert output["metadata"]["processing_mode"] == "batch"
            assert wb.repo.request(output["request_id"])["spec"]["moderation"] == moderation
        if references:
            assert wb.repo.image_path(references[0]).read_bytes() == png_bytes
    finally:
        wb.close()


def test_mixed_models_and_endpoints_split_preserving_samples(config, harness, png_bytes):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        source = config.root / "ref.png"
        source.write_bytes(png_bytes)
        references = wb.import_references([str(source)], "gpt-image-2.5-sunburst")
        base = GenerationSpec(prompt="A cup")
        runs = [
            Run(base, 1, 1),
            Run(replace(base, model="gpt-image-2.5-flare"), 2, 1),
            Run(replace(base, operation="edit", reference_ids=references), 3, 1),
        ]
        exp, jobs = submit(wb, runs)
        assert len(jobs) == 3
        assert sorted(r["config_index"] for r in wb.repo.requests(exp)) == [1, 2, 3]
        for job in jobs:
            lines = harness.lines(job["input_file_id"])
            assert {line["body"]["model"] for line in lines} == {job["model"]}
            assert {line["url"] for line in lines} == {job["endpoint"]}
    finally:
        wb.close()


def test_restart_unordered_mixed_results_discount_snapshot_and_idempotency(
    config, harness, api_body, png_bytes
):
    wb = Workbench(config, batch_api_factory=harness.factory)
    exp, jobs = submit(
        wb, [Run(GenerationSpec(prompt=f"Sample {i}", moderation="low"), i, 1) for i in range(1, 4)]
    )
    job = jobs[0]
    lines = harness.lines(job["input_file_id"])
    harness.finish(
        job["remote_id"],
        [ok(lines[2]["custom_id"], api_body), ok(lines[0]["custom_id"], api_body)],
        [refused(lines[1]["custom_id"])],
    )
    wb.close()
    calls = len(harness.calls)
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        wb.repo.recover_interrupted()
        assert len(harness.calls) == calls
        assert all(r["status"] == "batch_pending" for r in wb.repo.requests(exp))
        wb.costs.data["batch_rates_per_million"]["gpt-image-2.5"]["image_output"] = 999
        result = wb.batches.import_results(job["id"])
        assert result["import_state"] == "complete"
        records = {r["config_index"]: r for r in wb.repo.requests(exp)}
        assert [records[i]["status"] for i in (1, 2, 3)] == ["succeeded", "refused", "succeeded"]
        assert records[2]["error"]["moderation_details"] == {
            "moderation_stage": "output",
            "categories": ["sexual"],
        }
        assert records[2]["calculated_cost"] is None
        for i in (1, 3):
            assert records[i]["calculated_cost"] == pytest.approx(0.001565)
            assert records[i]["response"]["usage"] == api_body["usage"]
        images_before = wb.repo.images(experiment=exp)
        assert len(images_before) == 2
        assert all(wb.repo.image_path(i["id"]).read_bytes() == png_bytes for i in images_before)
        assert all(
            (config.data_dir / "portable" / f"{i['id']}-with-settings.png").is_file() for i in images_before
        )
        wb.batches.import_results(job["id"])
        assert {i["id"] for i in wb.repo.images(experiment=exp)} == {i["id"] for i in images_before}
        assert sum(r["calculated_cost"] or 0 for r in wb.repo.requests(exp)) == pytest.approx(0.00313)
        assert "Processing mode" in str(usage_dashboard(wb)[2])
        jp, cp = wb.repo.export(exp)
        exported = json.loads(jp.read_text())
        assert exported["batch_jobs"][0]["remote_id"] == job["remote_id"]
        assert "unit-test-only" not in jp.read_text()
        with cp.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 3 and {r["processing_mode"] for r in rows} == {"batch"}
        assert next(r for r in rows if r["status"] == "refused")["moderation_stage"] == "output"
    finally:
        wb.close()


@pytest.mark.parametrize("change", [{"stream": True}, {"model": "gpt-image-2"}])
def test_unsupported_batch_settings_are_not_silently_changed(config, harness, change):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        spec = GenerationSpec(prompt="A cup", **change)
        with pytest.raises(ValueError):
            wb.prepare("invalid", [Run(spec, 1, 1)], "batch")
        assert harness.calls == [] and wb.repo.requests() == []
    finally:
        wb.close()


def test_complete_budget_reserved_before_any_upload(config, harness):
    config.settings = Settings(spending_guard=True, monthly_budget_usd=0.001)
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        plan = wb.prepare(
            "too costly",
            [Run(GenerationSpec(prompt="A cup", quality="max"), 1, i) for i in range(1, 4)],
            "batch",
        )
        with pytest.raises(BudgetError):
            wb.submit(plan.id, True)
        assert harness.calls == []
        assert wb.repo.requests() == wb.repo.batch_jobs() == wb.repo.experiments() == []
    finally:
        wb.close()


def test_no_confirmation_or_key_blocks_upload(config, harness, monkeypatch):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        plan = wb.prepare("guard", [Run(GenerationSpec(prompt="A cup"), 1, 1)], "batch")
        with pytest.raises(ValueError, match="explicitly confirm"):
            wb.submit(plan.id, False)
        monkeypatch.delenv("OPENAI_API_KEY")
        with pytest.raises(ValueError, match="Demo mode"):
            wb.submit(plan.id, True)
        assert harness.calls == [] and wb.repo.requests() == []
    finally:
        wb.close()


@pytest.mark.parametrize("old_month", [False, True])
def test_resume_rechecks_changed_budget_before_upload(config, harness, old_month):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        plan = wb.prepare("saved", [Run(GenerationSpec(prompt="A cup"), 1, 1)], "batch")
        exp = wb.batches.reserve(plan)
        job = wb.repo.batch_jobs(exp)[0]
        config.settings = Settings(spending_guard=True, monthly_budget_usd=25 if old_month else 0.001)
        if old_month:
            with wb.repo.connect() as db:
                db.execute("UPDATE requests SET local_month='2020-01' WHERE experiment_id=?", (exp,))
        with pytest.raises(BudgetError, match="Spending guard"):
            wb.batches.submit_one(job["id"])
        assert harness.calls == []
        assert wb.batches.store.job(job["id"])["status"] == "prepared"
    finally:
        wb.close()


def test_lost_create_response_recovers_without_resubmission(config, harness, api_body):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        harness.timeout_create = True
        exp, jobs = submit(wb, [Run(GenerationSpec(prompt="A cup"), 1, 1)])
        job = jobs[0]
        assert job["status"] == "submission_unknown" and job["remote_id"] is None
        assert wb.repo.requests(exp)[0]["status"] == "batch_unknown"
        with pytest.raises(ValueError, match="cannot be resubmitted"):
            wb.batches.submit_one(job["id"])
        recovered = wb.batches.check(job["id"])
        assert recovered["remote_id"] == "batch_test_0"
        line = harness.lines(recovered["input_file_id"])[0]
        harness.finish(recovered["remote_id"], [ok(line["custom_id"], api_body)])
        wb.batches.import_results(job["id"])
        assert harness.calls.count(("POST", "/batches")) == 1
        assert wb.repo.requests(exp)[0]["status"] == "succeeded"
    finally:
        wb.close()


def test_recovery_rejects_wrong_remote_identity(config, harness):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        _, jobs = submit(wb, [Run(GenerationSpec(prompt="A cup"), 1, 1)])
        job = jobs[0]
        wrong = copy.deepcopy(harness.jobs[job["remote_id"]])
        wrong["id"] = "batch_wrong"
        wrong["metadata"] = {"workbench_batch_id": "another-local-job"}
        harness.jobs["batch_wrong"] = wrong
        with pytest.raises(ValueError, match="ownership"):
            wb.batches.check(job["id"], "batch_wrong")
        assert wb.batches.store.job(job["id"])["remote_id"] == job["remote_id"]
    finally:
        wb.close()


def test_cancel_and_import_partial_terminal_results(config, harness, api_body):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        exp, jobs = submit(wb, [Run(GenerationSpec(prompt="A cup"), 1, i) for i in range(1, 4)])
        job = jobs[0]
        assert wb.batches.cancel(job["id"])["status"] == "cancelling"
        lines = harness.lines(job["input_file_id"])
        expired = {
            "custom_id": lines[1]["custom_id"],
            "response": None,
            "error": {"code": "batch_expired", "message": "Not executed before expiry."},
        }
        harness.finish(job["remote_id"], [ok(lines[0]["custom_id"], api_body)], [expired], status="expired")
        result = wb.batches.import_results(job["id"])
        assert result["import_state"] == "partial"
        records = {r["id"]: r for r in wb.repo.requests(exp)}
        assert records[lines[0]["custom_id"]]["status"] == "succeeded"
        assert records[lines[1]["custom_id"]]["calculated_cost"] == 0
        assert records[lines[2]["custom_id"]]["status"] == "batch_unresolved"
        assert records[lines[2]["custom_id"]]["calculated_cost"] is None
    finally:
        wb.close()


def test_failed_batch_validation_and_local_cancel(config, harness):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        exp, jobs = submit(wb, [Run(GenerationSpec(prompt="A cup"), 1, 1)])
        job = jobs[0]
        harness.finish(job["remote_id"], status="failed")
        harness.jobs[job["remote_id"]]["errors"] = {
            "data": [{"code": "invalid_request", "line": 1, "message": "Example validation error"}]
        }
        wb.batches.import_results(job["id"])
        row = wb.repo.requests(exp)[0]
        assert row["calculated_cost"] == row["attempts"] == 0
        assert wb.batches.store.job(job["id"])["provider"]["errors"]["data"][0]["line"] == 1
        plan = wb.prepare("unsubmitted", [Run(GenerationSpec(prompt="A plate"), 1, 1)], "batch")
        local_exp = wb.batches.reserve(plan)
        local_job = wb.repo.batch_jobs(local_exp)[0]
        calls = len(harness.calls)
        wb.batches.cancel(local_job["id"])
        assert len(harness.calls) == calls and wb.repo.requests(local_exp)[0]["calculated_cost"] == 0
    finally:
        wb.close()


def test_crash_during_image_save_can_import_again_without_duplicates(config, harness, api_body, monkeypatch):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        exp, jobs = submit(wb, [Run(GenerationSpec(prompt="A cup", n=2), 1, 1)])
        job = jobs[0]
        line = harness.lines(job["input_file_id"])[0]
        body = {**api_body, "data": api_body["data"] * 2}
        harness.finish(job["remote_id"], [ok(line["custom_id"], body)])
        real_save = wb.repo.save_image
        failures = [True]

        def failing_save(*args, **kwargs):
            result = real_save(*args, **kwargs)
            if failures.pop() if failures else False:
                raise OSError("Simulated interruption after saving original")
            return result

        monkeypatch.setattr(wb.repo, "save_image", failing_save)
        with pytest.raises(ValueError, match="Simulated interruption"):
            wb.batches.import_results(job["id"])
        assert len(wb.repo.images(experiment=exp)) == 1
        wb.batches.import_results(job["id"])
        assert len(wb.repo.images(experiment=exp)) == 2
        row = wb.repo.requests(exp)[0]
        assert row["output_count"] == 2 and row["calculated_cost"] == pytest.approx(0.001565)
        assert harness.calls.count(("POST", "/batches")) == 1
    finally:
        wb.close()


@pytest.mark.parametrize("problem", ["unknown", "conflicting", "malformed"])
def test_malformed_or_conflicting_results_preserve_existing_records(config, harness, api_body, problem):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        exp, jobs = submit(wb, [Run(GenerationSpec(prompt="A cup"), 1, 1)])
        job = jobs[0]
        custom = harness.lines(job["input_file_id"])[0]["custom_id"]
        rows = [ok(custom, api_body)]
        if problem == "unknown":
            rows.append(ok("not-a-local-request", api_body))
        elif problem == "conflicting":
            rows.append(ok(custom, api_body, suffix="_different"))
        harness.finish(job["remote_id"], rows)
        if problem == "malformed":
            harness.files[harness.jobs[job["remote_id"]]["output_file_id"]] += b"{invalid json\n"
        with pytest.raises(ValueError):
            wb.batches.import_results(job["id"])
        assert len(wb.repo.images(experiment=exp)) == 1
        assert wb.repo.request(custom)["status"] == "succeeded"
        assert wb.batches.store.job(job["id"])["import_state"] == "error"
    finally:
        wb.close()


def test_restart_marks_unknown_submission_but_keeps_remote_jobs(config, harness):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        _, jobs = submit(wb, [Run(GenerationSpec(prompt="A cup"), 1, 1)])
        plan = wb.prepare("interrupted", [Run(GenerationSpec(prompt="A plate"), 1, 1)], "batch")
        exp = wb.batches.reserve(plan)
        incomplete = wb.repo.batch_jobs(exp)[0]
        wb.batches.store.update(incomplete["id"], status="submitting")
        wb.repo.recover_interrupted()
        assert wb.batches.store.job(incomplete["id"])["status"] == "submission_unknown"
        assert wb.repo.requests(exp)[0]["status"] == "batch_unknown"
        assert wb.batches.store.job(jobs[0]["id"])["status"] == "validating"
    finally:
        wb.close()


def test_ui_batch_preview_and_repeated_edit_configuration(config, harness, png_bytes):
    wb = Workbench(config, batch_api_factory=harness.factory)
    try:
        ui = build_ui(wb)

        def function(name):
            return next(fn.fn for fn in ui.fns.values() if fn.fn and fn.fn.__name__ == name)

        mode = next(b for b in ui.blocks.values() if getattr(b, "label", None) == "Processing mode")
        assert mode.value == "standard"
        values = form_values(GenerationSpec(prompt="A cup", moderation="low", quality="max"))
        preview = function("preview_generate")("batch", *values)
        assert preview[3]["processing_mode"] == "batch"
        assert "official Batch" in preview[1] and "24-hour" in preview[1]
        source = config.root / "ref.png"
        source.write_bytes(png_bytes)
        selected = function("append_edit")([], [str(source)], *values)[0]
        prepared = function("prepare_selected")("Repeated edit", selected, 10, "batch")
        plan = wb.plans[prepared[0]]
        assert len(plan.runs) == 10 and all(r.spec.operation == "edit" for r in plan.runs)
        assert all(r.spec.moderation == "low" and r.spec.reference_ids for r in plan.runs)
        assert harness.calls == []
        exp = wb.batches.reserve(plan)
        job = wb.repo.batch_jobs(exp)[0]
        with pytest.raises(gr.Error, match="Confirm"):
            function("resume_batch")(job["id"], False)
        assert harness.calls == []
    finally:
        wb.close()
