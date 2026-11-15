from __future__ import annotations

import csv
import json
import threading
from dataclasses import replace

import gradio as gr
import httpx
import pytest
from openai import OpenAI

from app.api.client import ImageAPI
from app.api.payload import build_payload
from app.config import Settings
from app.costs import CostEngine
from app.experiments import Run, matrix
from app.models.spec import GenerationSpec
from app.processing.metadata import recover_image_metadata
from app.service import Workbench
from app.storage.repository import Repository
from app.ui.application import PLAN_HEADERS, build_ui
from app.ui.components import form_values, make_spec
from app.ui.views import plan_rows

MODELS = ["gpt-image-2.6-sunburst", "gpt-image-2.6-flare"]


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("operation", ["generate", "edit"])
@pytest.mark.parametrize("complexity", [None, "low", "medium", "high"])
def test_26_sdk_wire_contract(registry, tmp_path, png_bytes, api_body, model, operation, complexity):
    captured = []

    def transport(request):
        captured.append((request.url.path, request.headers, request.read()))
        return httpx.Response(200, json=api_body, headers={"x-request-id": "req_26_mock"})

    with OpenAI(
        api_key="unit-test-placeholder",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    ) as client:
        reference = tmp_path / "reference.png"
        reference.write_bytes(png_bytes)
        spec = GenerationSpec(
            model=model,
            prompt="Keep the composition — 青い空",
            quality="max",
            complexity=complexity,
            width=1824,
            height=2576,
            moderation="low",
            operation=operation,
            reference_ids=("ref",) if operation == "edit" else (),
        )
        attempts = []
        result = ImageAPI("unused", registry, Settings(), client).run(
            spec,
            [reference] if operation == "edit" else [],
            threading.Event(),
            attempts.append,
            lambda _: None,
        )
    assert attempts == [1] and result.request_id == "req_26_mock" and len(captured) == 1
    path, headers, raw = captured[0]
    if operation == "generate":
        assert path.endswith("/generations")
        body = json.loads(raw)
        assert body["model"] == model and body["moderation"] == "low"
        assert body["quality"] == "max" and body["size"] == "1824x2576"
        assert "extra_body" not in body
        assert body.get("complexity") == complexity
        assert ("complexity" in body) == (complexity is not None)
    else:
        assert path.endswith("/edits") and "multipart/form-data" in headers["content-type"]
        for key, value in {
            "model": model,
            "quality": "max",
            "moderation": "low",
            "size": "1824x2576",
        }.items():
            assert f'name="{key}"\r\n\r\n{value}'.encode() in raw
        assert png_bytes in raw and b'name="image[]"' in raw
        if complexity is None:
            assert b'name="complexity"' not in raw
        else:
            assert f'name="complexity"\r\n\r\n{complexity}'.encode() in raw
    assert spec == make_spec(form_values(spec), operation=operation, references=spec.reference_ids)
    assert GenerationSpec.from_dict(spec.to_dict()) == spec


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    "quality,tokens",
    [
        ("low", 823),
        ("medium", 1449),
        ("high", 1756),
        ("xhigh", 3122),
        ("max", 7024),
        ("auto", 7024),
    ],
)
def test_26_official_calculator(config, registry, model, quality, tokens):
    costs = CostEngine(config.registry_path("pricing"), registry)
    spec = GenerationSpec(model=model, prompt="A cup", quality=quality, complexity="low")
    registry.validate(spec)
    estimate = costs.estimate(spec, Settings())
    assert estimate.output_tokens == tokens
    assert estimate.output_usd == pytest.approx(tokens * 30 / 1_000_000)
    assert costs.output_tokens(replace(spec, complexity="high")) == tokens
    assert costs.estimate(replace(spec, n=3), Settings()).output_tokens == tokens * 3
    assert (
        costs.estimate(replace(spec, stream=True, partial_images=2), Settings()).output_tokens == tokens + 200
    )
    usage = {"input_tokens_details": {"text_tokens": 10, "image_tokens": 20}, "output_tokens": 100}
    assert costs.calculate(model, usage) == pytest.approx(0.00321)
    with pytest.raises(ValueError, match="Batch pricing"):
        costs.estimate(spec, Settings(), "batch")
    with pytest.raises(ValueError, match="Batch support"):
        registry.validate_batch(spec)


