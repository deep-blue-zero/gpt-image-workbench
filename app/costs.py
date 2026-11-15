from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models.capabilities import Registry
from app.models.spec import GenerationSpec


@dataclass(frozen=True)
class Estimate:
    usd: float | None
    output_usd: float | None
    text_tokens: int
    image_input_tokens: int
    output_tokens: int | None
    basis: str
    pricing_version: str
    processing_mode: str = "standard"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CostEngine:
    def __init__(self, path: Path, registry: Registry):
        self.data = json.loads(path.read_text("utf-8"))
        self.registry = registry

    def output_tokens(self, spec: GenerationSpec) -> int | None:
        """Official calculator formula; round short grid edge with ties to even."""
        family = self.registry.get(spec.model)["family"]
        table = self.data.get("output_grid", {}).get(family, {})
        qualities = list(table) if spec.quality == "auto" else [spec.quality]
        if not qualities or any(not table.get(quality) for quality in qualities):
            return None
        multipliers = self.data.get("output_token_multipliers", {}).get(family, {})
        tokens = []
        for quality in qualities:
            grid = table[quality]
            short = round(Fraction(grid * min(spec.width, spec.height), max(spec.width, spec.height)))
            base = math.ceil(Fraction(grid * short * (2000000 + spec.width * spec.height), 4000000))
            # The 2.6 calculator scales the rounded base (low 4.2, medium 3.3),
            # then uses JS Math.round: positive half-ties round up, not to even.
            multiplier = Fraction(str(multipliers.get(quality, 1)))
            tokens.append(math.floor(base * multiplier + Fraction(1, 2)))
        return max(tokens)

    def rates(self, model: str, processing_mode: str = "standard", pricing: dict | None = None):
        if processing_mode not in ("standard", "batch"):
            raise ValueError("Processing mode must be standard or batch.")
        data = self.data if pricing is None else pricing
        table = "batch_rates_per_million" if processing_mode == "batch" else "rates_per_million"
        return data.get(table, {}).get(self.registry.get(model)["family"])

    def estimate(
        self, spec: GenerationSpec, settings: Settings, processing_mode: str = "standard"
    ) -> Estimate:
        rates = self.rates(spec.model, processing_mode)
        output = self.output_tokens(spec)
        # This is a disclosed allowance, NOT a tokenizer claim or account-level cap.
        text_tokens = len(spec.prompt.encode("utf-8"))
        image_input = len(spec.reference_ids) * settings.reference_token_allowance
        basis = "Official output calculator; text allowance = UTF-8 byte count."
        if processing_mode == "batch":
            if rates is None:
                raise ValueError(
                    "Batch pricing is unavailable. Update the pricing registry before submitting."
                )
            basis += " Official Batch token rates; 24-hour completion window."
        if spec.quality == "auto":
            basis += " Auto uses the highest explicit quality as a planning allowance."
        if spec.reference_ids:
            basis += f" Input image allowance = {settings.reference_token_allowance:,} tokens/reference (user configurable, not official)."
        if rates is None or output is None:
            return Estimate(
                None,
                None,
                text_tokens,
                image_input,
                None,
                "Pricing unavailable.",
                self.data["version"],
                processing_mode,
            )
        output = output * spec.n + spec.partial_images * self.data["partial_image_output_tokens"]
        out_cost = output * rates["image_output"] / 1000000
        total = out_cost + (text_tokens * rates["text_input"] + image_input * rates["image_input"]) / 1000000
        return Estimate(
            total, out_cost, text_tokens, image_input, output, basis, self.data["version"], processing_mode
        )

    def calculate(
        self,
        model: str,
        usage: dict[str, Any] | None,
        processing_mode: str = "standard",
        *,
        pricing: dict | None = None,
    ) -> float | None:
        """Charge once per response. Missing or inconsistent usage remains estimated."""
        if not isinstance(usage, dict):
            return None
        details = usage.get("input_tokens_details")
        if not isinstance(details, dict):
            return None
        text_tokens, image_tokens = details.get("text_tokens"), details.get("image_tokens")
        output = usage.get("output_tokens")
        values = (text_tokens, image_tokens, output)
        if any(type(v) is not int or v < 0 for v in values):
            return None
        if usage.get("input_tokens", text_tokens + image_tokens) != text_tokens + image_tokens:
            return None
        out_details = usage.get("output_tokens_details")
        if isinstance(out_details, dict):
            if out_details.get("text_tokens", 0) != 0 or out_details.get("image_tokens", output) != output:
                return None
        rates = self.rates(model, processing_mode, pricing)
        if not rates:
            return None
        return (
            text_tokens * rates["text_input"]
            + image_tokens * rates["image_input"]
            + output * rates["image_output"]
        ) / 1000000
