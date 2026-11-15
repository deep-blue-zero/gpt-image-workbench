from __future__ import annotations

import base64
import binascii
import hashlib
import io
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 100_000_000
MAX_INPUT_PIXELS = 50_000_000
FORMATS = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}


class ImageDataError(ValueError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    format: str
    sha256: str
    byte_count: int


def inspect_bytes(raw: bytes, *, byte_limit: int = MAX_IMAGE_BYTES) -> ImageInfo:
    if not raw or len(raw) > byte_limit:
        raise ImageDataError(f"Image is empty or exceeds the {byte_limit:,}-byte limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                fmt = FORMATS.get(image.format or "")
                if not fmt or image.width * image.height > MAX_INPUT_PIXELS:
                    raise ImageDataError("Use a PNG, JPEG or WebP image with at most 50 million pixels.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageDataError("Animated images are not supported. Export one still frame first.")
                width, height = image.size
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        return ImageInfo(width, height, fmt, hashlib.sha256(raw).hexdigest(), len(raw))
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImageDataError("Image data is corrupted or too large to decode safely.") from exc


def decode_image(encoded: str) -> tuple[bytes, ImageInfo]:
    if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 8:
        raise ImageDataError("Invalid or oversized base64 image response.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageDataError("The API returned invalid base64 image data.") from exc
    return raw, inspect_bytes(raw)


def thumbnail(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
        image.thumbnail((384, 384), Image.Resampling.LANCZOS)
        target = io.BytesIO()
        image.save(target, format="PNG")
        return target.getvalue()


def read_reference(path: Path, limit: int) -> bytes:
    if not path.is_file() or path.stat().st_size >= limit:
        raise ImageDataError(f"Reference must be a file smaller than {limit:,} bytes.")
    raw = path.read_bytes()
    inspect_bytes(raw, byte_limit=limit)
    return raw
