import json

import gradio as gr
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.models.spec import GenerationSpec
from app.processing.metadata import recover_image_metadata
from app.service import Workbench
from app.ui.application import build_ui
from app.ui.components import form_values


def png_file(root, fields=None, name="source.png"):
    path = root / name
    metadata = PngInfo()
    for key, value in (fields or {}).items():
        metadata.add_itxt(key, value)
    Image.new("RGB", (48, 32), "#19425b").save(path, pnginfo=metadata)
    return path


def test_exact_saved_image_recovers_request_without_embedded_metadata(config, png_bytes):
    wb = Workbench(config)
    try:
        spec = GenerationSpec(
            prompt="日本語のカップ ☕", quality="max", moderation="low", width=2160, height=3840
        )
        exp = wb.repo.create_experiment("metadata fixture", [])
        request = wb.repo.reserve(
            spec, wb.costs.estimate(spec, config.settings), wb.costs.data, exp, 1, 1, None
        )
        image = wb.repo.save_image(png_bytes, request_id=request)
        renamed = config.root / "download-renamed.png"
        renamed.write_bytes(png_bytes)
        report = recover_image_metadata(renamed, wb.repo, wb.registry)
        assert report["saved_image_id"] == image
        assert report["fields"]["prompt"] == spec.prompt
        assert report["fields"]["moderation"] == "low"
        assert (report["fields"]["width"], report["fields"]["height"]) == (2160, 3840)
        assert (report["file"]["width"], report["file"]["height"]) == (48, 32)
        assert report["missing"] == [] and report["invalid_fields"] == {}
        assert len(wb.repo.requests()) == len(wb.repo.images()) == 1
    finally:
        wb.close()


@pytest.mark.parametrize("moderation", ["auto", "low"])
def test_png_explicit_metadata_and_unicode(config, repo, registry, moderation):
    path = png_file(
        config.root,
        {
            "Prompt": "金色のティーカップ\nNo text.",
            "Model": "gpt-image-2.5-flare",
            "Quality": "max",
            "Moderation": moderation,
            "Size": "1024x1536",
        },
    )
    report = recover_image_metadata(path, repo, registry)
    assert report["fields"] == {
        "prompt": "金色のティーカップ\nNo text.",
        "model": "gpt-image-2.5-flare",
        "quality": "max",
        "moderation": moderation,
        "width": 1024,
        "height": 1536,
    }
    assert "output_format" in report["missing"]
    assert report["invalid_fields"] == {}
    assert repo.requests() == repo.images() == []


def test_png_nested_request_json(config, repo, registry):
    payload = {
        "request": {
            "spec": {
                "prompt": "A cup",
                "model": "gpt-image-2.5-sunburst",
                "quality": "max",
                "moderation": "low",
                "output_format": "webp",
                "output_compression": 85,
                "n": 2,
                "stream": False,
                "partial_images": 0,
            }
        }
    }
    path = png_file(config.root, {"gpt_image_workbench": json.dumps(payload)})
    fields = recover_image_metadata(path, repo, registry)["fields"]
    assert fields == payload["request"]["spec"]


def test_common_parameters_extracts_positive_prompt_and_supported_controls(config, repo, registry):
    text = "A porcelain cup\nJapanese illustration\nNegative prompt: noise\nSteps: 20, Sampler: Euler, Seed: 42, Size: 1024x1536, Model: gpt-image-2.5-flare, Quality: high, Moderation: low"
    path = png_file(config.root, {"parameters": text})
    report = recover_image_metadata(path, repo, registry)
    assert report["fields"]["prompt"] == "A porcelain cup\nJapanese illustration"
    assert report["fields"]["moderation"] == "low"
    assert "seed" not in report["fields"] and "negative_prompt" not in report["fields"]
    assert any("negative-prompt" in note for note in report["notes"])


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_exif_user_comment_json(config, repo, registry, fmt):
    path = config.root / f"metadata.{fmt.lower()}"
    exif = Image.Exif()
    exif[37510] = b"ASCII\x00\x00\x00" + json.dumps({"prompt": "An amber teacup", "quality": "high"}).encode()
    Image.new("RGB", (48, 32), "#19425b").save(path, format=fmt, exif=exif)
    assert recover_image_metadata(path, repo, registry)["fields"] == {
        "prompt": "An amber teacup",
        "quality": "high",
    }


def test_xmp_explicit_fields_and_entity_rejection(config, repo, registry):
    xml = '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:g="urn:generation"><g:prompt>A glass cup</g:prompt><g:moderation>auto</g:moderation></x:xmpmeta>'
    path = png_file(config.root, {"XML:com.adobe.xmp": xml})
    report = recover_image_metadata(path, repo, registry)
    assert report["fields"] == {"prompt": "A glass cup", "moderation": "auto"}
    malicious = png_file(
        config.root,
        {"XML:com.adobe.xmp": '<!DOCTYPE x [<!ENTITY a SYSTEM "file:///private">]><prompt>&a;</prompt>'},
        "untrusted.png",
    )
    rejected = recover_image_metadata(malicious, repo, registry)
    assert rejected["fields"] == {}
    assert any("entity" in note for note in rejected["notes"])


