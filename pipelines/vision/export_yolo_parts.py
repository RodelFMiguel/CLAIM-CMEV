"""Export HITL vehicle part polygon annotations into YOLOv8 segmentation format.

Reads the unified split JSONL files and raw Supervisely polygon annotations,
and formats images and polygon labels for Ultralytics YOLOv8 segmentation.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claim_cmev.contracts.common import PART_CODES
from claim_cmev.taxonomy import load_parts
from pipelines.vision.convert_hitl import PART_CODE_TO_ID
from pipelines.vision.splits import find_hitl_folder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmev.pipelines.export_yolo_parts")


def export_split(
    split_file: Path,
    raw_ann_dir: Path,
    output_img_dir: Path,
    output_lbl_dir: Path,
    title_to_yolo_id: dict[str, int],
    images_root: Path | None = None,
) -> int:
    output_img_dir.mkdir(parents=True, exist_ok=True)
    output_lbl_dir.mkdir(parents=True, exist_ok=True)

    records = [json.loads(line) for line in split_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    count = 0

    for rec in records:
        if "image_relative_path" in rec:  # portable record: relative to the HITL parts export
            src_path = images_root / rec["image_relative_path"]
        else:  # split 0.1.0: a repository-relative path
            src_path = Path(rec["source_path"])
            if not src_path.is_absolute():
                src_path = PROJECT_ROOT / src_path

        ann_file = raw_ann_dir / f"{src_path.name}.json"
        if not ann_file.exists():
            log.warning(f"Annotation file not found: {ann_file}")
            continue

        ann_data = json.loads(ann_file.read_text(encoding="utf-8"))
        img_w = ann_data["size"]["width"]
        img_h = ann_data["size"]["height"]

        # Copy or symlink image
        dst_img = output_img_dir / src_path.name
        if not dst_img.exists():
            try:
                os.symlink(src_path, dst_img)
            except OSError:
                shutil.copy2(src_path, dst_img)

        # Build YOLO segmentation label
        label_lines: list[str] = []
        for obj in ann_data.get("objects", []):
            if obj.get("geometryType") != "polygon":
                continue
            title = obj.get("classTitle")
            if title not in title_to_yolo_id:
                continue

            yolo_id = title_to_yolo_id[title]
            pts = obj.get("points", {}).get("exterior", [])
            if len(pts) < 3:
                continue

            coords = []
            for pt in pts:
                nx = max(0.0, min(1.0, pt[0] / img_w))
                ny = max(0.0, min(1.0, pt[1] / img_h))
                coords.append(f"{nx:.6f} {ny:.6f}")

            line = f"{yolo_id} " + " ".join(coords)
            label_lines.append(line)

        dst_lbl = output_lbl_dir / f"{src_path.stem}.txt"
        dst_lbl.write_text("\n".join(label_lines) + "\n", encoding="utf-8")
        count += 1

    return count


def main(
    split_dir: str | Path = "data/splits/parts/0.1.1",
    raw_ann_dir: str | Path | None = None,
    output_dir: str | Path = "data/interim/yolo_parts",
) -> None:
    split_dir = Path(split_dir)
    images_root = find_hitl_folder("parts")
    raw_ann_dir = Path(raw_ann_dir) if raw_ann_dir else images_root / "File1" / "ann"
    output_dir = Path(output_dir)

    parts_tax = load_parts()
    title_to_code = {p["hitl_title"]: p["code"] for p in parts_tax.meta["parts"]}
    title_to_yolo_id = {title: PART_CODE_TO_ID[code] - 1 for title, code in title_to_code.items()}

    for split_name in ["train", "val", "test"]:
        s_file = split_dir / f"{split_name}.jsonl"
        o_img = output_dir / "images" / split_name
        o_lbl = output_dir / "labels" / split_name
        n = export_split(s_file, raw_ann_dir, o_img, o_lbl, title_to_yolo_id, images_root)
        log.info(f"Exported {n} examples for {split_name} split.")

    # Write dataset.yaml
    names_dict = {i: code for i, code in enumerate(PART_CODES)}
    yaml_content = f"""path: {output_dir.resolve()}
train: images/train
val: images/val
test: images/test

names:
"""
    for idx, code in names_dict.items():
        yaml_content += f"  {idx}: {code}\n"

    yaml_path = output_dir / "dataset.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    log.info(f"YOLO dataset config written to: {yaml_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export YOLO parts dataset")
    parser.add_argument("--split-dir", type=str, default="data/splits/parts/0.1.1")
    parser.add_argument("--raw-ann-dir", type=str, default=None,
                        help="Default: File1/ann of the HITL parts export ($CMEV_HITL_PARTS_DIR, else found under data/raw).")
    parser.add_argument("--output-dir", type=str, default="data/interim/yolo_parts")
    args = parser.parse_args()

    main(args.split_dir, args.raw_ann_dir, args.output_dir)
