from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.credentials import Credentials
from app.paths import application_root, default_data_dir

PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    default_moderation: str = "auto"
    budget_warning_usd: float = 5.0
    monthly_budget_usd: float = 25.0
    spending_guard: bool = False
    reference_token_allowance: int = 4096
    timeout_seconds: float = 600.0
    max_retries: int = 2

    def validate(self) -> None:
        import math

        if self.default_moderation not in ("auto", "low"):
            raise ValueError("Default moderation must be auto or low.")
        for value in (self.budget_warning_usd, self.monthly_budget_usd, self.timeout_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Budget thresholds and timeout must be positive finite numbers.")
        if not 0 <= self.max_retries <= 3 or not 0 <= self.reference_token_allowance <= 1000000:
            raise ValueError("Retries must be 0–3; reference token allowance must be 0–1,000,000.")


class Config:
    def __init__(self, root: Path | None = None, data_dir: Path | None = None, *, demo: bool = False):
        self.root = (root or application_root()).resolve()
        load_dotenv(self.root / ".env", override=False)
        self.data_dir = (
            data_dir or Path(os.getenv("WORKBENCH_DATA_DIR", str(default_data_dir(self.root))))
        ).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.demo = demo
        self.credentials = Credentials(self.data_dir)
        self.timezone = ZoneInfo(os.getenv("WORKBENCH_TIMEZONE", "America/New_York"))
        self.settings_path = self.data_dir / "settings.json"
        self.settings = self.load_settings()

    @property
    def api_key(self) -> str | None:
        return None if self.demo else self.credentials.key

    def registry_path(self, name: str) -> Path:
        custom = self.root / "config" / f"{name}.json"
        return custom if custom.exists() else PACKAGE_DIR / "defaults" / f"{name}.json"

    def load_settings(self) -> Settings:
        data = json.loads(self.settings_path.read_text("utf-8")) if self.settings_path.exists() else {}
        settings = Settings(**data)
        settings.validate()
        return settings

    def save_settings(self, settings: Settings) -> None:
        settings.validate()
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        tmp.replace(self.settings_path)
        self.settings = settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Deliberately exclude exception text, prompts, payloads, headers and credentials.
        return json.dumps(
            {
                "time": self.formatTime(record),
                "level": record.levelname,
                "event": record.getMessage(),
                "request_id": getattr(record, "request_id", None),
                "error_type": getattr(record, "error_type", None),
                "status_code": getattr(record, "status_code", None),
            }
        )


def configure_logging(data_dir: Path) -> None:
    handler = logging.FileHandler(data_dir / "workbench.jsonl", encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("workbench")
    logger.setLevel(logging.INFO)
    logger.handlers[:] = [handler]
    logger.propagate = False
    for name in ("openai", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
