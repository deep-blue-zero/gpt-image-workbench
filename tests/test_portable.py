import io
import json
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from app.models.spec import GenerationSpec
from app.processing.metadata import FIELDS, recover_image_metadata
from app.processing.portable import portable_png, portable_record
from app.service import Workbench
from app.storage.repository import Repository
from app.ui.application import build_ui


def reserve(wb, spec):
    exp = wb.repo.create_experiment("portable metadata", [])
    return wb.repo.reserve(spec, wb.costs.estimate(spec, wb.config.settings), wb.costs.data, exp, 1, 1, None)


def chunks(raw):
    offset, result = 8, []
    while offset < len(raw):
        length = struct.unpack_from(">I", raw, offset)[0]
        result.append((raw[offset + 4 : offset + 8], raw[offset + 8 : offset + 8 + length]))
        offset += length + 12
    return result


@pytest.mark.parametrize("fmt", ["png", "jpeg", "webp"])
@pytest.mark.parametrize("operation,moderation", [("generate", "auto"), ("edit", "low")])
def test_portable_copy_roundtrips_without_database_and_preserves_pixels(
    config, registry, fmt, operation, moderation
):
    wb = Workbench(config)
    try:
        output = io.BytesIO()
        mode, color = ("RGB", (39, 72, 106)) if fmt == "jpeg" else ("RGBA", (39, 72, 106, 128))
        Image.new(mode, (48, 32), color).save(output, format=fmt.upper())
        raw = output.getvalue()
        parent = wb.repo.save_image(raw, kind="source") if operation == "edit" else None
        spec = GenerationSpec(
            prompt='  日本語のティーカップ ☕\nSize: words in the prompt, not controls\nNegative prompt: is literal here\n"quoted", text  ',
            model="gpt-image-2.5-flare",
            width=2160,
            height=3840,
            quality="max",
            moderation=moderation,
            output_format=fmt,
            output_compression=None if fmt == "png" else 85,
            operation=operation,
            reference_ids=(parent,) if parent else (),
        )
        request = reserve(wb, spec)
        identity = wb.repo.save_image(raw, request_id=request, references=spec.reference_ids)
        path = config.data_dir / "portable" / f"{identity}-with-settings.png"
        assert path.is_file(), "Saving an API result must automatically create a portable copy"
        assert wb.repo.image_path(identity).read_bytes() == raw
        with Image.open(io.BytesIO(raw)) as original, Image.open(path) as portable:
            assert portable.format == "PNG" and portable.size == original.size
            assert portable.convert("RGBA").tobytes() == original.convert("RGBA").tobytes()
            payload = json.loads(portable.info["gpt_image_workbench"])
            assert payload["original"]["format"] == fmt
            assert payload["reference_count"] == (1 if parent else 0)
            assert payload["references_included"] is False
            assert "Steps:" not in portable.info["parameters"]
            assert "Seed:" not in portable.info["parameters"]
        blank_repo = Repository(config.root / "other-installation", config.timezone)
        report = recover_image_metadata(path, blank_repo, registry)
        assert report["saved_image_id"] is None
        assert report["source"] == "Embedded workbench settings — database not required"
        assert report["fields"] == {field: spec.to_dict()[field] for field in FIELDS}
        assert report["invalid_fields"] == {} and report["missing"] == []
        assert report["file"]["width"] == 48 and report["fields"]["width"] == 2160
        assert blank_repo.images() == blank_repo.requests() == []
        first = path.stat().st_mtime_ns
        assert wb.repo.portable_image_path(identity) == path
        assert path.stat().st_mtime_ns == first
        assert not list(path.parent.glob("*.part"))
    finally:
        wb.close()


def test_png_keeps_original_pixel_stream_and_provenance_chunk(config, png_bytes):
    wb = Workbench(config)
    try:
        kind, data = b"caBX", b"test provenance bytes; no signature validation implied"
        box = struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        raw = png_bytes[:33] + box + png_bytes[33:]
        spec = GenerationSpec(prompt="A teacup")
        identity = wb.repo.save_image(raw, request_id=reserve(wb, spec))
        copy = wb.repo.portable_image_path(identity).read_bytes()
        assert [(k, v) for k, v in chunks(copy) if k != b"iTXt"] == chunks(raw)
        assert wb.repo.image_path(identity).read_bytes() == raw
        assert copy != raw
    finally:
        wb.close()


def test_exports_only_allowlisted_settings_and_checks_original_integrity(config, png_bytes):
    wb = Workbench(config)
    try:
        identity = wb.repo.save_image(png_bytes, request_id=reserve(wb, GenerationSpec(prompt="A cup")))
        record = wb.repo.image(identity)
        record["request"]["spec"]["api_key"] = "do-not-export"
        record["metadata"] = {"local_path": "private/path", "response_image": {"secret": "hidden"}}
        payload = portable_record(record)
        assert "do-not-export" not in json.dumps(payload)
        assert "private/path" not in json.dumps(payload)
        assert "hidden" not in json.dumps(payload)
        record["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="original has changed"):
            portable_png(png_bytes, record)
    finally:
        wb.close()


def test_export_failure_keeps_paid_result_and_can_retry(config, png_bytes, monkeypatch, caplog):
    wb = Workbench(config)
    try:
        export = wb.repo.portable_image_path
        request = reserve(wb, GenerationSpec(prompt="A cup"))

        def disk_error(identity):
            raise OSError("test disk failure")

        monkeypatch.setattr(wb.repo, "portable_image_path", disk_error)
        identity = wb.repo.save_image(png_bytes, request_id=request)
        assert wb.repo.image_path(identity).read_bytes() == png_bytes
        assert wb.repo.image(identity)["request_id"] == request
        assert "portable_export_failed" in caplog.text
        monkeypatch.setattr(wb.repo, "portable_image_path", export)
        assert export(identity).is_file()
    finally:
        wb.close()


def test_gallery_exposes_both_files_and_handles_sources(config, png_bytes, monkeypatch):
    wb = Workbench(config)
    try:
        identity = wb.repo.save_image(png_bytes, request_id=reserve(wb, GenerationSpec(prompt="A cup")))
        source = wb.repo.save_image(png_bytes, kind="source")
        ui = build_ui(wb)
        inspect = next(fn.fn for fn in ui.fns.values() if fn.fn and fn.fn.__name__ == "inspect")
        empty = inspect(None, "Fit")
        assert empty[1:3] == (None, None)
        view = inspect(identity, "Fit")
        assert Path(view[1]).read_bytes() == png_bytes
        assert Path(view[2]).is_file() and view[1] != view[2]
        assert view[4] == "A cup"
        assert inspect(source, "Fit")[2] is None

        def disk_error(identity):
            raise OSError("test disk failure")

        monkeypatch.setattr(wb.repo, "portable_image_path", disk_error)
        view = inspect(identity, "Fit")
        assert Path(view[1]).is_file() and view[2] is None
        assert "Portable PNG unavailable" in view[3]
    finally:
        wb.close()
