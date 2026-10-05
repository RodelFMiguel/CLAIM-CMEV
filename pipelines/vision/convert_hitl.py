"""Supervisely polygon to indexed semantic mask converter for M1 (HITL parts).

Converts raw Supervisely polygon annotations into single-channel indexed PNG masks (0 = background,
1-21 = vehicle part classes) with descending object area ordering.

Specification: docs/specs/model_training_specification.md section 7.1.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from claim_cmev.contracts.common import PART_CODES
from claim_cmev.taxonomy import load_parts
from claim_cmev.taxonomy.hitl import classify_supervisely_meta

log = logging.getLogger("cmev.pipelines.convert_hitl")

OVERLAP_POLICY = "descending_object_area"

from claim_cmev.vision.palette import ID_TO_PART_CODE, PART_CODE_TO_ID, build_palette


def rasterize_annotation(
    ann_data: dict[str, Any],
    title_to_class_id: dict[str, int],
) -> tuple[np.ndarray, dict[str, int], dict[str, int]]:
    """Rasterize Supervisely polygon objects to an indexed mask (H, W).
    
    Applies descending object area ordering: larger panels are rasterized first,
    smaller parts and sub-components are painted on top.
    
    Returns:
        mask: uint8 2D array of class IDs (0..21).
        class_pixel_counts: dict of part_code -> pixel count in mask.
        class_object_counts: dict of part_code -> object count.
    """
    height = ann_data["size"]["height"]
    width = ann_data["size"]["width"]
    mask = np.zeros((height, width), dtype=np.uint8)

    objects = ann_data.get("objects", [])
    parsed_objects = []
    class_object_counts: dict[str, int] = {}

    for obj in objects:
        title = obj.get("classTitle")
        if title not in title_to_class_id:
            raise ValueError(f"Unrecognized class title: {title!r}")
        class_id = title_to_class_id[title]
        part_code = ID_TO_PART_CODE[class_id]
        class_object_counts[part_code] = class_object_counts.get(part_code, 0) + 1

        points = obj.get("points", {})
        exterior = points.get("exterior", [])
        interior = points.get("interior", [])

        if len(exterior) < 3:
            continue

        ext_pts = np.array(exterior, dtype=np.int32)
        area = float(cv2.contourArea(ext_pts))
        int_pts = [np.array(h, dtype=np.int32) for h in interior if len(h) >= 3]

        parsed_objects.append({
            "class_id": class_id,
            "part_code": part_code,
            "ext_pts": ext_pts,
            "int_pts": int_pts,
            "area": area,
        })

    # Sort descending by area: largest parts first, smallest parts on top
    parsed_objects.sort(key=lambda o: o["area"], reverse=True)

    for obj in parsed_objects:
        # Fill exterior polygon
        cv2.fillPoly(mask, [obj["ext_pts"]], obj["class_id"])
        # Cut out interior holes
        if obj["int_pts"]:
            cv2.fillPoly(mask, obj["int_pts"], 0)

    # Compute pixel counts from final rasterized mask
    class_pixel_counts: dict[str, int] = {}
    unique_ids, counts = np.unique(mask, return_counts=True)
    for cid, cnt in zip(unique_ids, counts):
        if cid != 0 and int(cid) in ID_TO_PART_CODE:
            class_pixel_counts[ID_TO_PART_CODE[int(cid)]] = int(cnt)

    return mask, class_pixel_counts, class_object_counts


def sha256_of_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def convert_dataset(
    raw_dir: Path | str,
    output_dir: Path | str,
    sample_verify: int = 10,
) -> dict[str, Any]:
    """Execute complete dataset conversion and return summary report."""
    raw_dir = Path(raw_dir)
    output_dir = Path(output_dir)
    masks_dir = output_dir / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)

    log.info("Validating dataset taxonomy with classify_supervisely_meta...")
    meta_path = raw_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found in {raw_dir}")

    subset_info = classify_supervisely_meta(meta_path)
    if subset_info.kind != "parts":
        raise ValueError(f"Expected 'parts' dataset subset, but got {subset_info.kind!r}")

    meta_classes = json.loads(meta_path.read_text(encoding="utf-8")).get("classes", [])
    palette = build_palette(meta_classes)

    # Build mapping from Supervisely classTitle -> integer class ID (1..21)
    title_to_class_id = {}
    for title, code in subset_info.title_to_code.items():
        if code not in PART_CODE_TO_ID:
            raise ValueError(f"Code {code!r} from title {title!r} not in canonical PART_CODES")
        title_to_class_id[title] = PART_CODE_TO_ID[code]

    ann_dir = raw_dir / "File1" / "ann"
    img_dir = raw_dir / "File1" / "img"
    machine_dir = raw_dir / "File1" / "masks_machine"

    ann_files = sorted(ann_dir.glob("*.json"))
    if not ann_files:
        raise FileNotFoundError(f"No annotation JSON files found in {ann_dir}")

    log.info(f"Processing {len(ann_files)} annotation files...")

    index_records = []
    total_pixel_counts: dict[str, int] = {code: 0 for code in PART_CODES}
    total_object_counts: dict[str, int] = {code: 0 for code in PART_CODES}
    sample_verification_results = []

    for idx, ann_file in enumerate(ann_files):
        img_name = ann_file.name[:-5]  # strip .json
        img_path = img_dir / img_name
        if not img_path.exists():
            raise FileNotFoundError(f"Image {img_path} corresponding to {ann_file} not found")

        ann_data = json.loads(ann_file.read_text(encoding="utf-8"))
        mask, pixel_counts, obj_counts = rasterize_annotation(ann_data, title_to_class_id)

        # Update totals
        for code, count in pixel_counts.items():
            total_pixel_counts[code] = total_pixel_counts.get(code, 0) + count
        for code, count in obj_counts.items():
            total_object_counts[code] = total_object_counts.get(code, 0) + count

        # Save paletted PNG
        mask_filename = f"{img_path.stem}.png"
        mask_path = masks_dir / mask_filename
        pil_mask = Image.fromarray(mask, mode="P")
        pil_mask.putpalette(palette)
        pil_mask.save(mask_path, format="PNG", optimize=True)

        # Hashes
        img_sha = sha256_of_file(img_path)
        mask_sha = sha256_of_file(mask_path)

        index_records.append({
            "image_name": img_name,
            "image_path": str(img_path),
            "image_sha256": img_sha,
            "mask_name": mask_filename,
            "mask_path": str(mask_path),
            "mask_sha256": mask_sha,
            "width": int(ann_data["size"]["width"]),
            "height": int(ann_data["size"]["height"]),
            "class_pixel_counts": pixel_counts,
            "class_object_counts": obj_counts,
        })

        # Sample verification against masks_machine if available
        if idx < sample_verify and machine_dir.exists():
            machine_mask_path = machine_dir / img_name
            if machine_mask_path.exists():
                m_img = np.array(Image.open(machine_mask_path).convert("RGB"))
                m_fg = (m_img > 0).any(axis=-1)
                our_fg = mask > 0
                intersection = int(np.logical_and(our_fg, m_fg).sum())
                union = int(np.logical_or(our_fg, m_fg).sum())
                iou = float(intersection / union) if union > 0 else 1.0
                sample_verification_results.append({
                    "image_name": img_name,
                    "foreground_iou_vs_machine": round(iou, 4),
                    "matching_pixels": intersection,
                    "union_pixels": union,
                })

    # Save index.jsonl
    index_file = output_dir / "index.jsonl"
    with open(index_file, "w", encoding="utf-8") as f:
        for rec in index_records:
            f.write(json.dumps(rec) + "\n")

    # Save conversion report
    report = {
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": subset_info.taxonomy_version,
        "overlap_policy": OVERLAP_POLICY,
        "total_images": len(index_records),
        "total_objects": sum(total_object_counts.values()),
        "per_class_object_counts": total_object_counts,
        "per_class_pixel_counts": total_pixel_counts,
        "sample_verification": sample_verification_results,
        "index_file": str(index_file),
    }

    report_file = output_dir / "conversion_report.json"
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info(f"Conversion complete! Report written to {report_file}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert HITL Supervisely polygons to indexed part masks.")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/Car damages dataset"),
                        help="Path to raw HITL Car damages dataset folder.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/interim/hitl_parts"),
                        help="Output directory for masks and index.")
    parser.add_argument("--sample-verify", type=int, default=10,
                        help="Number of samples to verify against masks_machine.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    convert_dataset(args.raw_dir, args.output_dir, args.sample_verify)


if __name__ == "__main__":
    main()
