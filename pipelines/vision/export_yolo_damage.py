"""Export HITL vehicle damage polygon annotations into YOLOv8 segmentation format.

Reads the unified damage split JSONL files and raw Supervisely polygon annotations,
and formats images and polygon labels for Ultralytics YOLOv8 segmentation.

Specification: docs/specs/module-02-damage-segmentation.md
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

from pipelines.vision.convert_damage import DAMAGE_CLASSES, DAMAGE_CODE_TO_ID, SUPERVISELY_TITLE_TO_CODE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmev.pipelines.export_yolo_damage")

# In YOLO, classes are 0..7
TITLE_TO_YOLO_ID = {
    title: DAMAGE_CODE_TO_ID[code] - 1
    for title, code in SUPERVISELY_TITLE_TO_CODE.items()
}


def export_damage_split(
    split_file: Path,
    raw_ann_dir: Path,
    output_img_dir: Path,
    output_lbl_dir: Path,
    title_to_yolo_id: dict[str, int],
) -> int:
    output_img_dir.mkdir(parents=True, exist_ok=True)
    output_lbl_dir.mkdir(parents=True, exist_ok=True)

    records = [json.loads(line) for line in split_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    count = 0

    for rec in records:
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


def export_yolo_damage(
    split_dir: str | Path = "data/splits/damage/0.1.0",
    raw_ann_dir: str | Path = "data/raw/Car parts dataset/File1/ann",
    output_dir: str | Path = "data/interim/yolo_damage",
) -> None:
    split_dir = Path(split_dir)
    raw_ann_dir = Path(raw_ann_dir)
    output_dir = Path(output_dir)

    for split_name in ["train", "val", "test"]:
        s_file = split_dir / f"{split_name}.jsonl"
        out_img = output_dir / "images" / split_name
        out_lbl = output_dir / "labels" / split_name
        cnt = export_damage_split(s_file, raw_ann_dir, out_img, out_lbl, TITLE_TO_YOLO_ID)
        log.info(f"Exported {cnt} images/labels for split '{split_name}'")

    # Generate dataset.yaml
    yaml_lines = [
        f"path: {output_dir.resolve()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        "names:",
    ]
    for idx, code in enumerate(DAMAGE_CLASSES):
        yaml_lines.append(f"  {idx}: {code}")

    yaml_file = output_dir / "dataset.yaml"
    yaml_file.write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    log.info(f"Generated YOLO dataset config: {yaml_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export HITL damage dataset to YOLO format")
    parser.add_argument("--split-dir", type=str, default="data/splits/damage/0.1.0")
    parser.add_argument("--raw-ann-dir", type=str, default="data/raw/Car parts dataset/File1/ann")
    parser.add_argument("--output-dir", type=str, default="data/interim/yolo_damage")
    args = parser.parse_args()

    export_yolo_damage(args.split_dir, args.raw_ann_dir, args.output_dir)


if __name__ == "__main__":
    main()
