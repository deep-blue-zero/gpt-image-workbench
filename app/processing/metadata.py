"""Read explicit generation metadata locally; never infer prompts from pixels."""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from app.processing.images import inspect_bytes, read_reference
from app.storage.security import sanitize

MAX_METADATA_TEXT = 262_144
MAX_METADATA_TOTAL = 1_048_576
PORTABLE_SCHEMA = "gpt-image-workbench/1"
FIELDS = (
    "prompt",
    "model",
    "width",
    "height",
    "quality",
    "moderation",
    "output_format",
    "background",
    "n",
    "output_compression",
    "stream",
    "partial_images",
    "complexity",
)
ALIASES = {
    "prompt": "prompt",
    "positiveprompt": "prompt",
    "model": "model",
    "modelname": "model",
    "width": "width",
    "height": "height",
    "size": "size",
    "quality": "quality",
    "moderation": "moderation",
    "outputformat": "output_format",
    "background": "background",
    "n": "n",
    "outputcompression": "output_compression",
    "stream": "stream",
    "partialimages": "partial_images",
    "complexity": "complexity",
}
WRAPPERS = {
    "spec",
    "settings",
    "generation",
    "generationdata",
    "generationparameters",
    "metadata",
    "request",
    "body",
    "gptimageworkbench",
    "parameters",
}


def key_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def normalize(field, value, registry):
    if field == "complexity" and value is None:
        return None
    if field in {"width", "height", "n", "output_compression", "partial_images"}:
        if field == "output_compression" and value is None:
            return None
        if isinstance(value, str) and re.fullmatch(r"\d+", value):
            value = int(value)
        if type(value) is not int:
            raise ValueError("must be an integer")
        lower, upper = {
            "width": (16, 3840),
            "height": (16, 3840),
            "n": (1, 10),
            "output_compression": (0, 100),
            "partial_images": (0, 3),
        }[field]
        if not lower <= value <= upper:
            raise ValueError(f"must be {lower}–{upper}")
        if field in {"width", "height"} and value % 16:
            raise ValueError("must be divisible by 16")
        return value
    if field == "stream":
        if value in ("true", "false"):
            value = value == "true"
        if type(value) is not bool:
            raise ValueError("must be true or false")
        return value
    if not isinstance(value, str):
        raise ValueError("must be text")
    if field == "prompt":
        if not value.strip() or len(value) > 32000:
            raise ValueError("must contain 1–32,000 characters")
        return value
    choices = {
        "model": registry.models,
        "quality": ("auto", "low", "medium", "high", "xhigh", "max"),
        "complexity": ("low", "medium", "high"),
        "moderation": ("auto", "low"),
        "output_format": ("png", "jpeg", "webp"),
        "background": ("auto", "opaque", "transparent"),
    }
    if value not in choices[field]:
        raise ValueError("is not supported by this workbench")
    return value


