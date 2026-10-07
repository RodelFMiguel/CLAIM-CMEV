"""Portable HITL part splits: locating the data, split records without machine paths, mask verification.

Specification: docs/specs/model_training_specification.md section 7.1 and
docs/specs/module-01-vehicle-part-segmentation.md ("Split manifests cover both HITL subsets").
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from claim_cmev.taxonomy import load_parts
from pipelines.vision import build_splits, convert_hitl, splits
from pipelines.vision.dataset import HitlPartsDataset

REPO_ROOT = Path(__file__).resolve().parents[2]
SPLITS = REPO_ROOT / "data" / "splits" / "parts"
PARTITIONS = ("train", "val", "test")


def _png(array: np.ndarray, **options) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, format="PNG", **options)
    return buf.getvalue()


def _records(split_dir: Path, partition: str) -> list[dict]:
    return [json.loads(line) for line in (split_dir / f"{partition}.jsonl").read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# Locating the HITL exports
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parent", ["", "CarPartsAndCarDamages"], ids=["directly-under-raw", "one-folder-down"])
def test_hitl_export_is_found_where_the_archive_was_unpacked(tmp_path: Path, monkeypatch, parent: str):
    monkeypatch.delenv("CMEV_HITL_PARTS_DIR", raising=False)
    export = tmp_path / parent / "Car damages dataset"  # the parts export: the publisher's names are swapped
    export.mkdir(parents=True)
    (export / "meta.json").write_text("{}", encoding="utf-8")

    assert splits.find_hitl_folder("parts", raw_root=tmp_path) == export


def test_environment_names_the_hitl_export(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CMEV_HITL_DAMAGE_DIR", str(tmp_path / "elsewhere"))
    assert splits.find_hitl_folder("damage", raw_root=tmp_path / "raw") == tmp_path / "elsewhere"


def test_a_missing_hitl_export_is_an_error_that_says_how_to_point_at_it(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CMEV_HITL_DAMAGE_DIR", raising=False)
    with pytest.raises(FileNotFoundError, match="CMEV_HITL_DAMAGE_DIR"):
        splits.find_hitl_folder("damage", raw_root=tmp_path)


# ---------------------------------------------------------------------------
# Mask hash
# ---------------------------------------------------------------------------

def test_mask_hash_depends_on_the_pixels_and_not_on_the_png_encoding():
    mask = np.arange(48, dtype=np.uint8).reshape(6, 8) % 22
    plain, optimised = _png(mask, compress_level=1), _png(mask, optimize=True)
    assert plain != optimised

    hashes = {splits.mask_pixel_sha256(np.asarray(Image.open(io.BytesIO(data)))) for data in (plain, optimised)}

    assert len(hashes) == 1
    changed = mask.copy()
    changed[0, 0] += 1
    assert splits.mask_pixel_sha256(changed) not in hashes
    assert splits.mask_pixel_sha256(mask.reshape(8, 6)) not in hashes


# ---------------------------------------------------------------------------
# Conversion and split records
# ---------------------------------------------------------------------------

def _parts_export(folder: Path) -> Path:
    """A one-image HITL parts export: the real class titles, one front door polygon."""
    titles = [part["hitl_title"] for part in load_parts().meta["parts"]]
    (folder / "File1" / "ann").mkdir(parents=True)
    (folder / "File1" / "img").mkdir()
    (folder / "meta.json").write_text(json.dumps(
        {"classes": [{"title": title, "shape": "polygon", "color": "#FF0000"} for title in titles]}), encoding="utf-8")
    (folder / "File1" / "img" / "car 1.png").write_bytes(_png(np.full((40, 60, 3), 128, dtype=np.uint8)))
    door = next(t for t in titles if t.lower() == "front-door")
    (folder / "File1" / "ann" / "car 1.png.json").write_text(json.dumps({
        "size": {"height": 40, "width": 60},
        "objects": [{"classTitle": door, "geometryType": "polygon",
                     "points": {"exterior": [[5, 5], [30, 5], [30, 25], [5, 25]], "interior": []}}],
    }), encoding="utf-8")
    return folder


def test_conversion_index_names_files_relative_to_their_folders(tmp_path: Path):
    export, labels = _parts_export(tmp_path / "anywhere" / "Car damages dataset"), tmp_path / "labels"

    convert_hitl.convert_dataset(export, labels, sample_verify=0)

    [record] = [json.loads(line) for line in (labels / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert record["image_relative_path"] == "File1/img/car 1.png"
    assert record["mask_relative_path"] == "masks/car 1.png"
    assert record["mask_pixel_sha256"] == splits.mask_pixel_sha256(np.asarray(Image.open(labels / "masks" / "car 1.png")))
    assert str(tmp_path) not in json.dumps(record)


def _index_record(index: int) -> dict:
    return {"image_name": f"img_{index}.png", "image_relative_path": f"File1/img/img_{index}.png",
            "image_sha256": f"hash_{index}", "mask_name": f"img_{index}.png",
            "mask_relative_path": f"masks/img_{index}.png", "mask_pixel_sha256": f"pixels_{index}", "width": 640,
            "height": 480, "class_pixel_counts": {"hood": 100}, "class_object_counts": {"hood": 1}}


def test_split_records_carry_relative_paths_and_the_pixel_hash():
    records = [_index_record(i) for i in range(20)]

    built = build_splits.create_splits(records, [r["image_sha256"] for r in records], set(), seed=42)

    entry = built["train"][0]
    index = int(entry["example_id"].removeprefix("img_").removesuffix(".png"))
    assert (entry["image_relative_path"], entry["mask_relative_path"], entry["mask_pixel_sha256"]) == \
        (f"File1/img/img_{index}.png", f"masks/img_{index}.png", f"pixels_{index}")
    assert {"source_path", "mask_path", "mask_sha256"}.isdisjoint(entry)


def test_split_builder_refuses_a_membership_that_differs_from_the_named_split(tmp_path: Path):
    """Republishing 0.1.0 in a new format must not move a single image between partitions."""
    records = [_index_record(i) for i in range(40)]
    (tmp_path / "index.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    build_splits.build_and_save_splits(tmp_path / "index.jsonl", None, tmp_path / "first", seed=1, split_version="1.0.0")

    same = build_splits.build_and_save_splits(tmp_path / "index.jsonl", None, tmp_path / "again", seed=1,
                                              split_version="1.0.1", same_membership_as=tmp_path / "first")
    assert same["same_membership_as"]["split_version"] == "1.0.0"
    with pytest.raises(ValueError, match="membership"):
        build_splits.build_and_save_splits(tmp_path / "index.jsonl", None, tmp_path / "other", seed=2,
                                           split_version="1.0.1", same_membership_as=tmp_path / "first")


# ---------------------------------------------------------------------------
# The published split
# ---------------------------------------------------------------------------

def test_published_split_has_no_machine_paths_and_matches_its_manifest():
    manifest = json.loads((SPLITS / "0.1.1" / "split_manifest.json").read_text(encoding="utf-8"))
    assert manifest["split_version"] == "0.1.1"
    for partition in PARTITIONS:
        path = SPLITS / "0.1.1" / f"{partition}.jsonl"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["file_hashes"][f"{partition}.jsonl"]
        for record in _records(SPLITS / "0.1.1", partition):
            assert record["image_relative_path"].startswith("File1/img/")
            assert record["mask_relative_path"].startswith("masks/")
            assert len(record["mask_pixel_sha256"]) == 64
            assert {"source_path", "mask_path", "mask_sha256"}.isdisjoint(record)


def test_published_split_keeps_every_image_in_its_0_1_0_partition():
    def membership(version: str) -> dict[str, tuple]:
        return {r["example_id"]: (partition, r["content_sha256"], r["class_pixel_counts"])
                for partition in PARTITIONS for r in _records(SPLITS / version, partition)}

    assert membership("0.1.1") == membership("0.1.0")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _portable_split(tmp_path: Path, mask: np.ndarray, pixel_hash: str | None = None) -> Path:
    images, labels = tmp_path / "export", tmp_path / "labels"
    (images / "File1" / "img").mkdir(parents=True)
    (labels / "masks").mkdir(parents=True)
    (images / "File1" / "img" / "car 1.png").write_bytes(_png(np.full(mask.shape + (3,), 128, dtype=np.uint8)))
    (labels / "masks" / "car 1.png").write_bytes(_png(mask))
    record = {"example_id": "car 1.png", "image_relative_path": "File1/img/car 1.png",
              "mask_relative_path": "masks/car 1.png", "width": mask.shape[1], "height": mask.shape[0],
              "mask_pixel_sha256": pixel_hash or splits.mask_pixel_sha256(mask)}
    (tmp_path / "train.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return tmp_path / "train.jsonl"


def test_dataset_reads_portable_records_from_the_given_folders(tmp_path: Path):
    split = _portable_split(tmp_path, np.full((80, 100), 5, dtype=np.uint8))

    dataset = HitlPartsDataset(split, image_size=64, images_root=tmp_path / "export", labels_root=tmp_path / "labels",
                               verify=True)

    sample = dataset[0]
    assert sample["pixel_values"].shape == (3, 64, 64)
    assert (sample["labels"] == 5).sum() > 0


def test_dataset_says_which_files_are_missing_and_how_to_get_them(tmp_path: Path):
    split = _portable_split(tmp_path, np.full((80, 100), 5, dtype=np.uint8))
    (tmp_path / "labels" / "masks" / "car 1.png").unlink()

    with pytest.raises(FileNotFoundError, match="convert_hitl"):
        HitlPartsDataset(split, images_root=tmp_path / "export", labels_root=tmp_path / "labels")


def test_dataset_refuses_a_mask_whose_pixels_are_not_the_split_s(tmp_path: Path):
    split = _portable_split(tmp_path, np.full((80, 100), 5, dtype=np.uint8), pixel_hash="0" * 64)

    with pytest.raises(ValueError, match="car 1.png"):
        HitlPartsDataset(split, images_root=tmp_path / "export", labels_root=tmp_path / "labels", verify=True)


def test_yolo_export_reads_portable_split_records(tmp_path: Path, monkeypatch):
    from pipelines.vision import export_yolo_parts

    export, labels = _parts_export(tmp_path / "anywhere" / "Car damages dataset"), tmp_path / "labels"
    convert_hitl.convert_dataset(export, labels, sample_verify=0)
    [record] = [json.loads(line) for line in (labels / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    entry = {"example_id": record["image_name"], "image_relative_path": record["image_relative_path"],
             "mask_relative_path": record["mask_relative_path"]}
    for partition in PARTITIONS:
        (tmp_path / f"{partition}.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    monkeypatch.setenv("CMEV_HITL_PARTS_DIR", str(export))

    export_yolo_parts.main(split_dir=tmp_path, output_dir=tmp_path / "yolo")

    [label_line] = (tmp_path / "yolo" / "labels" / "train" / "car 1.txt").read_text(encoding="utf-8").splitlines()
    assert len(label_line.split()) == 1 + 2 * 4  # class id, then the four polygon corners
    assert (tmp_path / "yolo" / "images" / "train" / "car 1.png").exists()
