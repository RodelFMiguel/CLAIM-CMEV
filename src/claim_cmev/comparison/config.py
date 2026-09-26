"""Versioned M8 rule configuration (configs/pipeline/m8_rules.yaml), validated strictly.

Unknown and missing keys are errors, so a threshold is never silently defaulted in code.
Values the specification fixes for safety (adequate coverage, resolved side, quantity 1,
ROUND_HALF_UP) accept only that value.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from claim_cmev.contracts.common import DamageCode

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "pipeline" / "m8_rules.yaml"
CONFIG_ENV = "CMEV_M8_CONFIG"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DamageRules(_Strict):
    min_observation_confidence: float = Field(ge=0, le=1)
    supported_types: tuple[DamageCode, ...] = Field(min_length=1)

    @field_validator("supported_types")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("supported_types is a set of CarDD codes")
        return value


class CoverageRules(_Strict):
    required_state: Literal["adequate"]
    require_human_confirmation_for_negative: bool


class IdentityRules(_Strict):
    require_resolved_side: Literal[True]


class CostRules(_Strict):
    quantity_must_equal: Literal[1]
    money_places: int = Field(ge=0, le=6)
    rounding: Literal["ROUND_HALF_UP"]
    normalised_score_places: int = Field(ge=0, le=6)


class AdditionRules(_Strict):
    min_observation_confidence: float = Field(ge=0, le=1)
    require_completeness: tuple[Literal["complete", "explicitly_empty"], ...] = Field(min_length=1)


class ExplainerRules(_Strict):
    enabled: Literal[False]


class RuleConfig(_Strict):
    rules_config_version: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+/\-]*$")
    damage: DamageRules
    coverage: CoverageRules
    identity: IdentityRules
    cost: CostRules
    additions: AdditionRules
    explainer: ExplainerRules

    @property
    def sha256(self) -> str:
        """Content hash, so two configs carrying one version label can still be told apart."""
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    def with_overrides(self, overrides: Mapping[str, Any]) -> RuleConfig:
        """A validated copy with nested overrides, for tests and experiments."""
        return RuleConfig.model_validate(_merge(self.model_dump(mode="python"), overrides))


def _merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_rule_config(path: str | os.PathLike[str] | None = None) -> RuleConfig:
    """Load from ``path``, ``CMEV_M8_CONFIG`` or the repository default."""
    source = Path(path or os.getenv(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    with source.open(encoding="utf-8") as handle:
        return RuleConfig.model_validate(yaml.safe_load(handle))


__all__ = ["CONFIG_ENV", "DEFAULT_CONFIG_PATH", "RuleConfig", "load_rule_config"]
