"""Configuration models for M1 Vehicle Part Segmentation.

Specification: docs/specs/module-01-vehicle-part-segmentation.md

This module stays free of the model stack: the orchestrator and the API read it to pin the
versions of a parts command, and they must not import PyTorch to do so.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
import yaml

_REPOSITORY = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG_PATH = _REPOSITORY / "configs" / "models" / "parts.yaml"
CONFIG_ENV = "CMEV_PARTS_CONFIG"
DEFAULT_REGISTRY_PATH = _REPOSITORY / "artifacts" / "models"
REGISTRY_ENV = "CMEV_MODEL_REGISTRY_PATH"


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

    def stage_versions(self, code_version: str) -> dict[str, str]:
        """The versions pinned on a parts command and recorded on every M1 row.

        They name the checkpoint, the thresholds and the vocabulary (module-01, "Message
        fields read and written"), and they form the job-key signature.
        """
        return {"parts_model": self.model_version, "parts_config": self.config_version,
                "taxonomy": self.taxonomy_version, "code": code_version}

    def model_dir(self, registry_root: str | Path | None = None) -> Path:
        """The registry entry of ``model_version``: ``<registry>/parts/0.1.0`` for ``parts/0.1.0``.

        The registry is ``registry_root``, ``$CMEV_MODEL_REGISTRY_PATH`` or the repository's
        ``artifacts/models`` (technical specification 9.3).
        """
        root = Path(registry_root or os.getenv(REGISTRY_ENV) or DEFAULT_REGISTRY_PATH)
        return root / self.model_version


def load_parts_config(path: str | Path | None = None) -> PartsConfig:
    """Load from ``path``, ``$CMEV_PARTS_CONFIG`` or the repository default.

    A missing file is an error: built-in defaults must not stand in for a versioned
    configuration, because rows would then record a configuration version nobody wrote.
    """
    return PartsConfig.from_yaml(Path(path or os.getenv(CONFIG_ENV) or DEFAULT_CONFIG_PATH))


__all__ = ["CONFIG_ENV", "DEFAULT_CONFIG_PATH", "DEFAULT_REGISTRY_PATH", "PartsConfig", "REGISTRY_ENV",
           "load_parts_config"]
