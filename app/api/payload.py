from pathlib import Path
from typing import Any, BinaryIO

from app.api.errors import WorkbenchError
from app.models.capabilities import Registry
from app.models.spec import GenerationSpec

_IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".webp": "image/webp"}


def reference_file_part(source: BinaryIO) -> tuple[str, BinaryIO, str]:
    # Repository filenames use the format detected from the image bytes at import.
    # HTTPX otherwise guesses from the host MIME table, which can omit WebP on Windows.
    path = Path(source.name)
    media_type = _IMAGE_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise WorkbenchError("parameters", "The saved reference must be a PNG, JPEG, or WebP image.")
    return path.name, source, media_type


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
