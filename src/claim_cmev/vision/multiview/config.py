"""Versioned M3 configuration (configs/pipeline/m3_summary.yaml), validated strictly.

Unknown and missing keys are errors and no threshold has a default in code. Every
threshold is a proposed default; the sharpness scale in particular is uncalibrated on
real photographs.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...contracts.common import ContractError, PartCode
from ...taxonomy import Vocabulary, load_parts

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "configs" / "pipeline" / "m3_summary.yaml"
CONFIG_ENV = "CMEV_M3_CONFIG"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SummarySettings(_Strict):
    area_policy: Literal["max_member"]
    min_part_area_fraction: float = Field(gt=0, le=1)
    min_blur_score: float = Field(ge=0)
    min_mean_luma: float = Field(ge=0, le=255)
    max_mean_luma: float = Field(ge=0, le=255)
    max_clipped_fraction: float = Field(ge=0, le=1)
    max_border_touch_fraction: float = Field(ge=0, le=1)
    require_coverage_confirmation: Literal[True]
    supported_panel_list: Literal["taxonomy"] | tuple[PartCode, ...]
    max_attempts: int = Field(ge=1)
    skip_superseded_revisions: bool
    write_debug_crops: bool

    @model_validator(mode="after")
    def _luma(self) -> SummarySettings:
        if self.min_mean_luma >= self.max_mean_luma:
            raise ValueError("min_mean_luma must be below max_mean_luma")
        return self


class SummaryConfig(_Strict):
    config_version: str = Field(min_length=1)
    status: Literal["proposed", "frozen"]
    summary: SummarySettings

    def supported_parts(self, parts: Vocabulary | None = None) -> tuple[str, ...]:
        """The agreed panels that always get a coverage slot, in vocabulary order."""
        vocabulary = parts or load_parts()
        chosen: Any = self.summary.supported_panel_list
        if chosen == "taxonomy":
            chosen = vocabulary.meta.get("supported_panel_list")
            if chosen is None:
                raise ContractError("taxonomy_invalid", f"{vocabulary.version} records no supported_panel_list")
        if chosen == "all":
            return vocabulary.codes
        requested = {vocabulary.require(code) for code in chosen}
        return tuple(code for code in vocabulary.codes if code in requested)

    def with_overrides(self, overrides: Mapping[str, Any]) -> SummaryConfig:
        """A validated copy with nested overrides, for tests and threshold sweeps."""
        return SummaryConfig.model_validate(_merge(self.model_dump(mode="python"), overrides))


def _merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_summary_config(path: str | os.PathLike[str] | None = None) -> SummaryConfig:
    """Load from ``path``, ``$CMEV_M3_CONFIG`` or the repository default."""
    source = Path(path or os.getenv(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    with source.open(encoding="utf-8") as handle:
        return SummaryConfig.model_validate(yaml.safe_load(handle))