class MetadataReader:
    def __init__(self, registry):
        self.registry = registry
        self.values, self.sources, self.invalid, self.notes = {}, {}, {}, []
        self.total = 0

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def text(self, value):
        if isinstance(value, bytes):
            if value.startswith(b"ASCII\x00\x00\x00"):
                value = value[8:]
            elif value.startswith(b"UNICODE\x00"):
                payload = value[8:]
                encoding = "utf-16" if payload[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-16-be"
                value = payload.decode(encoding, errors="replace")
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
        if not isinstance(value, str):
            return None
        self.total += len(value)
        if len(value) > MAX_METADATA_TEXT or self.total > MAX_METADATA_TOTAL:
            self.note("Some embedded metadata exceeded the inspection limit and was skipped.")
            return None
        return value.rstrip("\x00")

    def add(self, field, value, source):
        if field == "size":
            match = re.fullmatch(r"(\d+)\s*[x×]\s*(\d+)", str(value))
            if match:
                self.add("width", match[1], source)
                self.add("height", match[2], source)
            else:
                self.note("The recorded size does not specify explicit width and height; it was not applied.")
            return
        try:
            normalized = normalize(field, value, self.registry)
        except ValueError as exc:
            self.invalid[field] = f"Recorded {field} {exc}."
            self.values.pop(field, None)
            return
        if field in self.invalid:
            return
        if field in self.values and self.values[field] != normalized:
            self.values.pop(field)
            self.invalid[field] = f"Conflicting {field} values were found; choose it manually."
            return
        self.values[field] = normalized
        self.sources[field] = source

    def mapping(self, value, source, depth=0):
        if depth > 8 or not isinstance(value, dict):
            return
        for key, item in list(value.items())[:128]:
            name = key_name(key)
            if name in ALIASES:
                self.add(ALIASES[name], item, source)
            elif name in WRAPPERS:
                if isinstance(item, dict):
                    self.mapping(item, source, depth + 1)
                elif isinstance(item, str):
                    self.document(item, source, depth + 1)

    def document(self, text, source, depth=0):
        if depth > 8:
            return False
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            value = None
        if isinstance(value, dict):
            self.mapping(value, source, depth)
            return True
        # Common PNG parameters / EXIF UserComment format, including Civitai exports.
        settings = re.search(r"(?m)^(?:Steps|Sampler|Seed|Size|Model|Quality):[^\n]*$", text)
        if settings:
            prompt = text[: settings.start()]
            prompt = re.split(r"(?m)^Negative prompt:", prompt, maxsplit=1)[0].rstrip()
            if prompt:
                self.add("prompt", prompt, source)
            for match in re.finditer(r"(?:^|,\s*)([\w ]+):\s*([^,]+)", settings[0]):
                name = key_name(match[1])
                if name in ALIASES:
                    self.add(ALIASES[name], match[2].strip(), source)
            self.note(
                "Only supported image controls are recovered; sampler, seed, steps, and negative-prompt fields are not imported."
            )
            return True
        return False

    def embedded(self, key, value):
        name = key_name(key)
        if (
            name not in ALIASES
            and name not in WRAPPERS
            and name
            not in {"comment", "usercomment", "description", "imagedescription", "xmlcomadobexmp", "xmp"}
        ):
            return
        text = self.text(value)
        if text is None:
            return
        if name in {"xmlcomadobexmp", "xmp"}:
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                self.note("XMP with document/entity declarations was skipped.")
                return
            try:
                root = ET.fromstring(text)
                for node in list(root.iter())[:256]:
                    tag = key_name(node.tag.rsplit("}", 1)[-1])
                    if tag in ALIASES:
                        self.add(ALIASES[tag], "".join(node.itertext()), f"XMP {tag}")
                    for attr, item in node.attrib.items():
                        attr = key_name(attr.rsplit("}", 1)[-1])
                        if attr in ALIASES:
                            self.add(ALIASES[attr], item, f"XMP {attr}")
            except (ET.ParseError, RecursionError):
                self.note("Unrecognized XMP was skipped.")
        elif name in ALIASES:
            if name == "prompt":
                try:
                    graph = json.loads(text)
                except (ValueError, RecursionError):
                    graph = None
                if isinstance(graph, dict) and any(
                    isinstance(v, dict) and "class_type" in v for v in graph.values()
                ):
                    self.note("Workflow graph metadata was found; its positive prompt was not guessed.")
                    return
            self.add(ALIASES[name], text, f"Embedded {key}")
        else:
            self.document(text, f"Embedded {key}")


def recover_image_metadata(path: Path, repository, registry):
    raw = read_reference(path, 50_000_000)
    info = inspect_bytes(raw)
    reader = MetadataReader(registry)
    matched = repository.generated_image_by_hash(info.sha256)
    if matched:
        reader.mapping(matched["request"]["spec"], "Saved request (exact image bytes)")
        source = "Saved workbench request — exact image-byte match"
        if matched["request"]["spec"].get("operation") == "edit":
            reader.note(
                "This image came from an edit. Select its desired references in Edit/Image to Image; this import restores the prompt and controls."
            )
    else:
        source = "Embedded image metadata"
        with Image.open(io.BytesIO(raw)) as image:
            portable_text = image.info.get("gpt_image_workbench")
            portable = None
            if isinstance(portable_text, str) and len(portable_text) <= MAX_METADATA_TEXT:
                try:
                    portable = json.loads(portable_text)
                except (ValueError, RecursionError):
                    pass
            if (
                isinstance(portable, dict)
                and portable.get("schema") == PORTABLE_SCHEMA
                and isinstance(portable.get("spec"), dict)
            ):
                # The versioned record preserves exact Unicode/whitespace and requested size.
                # Its companion infotext is for other tools and may be interpreted differently.
                reader.mapping(portable["spec"], "Embedded workbench settings")
                source = "Embedded workbench settings — database not required"
                reader.note("Settings were embedded by the workbench; they are not provider-signed metadata.")
                if portable.get("operation") == "edit":
                    reader.note(
                        "This image came from an edit. Select its references separately in Edit/Image to Image; reference images are not embedded."
                    )
            else:
                for key, value in list(image.info.items())[:64]:
                    reader.embedded(key, value)
            try:
                exif = image.getexif()
                for tags in (exif, exif.get_ifd(34665)):
                    for tag, name in ((270, "ImageDescription"), (37510, "UserComment")):
                        if tag in tags and source == "Embedded image metadata":
                            reader.embedded(name, tags[tag])
            except (ValueError, OSError, SyntaxError):
                reader.note("Some EXIF metadata could not be read.")
    if not reader.values:
        reader.note(
            "No usable prompt or generation settings were found. Try the original downloaded image; screenshots and clipboard copies often omit metadata."
        )
    reader.note(
        "Missing fields keep their current form values. File dimensions describe the image, not necessarily the originally requested size."
    )
    reader.note("Metadata inspection is local. No image generation or OpenAI verification request is sent.")
    return sanitize(
        {
            "path": str(path.resolve()),
            "source": source,
            "file": {
                "name": path.name,
                "width": info.width,
                "height": info.height,
                "format": info.format,
                "sha256": info.sha256,
            },
            "fields": reader.values,
            "field_sources": reader.sources,
            "missing": [field for field in FIELDS if field not in reader.values],
            "invalid_fields": reader.invalid,
            "notes": reader.notes,
            "saved_image_id": matched["id"] if matched else None,
        }
    )
