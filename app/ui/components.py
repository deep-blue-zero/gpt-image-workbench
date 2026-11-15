from __future__ import annotations

from dataclasses import dataclass

import gradio as gr

from app.models.spec import PRESETS, GenerationSpec
from app.service import Workbench
from app.ui.metadata import image_metadata_controls

MODERATION_HELP = "auto uses standard moderation. low reduces filtering strictness but does not remove content-policy restrictions."
COMPLEXITY_CHOICES = [("Model default (high on 2.6)", "default"), "low", "medium", "high"]
COMPLEXITY_HELP = (
    "GPT Image 2.6 planning effort, separate from rendering quality. "
    "Low suits focused edits; higher levels can help complex layouts. "
    "For older models, choose Model default; no complexity field is sent."
)


def whole(value: float | int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or value != int(value):
        raise ValueError(f"{label} must be a whole number.")
    return int(value)


@dataclass
class Form:
    model: gr.Dropdown
    prompt: gr.Textbox
    preset: gr.Dropdown
    width: gr.Slider
    height: gr.Slider
    quality: gr.Dropdown
    output_format: gr.Dropdown
    background: gr.Dropdown
    moderation: gr.Dropdown
    n: gr.Number
    compression_enabled: gr.Checkbox
    compression: gr.Slider
    stream: gr.Checkbox
    partial: gr.Slider
    description: gr.Markdown
    resolution_note: gr.Markdown
    complexity: gr.Dropdown

    @property
    def inputs(self) -> list:
        return [
            self.model,
            self.prompt,
            self.width,
            self.height,
            self.quality,
            self.output_format,
            self.background,
            self.moderation,
            self.n,
            self.compression_enabled,
            self.compression,
            self.stream,
            self.partial,
            self.complexity,
        ]


def make_spec(
    values: list | tuple, *, operation: str = "generate", references: tuple[str, ...] = ()
) -> GenerationSpec:
    (
        model,
        prompt,
        width,
        height,
        quality,
        fmt,
        background,
        moderation,
        n,
        use_compression,
        compression,
        stream,
        partial,
        complexity,
    ) = values
    return GenerationSpec(
        model=model,
        prompt=prompt,
        width=whole(width, "Width"),
        height=whole(height, "Height"),
        quality=quality,
        output_format=fmt,
        background=background,
        moderation=moderation,
        n=whole(n, "Image count"),
        output_compression=whole(compression, "Compression") if use_compression else None,
        stream=bool(stream),
        partial_images=whole(partial, "Partial images") if stream else 0,
        operation=operation,
        reference_ids=references,
        complexity=None if complexity == "default" else complexity,
    )


def form_values(spec: GenerationSpec) -> list:
    return [
        spec.model,
        spec.prompt,
        spec.width,
        spec.height,
        spec.quality,
        spec.output_format,
        spec.background,
        spec.moderation,
        spec.n,
        spec.output_compression is not None,
        spec.output_compression if spec.output_compression is not None else 100,
        spec.stream,
        spec.partial_images,
        spec.complexity or "default",
    ]


def generation_form(workbench: Workbench, *, editing: bool = False) -> Form:
    default_model = next(iter(workbench.registry.models))
    model = gr.Dropdown(list(workbench.registry.models), value=default_model, label="Model / pinned snapshot")
    description = gr.Markdown(workbench.registry.get(default_model)["description"])
    prompt = gr.Textbox(
        label="Edit instruction" if editing else "Prompt",
        lines=8,
        max_lines=24,
        placeholder="Describe the subject, composition, linework, palette, lighting, and details to preserve.",
        buttons=["copy"],
        elem_id="edit-prompt" if editing else "generate-prompt",
        elem_classes=["prompt-image-drop"],
        info="Drop an image here to recover its saved prompt and settings, or type normally.",
    )
    bind_metadata = image_metadata_controls(workbench, "edit" if editing else "generate")
    preset = gr.Dropdown(PRESETS + ["Custom"], value="1024x1024", label="Resolution preset")
    with gr.Row():
        width = gr.Slider(
            16,
            3840,
            value=1024,
            step=16,
            precision=0,
            label="Width",
            interactive=True,
            elem_id="edit-width" if editing else "generate-width",
        )
        height = gr.Slider(
            16,
            3840,
            value=1024,
            step=16,
            precision=0,
            label="Height",
            interactive=True,
            elem_id="edit-height" if editing else "generate-height",
        )
    gr.Markdown("Drag the sliders in 16-pixel steps, or type dimensions in the number fields.")
    resolution_note = gr.Markdown("1,048,576 requested pixels · standard range")
    with gr.Row():
        # All values remain visible; unsupported selections receive an actionable error, never substitution.
        quality = gr.Dropdown(
            ["auto", "low", "medium", "high", "xhigh", "max"], value="high", label="Quality"
        )
        n = gr.Number(value=1, minimum=1, maximum=10, precision=0, label="Images per request")
    complexity = gr.Dropdown(
        COMPLEXITY_CHOICES, value="default", label="Complexity / planning effort", info=COMPLEXITY_HELP
    )
    moderation = gr.Dropdown(
        ["auto", "low"],
        value=workbench.config.settings.default_moderation,
        label="Moderation",
        info=MODERATION_HELP,
    )
    with gr.Accordion("Output and streamed previews", open=False):
        with gr.Row():
            output_format = gr.Dropdown(["png", "jpeg", "webp"], value="png", label="Output format")
            background = gr.Dropdown(["auto", "opaque", "transparent"], value="auto", label="Background")
        compression_enabled = gr.Checkbox(False, label="Set JPEG/WebP compression (invalid for PNG)")
        compression = gr.Slider(0, 100, value=100, step=1, label="Compression / quality value sent to API")
        stream = gr.Checkbox(False, label="Stream partial previews (one output per request)")
        partial = gr.Slider(0, 3, value=2, step=1, label="Requested partial previews; adds output tokens")

    preset.input(
        lambda value: tuple(map(int, value.split("x"))) if value != "Custom" else (gr.skip(), gr.skip()),
        preset,
        [width, height],
        queue=False,
        trigger_mode="always_last",
        show_progress="hidden",
        api_visibility="private",
    )
    form = Form(
        model,
        prompt,
        preset,
        width,
        height,
        quality,
        output_format,
        background,
        moderation,
        n,
        compression_enabled,
        compression,
        stream,
        partial,
        description,
        resolution_note,
        complexity,
    )
    bind_metadata(form)
    return form


def matching_preset(w, h):
    try:
        size = f"{whole(w, 'Width')}x{whole(h, 'Height')}"
    except (ValueError, TypeError, OverflowError):
        return "Custom"
    return size if size in PRESETS else "Custom"


def bind_form_changes(workbench: Workbench, form: Form, plan: gr.State, confirmation: gr.Checkbox):
    """One backend dependency per form change, including cost-approval invalidation.

    Gradio 6.31 replays the entire original event when an always_last callback
    finishes with a deferred invocation. Separate callbacks on the same slider
    change can therefore keep requeuing each other after the user stops dragging.
    Keep these updates together, and retain change (not just input/release) so
    keyboard edits, resets, presets and recovered metadata also revoke approval.
    """

    def form_changed(model, width, height, current_preset):
        preset = matching_preset(width, height)
        try:
            experimental = workbench.registry.validate_resolution(
                model, whole(width, "Width"), whole(height, "Height")
            )
            note = f"{int(width * height):,} requested pixels · " + (
                "⚠ experimental resolution" if experimental else "standard range"
            )
        except (ValueError, TypeError, OverflowError) as exc:
            note = f"⚠ {exc}"
        cap = workbench.registry.get(model)
        description = cap["description"] + " Supported quality: " + ", ".join(cap["qualities"]) + "."
        return None, False, gr.skip() if preset == current_preset else preset, note, description

    gr.on(
        triggers=[component.change for component in form.inputs],
        fn=form_changed,
        inputs=[form.model, form.width, form.height, form.preset],
        outputs=[plan, confirmation, form.preset, form.resolution_note, form.description],
        queue=False,
        trigger_mode="always_last",
        show_progress="hidden",
        api_visibility="private",
    )
