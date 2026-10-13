"""Lightweight OCR registry selection; importing this does not import Paddle."""
from __future__ import annotations
import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
import yaml

_REPO = Path(__file__).resolve().parents[4]

class OcrModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_version: str = Field(min_length=1)
    engine: str
    engine_version: str
    runtime_version: str
    config_version: str

    def model_dir(self, registry_root: str | Path | None = None) -> Path:
        return Path(registry_root or os.getenv("CMEV_MODEL_REGISTRY_PATH") or _REPO / "artifacts/models") / self.model_version

    def stage_versions(self, code_version: str) -> dict[str, str]:
        from .config import load_page_reading_config
        page = load_page_reading_config()
        import hashlib
        manifest_path = Path(os.getenv("CMEV_OCR_MANIFEST") or _REPO / "configs/models/ocr-manifest.json")
        return {"ocr": f"{self.engine}/{self.engine_version}", "ocr_engine": self.engine,
                "ocr_version": self.engine_version, "ocr_weights": self.model_version,
                "page_config": page.config_version, "preprocess_config": page.config_version,
                "page_config_sha256": page.sha256,
                "ocr_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "m4_code": "m4-text-layout/0.1.0", "ocr_model_config": self.config_version, "code": code_version}


def load_ocr_model_config(path: str | Path | None = None) -> OcrModelConfig:
    selected = Path(path or os.getenv("CMEV_OCR_MODEL_CONFIG") or _REPO / "configs/models/ocr.yaml")
    config = OcrModelConfig.model_validate(yaml.safe_load(selected.read_text()))
    if config.model_version.startswith("/") or ".." in Path(config.model_version).parts:
        raise ValueError("OCR model version must be a relative registry path")
    return config


def verify_ocr_registry(config: OcrModelConfig, registry_root: str | Path | None = None) -> Path:
    """Validate the entire entry against source-controlled hashes before native load."""
    import hashlib
    import json
    pinned_path = Path(os.getenv("CMEV_OCR_MANIFEST") or _REPO / "configs/models/ocr-manifest.json")
    pinned = json.loads(pinned_path.read_text())
    root = config.model_dir(registry_root)
    installed = json.loads((root / "manifest.json").read_text())
    if installed != pinned:
        raise ValueError("OCR registry manifest differs from pinned release")
    expected = (config.model_version, config.engine, config.engine_version, config.runtime_version)
    actual = tuple(installed[k] for k in ("model_version", "engine", "engine_version", "runtime_version"))
    if actual != expected:
        raise ValueError("OCR model configuration differs from registry release")
    for relative, digest in pinned["files"].items():
        target = root / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"OCR artifact missing or corrupt: {relative}")
    return root
