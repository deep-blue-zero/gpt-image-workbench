from __future__ import annotations

import html
import os
import subprocess
import sys
from pathlib import Path

import gradio as gr

from app.config import Settings
from app.experiments import Run, matrix
from app.models.spec import GenerationSpec
from app.service import Workbench
from app.ui.batch_jobs import build_batch_jobs
from app.ui.components import (
    COMPLEXITY_CHOICES,
    COMPLEXITY_HELP,
    MODERATION_HELP,
    bind_form_changes,
    form_values,
    generation_form,
    make_spec,
    whole,
)
from app.ui.connection import build_connection, connection_status
from app.ui.desktop import show_in_explorer
from app.ui.views import moderation_view, native_view, plan_rows, plan_summary, usage_dashboard

PLAN_HEADERS = [
    "Req.",
    "Config",
    "Sample",
    "Model",
    "Requested size",
    "Quality",
    "Complexity",
    "Moderation",
    "Images",
    "Est. USD",
    "Prompt preview",
]
PLAN_WIDTHS = [55, 65, 70, 275, 130, 90, 115, 110, 70, 110, 250]


def build_ui(workbench: Workbench) -> gr.Blocks:
    models = list(workbench.registry.models)

    def prompt_choices() -> list[tuple[str, str]]:
        return [
            (f"{p['kind']} · {p['name']} · {p['created_at'][:16]}", p["id"]) for p in workbench.repo.prompts()
        ]

    def image_choices() -> list[tuple[str, str]]:
        return [
            (f"{i['id'][:10]} · {i['width']}×{i['height']} · {i['kind']}", i["id"])
            for i in workbench.repo.images()
        ]

    def experiment_choices() -> list[tuple[str, str]]:
        return [
            (f"{e['name']} · {e['id'][:10]} · {e['status']}", e["id"]) for e in workbench.repo.experiments()
        ]

    def preview_result(plan):
        return (
            plan.id,
            plan_summary(plan, workbench),
            plan_rows(plan),
            {"processing_mode": plan.processing_mode, "runs": [r.to_dict() for r in plan.runs]},
            False,
        )

    def submit(plan_id, confirmed):
        try:
            identity = workbench.submit(plan_id, confirmed)
            destination = (
                "Open Batch jobs to check remote progress and import results."
                if workbench.repo.batch_jobs(identity)
                else "Status and elapsed time appear above."
            )
            return f"Started experiment `{identity}`. {destination}", None, False
        except ValueError as exc:
            # Expected submission guards belong in the UI, not a console traceback.
            raise gr.Error(str(exc), print_exception=False) from None
        except OSError as exc:
            raise gr.Error(str(exc)) from exc

    def preview_generate(mode, *values):
        try:
            return preview_result(workbench.prepare("Generation", [Run(make_spec(values), 1, 1)], mode))
        except (ValueError, OSError) as exc:
            raise gr.Error(str(exc)) from exc

    def preview_edit(mode, files, *values):
        try:
            references = workbench.import_references(files, values[0])
            spec = make_spec(values, operation="edit", references=references)
            return preview_result(workbench.prepare("Image edit", [Run(spec, 1, 1)], mode))
        except (ValueError, OSError) as exc:
            raise gr.Error(str(exc)) from exc

    with gr.Blocks(title="GPT Image Workbench", analytics_enabled=False) as demo:
        gr.HTML(
            '<div class="workbench-header"><span class="eyebrow">Local image studio · experiments & inspection</span>'
            "<h1>GPT Image Workbench</h1><p>Generate, edit, compare, and inspect original pixels.</p></div>"
        )
        connection = build_connection(workbench)
        demo.load(lambda: connection_status(workbench), outputs=connection, api_visibility="private")
        processing_mode = gr.Radio(
            [
                ("Standard — immediate requests", "standard"),
                ("Official Batch — 50% token rates, up to 24 hours", "batch"),
            ],
            value="standard",
            label="Processing mode",
            info="Applies to new previews in Generate, Edit, Compare and reruns. Review the mode and cost before submitting.",
        )
        with gr.Row():
            status = gr.Markdown("Ready. No request submitted.", elem_classes="status-panel")
            cancel_button = gr.Button("Cancel remaining requests", variant="stop", scale=0)
        cancel_notice = gr.Markdown()
        with gr.Accordion("Latest request: provider moderation details", open=False):
            latest_moderation = gr.HTML(moderation_view(None))
        cancel_button.click(workbench.cancel, outputs=cancel_notice, queue=False, api_visibility="private")
        poll_state = gr.State(((), None))

        with gr.Tabs() as tabs:
            with gr.Tab("Generate", id="generate"):
                with gr.Row():
                    with gr.Column(scale=5):
                        generate = generation_form(workbench)
                        with gr.Accordion("Prompt library & history", open=False):
                            prompt_name = gr.Textbox(label="Prompt name", value="Anime illustration study")
                            with gr.Row():
                                save_prompt = gr.Button("Save prompt")
                                duplicate_prompt = gr.Button("Duplicate prompt")
                                clear_prompt = gr.Button("Clear prompt")
                            prompt_list = gr.Dropdown(
                                prompt_choices(), label="Saved prompts and generation history"
                            )
                            with gr.Row():
                                refresh_prompts = gr.Button("Refresh prompts")
                                load_prompt = gr.Button("Load prompt")
                            prompt_status = gr.Markdown()
                        with gr.Accordion("Prompting notes", open=False):
                            gr.Markdown(
                                "Specify subject and composition, then linework, shading, palette, lighting, and background. "
                                "For references, identify each image's role and what must stay unchanged. Japanese and other Unicode text "
                                "is preserved. [Official prompting guide](https://developers.openai.com/api/docs/guides/image-prompting)."
                            )
                    with gr.Column(scale=7):
                        gen_plan = gr.State()
                        gen_preview = gr.Button("Preview request & estimated cost", variant="primary")
                        gen_summary = gr.Markdown("Prepare a request to see validation and cost estimates.")
                        gen_table = gr.Dataframe(
                            headers=PLAN_HEADERS, column_widths=PLAN_WIDTHS, interactive=False, type="array"
                        )
                        with gr.Accordion("Complete prepared settings", open=False):
                            gen_json = gr.JSON()
                        gen_confirm = gr.Checkbox(
                            False, label="I reviewed this request and authorize its estimated API cost."
                        )
                        gen_submit = gr.Button("Generate", variant="primary")
                        gen_notice = gr.Markdown()
                        gen_results = gr.Gallery(
                            label="Generated images — portable PNG copies; original bytes in Gallery",
                            columns=2,
                            height=520,
                            object_fit="contain",
                            interactive=False,
                            buttons=["download", "fullscreen"],
                        )
                        gen_output_selection = gr.State(None)
                        gen_reveal = gr.Button("Show in Explorer", interactive=False)
                        gen_reveal_notice = gr.Markdown("Select an output, or use the newest result.")
                        partial_preview = gr.Image(
                            label="Temporary streamed preview — not a saved final image",
                            interactive=False,
                            height=240,
                        )
                        local_demo = gr.Button("Create offline inspection sample (free, local)")
                        demo_note = gr.Markdown()

                def save(name, text, duplicate=False):
                    try:
                        identity = workbench.repo.save_prompt(name + (" (copy)" if duplicate else ""), text)
                        return gr.update(choices=prompt_choices(), value=identity), "Prompt saved locally."
                    except ValueError as exc:
                        raise gr.Error(str(exc)) from exc

                save_prompt.click(
                    save,
                    [prompt_name, generate.prompt],
                    [prompt_list, prompt_status],
                    api_visibility="private",
                )
                duplicate_prompt.click(
                    lambda n, t: save(n, t, True),
                    [prompt_name, generate.prompt],
                    [prompt_list, prompt_status],
                    api_visibility="private",
                )
                clear_prompt.click(lambda: "", outputs=generate.prompt, queue=False, api_visibility="private")
                refresh_prompts.click(
                    lambda: gr.update(choices=prompt_choices()), outputs=prompt_list, api_visibility="private"
                )

                def load_prompt_text(identity):
                    item = next((p for p in workbench.repo.prompts() if p["id"] == identity), None)
                    if not item:
                        raise gr.Error("Select a saved prompt or history entry.")
                    return item["text"], item["name"]

                load_prompt.click(
                    load_prompt_text, prompt_list, [generate.prompt, prompt_name], api_visibility="private"
                )
                local_demo.click(
                    lambda: (
                        f"Created local sample `{workbench.demo_image()}`. Open Gallery and refresh to inspect it."
                    ),
                    outputs=demo_note,
                    api_visibility="private",
                )
                gen_preview.click(
                    preview_generate,
                    [processing_mode, *generate.inputs],
                    [gen_plan, gen_summary, gen_table, gen_json, gen_confirm],
                    api_visibility="private",
                )
                gen_submit.click(
                    submit,
                    [gen_plan, gen_confirm],
                    [gen_notice, gen_plan, gen_confirm],
                    api_visibility="private",
                )

            with gr.Tab("Edit/Image to Image", id="edit"):
                gr.Markdown(
                    "Upload source/reference images in order. The first image is the saved parent; all references are recorded. "
                    "Original files are preserved. Edits are generative and are not deterministic upscaling."
                )
                with gr.Row():
                    with gr.Column(scale=5):
                        edit_files = gr.File(
                            label="Source and references (up to 16 PNG / JPEG / WebP files)",
                            file_count="multiple",
                            file_types=[".png", ".jpg", ".jpeg", ".webp"],
                            type="filepath",
                            allow_reordering=True,
                            elem_id="edit-references",
                            elem_classes=["reference-image-drop"],
                        )
                        gr.Markdown(
                            "Drop more images into this box to append them. Drag file rows to change their order."
                        )
                        edit = generation_form(workbench, editing=True)
                    with gr.Column(scale=7):
                        edit_original = gr.Gallery(
                            label="Original references — scaled overview",
                            height=300,
                            columns=3,
                            object_fit="contain",
                            interactive=False,
                        )
                        edit_plan = gr.State()
                        edit_preview = gr.Button("Preview edit & estimated cost", variant="primary")
                        edit_summary = gr.Markdown()
                        edit_table = gr.Dataframe(
                            headers=PLAN_HEADERS, column_widths=PLAN_WIDTHS, interactive=False, type="array"
                        )
                        with gr.Accordion("Complete prepared settings", open=False):
                            edit_json = gr.JSON()
                        edit_confirm = gr.Checkbox(
                            False,
                            label="I reviewed the references and authorize this edit's estimated API cost.",
                        )
                        edit_submit = gr.Button("Generate edited outputs", variant="primary")
                        edit_notice = gr.Markdown()
                        edit_results = gr.Gallery(
                            label="Edited images — portable PNG copies; original bytes in Gallery",
                            height=500,
                            columns=2,
                            object_fit="contain",
                            interactive=False,
                        )
                        edit_output_selection = gr.State(None)
                        edit_reveal = gr.Button("Show in Explorer", interactive=False)
                        edit_reveal_notice = gr.Markdown("Select an output, or use the newest result.")
                edit_files.change(
                    lambda paths: paths or [], edit_files, edit_original, api_visibility="private"
                )
                edit_preview.click(
                    preview_edit,
                    [processing_mode, edit_files, *edit.inputs],
                    [edit_plan, edit_summary, edit_table, edit_json, edit_confirm],
                    api_visibility="private",
                )
                edit_submit.click(
                    submit,
                    [edit_plan, edit_confirm],
                    [edit_notice, edit_plan, edit_confirm],
                    api_visibility="private",
                )

            with gr.Tab("Compare", id="compare"):
                gr.Markdown(
                    "Build a Cartesian matrix, or append selected configurations from Generate or Edit. "
                    "Standard runs sequentially and stops on failure. Official Batch submits the whole plan to OpenAI; "
                    "individual failures do not stop other remote requests. Repetitions are independent samples."
                )
                compare_plan = gr.State()
                selected_runs = gr.State([])
                with gr.Row():
                    with gr.Column(scale=4):
                        compare_name = gr.Textbox(
                            label="Experiment name", value="Anime quality & resolution study"
                        )
                        compare_models = gr.CheckboxGroup(
                            models, value=[models[0]], label="Models / snapshots"
                        )
                        compare_sizes = gr.Textbox(
                            value="864x1536\n1440x2560\n2160x3840",
                            lines=5,
                            label="Resolutions — one WIDTHxHEIGHT per line",
                        )
                        compare_qualities = gr.CheckboxGroup(
                            ["auto", "low", "medium", "high", "xhigh", "max"],
                            value=["high", "max"],
                            label="Quality levels",
                        )
                        compare_moderations = gr.CheckboxGroup(
                            ["auto", "low"],
                            value=[workbench.config.settings.default_moderation],
                            label="Moderation levels",
                            info=MODERATION_HELP,
                        )
                        compare_complexities = gr.CheckboxGroup(
                            COMPLEXITY_CHOICES,
                            value=["default"],
                            label="Complexity levels (2.6)",
                            info=COMPLEXITY_HELP,
                        )
                        compare_prompts = gr.Textbox(
                            lines=7, label="Prompts — separate prompts with a line containing ---"
                        )
                        with gr.Row():
                            compare_repetitions = gr.Number(
                                1, minimum=1, maximum=100, precision=0, label="Independent repetitions"
                            )
                            compare_n = gr.Number(
                                1, minimum=1, maximum=10, precision=0, label="Images per request"
                            )
                        gr.Markdown(
                            "Matrix output: PNG, automatic background, no streaming. Use the selected-configuration list below for other controls."
                        )
                        compare_build = gr.Button("Preview matrix & estimated cost", variant="primary")
                        with gr.Accordion("Selected configurations", open=False):
                            gr.Markdown(
                                "Set up Generate or Edit, then append it here. The repetition count applies to every selected configuration."
                            )
                            append_config = gr.Button("Append current Generate configuration")
                            append_edit_config = gr.Button("Append current Edit configuration")
                            clear_configs = gr.Button("Clear selected configurations")
                            selected_table = gr.Dataframe(
                                headers=[
                                    "Model",
                                    "Size",
                                    "Quality",
                                    "Complexity",
                                    "Moderation",
                                    "Images",
                                    "Prompt",
                                    "Operation",
                                    "References",
                                ],
                                interactive=False,
                                type="array",
                            )
                            compare_selected = gr.Button("Preview selected configurations")
                    with gr.Column(scale=8):
                        compare_summary = gr.Markdown("No experiment prepared.")
                        compare_table = gr.Dataframe(
                            headers=PLAN_HEADERS, column_widths=PLAN_WIDTHS, interactive=False, type="array"
                        )
                        with gr.Accordion("Complete prepared matrix", open=False):
                            compare_json = gr.JSON()
                        compare_confirm = gr.Checkbox(
                            False,
                            label="I reviewed the entire matrix and authorize its total estimated API cost.",
                        )
                        compare_submit = gr.Button("Run confirmed experiment sequentially", variant="primary")
                        compare_notice = gr.Markdown()
                        gr.Markdown(
                            "Use the cancellation control above to stop remaining local requests. Already submitted calls continue, may be billed, and are saved when they finish."
                        )

                def prepare_matrix(
                    name,
                    ms,
                    sizes,
                    qualities,
                    prompts,
                    moderations,
                    repeats,
                    n,
                    mode="standard",
                    complexities=None,
                ):
                    try:
                        runs = matrix(
                            GenerationSpec(n=whole(n, "Image count")),
                            ms,
                            [s.strip() for s in sizes.splitlines() if s.strip()],
                            qualities,
                            [p.strip() for p in prompts.replace("\r\n", "\n").split("\n---\n") if p.strip()],
                            moderations,
                            whole(repeats, "Repetitions"),
                            workbench.registry,
                            complexities=(
                                [None if value == "default" else value for value in complexities]
                                if complexities is not None
                                else None
                            ),
                        )
                        return preview_result(workbench.prepare(name, runs, mode))
                    except ValueError as exc:
                        raise gr.Error(str(exc)) from exc

                matrix_inputs = [
                    compare_name,
                    compare_models,
                    compare_sizes,
                    compare_qualities,
                    compare_prompts,
                    compare_moderations,
                    compare_repetitions,
                    compare_n,
                    processing_mode,
                    compare_complexities,
                ]
                compare_outputs = [
                    compare_plan,
                    compare_summary,
                    compare_table,
                    compare_json,
                    compare_confirm,
                ]
                compare_build.click(prepare_matrix, matrix_inputs, compare_outputs, api_visibility="private")

                def append_spec(values, spec):
                    try:
                        workbench.registry.validate(spec)
                        if len(values) >= 100:
                            raise ValueError("At most 100 selected configurations.")
                        updated = values + [spec.to_dict()]
                        rows = [
                            [
                                s["model"],
                                f"{s['width']}x{s['height']}",
                                s["quality"],
                                s.get("complexity") or "model default",
                                s["moderation"],
                                s["n"],
                                s["prompt"][:100],
                                s["operation"],
                                len(s["reference_ids"]),
                            ]
                            for s in updated
                        ]
                        return updated, rows, None, False
                    except ValueError as exc:
                        raise gr.Error(str(exc)) from exc

                def append(values, *controls):
                    return append_spec(values, make_spec(controls))

                def append_edit(values, files, *controls):
                    try:
                        references = workbench.import_references(files, controls[0])
                        return append_spec(
                            values, make_spec(controls, operation="edit", references=references)
                        )
                    except (ValueError, OSError) as exc:
                        raise gr.Error(str(exc)) from exc

                append_config.click(
                    append,
                    [selected_runs, *generate.inputs],
                    [selected_runs, selected_table, compare_plan, compare_confirm],
                    api_visibility="private",
                )
                append_edit_config.click(
                    append_edit,
                    [selected_runs, edit_files, *edit.inputs],
                    [selected_runs, selected_table, compare_plan, compare_confirm],
                    api_visibility="private",
                )
                clear_configs.click(
                    lambda: ([], [], None, False),
                    outputs=[selected_runs, selected_table, compare_plan, compare_confirm],
                    api_visibility="private",
                )

                def prepare_selected(name, values, repetitions, mode="standard"):
                    try:
                        repeat_count = whole(repetitions, "Repetitions")
                        if not 1 <= repeat_count <= 100:
                            raise ValueError("Repetitions must be 1–100.")
                        runs = [
                            Run(GenerationSpec.from_dict(s), i, r)
                            for i, s in enumerate(values, 1)
                            for r in range(1, repeat_count + 1)
                        ]
                        return preview_result(workbench.prepare(name, runs, mode))
                    except ValueError as exc:
                        raise gr.Error(str(exc)) from exc

                compare_selected.click(
                    prepare_selected,
                    [compare_name, selected_runs, compare_repetitions, processing_mode],
                    compare_outputs,
                    api_visibility="private",
                )
                compare_submit.click(
                    submit,
                    [compare_plan, compare_confirm],
                    [compare_notice, compare_plan, compare_confirm],
                    api_visibility="private",
                )

            with gr.Tab("Batch jobs", id="batch_jobs"):
                build_batch_jobs(workbench)

            with gr.Tab("Gallery", id="gallery"):
                with gr.Row():
                    filter_model = gr.Dropdown([("All models", ""), *models], value="", label="Model")
                    filter_quality = gr.Dropdown(
                        ["", "auto", "low", "medium", "high", "xhigh", "max"], value="", label="Quality"
                    )
                    filter_experiment = gr.Textbox(label="Experiment ID (optional)")
                    filter_day = gr.Textbox(label="Local date YYYY-MM-DD")
                    filter_dimensions = gr.Textbox(label="Actual dimensions WIDTHxHEIGHT")
                    sort = gr.Dropdown(["Newest", "Oldest", "Largest"], value="Newest", label="Sort")
                refresh_gallery = gr.Button("Refresh gallery", variant="primary")
                with gr.Row():
                    gallery_columns = gr.Slider(
                        2,
                        10,
                        value=6,
                        step=1,
                        label="Thumbnails per row",
                        info="Fewer columns make thumbnails larger.",
                    )
                    gallery_height = gr.Dropdown(
                        [
                            ("Compact", "compact"),
                            ("Large — 75% of window", "large"),
                            ("Tall — 90% of window", "tall"),
                        ],
                        value="large",
                        label="Browsing area",
                    )
                    gallery_fullscreen = gr.Button("Fullscreen gallery", scale=0, min_width=180)
                gallery_ids = gr.State([])
                gallery = gr.Gallery(
                    label="Thumbnail grid — select to load the original below",
                    elem_id="image-browser",
                    columns=6,
                    height="75vh",
                    object_fit="contain",
                    allow_preview=False,
                    interactive=False,
                    fit_columns=False,
                    buttons=[],
                )
                gallery_fullscreen.click(
                    fn=None,
                    js="() => document.getElementById('image-browser').requestFullscreen()",
                    queue=False,
                    api_visibility="private",
                )

                def resize_gallery(columns, area):
                    return gr.update(
                        columns=int(columns),
                        height={"compact": 360, "large": "75vh", "tall": "90vh"}[area],
                    )

                for control in (gallery_columns, gallery_height):
                    control.change(
                        resize_gallery,
                        [gallery_columns, gallery_height],
                        gallery,
                        queue=False,
                        api_visibility="private",
                    )
                with gr.Row():
                    selected_image = gr.Dropdown(image_choices(), value=None, label="Individual image")
                    zoom = gr.Dropdown(
                        ["Fit", "25%", "50%", "100%", "200%", "400%"],
                        value="100%",
                        label="Original pixel zoom",
                    )
                gr.Markdown(
                    "100% maps one source pixel to one CSS pixel. Set browser zoom to 100%; operating-system display scaling can still affect physical pixels. Scroll to inspect the whole image."
                )
                original_view = gr.HTML(native_view(workbench, None))
                gr.Markdown(
                    "**Portable PNG** includes the full prompt and generation settings, so you can reuse it without this database. "
                    "Keep **original bytes** for provider provenance verification."
                )
                with gr.Row():
                    download_portable = gr.File(
                        label="Download PNG with prompt & settings",
                        interactive=False,
                        elem_id="portable-image-download",
                    )
                    download_original = gr.File(label="Download original bytes", interactive=False)
                with gr.Row():
                    open_viewer = gr.Button("Open original in desktop viewer")
                    reuse_generate = gr.Button("Reuse settings in Generate")
                    reuse_edit = gr.Button("Use as source in Edit")
                gallery_notice = gr.Markdown()
                gallery_prompt = gr.Textbox(label="Copy prompt", lines=5, buttons=["copy"], interactive=False)
                with gr.Accordion("Image and request metadata", open=False):
                    gallery_metadata = gr.JSON()
                with gr.Accordion("Side-by-side comparison", open=False):
                    with gr.Row():
                        compare_left = gr.Dropdown(image_choices(), value=None, label="Left image")
                        compare_right = gr.Dropdown(image_choices(), value=None, label="Right image")
                        compare_zoom = gr.Dropdown(
                            ["Fit", "50%", "100%", "200%"], value="Fit", label="Comparison zoom"
                        )
                    compare_images = gr.Button("Compare original images")
                    with gr.Row():
                        left_view = gr.HTML()
                        right_view = gr.HTML()

                def refresh(m, q, exp, day, dims, order):
                    items = workbench.repo.images(
                        model=m or "",
                        quality=q or "",
                        experiment=(exp or "").strip(),
                        day=(day or "").strip(),
                        dimensions=(dims or "").strip(),
                        sort=order,
                    )
                    items = items[:500]
                    thumbs = [
                        (
                            str(workbench.repo.image_path(i["id"], thumb=True)),
                            f"{i['id'][:8]} · {i['width']}×{i['height']} · {(i.get('spec') or {}).get('quality', i['kind'])}",
                        )
                        for i in items
                    ]
                    choices = image_choices()
                    return (
                        thumbs,
                        [i["id"] for i in items],
                        gr.update(choices=choices, value=None),
                        gr.update(choices=choices, value=None),
                        gr.update(choices=choices, value=None),
                    )

                refresh_gallery.click(
                    refresh,
                    [filter_model, filter_quality, filter_experiment, filter_day, filter_dimensions, sort],
                    [gallery, gallery_ids, selected_image, compare_left, compare_right],
                    api_visibility="private",
                )

                def inspect(identity, z):
                    if not identity:
                        return native_view(workbench, None), None, None, "", "", {}
                    record = workbench.repo.image(identity)
                    portable, notice = None, ""
                    if record.get("request"):
                        try:
                            portable = str(workbench.repo.portable_image_path(identity))
                        except (ValueError, OSError) as exc:
                            notice = f"Portable PNG unavailable: {html.escape(str(exc))}. Original bytes remain available."
                    else:
                        notice = "This source/demo image has no saved generation request to embed."
                    return (
                        native_view(workbench, identity, z),
                        str(workbench.repo.image_path(identity)),
                        portable,
                        notice,
                        record.get("request", {}).get("spec", {}).get("prompt", ""),
                        record,
                    )

                def select_from_grid(identities, z, evt: gr.SelectData):
                    if not isinstance(evt.index, int) or not 0 <= evt.index < len(identities):
                        return (gr.skip(),) * 7
                    identity = identities[evt.index]
                    return identity, *inspect(identity, z)

                gallery.select(
                    select_from_grid,
                    [gallery_ids, zoom],
                    [
                        selected_image,
                        original_view,
                        download_original,
                        download_portable,
                        gallery_notice,
                        gallery_prompt,
                        gallery_metadata,
                    ],
                    api_visibility="private",
                )

                selected_image.change(
                    inspect,
                    [selected_image, zoom],
                    [
                        original_view,
                        download_original,
                        download_portable,
                        gallery_notice,
                        gallery_prompt,
                        gallery_metadata,
                    ],
                    api_visibility="private",
                )
                zoom.change(
                    lambda identity, z: native_view(workbench, identity, z),
                    [selected_image, zoom],
                    original_view,
                    api_visibility="private",
                )

                def desktop_view(identity):
                    if not identity:
                        raise gr.Error("Select an image first.")
                    path = workbench.repo.image_path(identity)
                    if sys.platform == "win32":
                        os.startfile(str(path))
                    else:
                        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
                    return "Opened the saved original in your default desktop viewer."

                open_viewer.click(desktop_view, selected_image, gallery_notice, api_visibility="private")

                def reuse(identity):
                    if not identity:
                        raise gr.Error("Select a generated image first.")
                    record = workbench.repo.image(identity)
                    if "request" not in record:
                        raise gr.Error(
                            "This source/demo image has no generation settings. Use it as an edit source instead."
                        )
                    spec = GenerationSpec.from_dict(record["request"]["spec"])
                    return [
                        *form_values(spec),
                        gr.update(selected="generate"),
                        "Settings loaded. Preview again before generating.",
                    ]

                reuse_generate.click(
                    reuse, selected_image, [*generate.inputs, tabs, gallery_notice], api_visibility="private"
                )

                def edit_source(identity):
                    if not identity:
                        raise gr.Error("Select an image first.")
                    return [str(workbench.repo.image_path(identity))], gr.update(selected="edit")

                reuse_edit.click(edit_source, selected_image, [edit_files, tabs], api_visibility="private")
                compare_images.click(
                    lambda left, right, z: (
                        native_view(workbench, left, z),
                        native_view(workbench, right, z),
                    ),
                    [compare_left, compare_right, compare_zoom],
                    [left_view, right_view],
                    api_visibility="private",
                )

            with gr.Tab("Experiments", id="experiments"):
                experiment_select = gr.Dropdown(experiment_choices(), value=None, label="Saved experiment")
                refresh_experiments = gr.Button("Refresh experiments")
                experiment_detail = gr.JSON(label="Persistent experiment record")
                experiment_requests = gr.Dataframe(
                    headers=[
                        "Request ID",
                        "Configuration",
                        "Sample",
                        "Status",
                        "Model",
                        "Size",
                        "Quality",
                        "Moderation",
                        "Processing mode",
                    ],
                    interactive=False,
                    type="array",
                )
                export_button = gr.Button("Export experiment JSON + CSV")
                export_files = gr.File(label="Exports", file_count="multiple", interactive=False)
                rerun_id = gr.Dropdown(label="Recorded request / configuration")
                request_moderation = gr.HTML(moderation_view(None), label="Provider moderation details")
                with gr.Accordion("Recorded request, usage and error details", open=False):
                    request_detail = gr.JSON()

                def inspect_request(identity):
                    record = workbench.repo.request(identity) if identity else {}
                    return record, moderation_view(record.get("error"))

                rerun_id.change(
                    inspect_request,
                    rerun_id,
                    [request_detail, request_moderation],
                    api_visibility="private",
                )
                rerun_preview = gr.Button("Preview one new independent sample")
                rerun_plan = gr.State()
                rerun_summary = gr.Markdown()
                rerun_table = gr.Dataframe(
                    headers=PLAN_HEADERS, column_widths=PLAN_WIDTHS, interactive=False, type="array"
                )
                rerun_json = gr.JSON(label="Prepared rerun")
                rerun_confirm = gr.Checkbox(
                    False, label="I authorize the estimated API cost of this new independent sample."
                )
                rerun_submit = gr.Button("Run confirmed sample")
                rerun_notice = gr.Markdown()
                refresh_experiments.click(
                    lambda: gr.update(choices=experiment_choices(), value=None),
                    outputs=experiment_select,
                    api_visibility="private",
                )

                def experiment_info(identity):
                    records = workbench.repo.requests(identity) if identity else []
                    experiment = next((e for e in workbench.repo.experiments() if e["id"] == identity), {})
                    rows = [
                        [
                            r["id"],
                            r["config_index"],
                            r["sample_index"],
                            r["status"],
                            r["spec"]["model"],
                            f"{r['spec']['width']}x{r['spec']['height']}",
                            r["spec"]["quality"],
                            r["spec"]["moderation"],
                            r["processing_mode"],
                        ]
                        for r in records
                    ]
                    choices = [
                        (
                            f"Config {r['config_index']} · sample {r['sample_index']} · {r['status']} · {r['id'][:8]}",
                            r["id"],
                        )
                        for r in records
                    ]
                    return experiment, rows, gr.update(choices=choices, value=None)

                experiment_select.change(
                    experiment_info,
                    experiment_select,
                    [experiment_detail, experiment_requests, rerun_id],
                    api_visibility="private",
                )
                export_button.click(
                    lambda identity: [str(p) for p in workbench.repo.export(identity)],
                    experiment_select,
                    export_files,
                    api_visibility="private",
                )

                def prepare_rerun(identity, mode="standard"):
                    if not identity:
                        raise gr.Error("Select a recorded request.")
                    record = workbench.repo.request(identity)
                    spec = GenerationSpec.from_dict(record["spec"])
                    return preview_result(
                        workbench.prepare(f"Rerun {identity[:10]} (independent)", [Run(spec, 1, 1)], mode)
                    )

                rerun_preview.click(
                    prepare_rerun,
                    [rerun_id, processing_mode],
                    [rerun_plan, rerun_summary, rerun_table, rerun_json, rerun_confirm],
                    api_visibility="private",
                )
                rerun_submit.click(
                    submit,
                    [rerun_plan, rerun_confirm],
                    [rerun_notice, rerun_plan, rerun_confirm],
                    api_visibility="private",
                )

            with gr.Tab("Usage & Costs", id="usage"):
                refresh_usage = gr.Button("Refresh usage & costs", variant="primary")
                usage_summary = gr.Markdown()
                headings = [
                    "Group",
                    "Value",
                    "Attempts",
                    "Images",
                    "Usage-derived USD",
                    "Estimated/reserved USD",
                ]
                time_costs = gr.Dataframe(
                    headers=headings, interactive=False, type="array", label="Day / month / experiment"
                )
                setting_costs = gr.Dataframe(
                    headers=headings,
                    interactive=False,
                    type="array",
                    label="Model / resolution / quality / moderation / processing mode",
                )
                usage_requests = gr.Dataframe(
                    headers=[
                        "Request ID",
                        "Status",
                        "Model",
                        "Attempts",
                        "Images",
                        "Estimated USD",
                        "Calculated USD",
                        "API request ID",
                    ],
                    interactive=False,
                    type="array",
                )
                gr.Markdown(
                    "A request's usage is counted once even when it returns multiple images. Unknown outcomes retain their estimated "
                    "reservation. The spending guard checks the current local month before each submission; it cannot limit OpenAI account charges. "
                    "[Official pricing](https://developers.openai.com/api/docs/pricing)."
                )
                refresh_usage.click(
                    lambda: usage_dashboard(workbench),
                    outputs=[usage_summary, time_costs, setting_costs, usage_requests],
                    api_visibility="private",
                )

            with gr.Tab("Settings", id="settings"):
                settings = workbench.config.settings
                default_moderation = gr.Dropdown(
                    ["auto", "low"],
                    value=settings.default_moderation,
                    label="Default moderation for new sessions",
                    info=MODERATION_HELP,
                )
                budget_warning = gr.Number(
                    settings.budget_warning_usd, label="Warning threshold per plan (USD)", minimum=0.01
                )
                budget_amount = gr.Number(
                    settings.monthly_budget_usd,
                    label="Monthly local spending guard threshold (USD)",
                    minimum=0.01,
                )
                budget_enabled = gr.Checkbox(
                    settings.spending_guard,
                    label="Prevent new submissions that exceed the estimated monthly local budget",
                )
                reference_allowance = gr.Number(
                    settings.reference_token_allowance,
                    label="Planning allowance: image input tokens per reference (assumption)",
                    precision=0,
                )
                timeout = gr.Number(settings.timeout_seconds, label="API read timeout in seconds", minimum=1)
                retries = gr.Number(
                    settings.max_retries,
                    label="Maximum retries for rate limits / pre-send connection failures",
                    minimum=0,
                    maximum=3,
                    precision=0,
                )
                save_settings = gr.Button("Save persistent settings", variant="primary")
                apply_default = gr.Button("Apply saved moderation default to both current forms")
                settings_notice = gr.Markdown()
                gr.Markdown(
                    f"Data folder: `{workbench.config.data_dir}`\n\nUse **Connect OpenAI / API-key settings** above "
                    "to save or change credentials without restarting. Keys are not saved in library records.\n\n"
                    "Models and prices can be updated independently in `config/capabilities.json` and `config/pricing.json`; restart after editing. "
                    "Registry verification date: **2026-10-30**. The server binds to **127.0.0.1** with sharing and telemetry disabled."
                )
                with gr.Accordion("Model capability registry", open=False):
                    gr.JSON(workbench.registry.data)
                with gr.Accordion("Pricing and estimate rules", open=False):
                    gr.JSON(workbench.costs.data)

                def persist_settings(moderation, warning, amount, guard, allowance, seconds, max_retries):
                    try:
                        workbench.config.save_settings(
                            Settings(
                                default_moderation=moderation,
                                budget_warning_usd=float(warning),
                                monthly_budget_usd=float(amount),
                                spending_guard=bool(guard),
                                reference_token_allowance=whole(allowance, "Token allowance"),
                                timeout_seconds=float(seconds),
                                max_retries=whole(max_retries, "Retries"),
                            )
                        )
                        return "Settings saved. Current moderation selections are preserved. The default applies on next startup or via the button above."
                    except (ValueError, OSError) as exc:
                        raise gr.Error(str(exc)) from exc

                save_settings.click(
                    persist_settings,
                    [
                        default_moderation,
                        budget_warning,
                        budget_amount,
                        budget_enabled,
                        reference_allowance,
                        timeout,
                        retries,
                    ],
                    settings_notice,
                    api_visibility="private",
                )
                apply_default.click(
                    lambda: (
                        workbench.config.settings.default_moderation,
                        workbench.config.settings.default_moderation,
                    ),
                    outputs=[generate.moderation, edit.moderation],
                    api_visibility="private",
                )

        # Any change invalidates its cost preview and confirmation. No stale settings can be charged.
        # Form feedback and invalidation share one dependency to avoid slider event replay loops.
        bind_form_changes(workbench, generate, gen_plan, gen_confirm)
        bind_form_changes(workbench, edit, edit_plan, edit_confirm)
        for controls, plan, confirmation in (
            ([processing_mode], gen_plan, gen_confirm),
            ([processing_mode, edit_files], edit_plan, edit_confirm),
            (matrix_inputs, compare_plan, compare_confirm),
            ([rerun_id, processing_mode], rerun_plan, rerun_confirm),
        ):
            for component in controls:
                component.change(
                    lambda: (None, False), outputs=[plan, confirmation], queue=False, api_visibility="private"
                )

        processing_mode.change(
            lambda mode: (
                [
                    gr.update(value=label)
                    for label in (
                        (
                            "Submit generation to Batch",
                            "Submit edit to Batch",
                            "Submit confirmed experiment to Batch",
                            "Submit confirmed sample to Batch",
                        )
                        if mode == "batch"
                        else (
                            "Generate",
                            "Generate edited outputs",
                            "Run confirmed experiment sequentially",
                            "Run confirmed sample",
                        )
                    )
                ]
                + ["Processing mode changed. Preview again to review the current cost."] * 4
                + [[] for _ in range(4)]
                + [{} for _ in range(4)]
            ),
            processing_mode,
            [
                gen_submit,
                edit_submit,
                compare_submit,
                rerun_submit,
                gen_summary,
                edit_summary,
                compare_summary,
                rerun_summary,
                gen_table,
                edit_table,
                compare_table,
                rerun_table,
                gen_json,
                edit_json,
                compare_json,
                rerun_json,
            ],
            queue=False,
            api_visibility="private",
        )

        def select_output(previous, evt: gr.SelectData):
            identities = previous[0] if previous else ()
            if not evt.selected or not isinstance(evt.index, int) or not 0 <= evt.index < len(identities):
                return gr.skip(), gr.skip()
            identity = identities[evt.index]
            return identity, f"Selected output `{identity[:10]}`."

        def reveal_output(previous, selected):
            identities = previous[0] if previous else ()
            if not identities:
                raise gr.Error("Generate an output first.", print_exception=False)
            identity = selected if selected in identities else identities[-1]
            try:
                try:
                    path = workbench.repo.portable_image_path(identity)
                except (ValueError, OSError):
                    path = workbench.repo.image_path(identity)
                show_in_explorer(path)
                return f"Selected **{html.escape(path.name)}** in Explorer."
            except (ValueError, OSError) as exc:
                raise gr.Error(str(exc), print_exception=False) from exc

        for output, selection, button, notice in (
            (gen_results, gen_output_selection, gen_reveal, gen_reveal_notice),
            (edit_results, edit_output_selection, edit_reveal, edit_reveal_notice),
        ):
            output.select(select_output, poll_state, [selection, notice], api_visibility="private")
            button.click(reveal_output, [poll_state, selection], notice, api_visibility="private")

        def poll(previous, gen_selection, edit_selection):
            snap = workbench.snapshot()
            elapsed = int(snap["elapsed"])
            text = f"**{snap['status'].upper()}** · {elapsed // 60:02d}:{elapsed % 60:02d} elapsed"
            if "total" in snap:
                text += f" · {snap['completed']}/{snap['total']} requests saved · {snap['failed']} failed"
            text += "\n\n" + html.escape(snap["message"])
            identities, preview = tuple(snap["images"]), snap.get("preview")
            old_ids, old_preview = previous or ((), None)
            outputs = gr.skip()
            reveal_updates = [gr.skip()] * 6
            if tuple(old_ids) != identities:
                outputs = []
                for identity in identities:
                    try:
                        path = workbench.repo.portable_image_path(identity)
                        caption = "PNG with prompt & settings"
                    except (ValueError, OSError):
                        path = workbench.repo.image_path(identity)
                        caption = "original; portable copy unavailable in this view"
                    outputs.append((str(path), f"{identity[:10]} · {caption}"))
                selections = (gen_selection, edit_selection)
                reveal_updates = [
                    gr.update(interactive=bool(identities) and sys.platform == "win32"),
                    gr.update(interactive=bool(identities) and sys.platform == "win32"),
                    *(gr.skip() if value in identities else None for value in selections),
                    *(
                        gr.skip() if value in identities else "Select an output, or use the newest result."
                        for value in selections
                    ),
                ]
            return (
                text,
                outputs,
                outputs,
                preview if preview != old_preview else gr.skip(),
                moderation_view(snap.get("error")),
                (identities, preview),
                *reveal_updates,
            )

        timer = gr.Timer(1)
        timer.tick(
            poll,
            [poll_state, gen_output_selection, edit_output_selection],
            [
                status,
                gen_results,
                edit_results,
                partial_preview,
                latest_moderation,
                poll_state,
                gen_reveal,
                edit_reveal,
                gen_output_selection,
                edit_output_selection,
                gen_reveal_notice,
                edit_reveal_notice,
            ],
            queue=False,
            api_visibility="private",
        )
        demo.load(
            lambda: usage_dashboard(workbench),
            outputs=[usage_summary, time_costs, setting_costs, usage_requests],
            api_visibility="private",
        )
        demo.load(
            refresh,
            [filter_model, filter_quality, filter_experiment, filter_day, filter_dimensions, sort],
            [gallery, gallery_ids, selected_image, compare_left, compare_right],
            api_visibility="private",
        )
    return demo


def launch_options(workbench: Workbench) -> dict:
    theme = gr.themes.Base(
        primary_hue="teal",
        secondary_hue="blue",
        neutral_hue="slate",
        font=["Segoe UI", "Arial", "sans-serif"],
        font_mono=["Consolas", "monospace"],
    )
    return {
        "server_name": "127.0.0.1",
        "share": False,
        "show_error": False,
        "strict_cors": True,
        "enable_monitoring": False,
        "mcp_server": False,
        "footer_links": [],
        "theme": theme,
        "css": Path(__file__).with_name("theme.css").read_text("utf-8"),
        "js": Path(__file__).with_name("workbench.js").read_text("utf-8"),
        "allowed_paths": [
            str(workbench.config.data_dir / name)
            for name in ("images", "portable", "thumbnails", "experiments", "previews")
        ],
        "blocked_paths": [
            str(workbench.config.root / ".env"),
            str(workbench.repo.db_path),
            str(workbench.config.data_dir / "settings.json"),
        ],
        "max_file_size": "50mb",
    }
