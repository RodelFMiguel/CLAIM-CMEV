"""Configuration models for M1 Vehicle Part Segmentation.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
import yaml


class PartsConfig(BaseModel):
    """M1 serving and inference configuration."""

    model_id: str = "parts"
    model_version: str = "parts/0.1.0"
    taxonomy_version: str = "parts-1.0.0"
    config_version: str = "parts-cfg-0.1.0"

    device: Literal["cuda", "cpu", "auto"] = "auto"
    input_size: int = Field(default=512, gt=0)
    resize_policy: Literal["longest_edge_pad"] = "longest_edge_pad"
    batch_size: int = Field(default=4, gt=0)

    min_part_pixels: int = Field(default=512, ge=0)
    min_part_confidence: float = Field(default=0.50, ge=0.0, le=1.0)
    max_photos_per_job: int = Field(default=20, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    skip_superseded_revisions: bool = True
    write_overlay: bool = True

    model_config = {"extra": "ignore"}

    @classmethod
    def from_yaml(cls, path: str | Path) -> PartsConfig:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data or {})


def load_parts_config(path: str | Path | None = None) -> PartsConfig:
    default_path = Path("configs/models/parts.yaml")
    cfg_path = Path(path) if path else default_path
    if cfg_path.exists():
        return PartsConfig.from_yaml(cfg_path)
    return PartsConfig()