def test_legacy_specs_and_invalid_combinations(registry):
    legacy = GenerationSpec.from_dict({"model": "gpt-image-2.5-flare", "prompt": "Legacy prompt"})
    assert legacy.complexity is None and "complexity" not in build_payload(legacy, registry)
    assert GenerationSpec().model == next(iter(registry.models)) == "gpt-image-2.5-sunburst"
    for model in registry.models:
        if model not in MODELS:
            with pytest.raises(ValueError, match="complexity"):
                build_payload(replace(legacy, model=model, complexity="low"), registry)
    for model in MODELS:
        for bad in ("auto", "max", "", 3):
            with pytest.raises(ValueError, match="complexity"):
                registry.validate(replace(legacy, model=model, complexity=bad))
        for fmt in ("gif", "svg"):
            with pytest.raises(ValueError, match="output format"):
                registry.validate(replace(legacy, model=model, output_format=fmt))
        registry.validate_resolution(model, 2160, 3840)
        with pytest.raises(ValueError):
            registry.validate_resolution(model, 3840, 3840)


def test_complexity_matrix_and_request_limit(registry):
    args = (GenerationSpec(), MODELS, ["1024x1024"], ["low", "high"], ["A cup"], ["auto"], 2, registry)
    runs = matrix(*args, complexities=["low", "medium", "high"])
    assert len(runs) == 24
    assert {r.spec.complexity for r in runs} == {"low", "medium", "high"}
    assert runs[0].spec == runs[1].spec and runs[1].sample_index == 2
    with pytest.raises(ValueError, match="every experiment axis"):
        matrix(*args, complexities=[])
    with pytest.raises(ValueError, match="500-request"):
        matrix(*args, complexities=["low"] * 64)
    assert all(r.spec.complexity is None for r in matrix(*args))


@pytest.mark.parametrize("complexity", [None, "low", "medium", "high"])
def test_26_history_portable_export_and_ui_roundtrip(config, registry, png_bytes, complexity):
    wb = Workbench(config)
    try:
        spec = GenerationSpec(model=MODELS[0], prompt="A cup", complexity=complexity, quality="medium")
        plan = wb.prepare("2.6 offline test", [Run(spec, 1, 1)])
        assert len(plan_rows(plan)[0]) == len(PLAN_HEADERS)
        assert plan_rows(plan)[0][6] == (complexity or "model default")
        exp = wb.repo.create_experiment("2.6 metadata", [spec.to_dict()])
        req = wb.repo.reserve(spec, plan.estimates[0], wb.costs.data, exp, 1, 1, None)
        identity = wb.repo.save_image(png_bytes, request_id=req)
        path = wb.repo.portable_image_path(identity)
        blank = Repository(config.root / "other-installation", config.timezone)
        report = recover_image_metadata(path, blank, registry)
        assert not report["invalid_fields"]
        assert report["fields"]["complexity"] == complexity
        assert GenerationSpec.from_dict(wb.repo.request(req)["spec"]) == spec
        json_path, csv_path = wb.repo.export(exp)
        assert json.loads(json_path.read_text("utf-8"))["requests"][0]["spec"]["complexity"] == complexity
        with csv_path.open(encoding="utf-8-sig", newline="") as file:
            assert next(csv.DictReader(file))["complexity"] == (complexity or "")
        ui = build_ui(wb)
        models = [b for b in ui.blocks.values() if getattr(b, "label", None) == "Model / pinned snapshot"]
        assert len(models) == 2 and all(set(MODELS) <= {choice[1] for choice in b.choices} for b in models)
        current = form_values(GenerationSpec(prompt="Current prompt"))
        for fn in ui.fns.values():
            if fn.fn and fn.fn.__name__ == "apply_metadata":
                values = fn.fn(report, str(path), *current)[: len(current)]
                assert values[13] == (complexity or "default")
                assert make_spec(values) == spec
        handlers = [fn for fn in ui.fns.values() if fn.fn and fn.fn.__name__ == "form_changed"]
        for handler in handlers:
            controls = [ui.blocks[target[0]] for target in handler.targets]
            assert any(getattr(b, "label", None) == "Complexity / planning effort" for b in controls)
            assert handler.fn(spec.model, spec.width, spec.height, spec.size)[:2] == (None, False)
        assert blank.requests() == []  # Metadata inspection never submits a request.
        ui.close()
    finally:
        wb.close()


def test_26_ui_preview_matrix_and_batch_guard(config):
    wb = Workbench(config)
    try:
        ui = build_ui(wb)
        prepare = next(f.fn for f in ui.fns.values() if f.fn and f.fn.__name__ == "prepare_matrix")
        args = ("2.6", MODELS, "1024x1024", ["low"], "A cup", ["auto"], 1, 1, "standard")
        result = prepare(*args, ["low", "high"])
        assert len(result[3]["runs"]) == 4 and result[4] is False
        assert {r["spec"]["complexity"] for r in result[3]["runs"]} == {"low", "high"}
        preview = next(f.fn for f in ui.fns.values() if f.fn and f.fn.__name__ == "preview_generate")
        with pytest.raises(gr.Error, match="Batch support"):
            preview("batch", *form_values(GenerationSpec(model=MODELS[0], prompt="A cup")))
        assert wb.repo.requests() == wb.repo.experiments() == []
        ui.close()
    finally:
        wb.close()
