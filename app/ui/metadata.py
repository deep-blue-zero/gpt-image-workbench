from __future__ import annotations

from pathlib import Path

import gradio as gr

from app.models.spec import PRESETS
from app.processing.metadata import recover_image_metadata

FORM_FIELDS = {
    "model": 0,
    "prompt": 1,
    "width": 2,
    "height": 3,
    "quality": 4,
    "output_format": 5,
    "background": 6,
    "moderation": 7,
    "n": 8,
    "stream": 11,
    "partial_images": 12,
    "complexity": 13,
}


def image_metadata_controls(workbench, prefix):
    """Create beside the prompt, then bind after the remaining form controls exist."""
    recovered = gr.State(None)
    with gr.Accordion(
        "Recover prompt & settings from an image", open=False, elem_id=f"{prefix}-metadata-panel"
    ) as panel:
        gr.Markdown(
            "Drop an image onto the prompt box or choose a file here. Recovery uses embedded metadata "
            "or an exact match in your saved workbench images. Review the recovered values before applying them. "
            "Images without either source cannot reveal their original prompt or settings."
        )
        upload = gr.File(
            label="Image for prompt/settings recovery",
            type="filepath",
            file_types=[".png", ".jpg", ".jpeg", ".webp"],
            file_count="single",
            elem_id=f"{prefix}-metadata-file",
        )
        status = gr.Markdown("Drop one PNG, JPEG, or WebP image to inspect it locally.")
        recovered_prompt = gr.Textbox(
            label="Recovered prompt", lines=4, max_lines=12, interactive=False, buttons=["copy"]
        )
        notes = gr.Textbox(label="Recovery notes", lines=3, interactive=False)
        settings = gr.Textbox(label="Recovered generation settings", lines=3, max_lines=7, interactive=False)
        with gr.Accordion("Complete recovered metadata", open=False):
            details = gr.JSON(label="Recovered settings, source, and missing fields")
        with gr.Row():
            apply_all = gr.Button("Apply recovered prompt & settings", interactive=False)
            apply_prompt = gr.Button("Use recovered prompt only", interactive=False)

    def bind(form):
        def inspect_metadata(file):
            try:
                if not file:
                    raise ValueError("Drop one PNG, JPEG, or WebP image to inspect it locally.")
                report = recover_image_metadata(Path(file), workbench.repo, workbench.registry)
                fields = report["fields"]
                visible = {key: value for key, value in report.items() if key != "path"}
                description = (
                    f"Recovered **{len(fields)} field(s)**. Source: **{report['source']}**."
                    if fields
                    else "**No usable prompt or generation settings were found.**"
                )
                note_text = "\n".join([*report["invalid_fields"].values(), *report["notes"]])
                return (
                    report,
                    visible,
                    fields.get("prompt", ""),
                    description,
                    note_text,
                    gr.update(interactive=bool(fields) and not report["invalid_fields"]),
                    gr.update(interactive=bool(fields.get("prompt"))),
                    gr.update(open=True),
                    "\n".join(
                        f"{name.replace('_', ' ')}: {value}"
                        for name, value in fields.items()
                        if name != "prompt"
                    ),
                )
            except (ValueError, OSError) as exc:
                return (
                    None,
                    {},
                    "",
                    "**Image metadata could not be recovered.**",
                    str(exc),
                    gr.update(interactive=False),
                    gr.update(interactive=False),
                    gr.update(open=True),
                    "",
                )

        def clear_metadata():
            return (
                None,
                {},
                "",
                "Drop one PNG, JPEG, or WebP image to inspect it locally.",
                "",
                gr.update(interactive=False),
                gr.update(interactive=False),
                gr.update(open=True),
                "",
            )

        view = [recovered, details, recovered_prompt, status, notes, apply_all, apply_prompt, panel, settings]
        upload.upload(
            inspect_metadata,
            upload,
            view,
            api_visibility="private",
            concurrency_id=f"{prefix}-metadata-recovery",
            concurrency_limit=1,
        )
        upload.clear(
            clear_metadata,
            outputs=view,
            api_visibility="private",
            concurrency_id=f"{prefix}-metadata-recovery",
            concurrency_limit=1,
        )

        def current_report(report, file):
            if not report or not file or Path(file).resolve() != Path(report["path"]).resolve():
                raise gr.Error(
                    "Drop an image and wait for its metadata inspection before applying values.",
                    print_exception=False,
                )
            return report

        def apply_metadata(report, file, *current):
            report = current_report(report, file)
            if report["invalid_fields"]:
                raise gr.Error(
                    "Some recorded settings are unsupported or conflicting. Use the recovered prompt only and choose settings manually.",
                    print_exception=False,
                )
            fields = report["fields"]
            if not fields:
                raise gr.Error("No settings were recovered.", print_exception=False)
            updates, merged = [gr.skip() for _ in form.inputs], list(current)
            for field, index in FORM_FIELDS.items():
                if field in fields:
                    value = fields[field]
                    if field == "complexity" and value is None:
                        value = "default"
                    updates[index] = merged[index] = value
            if "output_compression" in fields:
                value = fields["output_compression"]
                updates[9] = value is not None
                if value is not None:
                    updates[10] = value
            preset = gr.skip()
            if "width" in fields or "height" in fields:
                try:
                    size = f"{int(merged[2])}x{int(merged[3])}"
                except (TypeError, ValueError, OverflowError):
                    size = "Custom"
                preset = size if size in PRESETS else "Custom"
            return [
                *updates,
                preset,
                "Recovered values applied. Missing fields retain their current values. Preview the request and cost before generating.",
            ]

        def apply_metadata_prompt(report, file):
            fields = current_report(report, file)["fields"]
            if not fields.get("prompt"):
                raise gr.Error("No prompt was recovered.", print_exception=False)
            return fields[
                "prompt"
            ], "Recovered prompt applied. Preview the request and cost before generating."

        apply_all.click(
            apply_metadata,
            [recovered, upload, *form.inputs],
            [*form.inputs, form.preset, status],
            api_visibility="private",
        )
        apply_prompt.click(
            apply_metadata_prompt,
            [recovered, upload],
            [form.prompt, status],
            api_visibility="private",
        )

    return bind
