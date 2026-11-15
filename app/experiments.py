from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from typing import Any

from app.models.capabilities import Registry, ValidationError
from app.models.spec import GenerationSpec


@dataclass(frozen=True)
class Run:
    spec: GenerationSpec
    config_index: int
    sample_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "config_index": self.config_index,
            "sample_index": self.sample_index,
        }


def matrix(
    base: GenerationSpec,
    models: list[str],
    sizes: list[str],
    qualities: list[str],
    prompts: list[str],
    moderations: list[str],
    repetitions: int,
    registry: Registry,
    complexities: list[str | None] | None = None,
) -> list[Run]:
    axes = (
        models,
        sizes,
        qualities,
        prompts,
        moderations,
        [base.complexity] if complexities is None else complexities,
    )
    if any(not a for a in axes):
        raise ValidationError("Select at least one value for every experiment axis.")
    if type(repetitions) is not int or not 1 <= repetitions <= 100:
        raise ValidationError("Repetitions must be a whole number from 1 to 100.")
    count = repetitions
    for axis in axes:
        count *= len(axis)
    if count > 500:
        raise ValidationError(
            "This matrix exceeds the workbench's 500-request limit. Split it into smaller experiments."
        )
    runs: list[Run] = []
    for index, (model, size, quality, prompt, moderation, complexity) in enumerate(product(*axes), 1):
        try:
            width, height = map(int, size.lower().replace("×", "x").replace(" ", "").split("x"))
        except (ValueError, AttributeError) as exc:
            raise ValidationError(f"Invalid size '{size}'. Use WIDTHxHEIGHT, one per line.") from exc
        spec = replace(
            base,
            model=model,
            width=width,
            height=height,
            quality=quality,
            prompt=prompt,
            moderation=moderation,
            complexity=complexity,
        )
        registry.validate(spec)
        runs.extend(Run(spec, index, repeat) for repeat in range(1, repetitions + 1))
    return runs
