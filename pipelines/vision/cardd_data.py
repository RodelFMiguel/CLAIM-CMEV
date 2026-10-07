"""Offline, immutable CarDD (COCO instance annotations) conversion for M2.

CarDD ships per-instance polygons/RLE in COCO JSON files with official train/val/test
splits. This converter writes one semantic class-index mask per photograph in the
six-class CarDD vocabulary (``configs/taxonomy/damage_cardd.yaml``): background 0,
damage 1..6, ignore 255. It never mixes HITL labels into CarDD data (M2 specification
section "CarDD access gate and contingency").

The prepared directory has the same layout and manifest keys as the HITL preparation,
so ``load_prepared`` and ``pipelines.vision.training`` read it unchanged with
``task="damage_cardd"``. Unannotated pixels are background by convention only; CarDD
does not certify that they are undamaged.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw

from pipelines.vision.hitl_data import (
    IGNORE_INDEX, _aligned, _dhash, _digest, _file_hash, _json_bytes, _manifest_hash, _pixel_hash,
    load_prepared,
)

TASK = "damage_cardd"
CONVERSION_VERSION = "cardd-coco-1.0.0"
OVERLAP_POLICY = "smaller_instance_area_wins_ties_by_class_then_annotation_id"
CROWD_POLICY = "iscrowd_regions_ignore_255_where_no_other_instance"
SPLIT_POLICY = "official_coco_files; exact/pixel duplicates spanning splits kept only in the earliest of train, val, test"
SPLIT_ORDER = ("train", "val", "test")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
POLYGON_TOLERANCE = 2.0  # pixels a polygon vertex may lie outside the frame before the image is rejected
_REPO = Path(__file__).resolve().parents[2]

__all__ = ["TASK", "prepare_cardd", "inspect_cardd", "load_prepared", "rasterize_coco", "decode_rle"]


def _normalise(name: str) -> str:
    return " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())


def _taxonomy() -> dict:
    path = _REPO / "configs" / "taxonomy" / "damage_cardd.yaml"
    config = yaml.safe_load(path.read_text())
    codes = config["codes"]
    return {"version": config["taxonomy_version"],
            "class_names": ["background"] + [row["code"] for row in codes],
            "category_to_id": {_normalise(row["source_category"]): i + 1 for i, row in enumerate(codes)},
            "source_sha256": _file_hash(path)}


def _split_of(path: Path) -> str | None:
    stem = path.stem.lower()
    found = [split for split in SPLIT_ORDER if split in stem]
    return found[0] if len(found) == 1 else None


def _coco_root(annotation_file: Path) -> Path:
    parent = annotation_file.parent
    return parent.parent if parent.name.lower() in {"annotations", "annotation", "labels"} else parent


def _discover(raw_root: Path, taxonomy: dict) -> list[dict]:
    """Find COCO files whose category names are exactly the six CarDD categories."""
    found = []
    expected = set(taxonomy["category_to_id"])
    for path in sorted(raw_root.rglob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not {"images", "annotations", "categories"} <= set(data):
            continue
        names = {_normalise(c["name"]) for c in data["categories"]}
        if names != expected:
            raise ValueError(f"{path} is COCO but its categories {sorted(names)} are not the six CarDD categories")
        split = _split_of(path)
        if split is None:
            raise ValueError(f"Cannot tell the split of {path.name}; expected exactly one of train/val/test in its name")
        found.append({"path": path, "split": split, "data": data})
    splits = Counter(item["split"] for item in found)
    duplicated = [split for split, count in splits.items() if count > 1]
    if duplicated:
        raise ValueError(f"More than one COCO file for split(s) {duplicated}")
    missing = [split for split in SPLIT_ORDER if split not in splits]
    if missing:
        raise ValueError(f"CarDD COCO annotations missing for split(s) {missing} under {raw_root}")
    return found


def _image_index(root: Path) -> dict[str, list[Path]]:
    index = defaultdict(list)
    for path in root.rglob("*"):
        if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file():
            index[path.name].append(path)
    return index


def _locate(file_name: str, split: str, root: Path, index: dict[str, list[Path]]) -> Path:
    direct = root / file_name
    if direct.is_file():
        return direct
    candidates = index.get(Path(file_name).name, [])
    if len(candidates) > 1:
        candidates = [p for p in candidates if any(split in part.lower() for part in p.relative_to(root).parts[:-1])]
    if len(candidates) != 1:
        raise FileNotFoundError(f"{'No' if not candidates else 'Ambiguous'} image file for {file_name!r} ({split})")
    return candidates[0]


def decode_rle(segmentation: dict, height: int, width: int) -> np.ndarray:
    """Decode COCO RLE (list counts or compressed string) to a boolean H x W mask."""
    size = segmentation.get("size")
    if size is not None and list(size) != [height, width]:
        raise ValueError(f"RLE size {size} differs from image size {[height, width]}")
    counts = segmentation["counts"]
    if isinstance(counts, str):
        counts = _rle_string_counts(counts)
    flat = np.zeros(height * width, dtype=bool)
    position, value = 0, False
    for run in counts:
        run = int(run)
        if run < 0 or position + run > flat.size:
            raise ValueError("RLE runs exceed the image")
        if value:
            flat[position:position + run] = True
        position += run
        value = not value
    if position != flat.size:
        raise ValueError("RLE runs do not cover the image")
    return flat.reshape(width, height).T  # COCO RLE is column-major


def _rle_string_counts(text: str) -> list[int]:
    """pycocotools' compressed RLE string format (rleFrString)."""
    counts, position = [], 0
    while position < len(text):
        value, shift, more = 0, 0, True
        while more:
            chunk = ord(text[position]) - 48
            value |= (chunk & 0x1F) << (5 * shift)
            more = bool(chunk & 0x20)
            position += 1
            shift += 1
            if not more and chunk & 0x10:
                value |= -1 << (5 * shift)
        if len(counts) > 2:
            value += counts[-2]
        counts.append(value)
    return counts


