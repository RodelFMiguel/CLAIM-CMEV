"""CarDD COCO conversion: geometry, official splits, duplicates and harness integration (synthetic data)."""
import importlib.util
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from pipelines.vision import cardd_data as cardd
from pipelines.vision import training as t

CATEGORIES = [{"id": i, "name": name} for i, name in enumerate(
    ["dent", "scratch", "crack", "glass shatter", "lamp broken", "tire flat"], 1)]
CLASS_IDS = {c["id"]: c["id"] for c in CATEGORIES}  # COCO ids 1..6 map to CarDD ids 1..6


def _rle_string(counts):
    """pycocotools rleToString, used to round-trip the decoder."""
    out = []
    for i, x in enumerate(counts):
        if i > 2:
            x -= counts[i - 2]
        more = True
        while more:
            c = x & 0x1F
            x >>= 5
            more = (x != -1) if (c & 0x10) else (x != 0)
            if more:
                c |= 0x20
            out.append(chr(c + 48))
    return "".join(out)


def _box(x0, y0, x1, y1):
    return [[x0, y0, x1, y0, x1, y1, x0, y1]]


def _rle_counts(mask):
    flat = mask.T.ravel()  # column-major
    counts, value, run = [], False, 0
    for pixel in flat:
        if bool(pixel) == value:
            run += 1
        else:
            counts.append(run)
            value, run = not value, 1
    counts.append(run)
    return counts


def test_rle_list_and_compressed_string_decode_to_the_same_mask():
    mask = np.zeros((5, 7), dtype=bool)
    mask[1:4, 2:6] = True
    mask[0, 0] = True
    counts = _rle_counts(mask)
    np.testing.assert_array_equal(cardd.decode_rle({"counts": counts, "size": [5, 7]}, 5, 7), mask)
    np.testing.assert_array_equal(cardd.decode_rle({"counts": _rle_string(counts), "size": [5, 7]}, 5, 7), mask)
    with pytest.raises(ValueError, match="size"):
        cardd.decode_rle({"counts": counts, "size": [7, 5]}, 5, 7)


def test_smaller_instance_wins_order_independently_and_crowd_is_ignored():
    dent = {"id": 1, "category_id": 1, "segmentation": _box(0, 0, 10, 10), "iscrowd": 0}
    crack = {"id": 2, "category_id": 3, "segmentation": _box(2, 2, 5, 5), "iscrowd": 0}
    crowd = {"id": 3, "category_id": 2, "segmentation": _box(12, 0, 16, 4), "iscrowd": 1}
    mask, stats = cardd.rasterize_coco([dent, crack, crowd], 16, 12, CLASS_IDS)
    labels = np.asarray(mask)
    assert labels[3, 3] == 3  # small crack stays visible on top of the large dent
    assert labels[8, 8] == 1
    assert labels[2, 14] == 255 and labels[11, 15] == 0
    assert stats["different_class_overlap_pixels"] > 0 and stats["crowd_instances"] == 1
    reordered, _ = cardd.rasterize_coco([crowd, crack, dent], 16, 12, CLASS_IDS)
    np.testing.assert_array_equal(labels, np.asarray(reordered))
    with pytest.raises(ValueError, match="outside"):
        cardd.rasterize_coco([{"id": 4, "category_id": 1, "segmentation": _box(0, 0, 40, 4)}], 16, 12, CLASS_IDS)
    with pytest.raises(ValueError, match="category_id"):
        cardd.rasterize_coco([{"id": 5, "category_id": 9, "segmentation": _box(0, 0, 4, 4)}], 16, 12, CLASS_IDS)


def _write_split(root, split, images, annotations):
    folder = root / f"{split}2017"
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for image_id, (name, pixels) in images.items():
        if pixels is not None:
            Image.fromarray(pixels).save(folder / name)
        entries.append({"id": image_id, "file_name": name, "width": 24, "height": 16})
    (root / "annotations").mkdir(exist_ok=True)
    (root / "annotations" / f"instances_{split}2017.json").write_text(json.dumps(
        {"images": entries, "annotations": annotations, "categories": CATEGORIES}))


@pytest.fixture
def raw(tmp_path):
    root = tmp_path / "CarDD_release" / "CarDD_COCO"
    rng = np.random.default_rng(0)
    pictures = {i: rng.integers(0, 256, (16, 24, 3), dtype=np.uint8) for i in range(12)}
    dent_mask = np.zeros((16, 24), dtype=bool)
    dent_mask[4:12, 6:18] = True
    train_ann = [{"id": 1, "image_id": 1, "category_id": 1, "segmentation": _box(2, 2, 20, 14), "iscrowd": 0},
                 {"id": 2, "image_id": 1, "category_id": 3, "segmentation": _box(4, 4, 8, 8), "iscrowd": 0},
                 {"id": 3, "image_id": 2, "category_id": 2,
                  "segmentation": {"counts": _rle_counts(dent_mask), "size": [16, 24]}, "iscrowd": 0},
                 {"id": 4, "image_id": 3, "category_id": 6,
                  "segmentation": {"counts": _rle_string(_rle_counts(dent_mask)), "size": [16, 24]}, "iscrowd": 0}]
    train_ann += [{"id": 10 + i, "image_id": i, "category_id": 1 + i % 6,
                   "segmentation": _box(1, 1, 9, 9), "iscrowd": 0} for i in range(4, 8)]
    _write_split(root, "train", {i: (f"{i:06d}.png", pictures[i]) for i in range(1, 8)}, train_ann)
    _write_split(root, "val", {8: ("000008.png", pictures[8]), 9: ("000009.png", pictures[9])},
                 [{"id": 20, "image_id": 8, "category_id": 4, "segmentation": _box(0, 0, 10, 10), "iscrowd": 0},
                  {"id": 21, "image_id": 9, "category_id": 5, "segmentation": _box(3, 3, 12, 12), "iscrowd": 0}])
    # Test split: one photograph byte-identical to training image 1, one missing file.
    _write_split(root, "test", {10: ("000010.png", pictures[10]), 11: ("000011.png", pictures[1]),
                                12: ("000012.png", None)},
                 [{"id": 30, "image_id": 10, "category_id": 1, "segmentation": _box(0, 0, 6, 6), "iscrowd": 0},
                  {"id": 31, "image_id": 11, "category_id": 2, "segmentation": _box(0, 0, 6, 6), "iscrowd": 0},
                  {"id": 32, "image_id": 12, "category_id": 2, "segmentation": _box(0, 0, 6, 6), "iscrowd": 0}])
    return tmp_path / "CarDD_release"


