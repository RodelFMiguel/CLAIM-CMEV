#!/usr/bin/env python3
"""Independently audit generated surveyor-mark data against immutable source pages."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_box(box: list, width: int, height: int) -> bool:
    return (len(box) == 4 and all(isinstance(value, (int, float)) for value in box)
            and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)


def validate(source: Path, output: Path) -> dict:
    errors = []
    counts = Counter()
    source_records = read_jsonl(source / "annotations.jsonl")
    output_records = read_jsonl(output / "annotations.jsonl")
    source_by_id = {row["image_id"]: row for row in source_records}
    output_by_id = {row["image_id"]: row for row in output_records}
    if len(output_by_id) != len(output_records):
        errors.append("Duplicate output image IDs")
    if source_by_id.keys() != output_by_id.keys():
        errors.append("Output image membership differs from source")
    claim_splits = defaultdict(set)
    mark_counts = Counter()
    mark_ids = set()
    expected_coco = defaultdict(list)
    for image_id, original in source_by_id.items():
        derived = output_by_id.get(image_id)
        if derived is None:
            continue
        for key in original:
            if derived.get(key) != original[key]:
                errors.append(f"{image_id}: original annotation field changed: {key}")
        counts[original["split"]] += 1
        claim_splits[original["claim_id"]].add(derived["split"])
        path = output / derived["file_name"]
        if not path.is_file():
            errors.append(f"Missing output image: {path}")
            continue
        try:
            with Image.open(path) as image:
                image.load()
                if image.size != (original["width"], original["height"]):
                    errors.append(f"{image_id}: image dimensions changed")
                if image.format != "JPEG":
                    errors.append(f"{image_id}: expected JPEG output")
        except Exception as exc:
            errors.append(f"{image_id}: unreadable image: {exc}")
            continue
        if derived.get("source_sha256") != sha256(source / original["file_name"]):
            errors.append(f"{image_id}: source checksum mismatch")
        if derived.get("output_sha256") != sha256(path):
            errors.append(f"{image_id}: output checksum mismatch")
        regions = {region["label"]: region for region in original["regions"]}
        decisions = {row["row_id"]: row for row in derived.get("row_decisions", [])}
        with Image.open(source / original["file_name"]) as src, Image.open(path) as dst:
            difference = ImageChops.difference(src.convert("RGB"), dst.convert("RGB"))
            for mark in derived.get("marks", []):
                mark_id = mark["mark_id"]
                if mark_id in mark_ids:
                    errors.append(f"Duplicate mark ID: {mark_id}")
                mark_ids.add(mark_id)
                mark_class = mark["class"]
                mark_counts[mark_class] += 1
                box = mark["bbox"]
                if not valid_box(box, original["width"], original["height"]):
                    errors.append(f"{mark_id}: invalid mark bbox")
                elif difference.crop(tuple(box)).getbbox() is None:
                    errors.append(f"{mark_id}: bbox contains no changed pixels")
                for component in mark.get("component_bboxes", []):
                    if not valid_box(component, original["width"], original["height"]):
                        errors.append(f"{mark_id}: invalid component bbox")
                    elif not (box[0] <= component[0] < component[2] <= box[2]
                              and box[1] <= component[1] < component[3] <= box[3]):
                        errors.append(f"{mark_id}: component outside mark box")
                if mark_class in ("exclusion", "price_change"):
                    expected_coco[original["split"]].append((image_id, mark_class, box))
                    if original["page_number"] == 1:
                        errors.append(f"{mark_id}: assessment page must be detector-negative")
                    target = mark.get("target_row_id")
                    if target not in decisions:
                        errors.append(f"{mark_id}: missing target row")
                    if mark.get("target_entry_id") != f"{image_id}:{target}":
                        errors.append(f"{mark_id}: inconsistent entry link")
                if mark.get("confirmation_status") == "confirmed":
                    errors.append(f"{mark_id}: generated mark must not be a real confirmation")
                target_row = decisions.get(mark.get("target_row_id"))
                if target_row:
                    rb = target_row["row_bbox"]
                    if not (rb[0] <= box[0] < box[2] <= rb[2] and rb[1] <= box[1] < box[3] <= rb[3]):
                        errors.append(f"{mark_id}: mark extends beyond target row")
                    if mark.get("currency") != "SGD" or mark.get("amount_basis") != "line_total_pre_tax_excluding_labour":
                        errors.append(f"{mark_id}: amount currency/basis missing")
                    if mark.get("original_amount") != target_row.get("declared_amount"):
                        errors.append(f"{mark_id}: original amount differs from declared line total")
                if mark.get("provenance") != "synthetic_intended_action" or mark.get("confirmation_status") != "not_human_confirmed":
                    errors.append(f"{mark_id}: missing synthetic/unconfirmed provenance")
                amount = mark.get("revised_amount")
                if amount is not None:
                    try:
                        if not isinstance(amount, str) or Decimal(amount) <= 0:
                            errors.append(f"{mark_id}: invalid exact revised amount")
                    except InvalidOperation:
                        errors.append(f"{mark_id}: unparseable revised amount")
                    if mark_class != "price_change":
                        errors.append(f"{mark_id}: numeric revision on non-price mark")
            for word in derived.get("handwritten_words", []):
                if word.get("m5_intended_field") != "O" or word.get("geometry_source") != "rendered_ink":
                    errors.append(f"{image_id}: surveyor text must remain separate from printed M5 fields")
                if not valid_box(word["bbox"], original["width"], original["height"]):
                    errors.append(f"{image_id}: invalid handwritten text bbox")
                elif difference.crop(tuple(word["bbox"])).getbbox() is None:
                    errors.append(f"{image_id}: handwritten text ROI unchanged")
        for row_id, row in decisions.items():
            row_number = int(row_id.split("_")[1])
            top = 477 + (row_number - 1) * 79
            if row["row_bbox"] != [70, top, 1170, top + 79]:
                errors.append(f"{image_id}:{row_id}: row geometry differs from source template")
            region = regions.get(f"{row_id}_amount")
            if region is None or Decimal(str(row["declared_amount"])) != Decimal(region["text"].replace(",", "")):
                errors.append(f"{image_id}:{row_id}: declared line total changed")
            if row.get("final_approved_amount") is not None:
                errors.append(f"{image_id}:{row_id}: synthetic row contains final approval")
            for mark_id in row.get("mark_ids", []):
                linked = [mark for mark in derived.get("marks", []) if mark["mark_id"] == mark_id]
                if len(linked) != 1 or linked[0].get("target_row_id") != row_id:
                    errors.append(f"{image_id}:{row_id}: inconsistent mark association")
            price_marks = [mark for mark in derived.get("marks", [])
                           if mark.get("target_row_id") == row_id and mark["class"] == "price_change"]
            if price_marks and row.get("surveyor_revised_amount") != price_marks[0].get("revised_amount"):
                errors.append(f"{image_id}:{row_id}: intended numeric revision mismatch")
    for claim_id, splits in claim_splits.items():
        if len(splits) != 1:
            errors.append(f"Claim crosses splits: {claim_id}")
    for name in ("claims.jsonl",):
        if (source / name).read_bytes() != (output / name).read_bytes():
            errors.append(f"Source {name} not preserved byte for byte")
    with (source / "manifest.csv").open(newline="", encoding="utf-8") as stream:
        source_manifest = list(csv.DictReader(stream))
    with (output / "manifest.csv").open(newline="", encoding="utf-8") as stream:
        output_manifest = list(csv.DictReader(stream))
    source_manifest_ids = {row["image_id"]: row for row in source_manifest}
    output_manifest_ids = {row["image_id"]: row for row in output_manifest}
    if source_manifest_ids.keys() != output_manifest_ids.keys() or len(output_manifest_ids) != len(output_manifest):
        errors.append("Manifest membership differs from source or contains duplicates")
    for image_id, row in output_manifest_ids.items():
        original = source_manifest_ids.get(image_id, {})
        for key in original.keys() & row.keys():
            if row[key] != original[key]:
                errors.append(f"{image_id}: manifest source field changed: {key}")
        for key in ("source_sha256", "output_sha256", "page_seed"):
            if str(output_by_id[image_id].get(key)) != row.get(key):
                errors.append(f"{image_id}: manifest/annotation mismatch: {key}")
    provenance = json.loads((output / "provenance.json").read_text())
    for key, filename in (("source_annotation_sha256", "annotations.jsonl"), ("source_claims_sha256", "claims.jsonl")):
        if provenance.get(key) != sha256(source / filename):
            errors.append(f"Provenance checksum mismatch: {key}")
    if provenance.get("page_count") != len(output_records) or provenance.get("split_counts") != dict(counts):
        errors.append("Provenance page/split counts mismatch")
    if provenance.get("provenance") != "fully_synthetic" or "SGD assumed" not in provenance.get("currency_provenance", ""):
        errors.append("Synthetic provenance or SGD assumption missing")
    declared_fonts = {font["path"] for font in provenance["fonts"]}
    for font in provenance["fonts"]:
        if Path(font["path"]).is_file() and sha256(Path(font["path"])) != font["sha256"]:
            errors.append(f"Font checksum mismatch: {font['path']}")
    if any(row.get("font_file") not in declared_fonts for row in output_records):
        errors.append("Per-page font missing from provenance")
    if (source / "annotations.jsonl").read_bytes() != (output / "source_annotations.jsonl").read_bytes():
        errors.append("Original annotation copy changed")
    for split in counts:
        coco = json.loads((output / f"marks_coco_{split}.json").read_text())
        categories = {category["id"]: category["name"] for category in coco["categories"]}
        if set(categories.values()) != {"exclusion", "price_change"}:
            errors.append(f"{split}: detector taxonomy includes distractors or lacks classes")
        coco_images = {record["id"]: Path(record["file_name"]).stem for record in coco["images"]}
        if set(coco_images.values()) != {row["image_id"] for row in output_records if row["split"] == split}:
            errors.append(f"{split}: COCO membership mismatch")
        observed = []
        for ann in coco["annotations"]:
            x, y, width, height = ann["bbox"]
            observed.append((coco_images.get(ann["image_id"]), categories.get(ann["category_id"]),
                             [x, y, x + width, y + height]))
            if ann["area"] != width * height:
                errors.append(f"{split}: incorrect COCO area")
        if sorted(observed) != sorted(expected_coco[split]):
            errors.append(f"{split}: COCO boxes/classes differ from semantic mark targets")
    actual_files = {str(path.relative_to(output)) for path in (output / "images").rglob("*.jpg")}
    expected_files = {row["file_name"] for row in output_records}
    if actual_files != expected_files:
        errors.append("JPEG file membership differs from annotations")
    return {"valid": not errors, "images": len(output_records),
            "image_splits": dict(counts), "mark_classes": dict(mark_counts),
            "detector_negative_pages": sum(not any(mark["class"] in ("exclusion", "price_change")
                for mark in row.get("marks", [])) for row in output_records),
            "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/raw/synthetic_motor_claims_500"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/synthetic_motor_claims_500_w_annotation"))
    args = parser.parse_args()
    result = validate(args.source, args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
