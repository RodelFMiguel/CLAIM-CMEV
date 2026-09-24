"""Versioned M6 linking configuration (configs/pipeline/m6_linking.yaml), validated strictly.

Unknown and missing keys are errors: a threshold is never silently defaulted in code.
Every value is proposed until it is selected on validation pages and frozen.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "configs" / "pipeline" / "m6_linking.yaml"
CONFIG_ENV = "CMEV_M6_LINKING_CONFIG"

ColumnSet = Literal["all"] | tuple[Literal["amount"], ...]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScoreThresholds(_Strict):
    exclusion: float = Field(ge=0, le=1)
    price_change: float = Field(ge=0, le=1)


class DetectionConfig(_Strict):
    score_threshold: ScoreThresholds
    dedupe_iou: float = Field(gt=0, le=1)


class BandConfig(_Strict):
    extend_above_row_heights: float = Field(ge=0)
    extend_below_row_heights: float = Field(ge=0)


class LinkWeights(_Strict):
    v: float = Field(ge=0, le=1)
    c: float = Field(ge=0, le=1)
    rc: float = Field(ge=0, le=1)
    col: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> LinkWeights:
        total = self.v + self.c + self.rc + self.col
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"link.weights must sum to 1.0, got {total}")
        return self


class LinkConfig(_Strict):
    weights: LinkWeights
    min_score: float = Field(gt=0, le=1)
    margin_to_second: float = Field(ge=0, le=1)
    candidate_min_score: float = Field(gt=0, le=1)


class PriceChangeConfig(_Strict):
    margin_to_second: float = Field(ge=0, le=1)
    amount_overlap_min: float = Field(gt=0, le=1)
    amount_overlap_second_max: float = Field(ge=0, le=1)


class ColumnConfig(_Strict):
    exclusion_columns: ColumnSet
    price_change_columns: ColumnSet
    right_margin_tolerance_px: float = Field(ge=0)
    default_page_width_px: int = Field(ge=1)


class RowConfig(_Strict):
    max_marks_per_row: int = Field(ge=1)


class LinkingConfig(_Strict):
    link_config_version: str = Field(min_length=1)
    detection: DetectionConfig
    band: BandConfig
    link: LinkConfig
    price_change: PriceChangeConfig
    column: ColumnConfig
    row: RowConfig

    @model_validator(mode="after")
    def _coherent(self) -> LinkingConfig:
        if self.link.candidate_min_score > self.link.min_score:
            raise ValueError("link.candidate_min_score must not exceed link.min_score")
        if self.price_change.amount_overlap_second_max >= self.price_change.amount_overlap_min:
            raise ValueError("price_change.amount_overlap_second_max must be below amount_overlap_min")
        if self.price_change.margin_to_second < self.link.margin_to_second:
            raise ValueError("price_change.margin_to_second is deliberately no looser than link.margin_to_second")
        return self

    @property
    def sha256(self) -> str:
        """Content hash so two configs with one version label can still be told apart."""
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    def score_threshold(self, mark_type: str) -> float:
        return getattr(self.detection.score_threshold, mark_type)

    def with_overrides(self, overrides: Mapping[str, Any]) -> LinkingConfig:
        """A validated copy with nested overrides, for tests and experiments."""
        return LinkingConfig.model_validate(_merge(self.model_dump(mode="python"), overrides))


def _merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_linking_config(path: str | os.PathLike[str] | None = None) -> LinkingConfig:
    """Load from ``path``, ``CMEV_M6_LINKING_CONFIG`` or the repository default."""
    source = Path(path or os.getenv(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    with source.open(encoding="utf-8") as handle:
        return LinkingConfig.model_validate(yaml.safe_load(handle))
