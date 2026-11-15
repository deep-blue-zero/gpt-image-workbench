from __future__ import annotations

import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from app.api.client import ImageAPI
from app.api.errors import WorkbenchError, classify_error
from app.batch import BatchService
from app.config import Config
from app.costs import CostEngine, Estimate
from app.experiments import Run
from app.models.capabilities import Registry
from app.processing.images import ImageDataError, decode_image, read_reference
from app.storage.repository import BudgetError, Repository, uid
from app.storage.security import sanitize

logger = logging.getLogger("workbench")


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    runs: tuple[Run, ...]
    estimates: tuple[Estimate, ...]
    processing_mode: str = "standard"

    @property
    def total(self) -> float | None:
        return None if any(x.usd is None for x in self.estimates) else sum(x.usd for x in self.estimates)


@dataclass
class Job:
    id: str
    total: int
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    completed: int = 0
    failed: int = 0
    status: str = "queued"
    message: str = "Queued locally."
    images: list[str] = field(default_factory=list)
    preview: str | None = None
    error: dict[str, Any] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


class Workbench:
    def __init__(
        self, config: Config, api_factory: Callable | None = None, batch_api_factory: Callable | None = None
    ):
        self.config = config
        self.registry = Registry(config.registry_path("capabilities"))
        self.costs = CostEngine(config.registry_path("pricing"), self.registry)
        self.repo = Repository(config.data_dir, config.timezone)
        self.api_factory = api_factory or ImageAPI
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-workbench")
        self.lock = threading.RLock()
        self.plans: dict[str, Plan] = {}
        self.job: Job | None = None
        self.batches = BatchService(self, batch_api_factory)

    def prepare(self, name: str, runs: list[Run], processing_mode: str = "standard") -> Plan:
        if processing_mode not in ("standard", "batch"):
            raise ValueError("Processing mode must be standard or batch.")
        if not runs or len(runs) > 500:
            raise ValueError("Prepare between 1 and 500 requests.")
        for run in runs:
            self.registry.validate(run.spec)
        if processing_mode == "batch":
            self.batches.validate(runs)
        estimates = tuple(self.costs.estimate(r.spec, self.config.settings, processing_mode) for r in runs)
        plan = Plan(uid(), name.strip() or "Untitled experiment", tuple(runs), estimates, processing_mode)
        with self.lock:
            if len(self.plans) > 200:
                self.plans.clear()
            self.plans[plan.id] = plan
        return plan

    def import_references(self, paths: list[str] | None, model: str) -> tuple[str, ...]:
        cap = self.registry.get(model)
        if not paths or len(paths) > cap["max_references"]:
            raise ValueError(f"Upload 1–{cap['max_references']} source/reference images.")
        return tuple(
            self.repo.import_image(Path(p), read_reference(Path(p), cap["max_reference_bytes"]))
            for p in paths
        )

    def submit(self, plan_id: str | None, confirmed: bool) -> str:
        if not confirmed:
            raise ValueError("Review the request preview and explicitly confirm the estimated API cost.")
        if not self.config.api_key:
            raise ValueError(
                "Demo mode: no API request was sent. Configure OPENAI_API_KEY in .env and restart in normal mode."
            )
        with self.lock:
            if self.job and self.job.finished is None:
                raise ValueError(
                    "An experiment is already running. Wait or cancel its remaining queued requests."
                )
            if not plan_id or plan_id not in self.plans:
                raise ValueError(
                    "Preview the current settings before submitting. Each preview can be submitted only once."
                )
            plan = self.plans[plan_id]
            # A settings change requires a fresh cost preview, rather than silent cost changes.
            current = tuple(
                self.costs.estimate(r.spec, self.config.settings, plan.processing_mode) for r in plan.runs
            )
            if current != plan.estimates:
                raise ValueError("Cost settings changed. Preview again before submitting.")
            identity = (
                self.batches.reserve(plan)
                if plan.processing_mode == "batch"
                else self.repo.create_experiment(plan.name, [r.to_dict() for r in plan.runs])
            )
            self.plans.pop(plan_id)
            self.job = Job(identity, len(plan.runs))
            self.executor.submit(
                self._execute_batch if plan.processing_mode == "batch" else self._execute, plan, self.job
            )
            return identity

    def _execute_batch(self, plan: Plan, job: Job) -> None:
        try:
            self._set(
                job, status="running", message="Uploading the confirmed experiment to the official Batch API."
            )
            for batch in self.repo.batch_jobs(job.id):
                if job.cancel.is_set():
                    self.batches.cancel(batch["id"])
                    continue
                self.batches.submit_one(batch["id"], cancel_event=job.cancel)
            self._set(
                job,
                status="batch_submitted",
                message="Batch submission stage finished. Open Batch jobs to check server status and import results. Submitted remote jobs continue if this app closes.",
            )
        except Exception as exc:
            self._set(
                job,
                status="failed",
                message=str(exc)
                if isinstance(exc, ValueError)
                else "Batch submission stopped. Review Batch jobs before taking further action.",
            )
        finally:
            self._set(job, finished=time.monotonic())

    def cancel(self) -> str:
        with self.lock:
            if not self.job or self.job.finished is not None:
                return "No active experiment."
            self.job.cancel.set()
            return "Cancellation requested for remaining local requests. An already submitted call continues and may be billed; its result will still be saved."

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            if self.job is None:
                return {
                    "status": "idle",
                    "message": "Ready. Preview settings and costs before generating.",
                    "images": [],
                    "elapsed": 0,
                }
            job = self.job
            return {
                "id": job.id,
                "status": job.status,
                "message": job.message,
                "completed": job.completed,
                "failed": job.failed,
                "total": job.total,
                "elapsed": (job.finished or time.monotonic()) - job.started,
                "images": list(job.images),
                "preview": job.preview,
                "error": job.error,
            }

    def _set(self, job: Job, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(job, key, value)

    def _execute(self, plan: Plan, job: Job) -> None:
        try:
            self.repo.set_experiment_status(job.id, "running")
            self._set(job, status="running")
            for index, (run, estimate) in enumerate(zip(plan.runs, plan.estimates, strict=True), 1):
                if job.cancel.is_set():
                    break
                settings = self.config.settings
                spec = run.spec
                if self.costs.estimate(spec, settings) != estimate:
                    raise BudgetError(
                        "Input cost allowances changed during this experiment. Preview a new plan before submitting remaining requests."
                    )
                request_id = self.repo.reserve(
                    spec,
                    estimate,
                    self.costs.data,
                    job.id,
                    run.config_index,
                    run.sample_index,
                    settings.monthly_budget_usd if settings.spending_guard else None,
                )
                start = time.monotonic()
                result_received = False
                api = None
                try:
                    references = [self.repo.image_path(identity) for identity in spec.reference_ids]
                    api = self.api_factory(self.config.api_key, self.registry, settings)

                    def on_attempt(attempt: int) -> None:
                        self.repo.update_request(request_id, status="submitted", attempts=attempt)
                        self._set(
                            job,
                            error=None,
                            message=f"Request {index}/{job.total} · configuration {run.config_index} · "
                            f"independent sample {run.sample_index} · attempt {attempt}. Waiting for OpenAI.",
                        )

                    def on_preview(encoded: str) -> None:
                        try:
                            raw, info = decode_image(encoded)
                            path = self.config.data_dir / "previews" / f"{uid()}.{info.format}"
                            path.write_bytes(raw)
                            self._set(job, preview=str(path))
                        except (ImageDataError, OSError):
                            logger.warning("partial_preview_unavailable", extra={"request_id": request_id})

                    result = api.run(spec, references, job.cancel, on_attempt, on_preview)
                    result_received = True
                    calculated = self.costs.calculate(spec.model, result.body.get("usage"))
                    self.repo.update_request(
                        request_id,
                        response_json=result.body,
                        api_request_id=result.request_id,
                        calculated_cost=calculated,
                        attempts=result.attempts,
                        elapsed_seconds=time.monotonic() - start,
                    )
                    output = result.body.get("data")
                    if (
                        not isinstance(output, list)
                        or not output
                        or len(output) > self.registry.get(spec.model)["max_images"]
                    ):
                        raise WorkbenchError(
                            "response",
                            "OpenAI returned no image data. Response metadata and usage were saved.",
                            uncertain=True,
                        )
                    saved = []
                    for entry in output:
                        raw, info = decode_image(entry.get("b64_json") if isinstance(entry, dict) else None)
                        image_id = self.repo.save_image(
                            raw,
                            request_id=request_id,
                            references=spec.reference_ids,
                            metadata={
                                "response_image": sanitize(entry),
                                "snapshot": self.registry.get(spec.model)["snapshot"],
                                "requested_size": spec.size,
                                "actual_size": f"{info.width}x{info.height}",
                                "format_matches_request": info.format == spec.output_format,
                            },
                        )
                        saved.append(image_id)
                        with self.lock:
                            job.images.append(image_id)
                        self.repo.update_request(request_id, output_count=len(saved))
                    status = "succeeded" if len(saved) == spec.n else "partial"
                    self.repo.update_request(request_id, status=status)
                    if status == "partial":
                        raise WorkbenchError(
                            "partial_response",
                            f"OpenAI returned {len(saved)} images for {spec.n} requested. Available originals and usage were saved; remaining requests stopped.",
                            uncertain=True,
                        )
                    self.repo.save_prompt(f"{spec.model} · {spec.size}", spec.prompt, "history")
                    self._set(
                        job,
                        completed=job.completed + 1,
                        preview=None,
                        message=f"Saved {len(saved)} native image(s) from request {index}/{job.total}.",
                    )
                    logger.info("request_saved", extra={"request_id": request_id})
                except Exception as exc:
                    if isinstance(exc, ImageDataError):
                        error = WorkbenchError("image_data", str(exc), uncertain=True)
                    elif isinstance(exc, OSError):
                        error = WorkbenchError(
                            "filesystem",
                            "Could not save or read an image. Check available disk space and permissions. "
                            "Do not rerun a submitted request without reviewing its recorded usage.",
                            uncertain=result_received,
                        )
                    else:
                        error = classify_error(exc)
                    update: dict[str, Any] = {
                        "status": "unknown" if error.uncertain else "failed",
                        "error_json": error.to_dict(),
                        "elapsed_seconds": time.monotonic() - start,
                    }
                    if error.request_id:
                        update["api_request_id"] = error.request_id
                    if error.category == "moderation":
                        update["status"] = "refused"
                    elif error.category == "partial_response":
                        update["status"] = "partial"
                    if not error.uncertain and not result_received and error.category != "moderation":
                        update["calculated_cost"] = 0.0
                    self.repo.update_request(request_id, **update)
                    self._set(
                        job,
                        failed=job.failed + 1,
                        message=f"{error.category}: {error.message}",
                        error=error.to_dict(),
                    )
                    # Stop the matrix on every failure: never continue spending silently.
                    self._set(job, status="failed")
                    break
                finally:
                    if api:
                        api.close()
            if job.status != "failed":
                self._set(job, status="cancelled" if job.cancel.is_set() else "completed")
        except (BudgetError, OSError) as exc:
            self._set(
                job,
                status="blocked",
                message=str(exc)
                if isinstance(exc, BudgetError)
                else "Storage error. Check the data directory and disk space before resubmitting.",
            )
        except Exception as exc:
            logger.error("experiment_failed", extra={"error_type": type(exc).__name__})
            self._set(
                job,
                status="failed",
                message="Experiment stopped after an unexpected local error. Review recorded requests before rerunning.",
            )
        finally:
            self._set(job, finished=time.monotonic())
            try:
                self.repo.set_experiment_status(job.id, job.status)
            except Exception as exc:
                logger.error("experiment_status_save_failed", extra={"error_type": type(exc).__name__})
                self._set(
                    job,
                    status="failed",
                    message="Could not persist the experiment status. Check storage and review saved requests before rerunning.",
                )

    def demo_image(self) -> str:
        """Local pixel inspection fixture; deliberately not presented as AI output."""
        image = Image.new("RGB", (1536, 1024), "#101827")
        draw = ImageDraw.Draw(image)
        for x in range(0, 1536, 32):
            draw.line((x, 0, x, 1024), fill="#28415a")
        for y in range(0, 1024, 32):
            draw.line((0, y, 1536, y), fill="#28415a")
        for x in range(1536):
            draw.line((x, 640, x, 960), fill=(int(255 * x / 1535), 128, int(255 * (1 - x / 1535))))
        draw.text((64, 64), "OFFLINE DEMO / PIXEL INSPECTION FIXTURE", fill="white", font_size=42)
        draw.text(
            (64, 132),
            "1536 x 1024 pixels - created locally, no API call or cost",
            fill="#9dc8d6",
            font_size=28,
        )
        out = io.BytesIO()
        image.save(out, format="PNG")
        return self.repo.save_image(
            out.getvalue(), kind="demo", metadata={"demo": True, "note": "Local test pattern; not AI output."}
        )

    def close(self) -> None:
        self.cancel()
        self.executor.shutdown(wait=True, cancel_futures=False)