def test_plain_image_and_provenance_do_not_invent_settings(config, repo, registry):
    path = png_file(config.root, {"C2PA": '{"issuer":"OpenAI","model":"gpt-image"}'})
    report = recover_image_metadata(path, repo, registry)
    assert report["fields"] == {}
    assert report["file"]["format"] == "png" and report["file"]["width"] == 48
    assert "prompt" in report["missing"] and "model" in report["missing"]
    assert any("No usable" in note for note in report["notes"])


def test_unsupported_conflicting_and_workflow_metadata_is_not_guessed(config, repo, registry):
    path = png_file(
        config.root,
        {
            "Prompt": "First",
            "parameters": json.dumps(
                {"prompt": "Second", "model": "unknown-image-model", "moderation": "off", "n": 100}
            ),
        },
    )
    report = recover_image_metadata(path, repo, registry)
    assert report["fields"] == {}
    assert set(report["invalid_fields"]) == {"prompt", "model", "moderation", "n"}
    graph = png_file(
        config.root,
        {"prompt": json.dumps({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "ambiguous"}}})},
        "workflow.png",
    )
    report = recover_image_metadata(graph, repo, registry)
    assert report["fields"] == {}
    assert any("Workflow graph" in note for note in report["notes"])


def test_metadata_bounds_invalid_files_and_secret_redaction(config, repo, registry, monkeypatch):
    import app.processing.metadata as metadata

    path = png_file(config.root, {"prompt": "A" * 200})
    monkeypatch.setattr(metadata, "MAX_METADATA_TEXT", 100)
    report = recover_image_metadata(path, repo, registry)
    assert report["fields"] == {} and any("limit" in note for note in report["notes"])
    bad = config.root / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        recover_image_metadata(bad, repo, registry)
    secret = "sk-" + "a" * 45
    path = png_file(
        config.root, {"parameters": json.dumps({"prompt": "A cup", "api_key": secret})}, "secret.png"
    )
    assert secret not in json.dumps(recover_image_metadata(path, repo, registry))


def test_ui_import_review_apply_clear_and_stale_upload_guard(config):
    wb = Workbench(config)
    try:
        ui = build_ui(wb)

        def functions(name):
            return [f.fn for f in ui.fns.values() if f.fn and f.fn.__name__ == name]

        path = png_file(
            config.root,
            {
                "parameters": json.dumps(
                    {"prompt": "Recovered ☕", "moderation": "low", "quality": "max", "size": "2160x3840"}
                )
            },
        )
        current = form_values(GenerationSpec(prompt="Current", model="gpt-image-2.5-flare"))
        for inspect, apply, prompt_only, clear in zip(
            functions("inspect_metadata"),
            functions("apply_metadata"),
            functions("apply_metadata_prompt"),
            functions("clear_metadata"),
            strict=True,
        ):
            result = inspect(str(path))
            report = result[0]
            assert result[2] == "Recovered ☕" and result[5]["interactive"] is True
            updates = apply(report, str(path), *current)
            assert updates[0] == gr.skip()  # Missing model is explicitly left as the current selection.
            assert updates[1:5] == ["Recovered ☕", 2160, 3840, "max"]
            assert updates[7] == "low" and updates[len(current)] == "2160x3840"
            assert prompt_only(report, str(path))[0] == "Recovered ☕"
            with pytest.raises(gr.Error):
                apply(report, str(config.root / "different.png"), *current)
            bad = config.root / "invalid.png"
            bad.write_bytes(b"bad")
            failed = inspect(str(bad))
            assert failed[0] is None and not failed[5]["interactive"]
            unsupported = png_file(
                config.root, {"Prompt": "Keep this prompt", "Model": "unknown-model"}, "unsupported.png"
            )
            unsupported_result = inspect(str(unsupported))
            assert not unsupported_result[5]["interactive"] and unsupported_result[6]["interactive"]
            with pytest.raises(gr.Error, match="unsupported"):
                apply(unsupported_result[0], str(unsupported), *current)
            assert prompt_only(unsupported_result[0], str(unsupported))[0] == "Keep this prompt"
            partial = png_file(config.root, {"Height": "1536"}, "partial.png")
            partial_report = inspect(str(partial))[0]
            incomplete = list(current)
            incomplete[2] = None
            assert apply(partial_report, str(partial), *incomplete)[len(current)] == "Custom"
            assert clear()[0] is None
        assert wb.repo.requests() == wb.repo.images() == []
        ui.close()
    finally:
        wb.close()
