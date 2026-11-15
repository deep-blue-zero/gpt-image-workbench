from __future__ import annotations

import base64
import io
import os

import pytest
from PIL import Image

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from app.config import Config  # noqa: E402
from app.models.capabilities import Registry  # noqa: E402
from app.storage.repository import Repository  # noqa: E402


def pytest_addoption(parser):
    parser.addoption(
        "--run-live", action="store_true", default=False, help="Enable explicitly authorized paid smoke test"
    )


def pytest_collection_modifyitems(config, items):
    allowed = config.getoption("--run-live") and os.getenv("WORKBENCH_RUN_LIVE") == "I_ACCEPT_API_CHARGES"
    for item in items:
        if "live" in item.keywords and not allowed:
            item.add_marker(
                pytest.mark.skip(
                    reason="Paid smoke test requires --run-live and explicit charge acknowledgement."
                )
            )


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return Config(root=tmp_path, data_dir=tmp_path / "data")


@pytest.fixture
def registry(config):
    return Registry(config.registry_path("capabilities"))


@pytest.fixture
def repo(config):
    return Repository(config.data_dir, config.timezone)


@pytest.fixture
def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (48, 32), "#2a4666").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def api_body(png_bytes):
    return {
        "created": 1793400000,
        "data": [{"b64_json": base64.b64encode(png_bytes).decode()}],
        "quality": "max",
        "size": "48x32",
        "output_format": "png",
        "usage": {
            "input_tokens": 20,
            "input_tokens_details": {"text_tokens": 10, "image_tokens": 10},
            "output_tokens": 100,
            "total_tokens": 120,
        },
    }
