"""Supervisely polygon to indexed semantic mask converter for M2 (HITL damage).

Converts raw Supervisely polygon annotations into single-channel indexed PNG masks (0 = background,
1-8 = damage classes) with descending object area ordering (smaller area wins the pixel).

Specification: docs/specs/module-02-damage-segmentation.md
               docs/specs/model_training_specification.md section 7.2 & 13.1
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

log = logging.getLogger("cmev.pipelines.convert_damage")

OVERLAP_POLICY = "descending_object_area"

DAMAGE_CLASSES = [
    "missing-part",
    "broken-part",
    "scratch",
    "cracked",
    "dent",
    "flaking",
    "paint-chip",
    "corrosion",
]

DAMAGE_CODE_TO_ID: dict[str, int] = {code: i + 1 for i, code in enumerate(DAMAGE_CLASSES)}
ID_TO_DAMAGE_CODE: dict[int, str] = {i + 1: code for i, code in enumerate(DAMAGE_CLASSES)}
ID_TO_DAMAGE_CODE[0] = "background"

SUPERVISELY_TITLE_TO_CODE: dict[str, str] = {
    "Missing part": "missing-part",
    "Broken part": "broken-part",
    "Scratch": "scratch",
    "Cracked": "cracked",
    "Dent": "dent",
    "Flaking": "flaking",
    "Paint chip": "paint-chip",
    "Corrosion": "corrosion",
}


def build_damage_palette(meta_classes: list[dict[str, Any]] | None = None) -> list[int]:
    """Generate a 256-color RGB palette (768 ints) for PIL 'P' mode masks.
    
    Index 0 is background (0, 0, 0). Indices 1..8 use the Supervisely hex colors.
    """
    palette = [0] * 768
    default_colors = [
        (0, 0, 0),        # 0: background
        (19, 164, 201),   # 1: missing-part (#13A4C9)
        (166, 255, 71),   # 2: broken-part (#A6FF47)
        (180, 45, 56),    # 3: scratch (#B42D38)
        (225, 150, 96),   # 4: cracked (#E19660)
        (144, 60, 89),    # 5: dent (#903C59)
        (167, 116, 27),   # 6: flaking (#A7741B)
        (180, 14, 19),    # 7: paint-chip (#B40E13)
        (115, 194, 206),  # 8: corrosion (#73C2CE)
    ]

    if meta_classes:
        for cls_info in meta_classes:
            title = cls_info.get("title")
            color_hex = cls_info.get("color", "")
            if title in SUPERVISELY_TITLE_TO_CODE and color_hex.startswith("#") and len(color_hex) == 7:
                code = SUPERVISELY_TITLE_TO_CODE[title]
                cid = DAMAGE_CODE_TO_ID[code]
                r = int(color_hex[1:3], 16)
                g = int(color_hex[3:5], 16)
                b = int(color_hex[5:7], 16)
                if cid < len(default_colors):
                    default_colors[cid] = (r, g, b)

    for i, (r, g, b) in enumerate(default_colors):
        palette[i * 3] = r
        palette[i * 3 + 1] = g
        palette[i * 3 + 2] = b

    return palette


def rasterize_polygons(
    ann_data: dict[str, Any],
    title_to_class_id: dict[str, int],
) -> tuple[np.ndarray, dict[str, int], dict[str, int]]:
    """Rasterize Supervisely polygon objects to an indexed mask (H, W).
    
    Applies descending object area ordering: larger defects (dents) are painted first,
    smaller and fine defects (cracks, scratches) are painted on top.
    
    Returns:
        mask: uint8 2D array of class IDs (0..8).
        class_pixel_counts: dict of damage_code -> pixel count in mask.
        class_object_counts: dict of damage_code -> object count.
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
            continue
        class_id = title_to_class_id[title]
        code = ID_TO_DAMAGE_CODE[class_id]
        class_object_counts[code] = class_object_counts.get(code, 0) + 1

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
            "code": code,
            "ext_pts": ext_pts,
            "int_pts": int_pts,
            "area": area,
        })

    # Sort descending by area: larger damages first, smaller damages (scratches/cracks) on top
    parsed_objects.sort(key=lambda o: o["area"], reverse=True)

    for obj in parsed_objects:
        cv2.fillPoly(mask, [obj["ext_pts"]], obj["class_id"])
        if obj["int_pts"]:
            cv2.fillPoly(mask, obj["int_pts"], 0)

    class_pixel_counts: dict[str, int] = {}
    unique_ids, counts = np.unique(mask, return_counts=True)
    for cid, cnt in zip(unique_ids, counts):
        if cid != 0 and int(cid) in ID_TO_DAMAGE_CODE:
            class_pixel_counts[ID_TO_DAMAGE_CODE[int(cid)]] = int(cnt)

    return mask, class_pixel_counts, class_object_counts


def sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def convert_damage_dataset(
    raw_dir: Path | str = "data/raw/Car parts dataset",
    output_dir: Path | str = "data/interim/damage_masks",
) -> dict[str, Any]:
    raw_dir = Path(raw_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    meta_file = raw_dir / "meta.json"
    if not meta_file.exists():
        raise FileNotFoundError(f"meta.json not found in {raw_dir}")

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta_classes = meta.get("classes", [])

    # Map titles
    title_to_class_id = {}
    for cls_info in meta_classes:
        title = cls_info["title"]
        if title in SUPERVISELY_TITLE_TO_CODE:
            code = SUPERVISELY_TITLE_TO_CODE[title]
            title_to_class_id[title] = DAMAGE_CODE_TO_ID[code]

    palette = build_damage_palette(meta_classes)

    ann_dir = raw_dir / "File1" / "ann"
    img_dir = raw_dir / "File1" / "img"

    if not ann_dir.exists():
        raise FileNotFoundError(f"Annotation directory not found: {ann_dir}")

    ann_files = sorted(ann_dir.glob("*.json"))
    log.info(f"Found {len(ann_files)} annotation files in {ann_dir}")

    converted_records: list[dict[str, Any]] = []
    total_class_pixels: dict[str, int] = {c: 0 for c in DAMAGE_CLASSES}
    total_class_objects: dict[str, int] = {c: 0 for c in DAMAGE_CLASSES}
    skipped_files: list[dict[str, str]] = []

    masks_dir = output_dir / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)

    for ann_path in ann_files:
        try:
            ann_data = json.loads(ann_path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped_files.append({"file": ann_path.name, "reason": f"json_decode_error: {exc}"})
            continue

        img_name = ann_path.name[:-5]  # strip .json
        img_path = img_dir / img_name
        if not img_path.exists():
            skipped_files.append({"file": ann_path.name, "reason": f"image_not_found: {img_name}"})
            continue

        mask, pixel_counts, object_counts = rasterize_polygons(ann_data, title_to_class_id)

        # Save indexed PNG
        mask_filename = f"{img_name}.png"
        mask_path = masks_dir / mask_filename
        mask_pil = Image.fromarray(mask, mode="P")
        mask_pil.putpalette(palette)
        mask_pil.save(mask_path, format="PNG")

        img_hash = sha256_of_file(img_path)
        mask_hash = sha256_of_file(mask_path)

        for code, count in pixel_counts.items():
            total_class_pixels[code] += count
        for code, count in object_counts.items():
            total_class_objects[code] += count

        rec = {
            "image_name": img_path.name,
            "image_path": str(img_path),
            "image_sha256": img_hash,
            "mask_name": mask_filename,
            "mask_path": str(mask_path),
            "mask_sha256": mask_hash,
            "width": int(ann_data["size"]["width"]),
            "height": int(ann_data["size"]["height"]),
            "class_pixel_counts": pixel_counts,
            "class_object_counts": object_counts,
        }
        converted_records.append(rec)

    # Save index
    index_path = output_dir / "damage_index.json"
    index_path.write_text(json.dumps(converted_records, indent=2), encoding="utf-8")

    report = {
        "dataset_name": "HITL damage dataset",
        "taxonomy": "hitl-damage-1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_converted": len(converted_records),
        "total_skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "overlap_policy": OVERLAP_POLICY,
        "class_pixel_counts": total_class_pixels,
        "class_object_counts": total_class_objects,
        "damage_index_path": str(index_path),
    }

    report_path = output_dir / "conversion_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info(f"Conversion complete: {len(converted_records)} masks saved to {output_dir}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert HITL damage polygons to indexed PNG masks")
    parser.add_argument("--raw-dir", type=str, default="data/raw/Car parts dataset")
    parser.add_argument("--output-dir", type=str, default="data/interim/damage_masks")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    convert_damage_dataset(raw_dir=args.raw_dir, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