def test_inspect_reports_splits_formats_and_missing_files(raw):
    summary = cardd.inspect_cardd(raw)
    assert set(summary["splits"]) == {"train", "val", "test"}
    assert summary["splits"]["train"]["images"] == 7
    assert summary["splits"]["train"]["segmentation_formats"] == {"list": 6, "dict": 2}
    assert summary["splits"]["test"]["missing_or_ambiguous_images"] == 1


def test_prepare_uses_official_splits_drops_cross_split_duplicates_and_reloads(raw, tmp_path):
    manifest = cardd.prepare_cardd(raw, tmp_path / "prepared", progress_every=0)
    names = manifest["class_names"][cardd.TASK]
    assert names == ["background", "dent", "scratch", "crack", "glass-shatter", "lamp-broken", "tire-flat"]
    assert manifest["split"]["image_counts"] == {"train": 7, "val": 2, "test": 1}
    reasons = dict(Counter(row["reason"] for row in manifest["excluded"]))
    assert reasons == {"image_not_found": 1, "duplicate_of_train_image": 1}
    first = next(r for r in manifest["records"] if r["source_image"].endswith("train2017/000001.png"))
    labels = np.asarray(Image.open(first["mask_path"]))
    assert labels[6, 6] == 3 and labels[12, 18] == 1  # crack inside dent
    assert first["image_sha256"] == first["content_sha256"]  # unchanged originals are byte copies
    t.task_manifest(manifest, cardd.TASK)  # no image spans two splits
    again = cardd.prepare_cardd(raw, tmp_path / "prepared", progress_every=0)
    assert again["manifest_hash"] == manifest["manifest_hash"]
    loaded = cardd.load_prepared(tmp_path / "prepared", cardd.TASK)
    assert len(loaded["records"]) == 10
    with pytest.raises(ValueError, match="new processed version"):
        cardd.prepare_cardd(raw, tmp_path / "prepared", max_side=64, progress_every=0)
    Path(first["mask_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="mask hash mismatch"):
        cardd.load_prepared(tmp_path / "prepared")


def test_max_side_downscales_images_and_masks(raw, tmp_path):
    manifest = cardd.prepare_cardd(raw, tmp_path / "small", max_side=16, progress_every=0)
    record = manifest["records"][0]
    assert record["image_relative_path"].endswith(".png")
    assert (record["width"], record["height"]) == (16, 11)  # 24x16 scaled by 16/24
    assert record["transform"]["downscaled_to"] == [16, 11]
    labels = np.asarray(Image.open(record["mask_path"]))
    assert labels.shape == (11, 16) and set(np.unique(labels)) <= set(range(7)) | {255}
    with pytest.raises(ValueError, match="at least 16"):
        cardd.prepare_cardd(raw, tmp_path / "tiny", max_side=8)


def test_wrong_categories_or_missing_split_fail_clearly(raw, tmp_path):
    test_file = raw / "CarDD_COCO/annotations/instances_test2017.json"
    data = json.loads(test_file.read_text())
    data["categories"][0]["name"] = "rust"
    test_file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="not the six CarDD categories"):
        cardd.inspect_cardd(raw)
    test_file.unlink()
    with pytest.raises(ValueError, match="missing for split"):
        cardd.prepare_cardd(raw, tmp_path / "prepared")


TORCH = importlib.util.find_spec("torch") is not None


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_damage_cardd_task_trains_into_its_own_artifact_folder(raw, tmp_path):
    import torch

    def _tiny_model(config, classes, **kwargs):
        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.network = torch.nn.Conv2d(3, 4, 1)
                self.head = torch.nn.Conv2d(4, len(classes), 1)
                self.architecture_config = {"fixture": True}

            def encoder_parameters(self):
                return list(self.network.parameters())

            def head_parameters(self):
                return list(self.head.parameters())

            def forward(self, pixels):
                return self.head(self.network(pixels).relu())
        return Tiny()
    manifest = cardd.prepare_cardd(raw, tmp_path / "prepared", progress_every=0)
    config = t.TrainingConfig(task="damage_cardd", epochs=1, image_size=32, batch_size=2, class_weighting="sqrt_inverse",
                              artifacts_root=str(tmp_path / "artifacts"), run_id="cardd", device="cpu", pretrained=False)
    with patch.object(t, "build_model", side_effect=_tiny_model):
        record = t.train(manifest, config)
        evaluated = t.evaluate_run(record["run_dir"], manifest, split="val", device="cpu")
    assert Path(record["run_dir"]).parent.name == "damage-cardd"
    assert record["dataset"] == "CarDD" and len(record["class_names"]) == 7
    assert evaluated["image_count"] == 2
