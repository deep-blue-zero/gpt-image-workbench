from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PRESETS = [
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "864x1536",
    "1536x864",
    "2048x2048",
    "2048x1152",
    "1152x2048",
    "2560x1440",
    "1440x2560",
    "3840x2160",
    "2160x3840",
]


@dataclass(frozen=True)
class GenerationSpec:
    model: str = "gpt-image-2.5-sunburst"
    prompt: str = ""
    width: int = 1024
    height: int = 1024
    quality: str = "high"
    output_format: str = "png"
    background: str = "auto"
    moderation: str = "auto"
    n: int = 1
    output_compression: int | None = None
    stream: bool = False
    partial_images: int = 0
    operation: str = "generate"
    reference_ids: tuple[str, ...] = ()
    complexity: str | None = None

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GenerationSpec:
        data = dict(value)
        data["reference_ids"] = tuple(data.get("reference_ids", ()))
        return cls(**data)
