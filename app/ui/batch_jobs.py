from __future__ import annotations

import gradio as gr

from app.api.errors import WorkbenchError, classify_error
from app.storage.security import sanitize
from app.ui.views import money


def build_batch_jobs(workbench):
    service = workbench.batches

    def choices():
        names = {e["id"]: e["name"] for e in workbench.repo.experiments()}
        return [
            (f"{names[j['experiment_id']]} · {j['model']} · {j['status']} · {j['id'][:8]}", j["id"])
            for j in workbench.repo.batch_jobs()
        ]

    gr.Markdown(
        "Official Batch jobs run on OpenAI's servers within a **24-hour completion window**, including while this app is closed. "
        "Check status, then import results into Gallery, Experiments, and Usage & Costs. "
        "Each model and endpoint has its own job. A refusal does not stop other requests in a submitted Batch."
    )
    selected = gr.Dropdown(choices(), label="Saved Batch job", value=None)
    with gr.Row():
        refresh = gr.Button("Refresh saved jobs")
        check = gr.Button("Check remote status")
        collect = gr.Button("Import available results", variant="primary")
        cancel = gr.Button("Cancel selected Batch", variant="stop")
    summary = gr.Markdown("Select a saved Batch job. Refreshing this list does not contact OpenAI.")
    rows = gr.Dataframe(
        headers=[
            "Request",
            "Configuration",
            "Sample",
            "Status",
            "Images",
            "Batch estimate USD",
            "Usage-derived USD",
        ],
        interactive=False,
        type="array",
    )
    with gr.Accordion("Batch details and submission recovery", open=False):
        details = gr.JSON(label="Saved provider status, file IDs and diagnostics")
        remote_id = gr.Textbox(label="Remote Batch ID (optional recovery)", placeholder="batch_…")
        gr.Markdown(
            "If submission was interrupted, Check remote status searches recent jobs using the saved identity. "
            "You can also paste the Batch ID from your OpenAI dashboard. Unknown submissions are never resubmitted automatically. "
            "Resume is available only for jobs known not to have been created."
        )
        confirm = gr.Checkbox(
            False,
            label="I reviewed this saved job and authorize its displayed Batch estimate for submission.",
        )
        resume = gr.Button("Resume submission of unsubmitted job")
    gr.Markdown(
        "Remote cancellation may take up to 10 minutes. Completed requests can still be billed and imported. "
        "The top cancellation button only stops local work. Uploaded inputs and references expire after 30 days; "
        "download remote result files before their 30-day expiry. Importing again reuses saved results."
    )
    notice = gr.Markdown()

    def inspect_batch(identity):
        if not identity:
            return "Select a saved Batch job.", [], {}, False
        job = service.store.job(identity)
        records = service.store.requests(identity)
        provider = job.get("provider") or {}
        total = sum(r["estimated_cost"] or 0 for r in records)
        calculated = sum(r["calculated_cost"] or 0 for r in records)
        missing = sum(r["calculated_cost"] is None for r in records)
        text = (
            f"**{job['status']}** · import: **{job['import_state']}** · {len(records)} requests\n\n"
            f"Saved Batch estimate: **{money(total)}** · known usage-derived cost: **{money(calculated)}** · "
            f"requests without calculated cost: {missing}.\n\n"
            f"Remote counts: {provider.get('request_counts', 'not yet available')}."
        )
        values = [
            [
                r["id"],
                r["config_index"],
                r["sample_index"],
                r["status"],
                r["output_count"],
                money(r["estimated_cost"]),
                money(r["calculated_cost"]),
            ]
            for r in records
        ]
        return text, values, job, False

    def refresh_batches():
        return gr.update(choices=choices(), value=None), *inspect_batch(None)

    def action(identity, operation, recovery=None, confirmed=False):
        try:
            if operation == "check":
                service.check(identity, recovery)
            elif operation == "import":
                service.import_results(identity)
            elif operation == "cancel":
                service.cancel(identity)
            else:
                service.submit_one(identity, confirmed)
            return (
                *inspect_batch(identity),
                gr.update(choices=choices(), value=identity),
                "Saved Batch state updated. Refresh Gallery and Usage & Costs after importing.",
            )
        except Exception as exc:
            error = (
                WorkbenchError("validation", str(exc)) if isinstance(exc, ValueError) else classify_error(exc)
            )
            if identity:
                service.store.update(identity, last_error_json=error.to_dict())
            raise gr.Error(sanitize(error.message), print_exception=False) from None

    def check_batch(identity, recovery):
        return action(identity, "check", recovery)

    def import_batch(identity):
        return action(identity, "import")

    def cancel_batch(identity):
        return action(identity, "cancel")

    def resume_batch(identity, confirmed):
        return action(identity, "resume", confirmed=confirmed)

    view = [summary, rows, details, confirm]
    selected.change(inspect_batch, selected, view, api_visibility="private")
    refresh.click(refresh_batches, outputs=[selected, *view], api_visibility="private")
    outputs = [*view, selected, notice]
    check.click(check_batch, [selected, remote_id], outputs, api_visibility="private")
    collect.click(import_batch, selected, outputs, api_visibility="private")
    cancel.click(cancel_batch, selected, outputs, api_visibility="private")
    resume.click(resume_batch, [selected, confirm], outputs, api_visibility="private")
