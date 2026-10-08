"""Serving configuration of the M2 damage model (configs/models/damage.yaml).

Free of the model stack: the orchestrator and the API read it to pin the versions of a
damage command. The region and assignment thresholds are not here; they are the versioned
``configs/pipeline/m2_assignment.yaml``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
import yaml

from ...contracts.common import ContractError, damage_codes_for
from ..parts.config import DEFAULT_REGISTRY_PATH, REGISTRY_ENV

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "configs" / "models" / "damage.yaml"
CONFIG_ENV = "CMEV_DAMAGE_CONFIG"


class DamageModelConfig(BaseModel):
    """Which damage checkpoint is served, under which vocabulary, in which model frame."""

    model_id: str = "damage"
    model_version: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)

    device: Literal["cuda", "cpu", "auto"] = "auto"
    input_size: int = Field(default=512, gt=0)
    resize_policy: Literal["longest_edge_pad"] = "longest_edge_pad"

    model_config = {"extra": "ignore"}

    @field_validator("taxonomy_version")
    @classmethod
    def _damage_taxonomy(cls, value: str) -> str:
        try:
            damage_codes_for(value)
        except ContractError as exc:
            raise ValueError(f"taxonomy_version must name a damage taxonomy: {exc.message}") from exc
        return value

    @property
    def damage_codes(self) -> tuple[str, ...]:
        return damage_codes_for(self.taxonomy_version)

    def stage_versions(self, code_version: str, *, parts_model: str, assignment_config: str) -> dict[str, str]:
        """The versions pinned on a damage command and recorded on every observation.

        The parts model is among them because the assignment depends on its masks: another
        parts checkpoint is another damage job.
        """
        return {"damage_model": self.model_version, "damage_config": self.config_version, "parts_model": parts_model,
                "assignment_config": assignment_config, "taxonomy": self.taxonomy_version, "code": code_version}

    def model_dir(self, registry_root: str | Path | None = None) -> Path:
        """The registry entry of ``model_version`` under the registry (technical specification 9.3)."""
        return Path(registry_root or os.getenv(REGISTRY_ENV) or DEFAULT_REGISTRY_PATH) / self.model_version


def load_damage_model_config(path: str | Path | None = None) -> DamageModelConfig:
    """Load from ``path``, ``$CMEV_DAMAGE_CONFIG`` or the repository default. A missing file is an error."""
    source = Path(path or os.getenv(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    return DamageModelConfig.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")) or {})


__all__ = ["CONFIG_ENV", "DEFAULT_CONFIG_PATH", "DamageModelConfig", "load_damage_model_config"]
