"""Offline, immutable Supervisely HITL conversion shared by both training notebooks.

Folder names are deliberately not used to identify a task. IDs 1..N follow the
repository taxonomy YAML order; 0 is background and 255 is ignored overlap.

HITL annotators nest parts: a door polygon includes its window and mirror, a bumper
its licence plate and grille. The default ``nested`` overlap policy lets a polygon
that lies mostly inside a larger different-class polygon keep its pixels, and ignores
only partial overlaps. ``ignore_all`` reproduces the original v1 preparation.
No vehicle identity is inferred from image filenames or similarity hashes.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import tempfile
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageOps

CONVERSION_VERSION = "hitl-polygons-1.1.0"
# policy -> (manifest policy string, conversion version). ignore_all keeps v1 hashes.
OVERLAP_POLICIES = {
    "ignore_all": ("different_class_overlap_ignore_255", "hitl-polygons-1.0.0"),
    "nested": ("nested_smaller_wins_partial_overlap_ignore_255", CONVERSION_VERSION),
}
DEFAULT_NESTED_CONTAINMENT = 0.8
IGNORE_INDEX = 255
_REPO = Path(__file__).resolve().parents[2]


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _taxonomies() -> dict:
    result = {}
    for task, name, key in (("parts", "parts.yaml", "parts"), ("damage", "damage_hitl.yaml", "codes")):
        path = _REPO / "configs" / "taxonomy" / name
        config = yaml.safe_load(path.read_text())
        rows = config[key]
        result[task] = {
            "version": config["taxonomy_version"],
            "class_names": ["background"] + [row["code"] for row in rows],
            "title_to_id": {row["hitl_title"]: i + 1 for i, row in enumerate(rows)},
            "source_sha256": _file_hash(path),
        }
    return result


def _discover(raw_root: Path, taxonomies: dict) -> tuple[list[dict], list[dict]]:
    sources, samples = [], []
    for meta_path in sorted(raw_root.rglob("meta.json")):
        meta = json.loads(meta_path.read_text())
        titles = [entry["title"] for entry in meta.get("classes", [])]
        matches = [task for task, taxonomy in taxonomies.items()
                   if set(titles) == set(taxonomy["title_to_id"])]
        if not matches:
            continue  # Other datasets may legitimately live in data/raw.
        if len(titles) != len(set(titles)):
            raise ValueError(f"Duplicate class titles in {meta_path}")
        task = matches[0]
        source = {"task": task, "path": str(meta_path.parent.relative_to(raw_root)),
                  "meta_sha256": _file_hash(meta_path), "annotation_count": 0}
        for ann_path in sorted(meta_path.parent.rglob("ann/*.json")):
            image_path = ann_path.parent.parent / "img" / ann_path.name.removesuffix(".json")
            if not image_path.is_file():
                raise ValueError(f"Missing image for {ann_path}")
            samples.append({"task": task, "annotation": ann_path, "image": image_path,
                            "content_sha256": _file_hash(image_path),
                            "annotation_sha256": _file_hash(ann_path)})
            source["annotation_count"] += 1
        if source["annotation_count"] == 0:
            raise ValueError(f"HITL vocabulary found but no Supervisely annotations in {meta_path.parent}")
        sources.append(source)
    if {source["task"] for source in sources} != {"parts", "damage"}:
        raise ValueError("Both HITL subsets are required for a union-level split; discover by meta.json vocabulary.")
    return sources, samples


def _ring(points: list, width: int, height: int) -> list[tuple[float, float]]:
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("Polygon rings require at least three points")
    result = []
    for point in points:
        if len(point) != 2:
            raise ValueError("Polygon points must be [x, y]")
        x, y = map(float, point)
        if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= width and 0 <= y <= height):
            raise ValueError(f"Polygon coordinate outside annotation frame: {point}")
        result.append((x, y))
    return result


def rasterize_annotation(annotation: dict, title_to_id: dict[str, int], *, policy: str = "nested",
                         containment: float = DEFAULT_NESTED_CONTAINMENT) -> tuple[Image.Image, dict]:
    """Rasterize polygons with holes; the result is independent of object order.

    Same-class overlap merges. For two different-class polygons, ``ignore_all`` marks
    every shared pixel 255. ``nested`` gives shared pixels to the smaller polygon when
    at least ``containment`` of its area lies inside the larger one (window inside
    door); other shared pixels stay 255 (seam slivers, partial damage overlaps).
    """
    if policy not in OVERLAP_POLICIES:
        raise ValueError(f"Unknown overlap policy {policy!r}; choose {sorted(OVERLAP_POLICIES)}")
    if not 0 < containment <= 1:
        raise ValueError("containment must lie in (0, 1]")
    width, height = annotation["size"]["width"], annotation["size"]["height"]
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        raise ValueError("Invalid annotation dimensions")
    counts = Counter()
    objects = []
    for obj in annotation.get("objects", []):
        title = obj["classTitle"]
        if title not in title_to_id:
            raise ValueError(f"Unmapped annotation class: {title!r}")
        if obj.get("geometryType", "polygon") != "polygon":
            raise ValueError(f"Unsupported geometry for {title}: {obj.get('geometryType')}")
        binary = Image.new("L", (width, height))
        draw = ImageDraw.Draw(binary)
        draw.polygon(_ring(obj["points"]["exterior"], width, height), fill=1)
        for interior in obj["points"].get("interior", []):
            draw.polygon(_ring(interior, width, height), fill=0)
        selected = np.asarray(binary, dtype=bool)
        objects.append((title_to_id[title], selected, int(selected.sum())))
        counts[title] += 1
    # Paint larger objects first so a nested smaller object ends on top. Ties sort
    # by class and pixel content, never by the order objects appear in the file.
    objects.sort(key=lambda item: (-item[2], item[0], hashlib.sha256(np.packbits(item[1]).tobytes()).digest()))
    output = np.zeros((height, width), dtype=np.uint8)
    for class_id, selected, _ in objects:
        output[selected] = class_id
    ignored = np.zeros((height, width), dtype=bool)
    stats = {"nested_pixels": 0, "partial_overlap_pixels": 0}
    for i, (class_i, mask_i, area_i) in enumerate(objects):
        for class_j, mask_j, area_j in objects[:i]:  # area_j >= area_i
            if class_i == class_j:
                continue
            shared = mask_i & mask_j
            overlap = int(shared.sum())
            if not overlap:
                continue
            if policy == "nested" and overlap >= containment * area_i:
                stats["nested_pixels"] += overlap
            else:
                ignored |= shared
                stats["partial_overlap_pixels"] += overlap
    output[ignored] = IGNORE_INDEX
    return Image.fromarray(output), dict(counts), stats


def _aligned(image_path: Path, mask: Image.Image) -> tuple[Image.Image, Image.Image, dict]:
    with Image.open(image_path) as source:
        orientation = source.getexif().get(274, 1)
        original_size = source.size
        image = ImageOps.exif_transpose(source).convert("RGB")
    if orientation not in range(1, 9):
        raise ValueError(f"Invalid EXIF orientation {orientation}: {image_path}")
    frame = "stored_pixels"
    if orientation == 1:
        if mask.size != original_size:
            raise ValueError(f"Image/annotation dimension mismatch: {image_path}")
    elif original_size == image.size:
        # Mirrored, 180-degree and square frames are indistinguishable by dimensions.
        raise ValueError(f"Ambiguous EXIF annotation frame in {image_path}; review orientation before conversion")
    elif mask.size == original_size:
        method = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                  4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                  6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                  8: Image.Transpose.ROTATE_90}[orientation]
        mask = mask.transpose(method)
    elif mask.size == image.size:
        frame = "display_pixels"
    else:
        raise ValueError(f"Image/annotation dimension mismatch: {image_path}")
    assert image.size == mask.size
    return image, mask, {"original_width": original_size[0], "original_height": original_size[1],
                         "exif_orientation": orientation, "annotation_frame": frame,
                         "normalized_width": image.width, "normalized_height": image.height}


def _pixel_hash(image: Image.Image) -> str:
    return hashlib.sha256(_json_bytes(image.size) + image.tobytes()).hexdigest()


def _dhash(image: Image.Image) -> str:
    pixels = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS))
    bits = pixels[:, 1:] > pixels[:, :-1]
    return f"{int.from_bytes(np.packbits(bits).tobytes(), 'big'):016x}"


def _read_reserved(value: Iterable[str] | str | Path | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (str, Path)):
        path = Path(value)
        data = json.loads(path.read_text()) if path.suffix == ".json" else path.read_text().splitlines()
        if not isinstance(data, list):
            raise ValueError("Reserved IDs JSON must contain a list")
    else:
        data = list(value)
    if any(not isinstance(item, str) for item in data):
        raise ValueError("Reserved IDs must be strings")
    return {item.strip() for item in data if item.strip() and not item.startswith("#")}


def _read_groups(path: str | Path | None) -> list[dict]:
    if path is None:
        return []
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if "group_id" not in fields or not ({"sample_id", "content_sha256", "pixel_sha256"} & set(fields)):
            raise ValueError("Group CSV requires group_id and sample_id, content_sha256 or pixel_sha256")
        rows = list(reader)
    if any(not row["group_id"].strip() for row in rows):
        raise ValueError("Group IDs must not be empty")
    return sorted(rows, key=lambda row: _json_bytes(row))


def _assign_splits(records: list[dict], seed: int, val_fraction: float, test_fraction: float,
                   group_rows: list[dict], reserved: set[str]) -> dict:
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        parent[find(j)] = find(i)

    seen = {}
    lookup = {}
    for i, record in enumerate(records):
        for key in ("content_sha256", "pixel_sha256"):
            value = (key, record[key])
            if value in seen:
                union(i, seen[value])
            seen[value] = i
        for key in ("sample_id", "content_sha256", "pixel_sha256"):
            lookup.setdefault(record[key], []).append(i)
    matched_groups = {}
    for row in group_rows:
        keys = [row.get(key) for key in ("sample_id", "content_sha256", "pixel_sha256") if row.get(key)]
        if not keys or any(key not in lookup for key in keys):
            raise ValueError(f"Unknown image in group CSV: {keys}")
        # All supplied identifiers in one row must refer to a common sample.
        matches = set(lookup[keys[0]])
        for key in keys[1:]:
            matches.intersection_update(lookup[key])
        if not matches:
            raise ValueError(f"Conflicting identifiers in group CSV: {keys}")
        for i in matches:
            group = row["group_id"]
            if group in matched_groups:
                union(i, matched_groups[group])
            matched_groups[group] = i
    unknown = reserved - set(lookup)
    if unknown:
        raise ValueError(f"Unknown reserved IDs: {sorted(unknown)[:5]}")
    reserved_roots = {find(i) for key in reserved for i in lookup[key]}
    components = {}
    for i, record in enumerate(records):
        components.setdefault(find(i), []).append(record)
    groups = {}
    for root, rows in components.items():
        group_id = _digest(sorted({row["content_sha256"] for row in rows}))
        for row in rows:
            row["group_id"] = group_id
        groups[group_id] = "reserved" if root in reserved_roots else None
    available = sorted(key for key, split in groups.items() if split is None)
    random.Random(seed).shuffle(available)
    n_val, n_test = round(len(available) * val_fraction), round(len(available) * test_fraction)
    if n_val + n_test >= len(available) and available:
        raise ValueError("Split fractions leave no training groups")
    for i, key in enumerate(available):
        groups[key] = "test" if i < n_test else "val" if i < n_test + n_val else "train"
    for record in records:
        record["split"] = groups[record["group_id"]]
    return {"group_counts": dict(Counter(groups.values())),
            "grouping_policy": "union of file SHA256, decoded RGB SHA256 and supplied group CSV",
            "provided_group_rows": len(group_rows), "reserved_identifiers": len(reserved),
            "vehicle_independence_established": False,
            "limitations": "Exact/pixel duplicates are grouped. Vehicle/related-view identity is unknown unless supplied and reviewed."}


def _near_duplicates(records: list[dict]) -> list[dict]:
    representatives = {}
    for record in records:
        representatives.setdefault(record["pixel_sha256"], record)
    rows = list(representatives.values())
    hashes = [int(row["dhash"], 16) for row in rows]
    candidates = []
    for i, left in enumerate(rows):
        for j in range(i):
            distance = (hashes[i] ^ hashes[j]).bit_count()
            if distance <= 4:
                right = rows[j]
                candidates.append({"sample_id_a": left["sample_id"], "sample_id_b": right["sample_id"],
                                   "hamming_distance": distance,
                                   "same_group": left["group_id"] == right["group_id"],
                                   "cross_split": left["split"] != right["split"]})
    return candidates


def prepare_hitl(raw_root: str | Path, output_dir: str | Path, seed: int = 42,
                 val_fraction: float = .15, test_fraction: float = .15,
                 group_csv: str | Path | None = None,
                 reserved_ids: Iterable[str] | str | Path | None = None,
                 overlap_policy: str = "nested",
                 nested_containment: float = DEFAULT_NESTED_CONTAINMENT) -> dict:
    """Prepare BOTH tasks together; reuse identical versions, reject changed inputs.

    ``output_dir`` should be ``data/processed/hitl/<version>``. Group CSV columns
    are ``group_id`` plus ``sample_id`` or ``content_sha256``/``pixel_sha256``.
    Reserved IDs accept any of those identifiers, as a list or JSON/text file.
    Near-duplicate candidates are an audit aid, never an assertion of identity.
    """
    if not (0 <= val_fraction < 1 and 0 <= test_fraction < 1 and val_fraction + test_fraction < 1):
        raise ValueError("Split fractions must be nonnegative and sum to less than one")
    raw_root, output_dir = Path(raw_root).resolve(), Path(output_dir).resolve()
    if output_dir == raw_root or raw_root in output_dir.parents:
        raise ValueError("Derived outputs must not be written inside immutable raw inputs")
    taxonomy = _taxonomies()
    sources, samples = _discover(raw_root, taxonomy)
    # The acquisition step owns permission evidence; include it without inventing
    # a release date or treating annotation timestamps as acquisition timestamps.
    receipt_path = raw_root / "hitl-source-receipt.json"
    if not receipt_path.is_file() and (raw_root.parent / "hitl-source-receipt.json").is_file():
        receipt_path = raw_root.parent / "hitl-source-receipt.json"
    acquisition = ({"receipt_sha256": _file_hash(receipt_path),
                    "receipt": json.loads(receipt_path.read_text())}
                   if receipt_path.is_file() else {"status": "acquisition_receipt_not_present"})
    reserved, group_rows = _read_reserved(reserved_ids), _read_groups(group_csv)
    if overlap_policy not in OVERLAP_POLICIES:
        raise ValueError(f"Unknown overlap policy {overlap_policy!r}; choose {sorted(OVERLAP_POLICIES)}")
    policy_name, conversion_version = OVERLAP_POLICIES[overlap_policy]
    config = {"conversion_version": conversion_version, "seed": seed,
              "val_fraction": val_fraction, "test_fraction": test_fraction,
              "reserved_ids": sorted(reserved), "group_rows": group_rows,
              "taxonomy": taxonomy, "overlap_policy": policy_name,
              "acquisition": acquisition,
              "orientation_policy": "infer_frame_from_dimensions_or_reject_ambiguous_exif",
              "source_fingerprint": _digest(sources + [
                  {"task": row["task"], "image": str(row["image"].relative_to(raw_root)),
                   "annotation": str(row["annotation"].relative_to(raw_root)),
                   "content_sha256": row["content_sha256"], "annotation_sha256": row["annotation_sha256"]}
                  for row in samples])}
    if overlap_policy == "nested":
        # Absent for ignore_all so existing v1 preparations keep their configuration hash.
        config["nested_containment"] = nested_containment
    config_hash = _digest(config)
    if output_dir.exists():
        manifest = load_prepared(output_dir)
        if manifest["preparation_config_hash"] != config_hash:
            raise ValueError("Preparation inputs/configuration changed; select a new processed version directory")
        return manifest
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".hitl-", dir=output_dir.parent) as temporary:
        stage = Path(temporary) / "prepared"
        stage.mkdir()
        records, duplicates = [], []
        seen_ids = set()
        for sample in samples:
            task = sample["task"]
            annotation = json.loads(sample["annotation"].read_text())
            mask, object_counts, overlap_stats = rasterize_annotation(
                annotation, taxonomy[task]["title_to_id"], policy=overlap_policy, containment=nested_containment)
            image, mask, transform = _aligned(sample["image"], mask)
            sample_id = task + "-" + _digest([task, sample["content_sha256"], sample["annotation_sha256"]])[:24]
            if sample_id in seen_ids:
                duplicates.append({"sample_id": sample_id, "source": str(sample["annotation"].relative_to(raw_root)),
                                   "reason": "identical_image_and_annotation"})
                continue
            seen_ids.add(sample_id)
            image_rel, mask_rel = Path("images") / f"{sample_id}.png", Path("masks") / f"{sample_id}.png"
            for relative in (image_rel, mask_rel):
                (stage / relative).parent.mkdir(parents=True, exist_ok=True)
            image.save(stage / image_rel)
            mask.save(stage / mask_rel)
            pixels = np.asarray(mask)
            record = {"sample_id": sample_id, "task": task,
                      "image_path": str(output_dir / image_rel), "mask_path": str(output_dir / mask_rel),
                      "image_relative_path": str(image_rel), "mask_relative_path": str(mask_rel),
                      "source_image": str(sample["image"].relative_to(raw_root)),
                      "source_annotation": str(sample["annotation"].relative_to(raw_root)),
                      "content_sha256": sample["content_sha256"], "annotation_sha256": sample["annotation_sha256"],
                      "image_sha256": _file_hash(stage / image_rel), "mask_sha256": _file_hash(stage / mask_rel),
                      "pixel_sha256": _pixel_hash(image), "dhash": _dhash(image),
                      "width": image.width, "height": image.height, "transform": transform,
                      "pixel_counts": np.bincount(pixels[pixels != IGNORE_INDEX], minlength=len(taxonomy[task]["class_names"])).tolist(),
                      "ignored_pixels": int(np.count_nonzero(pixels == IGNORE_INDEX)),
                      "object_counts": object_counts, "provenance": "real"}
            if overlap_policy == "nested":
                record["overlap_stats"] = overlap_stats
            records.append(record)
        records.sort(key=lambda row: row["sample_id"])
        split = _assign_splits(records, seed, val_fraction, test_fraction, group_rows, reserved)
        candidates = _near_duplicates(records)
        (stage / "near_duplicate_candidates.json").write_text(json.dumps(candidates, indent=2) + "\n")
        split_hashes, counts = {}, {}
        for task in taxonomy:
            counts[task] = {}
            for partition in ("train", "val", "test", "reserved"):
                rows = [row for row in records if row["task"] == task and row["split"] == partition]
                split_path = Path("splits") / task / f"{partition}.jsonl"
                (stage / split_path).parent.mkdir(parents=True, exist_ok=True)
                # Portable split membership is independent of local absolute paths.
                payload = b"".join(_json_bytes({key: row[key] for key in (
                    "sample_id", "group_id", "content_sha256", "annotation_sha256", "pixel_sha256", "split")}) + b"\n" for row in rows)
                (stage / split_path).write_bytes(payload)
                split_hashes[str(split_path)] = hashlib.sha256(payload).hexdigest()
                totals = [sum(row["pixel_counts"][i] for row in rows) for i in range(len(taxonomy[task]["class_names"]))]
                objects = Counter()
                for row in rows:
                    objects.update(row["object_counts"])
                counts[task][partition] = {"images": len(rows), "groups": len({row["group_id"] for row in rows}),
                                           "pixel_counts": totals, "object_counts": dict(objects),
                                           "ignored_pixels": sum(row["ignored_pixels"] for row in rows)}
        manifest = {"schema_version": 1, "conversion_version": conversion_version,
                    "preparation_config": config, "preparation_config_hash": config_hash,
                    "class_names": {task: entry["class_names"] for task, entry in taxonomy.items()},
                    "taxonomy": taxonomy, "records": records, "sources": sources, "counts": counts,
                    "split": split, "split_hashes": split_hashes, "ignore_index": IGNORE_INDEX,
                    "raw_root": str(raw_root), "output_dir": str(output_dir), "excluded": duplicates,
                    "source_release": "HITL Supervisely export; exact raw file hashes recorded",
                    "acquisition": acquisition,
                    "license_evidence": "See acquisition report/catalogue; converter does not infer permission from files",
                    "source_mask_comparison": "not_performed: masks_machine palette semantics not established; inspect overlays before fitting",
                    "near_duplicate_audit": {"method": "64-bit grayscale dHash, Hamming distance <= 4",
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


def _manifest_hash(manifest: dict) -> str:
    # Local locators are rebound on load, so copying data to another host retains identity.
    portable = {key: value for key, value in manifest.items() if key not in ("manifest_hash", "raw_root", "output_dir")}
    portable["records"] = [{key: value for key, value in row.items() if key not in ("image_path", "mask_path")}
                           for row in manifest["records"]]
    return _digest(portable)


def load_prepared(path: str | Path, task: str | None = None) -> dict:
    """Validate frozen metadata/artifact hashes; optionally filter a task.

    Shared manifests have a ``class_names`` dictionary. Task manifests have a
    ``class_names`` list and retain the SAME union-level ``manifest_hash``.
    Derived paths are rebound, allowing prepared directories to move machines.
    """
    path = Path(path).resolve()
    root = path.parent if path.name == "manifest.json" else path
    manifest = json.loads((root / "manifest.json").read_text())
    if _manifest_hash(manifest) != manifest["manifest_hash"]:
        raise ValueError("Prepared manifest hash mismatch")
    for relative, expected in manifest["split_hashes"].items():
        if _file_hash(root / relative) != expected:
            raise ValueError(f"Frozen split hash mismatch: {relative}")
    audit = manifest["near_duplicate_audit"]
    if _file_hash(root / audit["path"]) != audit["sha256"]:
        raise ValueError("Near duplicate audit hash mismatch")
    for row in manifest["records"]:
        for kind in ("image", "mask"):
            actual = root / row[f"{kind}_relative_path"]
            if _file_hash(actual) != row[f"{kind}_sha256"]:
                raise ValueError(f"Prepared {kind} hash mismatch: {row['sample_id']}")
            row[f"{kind}_path"] = str(actual)
    manifest["output_dir"] = str(root)
    if task is not None:
        if task not in manifest["class_names"]:
            raise ValueError(f"Unknown task {task!r}; choose parts or damage")
        manifest["task"] = task
        manifest["class_names"] = manifest["class_names"][task]
        manifest["records"] = [row for row in manifest["records"] if row["task"] == task]
    return manifest
