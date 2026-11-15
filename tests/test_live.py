"""Optional paid smoke test. Never runs under ordinary pytest."""

import base64
import hashlib
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from app.api.client import ImageAPI
from app.config import Config, configure_logging
from app.experiments import Run
from app.main import InstanceLock
from app.models.spec import GenerationSpec
from app.service import Workbench
from app.storage.repository import Repository


@pytest.mark.live
def test_live_single_image():
    config = Config(data_dir=Path(os.getenv("WORKBENCH_LIVE_DATA_DIR", ".validation/live")))
    if not config.api_key:
        pytest.fail("Live test explicitly enabled but OPENAI_API_KEY is unavailable.")
    # The deliberately enabled test authorizes one submission, never retries.
    config.settings = replace(config.settings, max_retries=0)
    operation = os.getenv("WORKBENCH_LIVE_OPERATION", "generate")
    if operation not in ("generate", "edit"):
        pytest.fail("WORKBENCH_LIVE_OPERATION must be generate or edit.")
    source_id = os.getenv("WORKBENCH_LIVE_REFERENCE_ID") if operation == "edit" else None
    if operation == "edit" and not source_id:
        pytest.fail("Live edit requires an explicit WORKBENCH_LIVE_REFERENCE_ID in the selected data folder.")
    spec = GenerationSpec(
        model=os.getenv("WORKBENCH_LIVE_MODEL", "gpt-image-2.5-flare"),
        prompt=(
            "Edit this blue ceramic teacup by adding a thin metallic gold band around its top rim. "
            "Preserve the blue glaze, cup shape, handle, lighting, plain white background, and camera composition. "
            "Change only the rim."
            if operation == "edit"
            else "A single blue ceramic teacup on a plain white table. Minimal studio photograph."
        ),
        width=1024,
        height=1024,
        quality="low",
        moderation="auto",
        n=1,
        operation=operation,
        reference_ids=(source_id,) if source_id else (),
    )
    # Explicit overrides keep larger user-authorized probes on the same audited path.
    prompt_file = os.getenv("WORKBENCH_LIVE_PROMPT_FILE")
    spec = replace(
        spec,
        width=int(os.getenv("WORKBENCH_LIVE_WIDTH", "1024")),
        height=int(os.getenv("WORKBENCH_LIVE_HEIGHT", "1024")),
        quality=os.getenv("WORKBENCH_LIVE_QUALITY", "low"),
        moderation=os.getenv("WORKBENCH_LIVE_MODERATION", "auto"),
        prompt=Path(prompt_file).read_text("utf-8") if prompt_file else spec.prompt,
    )
    observed = []

    class ObservedImageAPI(ImageAPI):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            observed.append(result)
            return result

    with InstanceLock(config.data_dir / ".instance.lock"):
        configure_logging(config.data_dir)
        workbench = Workbench(config, api_factory=ObservedImageAPI)
        source_path = None
        source_bytes = None
        try:
            if source_id:
                source_path = workbench.repo.image_path(source_id)
                source_bytes = source_path.read_bytes()
                assert hashlib.sha256(source_bytes).hexdigest() == workbench.repo.image(source_id)["sha256"]
            name = os.getenv("WORKBENCH_LIVE_NAME") or (
                "Explicit live edit smoke test" if source_id else "Explicit live smoke test"
            )
            plan = workbench.prepare(name, [Run(spec, 1, 1)])
            print(
                f"Single paid {operation} request: {spec.model}, {spec.size}, {spec.quality}, {spec.moderation}; estimated ${plan.total:.6f}; retries disabled.",
                flush=True,
            )
            exp = workbench.submit(plan.id, True)
            print(f"Experiment: {exp}", flush=True)
            while workbench.snapshot()["status"] in ("queued", "running"):
                time.sleep(0.25)
            assert workbench.snapshot()["status"] == "completed", workbench.snapshot()
        finally:
            workbench.close()

        # Reopen storage after the worker has fully shut down; do not submit again.
        repo = Repository(config.data_dir, config.timezone)
        requests = repo.requests(exp)
        assert len(requests) == 1 and len(observed) == 1
        request = requests[0]
        assert request["status"] == "succeeded" and request["attempts"] == 1
        assert request["spec"] == json.loads(json.dumps(spec.to_dict()))
        assert request["api_request_id"] == observed[0].request_id
        assert request["api_request_id"]
        assert request["output_count"] == 1
        outputs = repo.images(experiment=exp)
        assert len(outputs) == 1
        saved = repo.image(outputs[0]["id"])
        path = repo.image_path(saved["id"])
        original = base64.b64decode(observed[0].body["data"][0]["b64_json"], validate=True)
        assert path.read_bytes() == original
        assert saved["sha256"] == hashlib.sha256(original).hexdigest()
        assert saved["byte_count"] == len(original)
        assert (saved["width"], saved["height"]) == (spec.width, spec.height)
        assert saved["metadata"]["requested_size"] == saved["metadata"]["actual_size"] == spec.size
        assert saved["format"] == "png" and saved["metadata"]["format_matches_request"]
        assert saved["parent_image_id"] == source_id
        assert saved["reference_ids"] == list(spec.reference_ids)
        if source_path:
            assert path != source_path and source_path.read_bytes() == source_bytes
            assert path.read_bytes() != source_bytes
            assert repo.image(source_id)["sha256"] == hashlib.sha256(source_bytes).hexdigest()
        with Image.open(path) as image:
            image.verify()
        with Image.open(repo.image_path(saved["id"], thumb=True)) as thumb:
            thumb.verify()

        usage = request["response"].get("usage")
        assert usage and usage == observed[0].body.get("usage")
        assert request["calculated_cost"] == workbench.costs.calculate(spec.model, usage)
        assert request["calculated_cost"] is not None
        json_path, csv_path = repo.export(exp)
        exported = json.loads(json_path.read_text("utf-8"))
        assert exported["experiment"]["status"] == "completed"
        assert exported["requests"][0]["response"]["usage"] == usage
        assert exported["images"][0]["sha256"] == saved["sha256"]
        json_has_key = config.api_key in json_path.read_text("utf-8")
        csv_has_key = config.api_key in csv_path.read_text("utf-8-sig")
        assert not json_has_key and not csv_has_key, "Credential exclusion failed; inspect exports locally."
        report = {
            "experiment_id": exp,
            "request_id": request["id"],
            "api_request_id": request["api_request_id"],
            "model": spec.model,
            "operation": spec.operation,
            "parent_image_id": saved["parent_image_id"],
            "reference_ids": saved["reference_ids"],
            "source_path": str(source_path) if source_path else None,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest() if source_bytes else None,
            "source_unchanged": True if source_path else None,
            "prompt": spec.prompt,
            "quality": spec.quality,
            "moderation": spec.moderation,
            "requested_size": spec.size,
            "actual_size": saved["metadata"]["actual_size"],
            "attempts": request["attempts"],
            "image_path": str(path),
            "image_id": saved["id"],
            "sha256": saved["sha256"],
            "bytes": saved["byte_count"],
            "usage": usage,
            "estimated_usd": request["estimated_cost"],
            "usage_derived_usd": request["calculated_cost"],
            "elapsed_seconds": request["elapsed_seconds"],
            "json_export": str(json_path),
            "csv_export": str(csv_path),
            "checks": "Original API bytes, image decoding, thumbnail, exact settings, parent/reference links, source preservation, metadata, usage, calculated cost, exports and database reopening passed.",
        }
        report_path = json_path.with_name("smoke-validation.json")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
