"""Lightweight, explicitly pinned selections for the neural document workers.

API/orchestration read configuration only; model libraries and registry bytes are
loaded by workers. Exporting a candidate never changes these selections.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

ROOT = Path(__file__).resolve().parents[3]


class DocumentModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: Literal["lineitems", "penmarks"]
    model_version: str
    config_version: str
    device: Literal["cpu", "cuda", "mps"]
    allow_candidate: bool
    files: dict[str, str]
    method: Literal["layoutlmv3"] = "layoutlmv3"
    family_id: str = "family-a-ruled-grid"
    cost_basis: Literal["single_part_pre_tax_no_discount_v1"] = "single_part_pre_tax_no_discount_v1"
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    text_unit: Literal["line", "word", "block"] = "line"

    @model_validator(mode="after")
    def valid_paths(self):
        if not self.model_version.startswith(self.model_id + "/") or ".." in Path(self.model_version).parts:
            raise ValueError("model_version must be a relative path under the selected task")
        required = {"manifest.json", "model.safetensors", "preprocessing.json", "label_schema.json"}
        if self.model_id == "lineitems":
            required |= {"config.json", "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json"}
        if not required <= self.files.keys():
            raise ValueError("pin every serving file before selecting a model")
        for name, digest in self.files.items():
            if Path(name).name != name or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid registry file or SHA-256")
        return self

    def model_dir(self) -> Path:
        return Path(os.getenv("CMEV_MODEL_REGISTRY_PATH") or ROOT / "artifacts/models") / self.model_version

    @property
    def sha256(self) -> str:
        # Device is execution detail; model/config/rule bytes determine the result identity.
        data = self.model_dump(exclude={"device"})
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def verify(self) -> dict:
        root = self.model_dir()
        for name, expected in self.files.items():
            with (root / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError(f"selected {self.model_id} artifact differs from pinned bytes: {name}")
        manifest = json.loads((root / "manifest.json").read_text())
        if (manifest.get("model_id"), manifest.get("version"), manifest.get("weights_sha256")) != (
                self.model_id, self.model_version, self.files["model.safetensors"]):
            raise ValueError("registry manifest differs from selected model")
        if manifest.get("status") not in ("candidate", "approved") or (
                manifest["status"] == "candidate" and not self.allow_candidate):
            raise ValueError("selected model status is not allowed")
        if manifest.get("quick_run"):
            raise ValueError("quick-run exports cannot be served")
        return manifest

    def stage_versions(self, code: str) -> dict[str, str]:
        common = {"code": code, "model_config": self.config_version, "model_config_sha256": self.sha256,
                  "weights_sha256": self.files["model.safetensors"]}
        if self.model_id == "lineitems":
            from .line_items.config import load_layout_families
            from .line_items.vocabulary import load_estimate_vocabulary
            from .line_items.layoutlmv3 import ALIGNMENT_VERSION
            layout, vocab = load_layout_families(), load_estimate_vocabulary()
            if self.family_id not in {f.family_id for f in layout.families}:
                raise ValueError("selected layout family is not configured")
            return {**common, "line_items_model": self.model_version, "extraction_method": self.method,
                    "parser_config": layout.config_version, "layout_families": layout.config_version,
                    "layout_sha256": layout.sha256, "vocabulary": vocab.version,
                    "vocabulary_sha256": hashlib.sha256(json.dumps(vocab.data.model_dump(mode="json"), sort_keys=True).encode()).hexdigest(),
                    "taxonomy": vocab.taxonomy_version, "alignment": ALIGNMENT_VERSION,
                    "family": self.family_id, "cost_basis": self.cost_basis}
        from .pen_marks.config import load_linking_config
        link = load_linking_config()
        return {**common, "penmark_model": self.model_version,
                "link_config": link.link_config_version, "link_config_sha256": link.sha256}


def load_document_model_config(task: Literal["lineitems", "penmarks"]) -> DocumentModelConfig:
    path = Path(os.getenv(f"CMEV_{task.upper()}_CONFIG") or ROOT / f"configs/models/{task}.yaml")
    config = DocumentModelConfig.model_validate(yaml.safe_load(path.read_text()))
    if config.model_id != task:
        raise ValueError("model selection names another document task")
    return config
