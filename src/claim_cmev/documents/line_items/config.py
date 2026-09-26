"""Versioned M5 layout families plus parser keys (``configs/pipeline/m5_layout_families.yaml``).

Unknown and missing keys are errors, money settings are quoted decimal strings, and
aliases must stay unique after normalisation. Every value is PROPOSED until the team's
day-1 family freeze and day-6 threshold freeze.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from claim_cmev.contracts.common import Identifier

from .text import normalise_text

DEFAULT_LAYOUT_FAMILIES_PATH = Path(__file__).resolve().parents[4] / "configs" / "pipeline" / "m5_layout_families.yaml"
CONFIG_ENV = "CMEV_M5_LAYOUT_FAMILIES"
ColumnField = Literal["line_no", "description", "operation", "qty", "unit_price", "amount"]
VALUE_FIELDS: tuple[str, ...] = ("description", "operation", "qty", "unit_price", "amount")
RowPatternKind = Literal["total", "tax", "heading"]
UNSUPPORTED = "unsupported"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _decimal_setting(value: Any) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"money settings are quoted decimal strings, got {value!r}")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("a money setting must be a finite nonnegative decimal")
    return result


class ParserSettings(_Strict):
    method: Literal["parser"]  # stretch S1 (layoutlmv3) is not implemented and never a fallback
    min_matched_columns: int = Field(ge=1)
    band_tolerance_frac: float = Field(gt=0, lt=1)
    max_wrap_gap_frac: float = Field(ge=0, lt=1)
    x_tolerance_frac: float = Field(ge=0, lt=1)
    max_decimal_places: int = Field(ge=0, le=6)
    amount_tolerance: Decimal
    min_box_confidence: float = Field(ge=0, le=1)
    header_word_gap_frac: float = Field(ge=0, lt=1)
    max_attempts: int = Field(ge=1)
    skip_superseded_revisions: bool

    @field_validator("amount_tolerance", mode="before")
    @classmethod
    def _amount(cls, value: Any) -> Decimal:
        return _decimal_setting(value)


class HeaderSpec(_Strict):
    min_matched_columns: int | None = Field(default=None, ge=1)
    required_columns: tuple[ColumnField, ...]
    aliases: dict[ColumnField, tuple[StrictStr, ...]]


class ColumnBindingSpec(_Strict):
    mode: Literal["header_anchored", "gutter"]
    x_tolerance_frac: float | None = Field(default=None, ge=0, lt=1)


class RowGroupingSpec(_Strict):
    band_tolerance_frac: float | None = Field(default=None, gt=0, lt=1)
    max_wrap_gap_frac: float | None = Field(default=None, ge=0, lt=1)


class NumberSpec(_Strict):
    decimal_separator: Literal[".", ","]
    thousands_separator: Literal[",", ".", ""]
    max_decimal_places: int | None = Field(default=None, ge=0, le=6)
    quantity_units: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def _distinct(self) -> NumberSpec:
        if self.decimal_separator == self.thousands_separator:
            raise ValueError("decimal and thousands separators must differ")
        return self


class FamilySpec(_Strict):
    family_id: Identifier
    status: Literal["proposed", "frozen"]
    description: str = Field(min_length=1)
    header: HeaderSpec
    column_binding: ColumnBindingSpec
    row_grouping: RowGroupingSpec = RowGroupingSpec()
    numbers: NumberSpec
    currency_markers: dict[StrictStr, str | None]
    row_patterns: dict[RowPatternKind, tuple[StrictStr, ...]]
    terminator_kinds: tuple[Literal["total", "tax"], ...]
    subtotal_labels: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> FamilySpec:
        if self.family_id == UNSUPPORTED:
            raise ValueError(f"{UNSUPPORTED!r} is reserved for 'no family matched'")
        fields = set(self.header.aliases)
        if not {"description", "amount"} <= fields:
            raise ValueError(f"{self.family_id}: a family must locate description and amount columns")
        missing = set(self.header.required_columns) - fields
        if missing:
            raise ValueError(f"{self.family_id}: required columns {sorted(missing)} have no aliases")
        if "line_no" in self.header.required_columns:
            raise ValueError(f"{self.family_id}: line_no is never a required column")
        if self.header.min_matched_columns is not None and self.header.min_matched_columns > len(fields):
            raise ValueError(f"{self.family_id}: min_matched_columns exceeds the number of columns")
        seen: dict[str, str] = {}
        for field, aliases in self.header.aliases.items():
            if not aliases:
                raise ValueError(f"{self.family_id}: column {field} has no alias")
            for alias in aliases:
                key = normalise_text(alias)
                if not key:
                    raise ValueError(f"{self.family_id}: alias {alias!r} is empty after normalisation")
                if key in seen:
                    raise ValueError(f"{self.family_id}: header alias {alias!r} names both {seen[key]} and {field}")
                seen[key] = field
        for marker, currency in self.currency_markers.items():
            if not marker.strip():
                raise ValueError(f"{self.family_id}: empty currency marker")
            if currency is not None and not (len(currency) == 3 and currency.isalpha() and currency.isupper()):
                raise ValueError(f"{self.family_id}: currency marker {marker!r} names {currency!r}, not an ISO code")
        for kind, patterns in self.row_patterns.items():
            if any(not normalise_text(p) for p in patterns):
                raise ValueError(f"{self.family_id}: an empty {kind} pattern")
        missing_kinds = set(self.terminator_kinds) - set(self.row_patterns)
        if missing_kinds:
            raise ValueError(f"{self.family_id}: terminator kinds {sorted(missing_kinds)} have no patterns")
        return self

    # effective thresholds: family override, else the parser default
    def min_matched(self, parser: ParserSettings) -> int:
        return self.header.min_matched_columns or parser.min_matched_columns

    def x_tolerance(self, parser: ParserSettings) -> float:
        value = self.column_binding.x_tolerance_frac
        return parser.x_tolerance_frac if value is None else value

    def band_tolerance(self, parser: ParserSettings) -> float:
        return self.row_grouping.band_tolerance_frac or parser.band_tolerance_frac

    def wrap_gap(self, parser: ParserSettings) -> float:
        value = self.row_grouping.max_wrap_gap_frac
        return parser.max_wrap_gap_frac if value is None else value

    def decimal_places(self, parser: ParserSettings) -> int:
        value = self.numbers.max_decimal_places
        return parser.max_decimal_places if value is None else value


class LayoutFamilyConfig(_Strict):
    """The whole M5 parser configuration: parser keys plus the supported families."""

    schema_version: Literal["0.1.0"]
    config_version: str = Field(min_length=1)
    status: Literal["proposed", "frozen"]
    parser: ParserSettings
    families: tuple[FamilySpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> LayoutFamilyConfig:
        ids = [f.family_id for f in self.families]
        if len(ids) != len(set(ids)):
            raise ValueError("family ids must be unique")
        return self

    def family(self, family_id: str) -> FamilySpec:
        for family in self.families:
            if family.family_id == family_id:
                return family
        raise KeyError(family_id)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    def with_overrides(self, overrides: Mapping[str, Any]) -> LayoutFamilyConfig:
        """A validated copy with nested overrides (``families`` replaces the whole list)."""
        return LayoutFamilyConfig.model_validate(_merge(self.model_dump(mode="python"), overrides))


def _merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_layout_families(path: str | os.PathLike[str] | None = None) -> LayoutFamilyConfig:
    """Load from ``path``, ``$CMEV_M5_LAYOUT_FAMILIES`` or the repository default."""
    source = Path(path or os.getenv(CONFIG_ENV) or DEFAULT_LAYOUT_FAMILIES_PATH)
    with source.open(encoding="utf-8") as handle:
        return LayoutFamilyConfig.model_validate(yaml.safe_load(handle))
