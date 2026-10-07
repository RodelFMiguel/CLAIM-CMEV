"""Group-aware split manifest generator for M1 vehicle part segmentation.

Splits the HITL parts dataset into train (70%), val (15%), and test (15%) partitions
using image content_sha256 over the union of both HITL subsets to prevent cross-module
evaluation leakage (model training specification section 6 and 7.1).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import random
import sys
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claim_cmev.contracts.common import PART_CODES
from pipelines.vision.splits import (
    find_hitl_folder,
    labels_dir,
    membership,
    split_file_hashes,
    split_version as version_of,
)

log = logging.getLogger("cmev.pipelines.build_splits")

DEFAULT_SEED = 20260922
DEFAULT_SPLIT_VERSION = "0.1.1"


def sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_union_hashes(
    parts_records: list[dict[str, Any]],
    damage_raw_dir: Path | None = None,
) -> tuple[list[str], set[str]]:
    """Compute the sorted list of all unique image hashes in the union of both subsets.
    
    Returns:
        union_hashes: sorted list of unique SHA-256 strings across both subsets.
        shared_hashes: set of SHA-256 strings that exist in both parts and damage subsets.
    """
    parts_hashes = {rec["image_sha256"] for rec in parts_records}
    damage_hashes = set()

    if damage_raw_dir and damage_raw_dir.exists():
        img_dir = damage_raw_dir / "File1" / "img"
        if img_dir.exists():
            for img_path in img_dir.glob("*.*"):
                damage_hashes.add(sha256_of_file(img_path))

    shared_hashes = parts_hashes.intersection(damage_hashes)
    union_hashes = sorted(parts_hashes.union(damage_hashes))
    return union_hashes, shared_hashes


def create_splits(
    parts_records: list[dict[str, Any]],
    union_hashes: list[str],
    shared_hashes: set[str],
    seed: int = DEFAULT_SEED,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> dict[str, list[dict[str, Any]]]:
    """Deterministically partition records into train, val, and test.
    
    The split is performed on the union of hashes so that any image shared between
    parts and damage always falls into the same partition across modules.
    """
    rng = random.Random(seed)
    shuffled_hashes = list(union_hashes)
    rng.shuffle(shuffled_hashes)

    n_total = len(shuffled_hashes)
    n_train = int(round(n_total * train_ratio))
    n_val = int(round(n_total * val_ratio))

    train_hash_set = set(shuffled_hashes[:n_train])
    val_hash_set = set(shuffled_hashes[n_train:n_train + n_val])
    test_hash_set = set(shuffled_hashes[n_train + n_val:])

    splits: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}

    for rec in parts_records:
        h = rec["image_sha256"]
        is_shared = h in shared_hashes
        entry = {
            "example_id": rec["image_name"],
            "image_relative_path": rec["image_relative_path"],
            "content_sha256": h,
            "group_key": h,
            "mask_relative_path": rec["mask_relative_path"],
            "mask_pixel_sha256": rec["mask_pixel_sha256"],
            "width": rec["width"],
            "height": rec["height"],
            "class_pixel_counts": rec["class_pixel_counts"],
            "class_object_counts": rec["class_object_counts"],
            "is_shared_with_damage": is_shared,
            "provenance": "real",
        }

        if h in train_hash_set:
            splits["train"].append(entry)
        elif h in val_hash_set:
            splits["val"].append(entry)
        elif h in test_hash_set:
            splits["test"].append(entry)
        else:
            raise RuntimeError(f"Hash {h} not found in any partition set")

    # Sort each partition by example_id for reproducibility
    for split_name in splits:
        splits[split_name].sort(key=lambda r: r["example_id"])

    return splits


def build_and_save_splits(
    parts_index_path: Path | str,
    damage_raw_dir: Path | str | None,
    output_dir: Path | str,
    seed: int = DEFAULT_SEED,
    split_version: str = DEFAULT_SPLIT_VERSION,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    same_membership_as: Path | str | None = None,
) -> dict[str, Any]:
    """Build and write the split.

    ``damage_raw_dir=None`` means there is no damage export: the shuffle then runs over the
    part images alone, which gives a different split from one built with it.
    ``same_membership_as`` names an existing split folder; the new split must put every image
    in the same partition, or nothing is written.
    """
    parts_index_path = Path(parts_index_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not parts_index_path.exists():
        raise FileNotFoundError(f"Parts index file not found: {parts_index_path}")

    parts_records = [json.loads(line) for line in parts_index_path.read_text(encoding="utf-8").splitlines() if line]
    damage_path = Path(damage_raw_dir) if damage_raw_dir else None

    log.info(f"Loaded {len(parts_records)} records from {parts_index_path}")
    union_hashes, shared_hashes = compute_union_hashes(parts_records, damage_path)
    log.info(f"Union image hashes: {len(union_hashes)}, Shared with damage: {len(shared_hashes)}")

    splits = create_splits(parts_records, union_hashes, shared_hashes, seed, train_ratio, val_ratio)
    if same_membership_as is not None:
        expected = membership(same_membership_as)
        built = {r["example_id"]: (name, r["content_sha256"]) for name, rows in splits.items() for r in rows}
        moved = sum(1 for key in expected.keys() | built.keys() if expected.get(key) != built.get(key))
        if moved:
            raise ValueError(f"membership differs from {same_membership_as}: {moved} images are missing, new, "
                             "changed or in another partition")

    file_hashes: dict[str, str] = {}
    partition_counts: dict[str, int] = {}
    per_class_support: dict[str, dict[str, int]] = {}

    for split_name in ("train", "val", "test"):
        records = splits[split_name]
        partition_counts[split_name] = len(records)
        split_file = output_dir / f"{split_name}.jsonl"

        with open(split_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        file_hashes[f"{split_name}.jsonl"] = sha256_of_file(split_file)

        # Count per-class objects
        class_support: dict[str, int] = {code: 0 for code in PART_CODES}
        for r in records:
            for code, cnt in r["class_object_counts"].items():
                class_support[code] = class_support.get(code, 0) + cnt
        per_class_support[split_name] = class_support

    manifest = {
        "status": "frozen",
        "split_version": split_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "group_key": "content_sha256",
        "ratios": {
            "train": train_ratio,
            "val": val_ratio,
            "test": round(1.0 - train_ratio - val_ratio, 4),
        },
        "union_total_images": len(union_hashes),
        "shared_images_with_damage": len(shared_hashes),
        "parts_partition_counts": partition_counts,
        "total_parts_images": len(parts_records),
        "per_class_object_support": per_class_support,
        "file_hashes": file_hashes,
        "record_paths": {
            "image_relative_path": "relative to the HITL parts export, the folder whose meta.json lists the part classes",
            "mask_relative_path": "relative to the output folder of pipelines/vision/convert_hitl.py",
        },
        "mask_pixel_sha256": "sha256 of '<height>x<width>:' followed by the mask's uint8 class indices",
    }
    if same_membership_as is not None:
        manifest["same_membership_as"] = {"split_version": version_of(same_membership_as),
                                          "file_hashes": split_file_hashes(same_membership_as)}

    manifest_path = output_dir / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info(f"Split manifest written to {manifest_path}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create group-aware train/val/test splits for HITL parts.")
    parser.add_argument("--parts-index", type=Path, default=None,
                        help="index.jsonl produced by convert_hitl.py. Default: in its default output folder.")
    parser.add_argument("--damage-raw-dir", type=Path, default=None,
                        help="The HITL damage export ('Car parts dataset'), needed to keep shared images together. "
                             "Default: $CMEV_HITL_DAMAGE_DIR, else found under data/raw; the build fails without it.")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Output directory. Default: data/splits/parts/<split-version>.")
    parser.add_argument("--same-membership-as", type=Path, default=None,
                        help="An existing split folder whose partition of every image the new split must keep.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Random seed for deterministic splitting.")
    parser.add_argument("--split-version", type=str, default=DEFAULT_SPLIT_VERSION, help="Split version string.")
    parser.add_argument("--train-ratio", type=float, default=0.70, help="Fraction for training split.")
    parser.add_argument("--val-ratio", type=float, default=0.15, help="Fraction for validation split.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    build_and_save_splits(
        args.parts_index or labels_dir() / "index.jsonl",
        find_hitl_folder("damage", args.damage_raw_dir),
        args.output_dir or Path("data/splits/parts") / args.split_version,
        seed=args.seed,
        split_version=args.split_version,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        same_membership_as=args.same_membership_as,
    )


if __name__ == "__main__":
    main()
