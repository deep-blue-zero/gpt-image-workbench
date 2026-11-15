from __future__ import annotations

import gradio as gr

from app.credentials import test_connection


def connection_status(workbench):
    if workbench.config.demo:
        return "**Demo launch — API access disabled.** Explore the workbench without sending requests."
    credentials = workbench.config.credentials
    if workbench.config.api_key:
        return f"**API key configured** ({credentials.source}). Paid requests require a cost preview and confirmation."
    return (
        "**Welcome.** Connect your OpenAI API key below, or explore the workbench without one. "
        + credentials.notice
    )


def build_connection(workbench):
    status = gr.Markdown(connection_status(workbench))
    with gr.Accordion("Connect OpenAI / API-key settings", open=not bool(workbench.config.api_key)):
        gr.Markdown(
            "Use your own [OpenAI API key](https://platform.openai.com/api-keys). "
            "OpenAI API usage is billed separately from a ChatGPT subscription. "
            "Saving a key does not generate an image or incur an image-generation charge."
        )
        key = gr.Textbox(label="OpenAI API key", type="password", placeholder="Paste your API key", value="")
        remember = gr.Checkbox(True, label="Remember securely in Windows Credential Manager for this library")
        with gr.Row():
            save = gr.Button("Save API key", variant="primary", interactive=not workbench.config.demo)
            test = gr.Button("Test connection", interactive=not workbench.config.demo)
            forget = gr.Button("Disconnect and forget saved key", interactive=not workbench.config.demo)
        notice = gr.Markdown()
        gr.Markdown(
            "Uncheck Remember for session-only use. Keys are never included in images or library exports. "
            "An environment or .env key remains available after restarting unless removed there."
        )

        def save_key(value, persist):
            try:
                workbench.change_credentials(value, persist)
                message = "Key saved. Preview again before generating. Use Test connection to check access."
            except ValueError as exc:
                message = str(exc)
            return "", connection_status(workbench), message

        def forget_key():
            try:
                workbench.change_credentials(None)
                message = "Disconnected for this session and removed the saved key."
            except ValueError as exc:
                message = str(exc)
            return "", connection_status(workbench), message

        def check_connection():
            try:
                return test_connection(workbench.config.api_key)
            except ValueError as exc:
                return str(exc)

        save.click(save_key, [key, remember], [key, status, notice], api_visibility="private")
        forget.click(forget_key, outputs=[key, status, notice], api_visibility="private")
        test.click(check_connection, outputs=notice, api_visibility="private")
    return status