def _polygon_mask(polygons: list, height: int, width: int) -> np.ndarray:
    canvas = Image.new("L", (width, height))
    draw = ImageDraw.Draw(canvas)
    for flat in polygons:
        if not isinstance(flat, list) or len(flat) < 6 or len(flat) % 2:
            raise ValueError("COCO polygons need an even number of at least six coordinates")
        points = []
        for x, y in zip(flat[0::2], flat[1::2]):
            x, y = float(x), float(y)
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError("Polygon coordinate is not finite")
            if not (-POLYGON_TOLERANCE <= x <= width + POLYGON_TOLERANCE
                    and -POLYGON_TOLERANCE <= y <= height + POLYGON_TOLERANCE):
                raise ValueError(f"Polygon coordinate ({x}, {y}) outside the {width}x{height} frame")
            points.append((min(max(x, 0.0), width), min(max(y, 0.0), height)))
        draw.polygon(points, fill=1)
    return np.asarray(canvas, dtype=bool)


def rasterize_coco(annotations: list[dict], width: int, height: int,
                   category_to_id: dict[int, int]) -> tuple[Image.Image, dict]:
    """Semantic mask from COCO instances; the result is independent of annotation order.

    Different-class overlap goes to the instance with the smaller area (a crack stays
    visible on top of a large dent). ``iscrowd`` regions become ignore 255 where no
    other instance claims the pixel. ``category_to_id`` maps COCO category IDs to
    CarDD class IDs.
    """
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        raise ValueError("Invalid image dimensions")
    instances, crowd = [], np.zeros((height, width), dtype=bool)
    counts = Counter()
    for annotation in annotations:
        if annotation["category_id"] not in category_to_id:
            raise ValueError(f"Unknown category_id {annotation['category_id']}")
        segmentation = annotation.get("segmentation")
        if isinstance(segmentation, dict):
            selected = decode_rle(segmentation, height, width)
        elif isinstance(segmentation, list) and segmentation:
            selected = _polygon_mask(segmentation, height, width)
        else:
            raise ValueError(f"Annotation {annotation.get('id')} has no usable segmentation")
        class_id = category_to_id[annotation["category_id"]]
        if annotation.get("iscrowd"):
            crowd |= selected
            continue
        instances.append((class_id, selected, int(selected.sum()), int(annotation.get("id", 0))))
        counts[class_id] += 1
    instances.sort(key=lambda item: (-item[2], item[0], item[3]))
    output = np.zeros((height, width), dtype=np.uint8)
    classes_here = np.zeros((height, width), dtype=np.uint8)
    seen = {}
    for class_id, selected, _, _ in instances:
        output[selected] = class_id
        seen.setdefault(class_id, np.zeros((height, width), dtype=bool))
        seen[class_id] |= selected
    for covered in seen.values():
        classes_here += covered
    crowd_only = crowd & (output == 0)
    output[crowd_only] = IGNORE_INDEX
    stats = {"instances": len(instances), "instances_per_class": {str(k): v for k, v in sorted(counts.items())},
             "crowd_instances": sum(1 for a in annotations if a.get("iscrowd")),
             "different_class_overlap_pixels": int(np.count_nonzero(classes_here > 1)),
             "crowd_ignored_pixels": int(crowd_only.sum())}
    return Image.fromarray(output), stats


