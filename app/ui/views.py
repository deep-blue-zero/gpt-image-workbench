from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.service import Plan, Workbench
from app.storage.security import sanitize


def moderation_view(error: dict[str, Any] | None) -> str:
    """Render provider data as escaped text, never as trusted HTML/Markdown."""
    if not error or error.get("category") != "moderation":
        return "<p>No moderation refusal recorded for this request.</p>"
    error = sanitize(error)
    details = error.get("moderation_details")
    if "moderation_details" not in error:
        note = "This older record did not retain provider moderation details."
    elif not isinstance(details, dict):
        note = "The provider did not return a moderation_details object."
    else:
        note = "Provider diagnostics are optional and do not expose internal classifier scores."
    details = details if isinstance(details, dict) else {}
    values = [
        ("Moderation stage", details.get("moderation_stage")),
        ("Categories", details.get("categories")),
        ("Provider error code", error.get("code")),
        ("Provider error type", error.get("provider_type")),
        ("API request ID", error.get("request_id")),
        ("Provider message", error.get("provider_message")),
    ]
    rows = []
    for label, value in values:
        if value is None:
            text = "Not returned / not retained"
        else:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        rows.append(
            f"<dt><strong>{label}</strong></dt>"
            f'<dd style="white-space:pre-wrap;overflow-wrap:anywhere">{html.escape(text)}</dd>'
        )
    return f"<p>{note}</p><dl>{''.join(rows)}</dl>"


def money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.5f}"


def plan_summary(plan: Plan, workbench: Workbench) -> str:
    outputs = sum(run.spec.n for run in plan.runs)
    output_cost = sum(e.output_usd or 0 for e in plan.estimates)
    mode = (
        "official Batch · asynchronous"
        if plan.processing_mode == "batch"
        else "Standard · sequential execution"
    )
    text = (
        f"**{len(plan.runs)} API request(s) · {outputs} expected image(s) · {mode}**\n\n"
        f"Estimated total: **{money(plan.total)} USD** · image output portion: **{money(output_cost)}**.\n\n"
        "Estimate only. Input allowances and actual model usage can differ. No account-level limit is set."
    )
    if plan.processing_mode == "batch":
        groups = {(r.spec.model, r.spec.operation) for r in plan.runs}
        standard = sum(
            workbench.costs.estimate(r.spec, workbench.config.settings).usd or 0 for r in plan.runs
        )
        references = {ref for r in plan.runs for ref in r.spec.reference_ids}
        text += (
            f"\n\n**{len(groups)} remote Batch job(s)** · Standard estimate for comparison: **{money(standard)}**. "
            "24-hour completion window; no streamed previews. Each remote job contains one model and endpoint. "
            "All requests are submitted together, so a refusal will not stop remaining requests. "
            f"{len(references)} distinct reference image(s) will be uploaded for editing. "
            "Check Batch jobs and import results, including after restarting this app."
        )
    if len(plan.runs) > 1 or (plan.total or 0) >= workbench.config.settings.budget_warning_usd:
        text += "\n\n⚠ **Paid batch:** review every configuration and the total before confirming."
    for basis in dict.fromkeys(e.basis for e in plan.estimates):
        text += "\n\n" + basis
    text += "\n\nRepeated runs are independent samples, not exact reproductions."
    return text


def plan_rows(plan: Plan) -> list[list[Any]]:
    return [
        [
            i,
            run.config_index,
            run.sample_index,
            run.spec.model,
            run.spec.size,
            run.spec.quality,
            run.spec.complexity or "model default",
            run.spec.moderation,
            run.spec.n,
            money(estimate.usd),
            run.spec.prompt[:100],
        ]
        for i, (run, estimate) in enumerate(zip(plan.runs, plan.estimates, strict=True), 1)
    ]


def file_url(path: Path) -> str:
    return "/gradio_api/file=" + quote(path.as_posix(), safe="/")


