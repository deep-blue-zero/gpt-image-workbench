import gradio as gr
import pytest

from app.models.spec import PRESETS
from app.service import Workbench
from app.ui.application import build_ui
from app.ui.components import matching_preset


@pytest.fixture
def form_ui(config):
    workbench = Workbench(config)
    try:
        yield workbench, build_ui(workbench)
    finally:
        workbench.close()


@pytest.mark.parametrize("size", PRESETS)
def test_matching_preset(size):
    width, height = map(int, size.split("x"))
    assert matching_preset(width, height) == size


@pytest.mark.parametrize("width,height", [(1824, 2576), (None, 1024), (float("inf"), 1024)])
def test_custom_and_incomplete_dimensions(width, height):
    assert matching_preset(width, height) == "Custom"


def test_each_form_change_has_one_backend_dependency(form_ui):
    _, ui = form_ui
    handlers = [fn for fn in ui.fns.values() if fn.fn and fn.fn.__name__ == "form_changed"]
    assert len(handlers) == 2
    for handler in handlers:
        assert len(handler.targets) == 14
        assert handler.trigger_mode == "always_last"
        assert handler.show_progress == "hidden"
        assert handler.queue is False
        assert isinstance(handler.outputs[0], gr.State)
        assert isinstance(handler.outputs[1], gr.Checkbox)
        for target in handler.targets:
            assert target[1] == "change"
            owners = [fn for fn in ui.fns.values() if fn.fn and target in fn.targets]
            assert owners == [handler], "Multiple callbacks can endlessly replay the same change event."
        # The preset is output-only for change; only a deliberate user input applies it.
        preset_id = handler.outputs[2]._id
        assert not any((preset_id, "change") in fn.targets for fn in ui.fns.values())
        preset_handler = next(fn for fn in ui.fns.values() if (preset_id, "input") in fn.targets)
        assert preset_handler.show_progress == "hidden"
        assert preset_handler.trigger_mode == "always_last"
        assert {block.elem_id for block in preset_handler.outputs} <= {
            "generate-width",
            "generate-height",
            "edit-width",
            "edit-height",
        }


@pytest.mark.parametrize("current_preset", ["1024x1024", "Custom"])
def test_slider_feedback_revokes_plan_and_approval(form_ui, current_preset):
    workbench, ui = form_ui
    model = next(iter(workbench.registry.models))
    for fn in ui.fns.values():
        if not fn.fn or fn.fn.__name__ != "form_changed":
            continue
        result = fn.fn(model, 1824, 2576, current_preset)
        assert result[:2] == (None, False)
        assert result[2] == (gr.skip() if current_preset == "Custom" else "Custom")
        assert result[3].startswith("4,698,624 requested pixels")
        assert "Supported quality:" in result[4]


@pytest.mark.parametrize("width", [None, float("inf"), 15])
def test_invalid_dimensions_still_revoke_approval(form_ui, width):
    workbench, ui = form_ui
    model = next(iter(workbench.registry.models))
    for fn in ui.fns.values():
        if fn.fn and fn.fn.__name__ == "form_changed":
            result = fn.fn(model, width, 1024, "1024x1024")
            assert result[:2] == (None, False)
            assert result[2] == "Custom"
            assert result[3].startswith("⚠ ")


def test_programmatic_preset_updates_have_invalidation_path(form_ui):
    workbench, ui = form_ui
    model = next(iter(workbench.registry.models))
    for handler in ui.fns.values():
        if not handler.fn or handler.fn.__name__ != "form_changed":
            continue
        preset_id = handler.outputs[2]._id
        apply_preset = next(fn for fn in ui.fns.values() if (preset_id, "input") in fn.targets)
        assert apply_preset.fn("Custom") == (gr.skip(), gr.skip())
        dimensions = apply_preset.fn("2160x3840")
        assert dimensions == (2160, 3840)
        assert all((block._id, "change") in handler.targets for block in apply_preset.outputs)
        result = handler.fn(model, *dimensions, "2160x3840")
        assert result[:3] == (None, False, gr.skip())
