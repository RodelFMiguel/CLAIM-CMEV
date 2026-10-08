"""Verification of a model registry entry before a worker serves it.

Technical specification 9.3: a worker verifies the weight file's SHA-256 against its manifest
at start and refuses to start on a mismatch. Another model version, taxonomy version or class
numbering is refused as well, never remapped. So is a model trained in another frame than the
one the worker builds: its recorded scores would not describe what is served.

No model library is imported here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from .frame import PAD_VALUE, PIXEL_MEAN, PIXEL_STD

WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


class ModelUnavailable(RuntimeError):
    """The configured checkpoint cannot be served, so the worker must refuse to start."""

    def __init__(self, reason_code: str, reason_text: str):
        super().__init__(f"{reason_code}: {reason_text}")
        self.reason_code, self.reason_text = reason_code, reason_text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def serving_preprocessing(input_size: int, resize_policy: str) -> dict[str, Any]:
    """The model frame a worker builds, in the vocabulary of a registry entry's ``preprocessing.json``."""
    return {"input_size": input_size, "resize_policy": resize_policy, "pixel_mean": list(PIXEL_MEAN),
            "pixel_std": list(PIXEL_STD), "pad_value": PAD_VALUE}


def _same_setting(recorded: Any, served: Any) -> bool:
    if isinstance(served, list):
        return (isinstance(recorded, list) and len(recorded) == len(served)
                and all(isinstance(value, (int, float)) and abs(value - expected) < 1e-6
                        for value, expected in zip(recorded, served)))
    return recorded == served


def class_map(model_dir: Path, from_schema: bool = True) -> dict[int, str]:
    """Class id to code, from the checkpoint's ``id2label``.

    With ``from_schema``, an entry's ``label_schema.json`` is read instead when it exists: a
    trainer that left ``id2label`` at its defaults records the numbering there.
    """
    schema = model_dir / "label_schema.json"
    if from_schema and schema.is_file():
        labels = json.loads(schema.read_text(encoding="utf-8")).get("id_to_code") or {}
    else:
        labels = json.loads((model_dir / "config.json").read_text(encoding="utf-8")).get("id2label") or {}
    return {int(class_id): code for class_id, code in labels.items()}


def verify_registry_entry(model_dir: str | Path, *, model_version: str, taxonomy_version: str,
                          preprocessing: Mapping[str, Any], labels_match: Callable[[dict[int, str]], bool],
                          taxonomy_name: str, labels_from_schema: bool = True) -> dict[str, Any]:
    """Check a registry entry against its manifest and what the worker will do; returns the manifest."""
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise ModelUnavailable("model_not_found", f"no registry entry at {model_dir}")
    manifest_path = model_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ModelUnavailable("model_manifest_missing", f"{manifest_path} does not exist")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != model_version:
        raise ModelUnavailable("model_version_mismatch", f"the manifest names {manifest.get('version')!r}, "
                               f"the configuration {model_version!r}")
    if manifest.get("taxonomy_version") != taxonomy_version:
        raise ModelUnavailable("taxonomy_version_mismatch", f"the manifest names {manifest.get('taxonomy_version')!r}, "
                               f"the configuration {taxonomy_version!r}")
    weights = next((model_dir / name for name in WEIGHT_FILES if (model_dir / name).is_file()), None)
    if weights is None:
        raise ModelUnavailable("model_weights_missing", f"no weight file in {model_dir}")
    if sha256_file(weights) != manifest.get("weights_sha256"):
        raise ModelUnavailable("model_weights_hash_mismatch", f"{weights.name} does not match its manifest hash")
    if not labels_match(class_map(model_dir, labels_from_schema)):
        raise ModelUnavailable("label_map_mismatch", f"the checkpoint's class map is not the {taxonomy_name}'s")
    preprocessing_path = model_dir / "preprocessing.json"
    if not preprocessing_path.is_file():
        raise ModelUnavailable("preprocessing_missing", f"{preprocessing_path} does not exist, so the frame the "
                               "model was trained in is not recorded")
    trained = json.loads(preprocessing_path.read_text(encoding="utf-8"))
    differing = [key for key, value in preprocessing.items() if not _same_setting(trained.get(key), value)]
    if differing:
        raise ModelUnavailable("preprocessing_mismatch", "; ".join(
            f"{key}: trained with {trained.get(key)!r}, served with {preprocessing[key]!r}" for key in differing))
    return manifest


__all__ = ["ModelUnavailable", "WEIGHT_FILES", "class_map", "serving_preprocessing", "sha256_file",
           "verify_registry_entry"]
