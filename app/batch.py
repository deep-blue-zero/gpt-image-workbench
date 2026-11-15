"""Durable asynchronous image experiments, separate from the local request queue."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from uuid import NAMESPACE_URL, uuid5

from app.api.batch import BatchAPI
from app.api.errors import WorkbenchError, classify_error
from app.api.payload import build_payload
from app.models.spec import GenerationSpec
from app.processing.images import decode_image, read_reference
from app.storage.batches import REMOTE_TERMINAL, BatchStore

MAX_INPUT_BYTES = 200_000_000
MAX_RESULT_LINE_BYTES = 512 * 1024 * 1024
SAFE_TO_SUBMIT = {"prepared", "upload_failed", "create_failed"}


class BatchResponseError(Exception):
    def __init__(self, body, status, request_id):
        self.body, self.status_code, self.request_id = body, status, request_id


class BatchService:
    def __init__(self, workbench, api_factory=None):
        self.wb = workbench
        self.store = BatchStore(workbench.repo)
        self.api_factory = api_factory or BatchAPI
        self.lock = threading.RLock()

    def _api(self):
        if self.wb.closing:
            raise ValueError("Workbench is shutting down. No new Batch API operations can start.")
        if not self.wb.config.api_key:
            raise ValueError(
                "No Batch API request was sent. Connect an API key using Connect OpenAI above. "
                "If launched with --demo, restart in normal mode first."
            )
        return self.api_factory(self.wb.config.api_key, self.wb.config.settings)

    def validate(self, runs):
        checked = set()
        for run in runs:
            self.wb.registry.validate_batch(run.spec)
            for identity in run.spec.reference_ids:
                if (identity, run.spec.model) in checked:
                    continue
                path = self.wb.repo.image_path(identity)
                record = self.wb.repo.image(identity)
                raw = read_reference(path, self.wb.registry.get(run.spec.model)["max_reference_bytes"])
                if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                    raise ValueError("A stored reference has changed. Reimport it before preparing a Batch.")
                checked.add((identity, run.spec.model))

    def reserve(self, plan):
        self.validate(plan.runs)
        budget = (
            self.wb.config.settings.monthly_budget_usd if self.wb.config.settings.spending_guard else None
        )
        return self.store.create(plan, self.wb.costs.data, budget)

    def _folder(self, identity):
        self.store.job(identity)  # Must be a local record, not an arbitrary path.
        folder = self.wb.config.data_dir / "batches" / identity
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _accept(self, identity, remote):
        local = self.store.job(identity)
        if (
            remote.get("endpoint") != local["endpoint"]
            or remote.get("input_file_id") != local["input_file_id"]
        ):
            raise ValueError("The remote Batch does not match this job's input file and endpoint.")
        if (remote.get("metadata") or {}).get("workbench_batch_id") != identity:
            raise ValueError("The remote Batch has different workbench ownership metadata.")
        if not isinstance(remote.get("id"), str) or not remote["id"].startswith("batch_"):
            raise ValueError("Invalid provider Batch ID.")
        if local["remote_id"] and remote["id"] != local["remote_id"]:
            raise ValueError("The remote Batch ID changed unexpectedly.")
        self.store.update(
            identity,
            remote_id=remote["id"],
            status=remote["status"],
            provider_json=remote,
            last_error_json=None,
        )
        self.store.request_states(identity, "batch_pending", attempts=1)
        self.store.reconcile_experiment(identity)
        return self.store.job(identity)

    def submit_one(self, identity, confirmed=True, cancel_event=None):
        if not confirmed:
            raise ValueError("Confirm the saved Batch cost before submitting or resuming uploads.")
        with self.lock:
            job = self.store.job(identity)
            if job["remote_id"] or job["status"] not in SAFE_TO_SUBMIT:
                raise ValueError(
                    "This Batch cannot be resubmitted. Check status or recover its remote ID first."
                )
            if self.wb.config.settings.spending_guard:
                self.store.check_budget(identity, self.wb.config.settings.monthly_budget_usd)
            rows = self.store.requests(identity)
            for row in rows:
                spec = GenerationSpec.from_dict(row["spec"])
                self.wb.registry.validate_batch(spec)
                current = self.wb.costs.estimate(spec, self.wb.config.settings, "batch").to_dict()
                if current != row["estimate"]:
                    raise ValueError(
                        "Cost settings changed. Cancel this unsubmitted job and prepare a fresh preview."
                    )
            api = self._api()
            try:
                self.store.update(identity, status="uploading", last_error_json=None)
                references = dict(job["reference_files"])
                checked = set()
                for row in rows:
                    spec = GenerationSpec.from_dict(row["spec"])
                    for reference in spec.reference_ids:
                        if reference in checked:
                            continue
                        path = self.wb.repo.image_path(reference)
                        record = self.wb.repo.image(reference)
                        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                            raise ValueError("A stored reference has changed; submission stopped.")
                        if reference not in references:
                            references[reference] = api.upload(path, "vision")
                            self.store.update(identity, reference_files_json=references)
                        checked.add(reference)
                path = self._folder(identity) / "input.jsonl"
                size = 0
                with path.open("wb") as target:
                    for row in rows:
                        spec = GenerationSpec.from_dict(row["spec"])
                        body = build_payload(spec, self.wb.registry)
                        if spec.operation == "edit":
                            body["images"] = [{"file_id": references[r]} for r in spec.reference_ids]
                        line = (
                            json.dumps(
                                {
                                    "custom_id": row["id"],
                                    "method": "POST",
                                    "url": job["endpoint"],
                                    "body": body,
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                            + b"\n"
                        )
                        size += len(line)
                        if size > MAX_INPUT_BYTES:
                            raise ValueError("Batch JSONL exceeds 200 MB. Split this experiment.")
                        target.write(line)
                if not job["input_file_id"]:
                    input_id = api.upload(path, "batch")
                    self.store.update(identity, input_file_id=input_id)
                else:
                    input_id = job["input_file_id"]
                if cancel_event is not None and cancel_event.is_set():
                    self.store.update(identity, status="cancelled", import_state="complete")
                    self.store.request_states(identity, "cancelled", attempts=0, cost=0.0)
                    self.store.reconcile_experiment(identity)
                    return self.store.job(identity)
                self.store.update(identity, status="submitting")
                remote = api.create(input_id, job["endpoint"], identity, job["experiment_id"])
                return self._accept(identity, remote)
            except Exception as exc:
                error = classify_error(exc)
                latest = self.store.job(identity)
                if latest["status"] == "submitting":
                    known_rejection = (
                        error.status_code is not None
                        and 400 <= error.status_code < 500
                        and error.status_code != 408
                    )
                    status = "create_failed" if known_rejection else "submission_unknown"
                    if not known_rejection:
                        self.store.request_states(identity, "batch_unknown", attempts=1)
                else:
                    status = "upload_failed"
                if isinstance(exc, ValueError):
                    error = WorkbenchError("validation", str(exc))
                self.store.update(identity, status=status, last_error_json=error.to_dict())
                self.store.reconcile_experiment(identity)
                raise ValueError(
                    f"Batch {status}: {error.message} Open Batch jobs to review; no automatic resubmission."
                ) from exc
            finally:
                api.close()

    def check(self, identity, remote_id=None):
        with self.lock:
            local = self.store.job(identity)
            api = self._api()
            try:
                remote_id = (remote_id or local["remote_id"] or "").strip()
                if remote_id:
                    remote = api.retrieve(remote_id)
                elif local["status"] in {"submission_unknown", "submitting"}:
                    matches = api.find(identity)
                    if len(matches) != 1:
                        raise ValueError(
                            "No unique matching Batch found in the latest 1,000 jobs. Paste its remote Batch ID from the OpenAI dashboard. Do not resubmit an unknown job."
                        )
                    remote = matches[0]
                else:
                    raise ValueError(
                        "This job has not been submitted. Use Resume submission or cancel it locally."
                    )
                return self._accept(identity, remote)
            finally:
                api.close()

    def cancel(self, identity):
        with self.lock:
            job = self.store.job(identity)
            if not job["remote_id"]:
                if job["status"] not in SAFE_TO_SUBMIT:
                    raise ValueError(
                        "Recover this job's remote ID before cancelling; its server outcome is unknown."
                    )
                self.store.update(identity, status="cancelled", import_state="complete")
                self.store.request_states(identity, "cancelled", attempts=0, cost=0.0)
                self.store.reconcile_experiment(identity)
                return self.store.job(identity)
            if job["status"] in REMOTE_TERMINAL:
                return job
            api = self._api()
            try:
                return self._accept(identity, api.cancel(job["remote_id"]))
            finally:
                api.close()

    def _import_line(self, identity, raw, expected):
        value = json.loads(raw)
        custom = value.get("custom_id")
        if custom not in expected:
            raise ValueError(
                "Batch result contains an unknown custom_id; no result was assigned by line order."
            )
        row = self.wb.repo.request(custom)
        digest = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
        if row["batch_result_sha256"]:
            if row["batch_result_sha256"] != digest:
                raise ValueError("Conflicting duplicate Batch response; existing result preserved.")
            return
        response = value.get("response") or {}
        body = response.get("body") or {}
        request_id = response.get("request_id")
        spec = GenerationSpec.from_dict(row["spec"])
        usage_cost = self.wb.costs.calculate(spec.model, body.get("usage"), "batch", pricing=row["pricing"])
        self.wb.repo.update_request(
            custom,
            response_json=body or None,
            api_request_id=request_id,
            calculated_cost=usage_cost,
            attempts=1,
            status="batch_importing",
        )
        provider_error = value.get("error") or body.get("error")
        if provider_error or response.get("status_code") != 200:
            error = classify_error(
                BatchResponseError(
                    {"error": provider_error} if provider_error else body,
                    response.get("status_code"),
                    request_id,
                )
            )
            status = "refused" if error.category == "moderation" else "failed"
            if error.code in {"batch_expired", "batch_cancelled"}:
                status, usage_cost = ("expired" if error.code == "batch_expired" else "cancelled"), 0.0
            self.wb.repo.update_request(
                custom,
                status=status,
                error_json=error.to_dict(),
                calculated_cost=usage_cost,
                batch_result_sha256=digest,
            )
            return
        outputs = body.get("data")
        if not isinstance(outputs, list) or not outputs or len(outputs) > spec.n:
            raise ValueError(
                "Batch response has an invalid image count; usage retained and import can be reviewed."
            )
        for index, entry in enumerate(outputs):
            image_bytes, info = decode_image(entry.get("b64_json"))
            image_id = uuid5(NAMESPACE_URL, f"gpt-image-workbench:{custom}:{index}").hex
            self.wb.repo.save_image(
                image_bytes,
                request_id=custom,
                references=spec.reference_ids,
                identity=image_id,
                metadata={
                    "response_image": entry,
                    "snapshot": self.wb.registry.get(spec.model)["snapshot"],
                    "requested_size": spec.size,
                    "actual_size": f"{info.width}x{info.height}",
                    "format_matches_request": info.format == spec.output_format,
                    "processing_mode": "batch",
                    "batch_job_id": identity,
                    "output_index": index,
                },
            )
            self.wb.repo.update_request(custom, output_count=index + 1)
        error = (
            None
            if len(outputs) == spec.n
            else WorkbenchError(
                "partial_response", f"Returned {len(outputs)} of {spec.n} requested images.", uncertain=True
            ).to_dict()
        )
        self.wb.repo.update_request(
            custom,
            status="succeeded" if error is None else "partial",
            error_json=error,
            batch_result_sha256=digest,
        )

    def import_results(self, identity):
        with self.lock:
            job = self.check(identity)
            if job["status"] not in REMOTE_TERMINAL:
                raise ValueError(
                    "This Batch is still processing. Check again when it reaches a terminal status."
                )
            api = self._api()
            try:
                expected = {r["id"] for r in self.store.requests(identity)}
                for kind in ("output", "error"):
                    file_id = (job["provider"] or {}).get(f"{kind}_file_id")
                    if not file_id:
                        continue
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
                        raise ValueError("Invalid provider result-file identifier.")
                    path = self._folder(identity) / f"{kind}-{file_id}.jsonl"
                    if not path.exists():
                        api.download(file_id, path)
                    with path.open("rb") as source:
                        while raw := source.readline(MAX_RESULT_LINE_BYTES + 1):
                            if len(raw) > MAX_RESULT_LINE_BYTES:
                                raise ValueError(
                                    "A Batch result line exceeds the workbench's 512 MiB import limit."
                                )
                            if raw.strip():
                                self._import_line(identity, raw, expected)
                missing = [r for r in self.store.requests(identity) if not r["batch_result_sha256"]]
                if (
                    missing
                    and job["status"] == "failed"
                    and not (job["provider"] or {}).get("output_file_id")
                    and not (job["provider"] or {}).get("error_file_id")
                ):
                    for row in missing:
                        error = WorkbenchError(
                            "batch_validation",
                            "OpenAI rejected the Batch input file before execution. See the saved Batch validation errors.",
                        )
                        self.wb.repo.update_request(
                            row["id"],
                            status="failed",
                            attempts=0,
                            calculated_cost=0.0,
                            error_json=error.to_dict(),
                            batch_result_sha256="not-executed",
                        )
                    missing = []
                if missing:
                    for row in missing:
                        self.wb.repo.update_request(row["id"], status="batch_unresolved")
                self.store.update(
                    identity, import_state="partial" if missing else "complete", last_error_json=None
                )
                self.store.reconcile_experiment(identity)
                self.wb.repo.export(job["experiment_id"])
                return self.store.job(identity)
            except Exception as exc:
                error = WorkbenchError(
                    "batch_import",
                    str(exc)
                    if isinstance(exc, (ValueError, OSError))
                    else "Could not import Batch results. Existing originals and usage were preserved; retry import, not generation.",
                    uncertain=True,
                )
                self.store.update(identity, import_state="error", last_error_json=error.to_dict())
                raise ValueError(error.message) from exc
            finally:
                api.close()