def native_view(workbench: Workbench, identity: str | None, zoom: str = "100%") -> str:
    if not identity:
        return '<div class="empty-view">Select an image to inspect its original pixels.</div>'
    record = workbench.repo.image(identity)
    path = workbench.repo.image_path(identity)
    if zoom == "Fit":
        style = "width:auto;max-width:100%;height:auto;max-height:650px;object-fit:contain"
    else:
        factor = float(zoom.rstrip("%")) / 100
        style = f"width:{record['width'] * factor}px;height:{record['height'] * factor}px;max-width:none;max-height:none"
    return (
        f'<div class="pixel-caption">Original {record["width"]} × {record["height"]} · {html.escape(zoom)} · '
        f"{html.escape(record['format'].upper())} · {record['byte_count']:,} bytes</div>"
        f'<div class="pixel-viewport"><img src="{file_url(path)}" style="{style}" '
        f'alt="Original image {html.escape(identity)}" loading="lazy"></div>'
    )


def usage_dashboard(workbench: Workbench) -> tuple[str, list[list[Any]], list[list[Any]], list[list[Any]]]:
    records = workbench.repo.requests()
    calculated = sum(r["calculated_cost"] or 0 for r in records)
    estimated = sum(r["estimated_cost"] or 0 for r in records if r["calculated_cost"] is None)
    unknown = sum(r["estimated_cost"] is None and r["calculated_cost"] is None for r in records)
    uncertain = sum(
        r["status"] in ("unknown", "interrupted", "submitted", "batch_unknown", "batch_unresolved")
        for r in records
    )
    tokens = defaultdict(int)
    for record in records:
        usage = (record.get("response") or {}).get("usage") or {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if isinstance(usage.get(key), int):
                tokens[key] += usage[key]
    text = (
        f"**{sum(r['attempts'] for r in records)} API submission attempts · {len(records)} local request records · "
        f"{sum(r['output_count'] for r in records)} saved outputs**\n\n"
        f"Usage-derived: **{money(calculated)}** · estimated/reserved remainder: **{money(estimated)}** · "
        f"combined planning total: **{money(calculated + estimated)} USD**\n\n"
        f"Unknown prices: {unknown} · uncertain/interrupted requests: {uncertain}. "
        "Totals cover only this local database. Usage-derived costs are not OpenAI invoices.\n\n"
        f"Reported tokens: input {tokens['input_tokens']:,} · output {tokens['output_tokens']:,} · total {tokens['total_tokens']:,}."
    )
    groups: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for r in records:
        spec = r["spec"]
        for axis, value in (
            ("Day", r["local_day"]),
            ("Month", r["local_month"]),
            ("Experiment", r["experiment_id"]),
            ("Model", spec["model"]),
            ("Resolution", f"{spec['width']}x{spec['height']}"),
            ("Quality", spec["quality"]),
            ("Moderation", spec["moderation"]),
            ("Processing mode", r.get("processing_mode", "standard")),
        ):
            row = groups[(axis, value)]
            row[0] += r["attempts"]
            row[1] += r["output_count"]
            row[2] += r["calculated_cost"] or 0
            row[3] += (r["estimated_cost"] or 0) if r["calculated_cost"] is None else 0
    by_time = [
        [axis, value, int(row[0]), int(row[1]), money(row[2]), money(row[3])]
        for (axis, value), row in sorted(groups.items(), reverse=True)
        if axis in ("Day", "Month", "Experiment")
    ]
    by_setting = [
        [axis, value, int(row[0]), int(row[1]), money(row[2]), money(row[3])]
        for (axis, value), row in sorted(groups.items())
        if axis not in ("Day", "Month", "Experiment")
    ]
    requests = [
        [
            r["id"],
            r["status"],
            r["spec"]["model"],
            r["attempts"],
            r["output_count"],
            money(r["estimated_cost"]),
            money(r["calculated_cost"]),
            r["api_request_id"],
        ]
        for r in records
    ]
    return text, by_time, by_setting, requests
