from dataclasses import replace

import pytest

from app.api.payload import build_payload
from app.models.capabilities import ValidationError
from app.models.spec import PRESETS, GenerationSpec


@pytest.mark.parametrize("size", PRESETS)
def test_all_requested_presets(registry, size):
    w, h = map(int, size.split("x"))
    registry.validate(GenerationSpec(prompt="桜の下で、柔らかな光。", width=w, height=h))


@pytest.mark.parametrize(
    "w,h",
    [
        (1000, 1024),
        (3841, 2160),
        (4096, 2048),
        (128, 128),
        (3840, 3840),
        (3840, 640),
        (0, 1024),
        (-16, 1024),
        (1024.5, 1024),
        (True, 1024),
    ],
)
def test_invalid_resolutions(registry, w, h):
    with pytest.raises(ValidationError):
        registry.validate_resolution("gpt-image-2.5-sunburst", w, h)


def test_resolution_boundaries(registry):
    assert not registry.validate_resolution("gpt-image-2", 640, 1024)
    assert not registry.validate_resolution("gpt-image-2", 2560, 1440)
    assert registry.validate_resolution("gpt-image-2", 3840, 2160)
    assert registry.validate_resolution("gpt-image-2", 2160, 3840)
    assert registry.validate_resolution("gpt-image-2", 3840, 1280)


@pytest.mark.parametrize(
    "model",
    [
        "gpt-image-2.5-sunburst",
        "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst-2026-09-08",
        "gpt-image-2.5-flare-2026-09-08",
    ],
)
def test_new_quality_and_exact_model(registry, model):
    spec = GenerationSpec(prompt="日本語", model=model, quality="max", moderation="low")
    payload = build_payload(spec, registry)
    assert payload["model"] == model and payload["quality"] == "max" and payload["moderation"] == "low"
    assert not {"seed", "response_format", "input_fidelity", "style"} & payload.keys()


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "invented-model"},
        {"model": "gpt-image-2", "quality": "max"},
        {"moderation": "none"},
        {"n": 0},
        {"n": 11},
        {"n": 1.5},
        {"prompt": ""},
        {"prompt": "a" * 32001},
        {"output_format": "jpeg", "background": "transparent"},
        {"output_compression": 100},
        {"output_format": "jpeg", "output_compression": -1},
        {"operation": "edit"},
        {"reference_ids": ("id",)},
        {"stream": True, "n": 2},
        {"partial_images": 1},
        {"partial_images": 4},
    ],
)
def test_invalid_capability_combinations(registry, changes):
    with pytest.raises(ValidationError):
        registry.validate(replace(GenerationSpec(prompt="test"), **changes))


def test_endpoint_specific_moderation_compatibility(registry):
    registry.data["families"]["gpt-image-2.5"]["moderation"]["edit"] = ["auto"]
    with pytest.raises(ValidationError, match="moderation"):
        registry.validate(
            GenerationSpec(prompt="edit", operation="edit", reference_ids=("source",), moderation="low")
        )


def test_compression_stream_payload(registry):
    spec = GenerationSpec(
        prompt="long " * 4000, stream=True, partial_images=3, output_format="webp", output_compression=75
    )
    result = build_payload(spec, registry)
    assert result["size"] == "1024x1024" and result["partial_images"] == 3
    assert result["output_compression"] == 75 and result["stream"] is True
