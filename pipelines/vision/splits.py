"""Locating the HITL data and reading split files without machine-specific paths.

A split record names its image relative to the HITL parts export and its mask relative to
the folder ``convert_hitl.py`` writes, so one split file works on every workstation. The
records of split 0.1.0 carry repository-relative ``source_path``/``mask_path`` instead; they
are still read as written.

Specification: docs/specs/model_training_specification.md section 7.1.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

REPOSITORY = Path(__file__).resolve().parents[2]
RAW_ROOT = REPOSITORY / "data" / "raw"
DEFAULT_LABELS_DIR = REPOSITORY / "data" / "interim" / "hitl_parts"
LABELS_ENV = "CMEV_HITL_PARTS_LABELS_DIR"
PARTITIONS = ("train", "val", "test")
# The publisher's folder names are swapped relative to their contents: the export named
# "Car damages dataset" holds the part polygons (data/manifests/dataset_sources.json).
HITL_FOLDERS = {"parts": ("Car damages dataset", "CMEV_HITL_PARTS_DIR"),
                "damage": ("Car parts dataset", "CMEV_HITL_DAMAGE_DIR")}


def find_hitl_folder(subset: str, explicit: str | Path | None = None, raw_root: str | Path = RAW_ROOT) -> Path:
    """The HITL export that holds ``subset`` (``"parts"`` or ``"damage"``).

    ``explicit`` or the subset's environment variable wins. Otherwise ``raw_root`` is
    searched directly and one folder down, because the archive unpacks either way.
    """
    name, env = HITL_FOLDERS[subset]
    configured = explicit or os.getenv(env)
    if configured:
        return Path(configured)
    raw_root = Path(raw_root)
    for candidate in (raw_root / name, *sorted(raw_root.glob(f"*/{name}"))):
        if (candidate / "meta.json").is_file():
            return candidate
    raise FileNotFoundError(f"the HITL {subset} export ({name!r}) is not under {raw_root} or one folder below it; "
                            f"set {env} to its path")


def labels_dir(explicit: str | Path | None = None) -> Path:
    """Where ``convert_hitl.py`` writes the part masks: ``explicit``, ``$CMEV_HITL_PARTS_LABELS_DIR`` or the default."""
    return Path(explicit or os.getenv(LABELS_ENV) or DEFAULT_LABELS_DIR)


def mask_pixel_sha256(mask: np.ndarray) -> str:
    """SHA-256 of a class-index mask's size and pixels.

    The PNG file's own hash depends on the encoder (Pillow and zlib versions), so it differs
    between workstations for identical labels. This one does not.
    """
    pixels = np.ascontiguousarray(mask, dtype=np.uint8)
    return hashlib.sha256(f"{pixels.shape[0]}x{pixels.shape[1]}:".encode() + pixels.tobytes()).hexdigest()


def read_split(split_path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(split_path).read_text(encoding="utf-8").splitlines() if line.strip()]


def split_manifest(split_dir: str | Path) -> dict[str, Any]:
    """The split's ``split_manifest.json``, or an empty mapping when the folder has none."""
    path = Path(split_dir) / "split_manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def split_file_hashes(split_dir: str | Path) -> dict[str, str]:
    return dict(split_manifest(split_dir).get("file_hashes", {}))


def split_version(split_dir: str | Path) -> str | None:
    return split_manifest(split_dir).get("split_version")


def membership(split_dir: str | Path) -> dict[str, tuple[str, str]]:
    """``example_id -> (partition, content_sha256)`` over the three partition files."""
    return {record["example_id"]: (partition, record["content_sha256"])
            for partition in PARTITIONS for record in read_split(Path(split_dir) / f"{partition}.jsonl")}


__all__ = ["DEFAULT_LABELS_DIR", "HITL_FOLDERS", "LABELS_ENV", "PARTITIONS", "RAW_ROOT", "find_hitl_folder",
           "labels_dir", "mask_pixel_sha256", "membership", "read_split", "split_file_hashes", "split_manifest",
           "split_version"]
