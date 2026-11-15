"""Portable PNG copies with app-authored settings; provider originals stay intact."""

from __future__ import annotations

import io
import json
import struct
import zlib

from PIL import Image

from app.processing.images import inspect_bytes
from app.processing.metadata import FIELDS, PORTABLE_SCHEMA
from app.storage.security import sanitize

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def portable_record(record: dict) -> dict:
    spec = record.get("request", {}).get("spec")
    if not spec or not spec.get("prompt"):
        raise ValueError("This image has no saved generation request to embed.")
    return sanitize(
        {
            "schema": PORTABLE_SCHEMA,
            "software": "GPT Image Workbench",
            "spec": {field: spec[field] for field in FIELDS if field in spec},
            "operation": spec.get("operation", "generate"),
            "reference_count": len(spec.get("reference_ids", [])),
            "references_included": False,
            "created_at": record["created_at"],
            "original": {
                "sha256": record["sha256"],
                "format": record["format"],
                "width": record["width"],
                "height": record["height"],
            },
            "notice": "Settings added by GPT Image Workbench. Use the unchanged original for provider provenance verification.",
        }
    )


def parameters_text(payload: dict) -> str:
    """Common infotext layout, with only real API controls (no invented SD fields)."""
    spec = payload["spec"]
    values = {
        "Size": f"{spec['width']}x{spec['height']}",
        "Model": spec["model"],
        "Quality": spec["quality"],
        "Moderation": spec["moderation"],
        "Output format": spec["output_format"],
        "Background": spec["background"],
        "N": spec["n"],
        "Software": "GPT Image Workbench",
    }
    if spec.get("complexity") is not None:
        values["Complexity"] = spec["complexity"]
    return spec["prompt"] + "\n" + ", ".join(f"{key}: {value}" for key, value in values.items())


def text_chunk(key: str, value: str) -> bytes:
    # Uncompressed UTF-8 iTXt: keyword, compression flag/method, language, translated keyword, text.
    data = key.encode("latin-1") + b"\x00\x00\x00\x00\x00" + value.encode("utf-8")
    kind = b"iTXt"
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def embed_png_metadata(raw: bytes, fields: dict[str, str]) -> bytes:
    """Insert text without re-encoding PNG pixels or discarding other chunks."""
    if not raw.startswith(PNG_SIGNATURE):
        raise ValueError("Expected a PNG image.")
    result = [PNG_SIGNATURE]
    offset, inserted, ended = 8, False, False
    while offset + 12 <= len(raw):
        length = struct.unpack_from(">I", raw, offset)[0]
        end = offset + length + 12
        if end > len(raw):
            raise ValueError("Truncated PNG chunk.")
        kind = raw[offset + 4 : offset + 8]
        data = raw[offset + 8 : end - 4]
        if kind == b"IDAT" and not inserted:
            result.extend(text_chunk(key, value) for key, value in fields.items())
            inserted = True
        key = data.split(b"\x00", 1)[0].decode("latin-1") if kind in (b"tEXt", b"zTXt", b"iTXt") else None
        if key not in fields:
            result.append(raw[offset:end])
        offset = end
        if kind == b"IEND":
            ended = True
            break
    if not inserted or not ended or offset != len(raw):
        raise ValueError("PNG must contain image data and end at IEND.")
    return b"".join(result)


def portable_png(raw: bytes, record: dict) -> bytes:
    info = inspect_bytes(raw)
    if info.sha256 != record["sha256"]:
        raise ValueError("The saved original has changed; portable export stopped.")
    payload = portable_record(record)
    if info.format != "png":
        # PNG avoids another lossy JPEG/WebP encode; the request's output_format stays in spec.
        with Image.open(io.BytesIO(raw)) as image:
            if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "I;16"):
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="PNG")
            raw = output.getvalue()
    return embed_png_metadata(
        raw,
        {
            "gpt_image_workbench": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "parameters": parameters_text(payload),
        },
    )
