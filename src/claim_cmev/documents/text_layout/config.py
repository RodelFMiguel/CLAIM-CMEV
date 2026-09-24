"""Versioned M4 configuration (configs/pipeline/m4_page_reading.yaml), validated strictly.

Unknown and missing keys are errors: a threshold is never silently defaulted in code.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "configs" / "pipeline" / "m4_page_reading.yaml"
Granularity = Literal["word", "line", "block"]
QualityFlag = Literal["blur", "glare", "uneven_illumination", "low_contrast", "low_resolution"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FallbackEngine(_Strict):
    name: str
    enabled: bool


class OcrConfig(_Strict):
    engine: str
    engine_version: str
    lang: str
    use_angle_cls: bool
    min_box_confidence: float = Field(ge=0, le=1)
    max_low_confidence_fraction: float = Field(ge=0, le=1)
    max_attempts: int = Field(ge=1)
    skip_superseded_revisions: bool
    nested_box_containment: float = Field(gt=0, le=1)
    fallback_engine: FallbackEngine


class PageConfig(_Strict):
    render_dpi: int = Field(ge=36, le=1200)
    rectified_width_px: int = Field(ge=64)
    min_page_area_fraction: float = Field(gt=0, le=1)
    max_corner_angle_deviation_deg: float = Field(gt=0, lt=90)
    min_boundary_contrast: float = Field(ge=0)
    boundary_strip_px: int = Field(ge=1)
    boundary_detect_max_side_px: int = Field(ge=100)
    canny_low: int = Field(ge=0)
    canny_high: int = Field(ge=1)
    min_deskew_angle_deg: float = Field(ge=0)
    max_deskew_angle_deg: float = Field(gt=0)
    skew_search_range_deg: float = Field(gt=0, le=89)
    skew_min_foreground_px: int = Field(ge=1)
    skew_min_peak_ratio: float = Field(ge=1)
    expected_text_box_granularity: Granularity
    row_band_tolerance_px: float = Field(gt=0)
    max_pages_per_job: int = Field(ge=1)
    write_debug: bool


class QualityConfig(_Strict):
    eval_width_px: int = Field(ge=64)
    min_sharpness: float = Field(ge=0, le=1)
    clip_level: int = Field(ge=1, le=255)
    glare_margin: int = Field(ge=0, le=255)
    max_glare_fraction: float = Field(ge=0, le=1)
    min_illumination_ratio: float = Field(ge=0, le=1)
    min_contrast_range: float = Field(ge=0, le=255)
    min_source_page_width_px: int = Field(ge=1)
    partial_on_flags: tuple[QualityFlag, ...]


class TransformConfig(_Strict):
    round_trip_tolerance_px: float = Field(gt=0)


class PageReadingConfig(_Strict):
    config_version: str = Field(min_length=1)
    ocr: OcrConfig
    page: PageConfig
    quality: QualityConfig
    transforms: TransformConfig

    @property
    def sha256(self) -> str:
        """Content hash so two configs with one version label can still be told apart."""
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    def with_overrides(self, overrides: Mapping[str, Any]) -> "PageReadingConfig":
        """A validated copy with nested overrides, for tests and experiments."""
        return PageReadingConfig.model_validate(_merge(self.model_dump(mode="python"), overrides))


def _merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_page_reading_config(path: str | os.PathLike[str] | None = None) -> PageReadingConfig:
    """Load from ``path``, ``CMEV_M4_CONFIG`` or the repository default."""
    source = Path(path or os.getenv("CMEV_M4_CONFIG") or DEFAULT_CONFIG_PATH)
    with source.open(encoding="utf-8") as handle:
        return PageReadingConfig.model_validate(yaml.safe_load(handle))
