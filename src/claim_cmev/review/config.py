"""Versioned M9 configuration (configs/pipeline/m9_review.yaml), validated strictly.

Unknown and missing keys are errors, so a rule value is never silently defaulted in
code. ``CMEV_M9_REVIEW_CONFIG`` overrides the file location.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

from ..contracts.review import DISMISSAL_REASONS

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "pipeline" / "m9_review.yaml"
RGB = tuple[int, int, int]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewRules(_Strict):
    dismissal_reasons: tuple[str, ...]
    note_required_for_reasons: tuple[str, ...]
    dismissal_note_max_chars: int = Field(ge=1)
    note_max_chars: int = Field(ge=1)
    line_item_correction_reasons: tuple[str, ...] = Field(min_length=1)
    idempotency_ttl_hours: int = Field(ge=1)
    usability_telemetry_enabled: bool
    explainer_enabled: bool

    @field_validator("dismissal_reasons")
    @classmethod
    def _matches_contract(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if set(value) != set(DISMISSAL_REASONS) or len(value) != len(set(value)):
            raise ValueError(f"dismissal_reasons must equal the contract list {list(DISMISSAL_REASONS)}")
        return value


class AmountRules(_Strict):
    max_decimal_places: int = Field(ge=0, le=6)
    max_amount: Decimal = Field(gt=0)
    allow_zero: bool


class FinalizeRules(_Strict):
    enforce_p1_assessment_not_superseded: bool
    enforce_p2_completeness_confirmed: bool
    required_stages: tuple[str, ...]
    passing_stage_states: tuple[str, ...] = Field(min_length=1)


class PrintText(_Strict):
    synthetic_cost_statement: str = Field(min_length=1)
    cost_basis_statement: str = Field(min_length=1)
    final_approval_not_recorded: str = Field(min_length=1)
    fixture_notice: str = Field(min_length=1)
    no_judgement_statement: str = Field(min_length=1)


class OverlayColors(_Strict):
    row: RGB
    row_selected: RGB
    mark_exclusion: RGB
    mark_price_change: RGB
    mark_selected: RGB
    damage: RGB
    damage_selected: RGB
    mask_outline: RGB


class OverlayStyle(_Strict):
    line_width_px: int = Field(ge=1)
    selected_line_width_px: int = Field(ge=1)
    selected_fill_alpha: int = Field(ge=0, le=255)
    colors: OverlayColors


class ReviewConfig(_Strict):
    config_version: str = Field(min_length=1)
    review: ReviewRules
    amounts: AmountRules
    finalize: FinalizeRules
    print: PrintText
    overlay: OverlayStyle


def load_review_config(path: str | os.PathLike[str] | None = None) -> ReviewConfig:
    """Load and validate the M9 configuration; the environment override applies when no path is given."""
    source = Path(path or os.environ.get("CMEV_M9_REVIEW_CONFIG") or DEFAULT_CONFIG_PATH)
    return ReviewConfig.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_review_config() -> ReviewConfig:
    """The repository configuration, loaded once per process."""
    return load_review_config()