def inspect_cardd(raw_root: str | Path) -> dict:
    """Fast, read-only check of a CarDD copy: COCO files, splits, counts and image sizes."""
    raw_root = Path(raw_root).resolve()
    taxonomy = _taxonomy()
    files = _discover(raw_root, taxonomy)
    summary = {"raw_root": str(raw_root), "splits": {}}
    for item in files:
        data, root = item["data"], _coco_root(item["path"])
        index = _image_index(root)
        missing = []
        for image in data["images"]:
            try:
                _locate(image["file_name"], item["split"], root, index)
            except FileNotFoundError as error:
                missing.append(str(error))
        widths = np.asarray([i["width"] for i in data["images"]])
        heights = np.asarray([i["height"] for i in data["images"]])
        names = {c["id"]: _normalise(c["name"]) for c in data["categories"]}
        summary["splits"][item["split"]] = {
            "annotation_file": str(item["path"].relative_to(raw_root)),
            "images": len(data["images"]), "annotations": len(data["annotations"]),
            "instances_per_category": dict(Counter(names[a["category_id"]] for a in data["annotations"])),
            "segmentation_formats": dict(Counter(type(a.get("segmentation")).__name__ for a in data["annotations"])),
            "crowd_annotations": sum(1 for a in data["annotations"] if a.get("iscrowd")),
            "median_size": [int(np.median(widths)), int(np.median(heights))] if len(widths) else None,
            "max_side_percentiles": {p: int(np.percentile(np.maximum(widths, heights), p)) for p in (50, 90, 100)}
            if len(widths) else None,
            "missing_or_ambiguous_images": len(missing), "missing_examples": missing[:5]}
    return summary


def _union_groups(records: list[dict]) -> None:
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, record in enumerate(records):
        for key in ("content_sha256", "pixel_sha256"):
            value = (key, record[key])
            if value in seen:
                parent[find(i)] = find(seen[value])
            seen[value] = i
    members = defaultdict(list)
    for i in range(len(records)):
        members[find(i)].append(records[i])
    for rows in members.values():
        group_id = _digest(sorted({row["content_sha256"] for row in rows}))
        for row in rows:
            row["group_id"] = group_id


def _near_duplicates(records: list[dict], distance: int) -> list[dict]:
    representatives = {}
    for record in records:
        representatives.setdefault(record["pixel_sha256"], record)
    rows = list(representatives.values())
    hashes = np.asarray([int(r["dhash"], 16) for r in rows], dtype=np.uint64)
    candidates = []
    for i in range(1, len(rows)):
        bits = np.bitwise_count(hashes[:i] ^ hashes[i])
        for j in np.nonzero(bits <= distance)[0]:
            left, right = rows[i], rows[int(j)]
            candidates.append({"sample_id_a": left["sample_id"], "sample_id_b": right["sample_id"],
                               "hamming_distance": int(bits[j]), "same_group": left["group_id"] == right["group_id"],
                               "cross_split": left["split"] != right["split"]})
    return candidates


