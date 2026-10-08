"""Group-aware split manifest generator for M2 vehicle damage segmentation.

Splits the HITL damage dataset into train (70%), val (15%), and test (15%) partitions
using image content_sha256 over the union of both HITL subsets to guarantee zero
cross-module evaluation leakage with M1 (model training specification section 6 and 7.2).
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.vision.build_splits import sha256_of_file, compute_union_hashes, DEFAULT_SEED, DEFAULT_SPLIT_VERSION
from pipelines.vision.convert_damage import DAMAGE_CLASSES

log = logging.getLogger("cmev.pipelines.build_damage_splits")


def create_damage_splits(
    damage_records: list[dict[str, Any]],
    parts_records: list[dict[str, Any]],
    seed: int = DEFAULT_SEED,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Deterministically partition damage records into train, val, and test.
    
    The split is performed on the union of hashes so that any image shared between
    parts and damage always falls into the same partition across modules.
    """
    parts_hashes = {rec["image_sha256"] for rec in parts_records}
    damage_hashes = {rec["image_sha256"] for rec in damage_records}
    shared_hashes = parts_hashes.intersection(damage_hashes)
    union_hashes = sorted(parts_hashes.union(damage_hashes))

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

    for rec in damage_records:
        h = rec["image_sha256"]
        is_shared = h in shared_hashes
        entry = {
            "example_id": rec["image_name"],
            "source_path": rec["image_path"],
            "content_sha256": h,
            "group_key": h,
            "mask_path": rec["mask_path"],
            "mask_sha256": rec["mask_sha256"],
            "width": rec["width"],
            "height": rec["height"],
            "class_pixel_counts": rec["class_pixel_counts"],
            "class_object_counts": rec["class_object_counts"],
            "is_shared_with_parts": is_shared,
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

    manifest_metadata = {
        "status": "frozen",
        "split_version": DEFAULT_SPLIT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "group_key": "content_sha256",
        "ratios": {
            "train": train_ratio,
            "val": val_ratio,
            "test": round(1.0 - train_ratio - val_ratio, 4),
        },
        "union_total_images": n_total,
        "shared_images_with_parts": len(shared_hashes),
        "damage_partition_counts": {k: len(v) for k, v in splits.items()},
        "total_damage_images": len(damage_records),
    }

    return splits, manifest_metadata


def build_and_save_damage_splits(
    damage_index_path: Path | str = "data/interim/damage_masks/damage_index.json",
    parts_index_path: Path | str = "data/interim/parts_masks/parts_index.json",
    output_dir: Path | str = "data/splits/damage/0.1.0",
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    damage_index_path = Path(damage_index_path)
    parts_index_path = Path(parts_index_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    damage_records = json.loads(damage_index_path.read_text(encoding="utf-8"))
    
    parts_records = []
    parts_split_dir = Path("data/splits/parts/0.1.0")
    if (parts_split_dir / "train.jsonl").exists():
        for s_name in ["train", "val", "test"]:
            f = parts_split_dir / f"{s_name}.jsonl"
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    parts_records.append({"image_sha256": rec.get("content_sha256", "")})
    else:
        parts_raw_dir = Path("data/raw/Car damages dataset/File1/img")
        for p in parts_raw_dir.glob("*.*"):
            parts_records.append({"image_sha256": sha256_of_file(p)})

    splits, manifest = create_damage_splits(damage_records, parts_records, seed=seed)

    per_class_support: dict[str, dict[str, int]] = {k: {c: 0 for c in DAMAGE_CLASSES} for k in splits}
    for s_name, entries in splits.items():
        for e in entries:
            for c, cnt in e.get("class_object_counts", {}).items():
                if c in per_class_support[s_name]:
                    per_class_support[s_name][c] += cnt

    manifest["per_class_object_support"] = per_class_support
    split_hashes = {}

    for split_name, entries in splits.items():
        out_file = output_dir / f"{split_name}.jsonl"
        lines = [json.dumps(entry, ensure_ascii=False) for entry in entries]
        out_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        h = sha256_of_file(out_file)
        split_hashes[f"{split_name}_jsonl_sha256"] = h
        log.info(f"Saved {split_name} ({len(entries)} items, sha256={h[:12]}...) to {out_file}")

    manifest["partition_file_hashes"] = split_hashes
    manifest_file = output_dir / "split_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info(f"Saved manifest to {manifest_file}")

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build synchronized splits for M2 vehicle damage")
    parser.add_argument("--damage-index", type=str, default="data/interim/damage_masks/damage_index.json")
    parser.add_argument("--parts-index", type=str, default="data/interim/parts_masks/parts_index.json")
    parser.add_argument("--output-dir", type=str, default="data/splits/damage/0.1.0")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    build_and_save_damage_splits(
        damage_index_path=args.damage_index,
        parts_index_path=args.parts_index,
        output_dir=args.output_dir,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
