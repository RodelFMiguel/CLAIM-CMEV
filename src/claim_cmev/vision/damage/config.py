"""Versioned M2 assignment configuration (configs/pipeline/m2_assignment.yaml), validated strictly.

Unknown and missing keys are errors, and no threshold has a default in code
(integration contracts section 3.5). Every value is a proposed default until it is
selected on validation data and frozen at the day 6 checkpoint.
"""
from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "configs" / "pipeline" / "m2_assignment.yaml"
CONFIG_ENV = "CMEV_M2_CONFIG"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RegionConfig(_Strict):
    min_damage_confidence: float = Field(ge=0, le=1)
    min_damage_pixels: int = Field(ge=1)
    connectivity: Literal[4, 8]
    max_components_per_photo: int = Field(ge=1)


class AssignmentRule(_Strict):
    assign_min_containment: float = Field(gt=0, le=1)
    assign_ambiguity_margin: float = Field(ge=0, le=1)
    assign_background_max: float = Field(ge=0, le=1)
    split_components: Literal[False]

    def exact(self, name: str) -> Fraction:
        """A threshold as an exact fraction of its decimal text, so 0.7 - 0.5 >= 0.2 holds."""
        return Fraction(str(getattr(self, name)))


class AssignmentConfig(_Strict):
    config_version: str = Field(min_length=1)
    status: Literal["proposed", "frozen"]
    regions: RegionConfig
    assignment: AssignmentRule

    def with_overrides(self, overrides: Mapping[str, Any]) -> AssignmentConfig:
        """A validated copy with nested overrides, for tests and threshold sweeps."""
        return AssignmentConfig.model_validate(_merge(self.model_dump(mode="python"), overrides))


def _merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_assignment_config(path: str | os.PathLike[str] | None = None) -> AssignmentConfig:
    """Load from ``path``, ``$CMEV_M2_CONFIG`` or the repository default."""
    source = Path(path or os.getenv(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    with source.open(encoding="utf-8") as handle:
        return AssignmentConfig.model_validate(yaml.safe_load(handle))