def prepare_cardd(raw_root: str | Path, output_dir: str | Path, max_side: int | None = None,
                  near_duplicate_distance: int = 4, progress_every: int = 250) -> dict:
    """Prepare CarDD semantic masks once; reuse identical versions, reject changed inputs.

    ``raw_root`` is the CarDD release folder (it must contain the COCO train/val/test
    files and their images). ``output_dir`` should be ``data/processed/cardd/<version>``.
    ``max_side`` optionally stores a downscaled copy (image bilinear-Lanczos, mask
    nearest) to speed training; None keeps original pixels and copies the files as-is.
    """
    if max_side is not None and max_side < 16:
        raise ValueError("max_side must be None or at least 16 pixels")
    raw_root, output_dir = Path(raw_root).resolve(), Path(output_dir).resolve()
    if output_dir == raw_root or raw_root in output_dir.parents:
        raise ValueError("Derived outputs must not be written inside immutable raw inputs")
    taxonomy = _taxonomy()
    files = _discover(raw_root, taxonomy)
    work = []
    for item in files:
        data, root = item["data"], _coco_root(item["path"])
        index = _image_index(root)
        id_map = {c["id"]: taxonomy["category_to_id"][_normalise(c["name"])] for c in data["categories"]}
        by_image = defaultdict(list)
        for annotation in data["annotations"]:
            by_image[annotation["image_id"]].append(annotation)
        for image in sorted(data["images"], key=lambda i: i["id"]):
            work.append({"split": item["split"], "image": image, "annotations": by_image.get(image["id"], []),
                         "id_map": id_map, "root": root, "index": index, "annotation_file": item["path"]})
    excluded = []
    located = []
    for entry in work:
        try:
            entry["path"] = _locate(entry["image"]["file_name"], entry["split"], entry["root"], entry["index"])
            located.append(entry)
        except FileNotFoundError as error:
            excluded.append({"split": entry["split"], "file_name": entry["image"]["file_name"],
                             "reason": "image_not_found", "detail": str(error)})
    receipt_path = raw_root / "cardd-source-receipt.json"
    acquisition = ({"receipt_sha256": _file_hash(receipt_path), "receipt": json.loads(receipt_path.read_text())}
                   if receipt_path.is_file() else {"status": "acquisition_receipt_not_present"})
    for entry in located:
        entry["content_sha256"] = _file_hash(entry["path"])
        entry["annotation_digest"] = _digest(sorted(entry["annotations"], key=lambda a: a.get("id", 0)))
    config = {"conversion_version": CONVERSION_VERSION, "task": TASK, "taxonomy": taxonomy,
              "overlap_policy": OVERLAP_POLICY, "crowd_policy": CROWD_POLICY, "split_policy": SPLIT_POLICY,
              "max_side": max_side, "polygon_tolerance_pixels": POLYGON_TOLERANCE,
              "near_duplicate_distance": near_duplicate_distance, "acquisition": acquisition,
              "orientation_policy": "infer_frame_from_dimensions_or_reject_ambiguous_exif",
              "source_fingerprint": _digest(
                  [{"annotation_file": str(i["path"].relative_to(raw_root)), "sha256": _file_hash(i["path"]),
                    "split": i["split"]} for i in files]
                  + [{"image": str(e["path"].relative_to(raw_root)), "content_sha256": e["content_sha256"]}
                     for e in located])}
    config_hash = _digest(config)
    if output_dir.exists():
        manifest = load_prepared(output_dir)
        if manifest["preparation_config_hash"] != config_hash:
            raise ValueError("Preparation inputs/configuration changed; select a new processed version directory")
        return manifest
    names = taxonomy["class_names"]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cardd-", dir=output_dir.parent) as temporary:
        stage = Path(temporary) / "prepared"
        (stage / "images").mkdir(parents=True)
        (stage / "masks").mkdir()
        records, seen_ids = [], set()
        for number, entry in enumerate(located, 1):
            image_info = entry["image"]
            source = str(entry["path"].relative_to(raw_root))
            try:
                mask, stats = rasterize_coco(entry["annotations"], image_info["width"], image_info["height"],
                                             entry["id_map"])
                image, mask, transform = _aligned(entry["path"], mask)
            except (ValueError, OSError) as error:
                excluded.append({"split": entry["split"], "file_name": image_info["file_name"],
                                 "reason": "conversion_failed", "detail": f"{type(error).__name__}: {error}"})
                continue
            sample_id = "cardd-" + _digest([entry["content_sha256"], entry["annotation_digest"]])[:24]
            if sample_id in seen_ids:
                excluded.append({"split": entry["split"], "file_name": image_info["file_name"],
                                 "reason": "identical_image_and_annotations"})
                continue
            seen_ids.add(sample_id)
            if max_side is not None and max(image.size) > max_side:
                scale = max_side / max(image.size)
                size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
                image = image.resize(size, Image.Resampling.LANCZOS)
                mask = mask.resize(size, Image.Resampling.NEAREST)
                transform["downscaled_to"] = list(size)
            unchanged = (transform["exif_orientation"] == 1 and "downscaled_to" not in transform)
            suffix = entry["path"].suffix.lower() if unchanged else ".png"
            image_rel, mask_rel = Path("images") / f"{sample_id}{suffix}", Path("masks") / f"{sample_id}.png"
            if unchanged:
                shutil.copyfile(entry["path"], stage / image_rel)  # byte-identical copy of the original
            else:
                image.save(stage / image_rel)
            mask.save(stage / mask_rel)
            pixels = np.asarray(mask)
            records.append({
                "sample_id": sample_id, "task": TASK, "split": entry["split"],
                "image_path": str(output_dir / image_rel), "mask_path": str(output_dir / mask_rel),
                "image_relative_path": str(image_rel), "mask_relative_path": str(mask_rel),
                "source_image": source, "source_annotation": str(entry["annotation_file"].relative_to(raw_root)),
                "coco_image_id": image_info["id"], "content_sha256": entry["content_sha256"],
                "annotation_sha256": entry["annotation_digest"],
                "image_sha256": _file_hash(stage / image_rel), "mask_sha256": _file_hash(stage / mask_rel),
                "pixel_sha256": _pixel_hash(image), "dhash": _dhash(image),
                "width": image.width, "height": image.height, "transform": transform,
                "pixel_counts": np.bincount(pixels[pixels != IGNORE_INDEX], minlength=len(names)).tolist(),
                "ignored_pixels": int(np.count_nonzero(pixels == IGNORE_INDEX)),
                "instance_stats": stats, "provenance": "real"})
            if progress_every and (number % progress_every == 0 or number == len(located)):
                print(f"CarDD conversion: {number}/{len(located)} images", flush=True)
        records.sort(key=lambda row: row["sample_id"])
        _union_groups(records)
        best_split = {}
        for row in records:
            rank = SPLIT_ORDER.index(row["split"])
            best_split[row["group_id"]] = min(best_split.get(row["group_id"], rank), rank)
        kept = []
        for row in records:
            if SPLIT_ORDER.index(row["split"]) != best_split[row["group_id"]]:
                (stage / row["image_relative_path"]).unlink()
                (stage / row["mask_relative_path"]).unlink()
                excluded.append({"split": row["split"], "file_name": row["source_image"], "sample_id": row["sample_id"],
                                 "reason": f"duplicate_of_{SPLIT_ORDER[best_split[row['group_id']]]}_image"})
                continue
            kept.append(row)
        records = kept
        candidates = _near_duplicates(records, near_duplicate_distance)
        (stage / "near_duplicate_candidates.json").write_text(json.dumps(candidates, indent=2) + "\n")
        split_hashes, counts = {}, {TASK: {}}
        for split in SPLIT_ORDER:
            rows = [row for row in records if row["split"] == split]
            split_path = Path("splits") / TASK / f"{split}.jsonl"
            (stage / split_path).parent.mkdir(parents=True, exist_ok=True)
            payload = b"".join(_json_bytes({key: row[key] for key in (
                "sample_id", "group_id", "content_sha256", "annotation_sha256", "pixel_sha256", "split")}) + b"\n"
                for row in rows)
            (stage / split_path).write_bytes(payload)
            split_hashes[str(split_path)] = hashlib.sha256(payload).hexdigest()
            instances = Counter()
            for row in rows:
                instances.update({names[int(k)]: v for k, v in row["instance_stats"]["instances_per_class"].items()})
            counts[TASK][split] = {
                "images": len(rows), "groups": len({row["group_id"] for row in rows}),
                "pixel_counts": [sum(row["pixel_counts"][i] for row in rows) for i in range(len(names))],
                "images_with_class": [sum(1 for row in rows if row["pixel_counts"][i]) for i in range(len(names))],
                "instances": dict(instances), "ignored_pixels": sum(row["ignored_pixels"] for row in rows)}
        manifest = {
            "schema_version": 1, "conversion_version": CONVERSION_VERSION, "dataset": "CarDD",
            "preparation_config": config, "preparation_config_hash": config_hash,
            "class_names": {TASK: names}, "taxonomy": {TASK: taxonomy}, "records": records,
            "sources": [{"task": TASK, "split": i["split"], "path": str(i["path"].relative_to(raw_root)),
                         "sha256": _file_hash(i["path"]), "images": len(i["data"]["images"]),
                         "annotations": len(i["data"]["annotations"])} for i in files],
            "counts": counts, "excluded": excluded, "ignore_index": IGNORE_INDEX,
            "split": {"policy": SPLIT_POLICY, "image_counts": {s: counts[TASK][s]["images"] for s in SPLIT_ORDER},
                      "excluded_counts": dict(Counter(row["reason"] for row in excluded)),
                      "vehicle_independence_established": False,
                      "limitations": "Official CarDD splits; exact/pixel duplicates are grouped. Related views of one vehicle are not identified."},
            "split_hashes": split_hashes, "raw_root": str(raw_root), "output_dir": str(output_dir),
            "license_evidence": "CarDD research use requires the publisher's consent; this converter does not verify it.",
            "label_limitation": "CarDD annotates six damage types only; unannotated pixels are background by convention, not certified undamaged.",
            "near_duplicate_audit": {"method": f"64-bit grayscale dHash, Hamming distance <= {near_duplicate_distance}",
                                     "candidate_count": len(candidates),
                                     "cross_split_candidates": sum(row["cross_split"] for row in candidates),
                                     "status": "requires_human_review" if candidates else "no_candidates_at_selected_threshold",
                                     "sha256": _file_hash(stage / "near_duplicate_candidates.json"),
                                     "path": "near_duplicate_candidates.json",
                                     "limits": "Candidates can be false positives; absence does not exclude related views."}}
        manifest["manifest_hash"] = _manifest_hash(manifest)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        stage.rename(output_dir)
    return manifest
