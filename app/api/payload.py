from typing import Any

from app.models.capabilities import Registry
from app.models.spec import GenerationSpec


def build_payload(spec: GenerationSpec, registry: Registry) -> dict[str, Any]:
    registry.validate(spec)
    payload: dict[str, Any] = {
        "model": spec.model,
        "prompt": spec.prompt,
        "size": spec.size,
        "quality": spec.quality,
        "n": spec.n,
        "output_format": spec.output_format,
        "background": spec.background,
        "moderation": spec.moderation,
    }
    if spec.output_compression is not None:
        payload["output_compression"] = spec.output_compression
    if spec.complexity is not None:
        payload["complexity"] = spec.complexity
    if spec.stream:
        payload.update(stream=True, partial_images=spec.partial_images)
    return payload
