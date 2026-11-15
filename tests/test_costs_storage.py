from __future__ import annotations

import base64
import csv
import json
from dataclasses import replace

import pytest

from app.config import Config, Settings
from app.costs import CostEngine
from app.experiments import matrix
from app.models.spec import GenerationSpec
from app.processing.images import ImageDataError, decode_image
from app.storage.repository import BudgetError, Repository


def test_costs_official_calculator_and_usage(config, registry):
    costs = CostEngine(config.registry_path("pricing"), registry)
    spec = GenerationSpec(prompt="日本語", quality="low")
    estimate = costs.estimate(spec, Settings())
    assert estimate.output_tokens == 196
    assert estimate.output_usd == pytest.approx(0.00588)
    assert estimate.text_tokens == 9
    assert costs.output_tokens(replace(spec, quality="max")) == 7024
    assert costs.output_tokens(replace(spec, quality="high")) == 1756
    assert costs.output_tokens(replace(spec, model="gpt-image-2", quality="high")) == 7024
    usage = {
        "input_tokens": 30,
        "input_tokens_details": {"text_tokens": 10, "image_tokens": 20},
        "output_tokens": 100,
    }
    assert costs.calculate(spec.model, usage) == pytest.approx(0.00321)
    assert costs.calculate(spec.model, {"input_tokens": 30, "output_tokens": 100}) is None
    assert costs.calculate(spec.model, {**usage, "input_tokens": 999}) is None


def test_batches_streaming_and_auto_estimate(config, registry):
    costs = CostEngine(config.registry_path("pricing"), registry)
    spec = GenerationSpec(prompt="a", quality="low", n=3)
    assert costs.estimate(spec, Settings()).output_tokens == 196 * 3
    stream = replace(spec, n=1, stream=True, partial_images=3)
    assert costs.estimate(stream, Settings()).output_tokens == 496
    assert "highest explicit quality" in costs.estimate(replace(spec, quality="auto"), Settings()).basis
    edit = replace(spec, operation="edit", reference_ids=("a", "b"))
    assert costs.estimate(edit, Settings(reference_token_allowance=5000)).image_input_tokens == 10000


def test_decode_original_bytes(png_bytes):
    raw, info = decode_image(base64.b64encode(png_bytes).decode())
    assert raw == png_bytes and (info.width, info.height, info.format) == (48, 32, "png")
    for bad in ("%%%%", "YWJj", "", None):
        with pytest.raises(ImageDataError):
            decode_image(bad)


def test_persistence_restart_parent_export_and_secrets(
    config, registry, repo, png_bytes, api_body, monkeypatch
):
    key = "sk-do-not-record-this-secret-value-1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    spec = GenerationSpec(prompt="=1+1\n桜の道", quality="max", moderation="low")
    costs = CostEngine(config.registry_path("pricing"), registry)
    exp = repo.create_experiment("日本語の実験", [spec.to_dict()])
    request = repo.reserve(spec, costs.estimate(spec, Settings()), costs.data, exp, 1, 1, None)
    parent = repo.save_image(png_bytes, kind="source")
    child = repo.save_image(png_bytes, request_id=request, references=(parent,), metadata={"actual": "48x32"})
    repo.update_request(
        request,
        status="succeeded",
        response_json={**api_body, "api_key": key, "debug": key},
        calculated_cost=costs.calculate(spec.model, api_body["usage"]),
        output_count=1,
    )
    restarted = Repository(config.data_dir, config.timezone)
    record = restarted.image(child)
    assert record["parent_image_id"] == parent and record["reference_ids"] == [parent]
    assert record["width"] == 48 and record["request"]["spec"]["width"] == 1024
    assert (
        record["request"]["spec"]["moderation"] == "low"
        and record["request"]["spec"]["prompt"] == spec.prompt
    )
    assert restarted.image_path(child).read_bytes() == png_bytes
    assert restarted.image_path(parent).read_bytes() == png_bytes
    json_path, csv_path = restarted.export(exp)
    assert key not in json_path.read_text("utf-8") and "b64_json" not in json_path.read_text("utf-8")
    payload = json.loads(json_path.read_text("utf-8"))
    assert payload["experiment"]["name"] == "日本語の実験"
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["prompt"].startswith("'=1+1") and row["moderation"] == "low"
    assert row["actual_size"] == "48x32"


def test_budget_guard_and_unknown_reservation(config, registry, repo):
    costs = CostEngine(config.registry_path("pricing"), registry)
    spec = GenerationSpec(prompt="test", quality="low")
    estimate = costs.estimate(spec, Settings())
    exp = repo.create_experiment("budget", [])
    first = repo.reserve(spec, estimate, costs.data, exp, 1, 1, estimate.usd * 1.5)
    repo.update_request(first, status="unknown")
    with pytest.raises(BudgetError):
        repo.reserve(spec, estimate, costs.data, exp, 1, 2, estimate.usd * 1.5)
    repo.update_request(first, calculated_cost=0.0, status="failed")
    repo.reserve(spec, estimate, costs.data, exp, 1, 3, estimate.usd * 1.5)


def test_prompt_history_and_settings(config, repo):
    prompt = "優しい光。\n日本語、emoji 🎨"
    identity = repo.save_prompt("日本語", prompt)
    assert repo.prompts()[0]["id"] == identity and repo.prompts()[0]["text"] == prompt
    config.save_settings(Settings(default_moderation="low", spending_guard=True))
    restarted = Config(root=config.root, data_dir=config.data_dir)
    assert restarted.settings.default_moderation == "low" and restarted.settings.spending_guard
    with pytest.raises(ValueError):
        config.save_settings(Settings(default_moderation="none"))


def test_matrix_includes_moderation_and_repetitions(registry):
    runs = matrix(
        GenerationSpec(),
        ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"],
        ["864x1536", "1440x2560"],
        ["high", "max"],
        ["日本語", "English"],
        ["auto", "low"],
        2,
        registry,
    )
    assert len(runs) == 64
    assert len({r.config_index for r in runs}) == 32
    assert {r.spec.moderation for r in runs} == {"auto", "low"}
    assert runs[0].sample_index == 1 and runs[1].sample_index == 2
    assert runs[0].spec == runs[1].spec


def test_invalid_matrix_never_silently_skips(registry):
    with pytest.raises(ValueError):
        matrix(GenerationSpec(), ["gpt-image-2"], ["1024x1024"], ["max"], ["test"], ["auto"], 1, registry)
    with pytest.raises(ValueError):
        matrix(GenerationSpec(), [], ["1024x1024"], ["high"], ["test"], ["auto"], 1, registry)
