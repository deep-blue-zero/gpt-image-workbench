from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.spec import GenerationSpec


class ValidationError(ValueError):
    pass


class Registry:
    def __init__(self, path: Path):
        self.data = json.loads(path.read_text("utf-8"))
        self.models = self.data["models"]

    def _family(self, name: str) -> dict[str, Any]:
        values = self.data["families"][name]
        return {**(self._family(values["extends"]) if "extends" in values else {}), **values}

    def get(self, model: str) -> dict[str, Any]:
        if model not in self.models:
            raise ValidationError(
                f"Unsupported model: {model}. Update config/capabilities.json after verifying documentation."
            )
        info = self.models[model]
        return {**self._family(info["family"]), **info}

    def validate_resolution(self, model: str, width: int, height: int) -> bool:
        limits = self.get(model)["resolution"]
        if any(type(v) is not int or v <= 0 for v in (width, height)):
            raise ValidationError("Width and height must be positive whole numbers.")
        if width % limits["multiple"] or height % limits["multiple"]:
            raise ValidationError(f"Both dimensions must be divisible by {limits['multiple']}.")
        if max(width, height) > limits["max_edge"]:
            raise ValidationError(f"Neither edge may exceed {limits['max_edge']} pixels.")
        if max(width, height) > limits["max_ratio"] * min(width, height):
            raise ValidationError("Aspect ratio must remain between 1:3 and 3:1.")
        if not limits["min_pixels"] <= width * height <= limits["max_pixels"]:
            raise ValidationError(f"Total pixels must be {limits['min_pixels']:,}–{limits['max_pixels']:,}.")
        return width * height > limits["experimental_pixels"]

    def validate(self, spec: GenerationSpec) -> bool:
        cap = self.get(spec.model)
        experimental = self.validate_resolution(spec.model, spec.width, spec.height)
        if spec.operation not in cap["endpoints"]:
            raise ValidationError(f"{spec.model} does not support {spec.operation}.")
        if spec.complexity is not None and spec.complexity not in cap.get("complexities", []):
            raise ValidationError(
                f"{spec.model}: unsupported complexity '{spec.complexity}'. "
                "Choose model default, or select GPT Image 2.6 with low, medium, or high complexity."
            )
        for value, choices, label in (
            (spec.quality, cap["qualities"], "quality"),
            (spec.output_format, cap["formats"], "output format"),
            (spec.background, cap["backgrounds"], "background"),
            (spec.moderation, cap["moderation"].get(spec.operation, []), "moderation"),
        ):
            if value not in choices:
                raise ValidationError(
                    f"{spec.model} / {spec.operation}: unsupported {label} '{value}'. Choose {', '.join(choices)}."
                )
        if (
            not isinstance(spec.prompt, str)
            or not spec.prompt.strip()
            or len(spec.prompt) > cap["max_prompt_chars"]
        ):
            raise ValidationError(f"Enter a prompt of 1–{cap['max_prompt_chars']:,} characters.")
        if type(spec.n) is not int or not 1 <= spec.n <= cap["max_images"]:
            raise ValidationError(f"Images per request must be 1–{cap['max_images']}.")
        if spec.background == "transparent" and spec.output_format not in ("png", "webp"):
            raise ValidationError("Transparent output requires PNG or WebP.")
        if spec.output_compression is not None:
            if spec.output_format not in cap["compression_formats"]:
                raise ValidationError(
                    "Compression applies only to JPEG or WebP. Disable compression for PNG."
                )
            if type(spec.output_compression) is not int or not 0 <= spec.output_compression <= 100:
                raise ValidationError("Compression must be a whole number from 0 to 100.")
        if spec.stream and spec.operation not in cap["streaming"]:
            raise ValidationError("Streaming is not documented for this model and endpoint.")
        if type(spec.partial_images) is not int or not 0 <= spec.partial_images <= 3:
            raise ValidationError("Partial previews must be 0–3.")
        if spec.partial_images and not spec.stream:
            raise ValidationError("Enable streaming to request partial previews.")
        if spec.stream and spec.n != 1:
            raise ValidationError(
                "The workbench supports streaming one image per request. Use repetitions for several samples."
            )
        if spec.operation == "edit" and not 1 <= len(spec.reference_ids) <= cap["max_references"]:
            raise ValidationError(f"Edits require 1–{cap['max_references']} reference images.")
        if spec.operation == "generate" and spec.reference_ids:
            raise ValidationError("Use Edit to submit reference images.")
        return experimental

    def validate_batch(self, spec: GenerationSpec) -> None:
        self.validate(spec)
        if spec.operation not in self.get(spec.model).get("batch_endpoints", []):
            raise ValidationError(
                f"Official Batch support is not configured for {spec.model} / {spec.operation}."
            )
        if spec.stream or spec.partial_images:
            raise ValidationError(
                "Batch mode does not support streaming or partial previews. Disable them explicitly."
            )
