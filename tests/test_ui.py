import gradio as gr
import pytest

from app.service import Job, Workbench
from app.ui.application import build_ui, launch_options


def functions_named(ui, name):
    return [fn.fn for fn in ui.fns.values() if fn.fn and fn.fn.__name__ == name]


def test_ui_build_empty_gallery_and_offline_defaults(config):
    workbench = Workbench(config)
    try:
        ui = build_ui(workbench)
        refresh = functions_named(ui, "refresh")[0]
        outputs = refresh("", "", None, None, None, "Newest")
        assert outputs[0] == [] and outputs[1] == []
        options = launch_options(workbench)
        assert options["server_name"] == "127.0.0.1" and options["share"] is False
        moderation = [b for b in ui.blocks.values() if getattr(b, "label", None) == "Moderation"]
        assert len(moderation) == 2 and all(b.value == "auto" for b in moderation)
    finally:
        workbench.close()


def test_ui_loads_saved_moderation_default(config):
    from app.config import Settings

    config.save_settings(Settings(default_moderation="low"))
    workbench = Workbench(config)
    try:
        ui = build_ui(workbench)
        moderation = [b for b in ui.blocks.values() if getattr(b, "label", None) == "Moderation"]
        assert all(b.value == "low" for b in moderation)
        compare = next(b for b in ui.blocks.values() if getattr(b, "label", None) == "Moderation levels")
        assert compare.value == ["low"]
    finally:
        workbench.close()


def test_busy_submission_shows_message_without_traceback_or_new_request(config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    workbench = Workbench(config)
    try:
        ui = build_ui(workbench)
        workbench.job = Job("already-running", 1)
        submit = functions_named(ui, "submit")[0]
        with pytest.raises(gr.Error) as caught:
            submit("another-plan", True)
        assert "already running" in str(caught.value)
        assert caught.value.print_exception is False
        assert workbench.repo.requests() == []
        assert workbench.repo.experiments() == []
    finally:
        workbench.close()
