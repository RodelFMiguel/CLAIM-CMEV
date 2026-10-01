"""Leakage, annotation geometry and immutable preparation regression checks."""
import csv
import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# pipelines is a repository-level namespace, not an installed runtime dependency.
_PATH = Path(__file__).resolve().parents[2] / "pipelines/vision/hitl_data.py"
_SPEC = importlib.util.spec_from_file_location("hitl_data", _PATH)
data = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(data)


def obj(title, exterior, interior=None):
    return {"classTitle": title, "geometryType": "polygon",
            "points": {"exterior": exterior, "interior": interior or []}}


@pytest.fixture
def raw(tmp_path):
    root = tmp_path / "raw"
    taxonomy = data._taxonomies()
    for task, swapped_name in (("parts", "Car damages dataset"), ("damage", "Car parts dataset")):
        folder = root / swapped_name
        (folder / "File1/ann").mkdir(parents=True)
        (folder / "File1/img").mkdir()
        (folder / "meta.json").write_text(json.dumps({"classes": [
            {"title": title} for title in taxonomy[task]["title_to_id"]]}))
        for i in range(12):
            # Byte-identical photographs occur across the task subsets.
            pixels = np.random.default_rng(i).integers(0, 256, (12, 16, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(folder / f"File1/img/{i}.png")
            title = next(iter(taxonomy[task]["title_to_id"]))
            annotation = {"size": {"width": 16, "height": 12},
                          "objects": [obj(title, [[1, 1], [10, 1], [10, 8], [1, 8]])]}
            (folder / f"File1/ann/{i}.png.json").write_text(json.dumps(annotation))
    return root


def test_holes_overlap_and_background_are_order_independent():
    a = obj("a", [[1, 1], [7, 1], [7, 7], [1, 7]], [[[3, 3], [5, 3], [5, 5], [3, 5]]])
    b = obj("b", [[6, 0], [9, 0], [9, 9], [6, 9]])
    annotation = {"size": {"width": 10, "height": 10}, "objects": [a, b]}
    mask, counts, stats = data.rasterize_annotation(annotation, {"a": 1, "b": 2})
    pixels = np.asarray(mask)
    assert pixels[4, 4] == 0  # interior is a hole
    assert pixels[2, 2] == 1
    assert pixels[4, 6] == 255  # no arbitrary last-polygon winner
    assert pixels[8, 8] == 2
    assert pixels[0, 0] == 0
    annotation["objects"].reverse()
    other, _, _ = data.rasterize_annotation(annotation, {"a": 1, "b": 2})
    np.testing.assert_array_equal(pixels, np.asarray(other))
    assert counts == {"a": 1, "b": 1}
    assert stats["nested_pixels"] == 0 and stats["partial_overlap_pixels"] > 0


def _door_and_window():
    door = obj("door", [[0, 0], [9, 0], [9, 9], [0, 9]])
    window = obj("window", [[2, 2], [5, 2], [5, 5], [2, 5]])  # entirely inside the door
    return {"size": {"width": 10, "height": 10}, "objects": [window, door]}


def test_nested_polygon_keeps_its_pixels_in_either_order():
    annotation = _door_and_window()
    mask, _, stats = data.rasterize_annotation(annotation, {"door": 1, "window": 2})
    pixels = np.asarray(mask)
    assert pixels[3, 3] == 2  # window inside the door is kept, not ignored
    assert pixels[7, 7] == 1
    assert not np.any(pixels == 255)
    assert stats["nested_pixels"] > 0 and stats["partial_overlap_pixels"] == 0
    annotation["objects"].reverse()
    other, _, _ = data.rasterize_annotation(annotation, {"door": 1, "window": 2})
    np.testing.assert_array_equal(pixels, np.asarray(other))


def test_ignore_all_policy_reproduces_v1_and_threshold_is_respected():
    annotation = _door_and_window()
    legacy, _, _ = data.rasterize_annotation(annotation, {"door": 1, "window": 2}, policy="ignore_all")
    assert np.asarray(legacy)[3, 3] == 255
    # Window half outside the door: below an 0.8 containment threshold, so ignored.
    annotation["objects"][0]["points"]["exterior"] = [[6, 2], [10, 2], [10, 5], [6, 5]]
    annotation["objects"][1]["points"]["exterior"] = [[0, 0], [8, 0], [8, 9], [0, 9]]
    partial, _, _ = data.rasterize_annotation(annotation, {"door": 1, "window": 2})
    assert np.asarray(partial)[3, 7] == 255
    assert np.asarray(partial)[3, 9] == 2
    loose, _, _ = data.rasterize_annotation(annotation, {"door": 1, "window": 2}, containment=0.3)
    assert np.asarray(loose)[3, 7] == 2
    with pytest.raises(ValueError, match="overlap policy"):
        data.rasterize_annotation(annotation, {"door": 1, "window": 2}, policy="last_wins")


def test_union_split_and_canonical_taxonomy_ignore_swapped_names(raw, tmp_path):
    manifest = data.prepare_hitl(raw, tmp_path / "prepared", seed=42)
    assert len(manifest["records"]) == 24
    assert len(manifest["class_names"]["parts"]) == 22
    assert len(manifest["class_names"]["damage"]) == 9
    assert "licence-plate" in manifest["class_names"]["parts"]
    for key in {r["content_sha256"] for r in manifest["records"]}:
        shared = [r for r in manifest["records"] if r["content_sha256"] == key]
        assert {r["task"] for r in shared} == {"parts", "damage"}
        assert len({r["split"] for r in shared}) == 1
        assert len({r["group_id"] for r in shared}) == 1
    assert set(r["split"] for r in manifest["records"]) == {"train", "val", "test"}
    again = data.prepare_hitl(raw, tmp_path / "prepared", seed=42)
    assert again["manifest_hash"] == manifest["manifest_hash"]
    task = data.load_prepared(tmp_path / "prepared", "parts")
    assert task["class_names"] == manifest["class_names"]["parts"]
    assert len(task["records"]) == 12


def test_pixel_identical_different_encoding_kept_together(raw, tmp_path):
    path = raw / "Car parts dataset/File1/img/0.png"
    with Image.open(path) as image:
        image.save(path, compress_level=0)
    manifest = data.prepare_hitl(raw, tmp_path / "prepared")
    pair = [r for r in manifest["records"] if r["source_image"].endswith("/0.png")]
    assert len({r["content_sha256"] for r in pair}) == 2
    assert len({r["pixel_sha256"] for r in pair}) == 1
    assert len({r["group_id"] for r in pair}) == 1
    assert len({r["split"] for r in pair}) == 1


def test_reserved_and_supplied_groups_propagate_across_tasks(raw, tmp_path):
    hashes = [data._file_hash(raw / f"Car parts dataset/File1/img/{i}.png") for i in range(2)]
    group_csv = tmp_path / "groups.csv"
    with group_csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["content_sha256", "group_id"])
        writer.writeheader()
        for value in hashes:
            writer.writerow({"content_sha256": value, "group_id": "reviewed-vehicle-1"})
    manifest = data.prepare_hitl(raw, tmp_path / "prepared", group_csv=group_csv, reserved_ids=[hashes[0]])
    reserved = [row for row in manifest["records"] if row["split"] == "reserved"]
    assert len(reserved) == 4
    assert {row["content_sha256"] for row in reserved} == set(hashes)
    assert len({row["group_id"] for row in reserved}) == 1


def test_overlap_policy_is_versioned_and_legacy_config_is_unchanged(raw, tmp_path):
    nested = data.prepare_hitl(raw, tmp_path / "nested")
    assert nested["conversion_version"] == data.CONVERSION_VERSION
    assert nested["preparation_config"]["overlap_policy"] == "nested_smaller_wins_partial_overlap_ignore_255"
    assert nested["preparation_config"]["nested_containment"] == data.DEFAULT_NESTED_CONTAINMENT
    assert all("overlap_stats" in row for row in nested["records"])
    legacy = data.prepare_hitl(raw, tmp_path / "legacy", overlap_policy="ignore_all")
    assert legacy["conversion_version"] == "hitl-polygons-1.0.0"
    assert legacy["preparation_config"]["overlap_policy"] == "different_class_overlap_ignore_255"
    assert "nested_containment" not in legacy["preparation_config"]
    assert not any("overlap_stats" in row for row in legacy["records"])
    with pytest.raises(ValueError, match="new processed version"):
        data.prepare_hitl(raw, tmp_path / "legacy")  # default nested policy differs
    with pytest.raises(ValueError, match="new processed version"):
        data.prepare_hitl(raw, tmp_path / "nested", nested_containment=0.5)


def test_changed_config_and_tampered_masks_fail_closed(raw, tmp_path):
    output = tmp_path / "prepared"
    manifest = data.prepare_hitl(raw, output)
    with pytest.raises(ValueError, match="new processed version"):
        data.prepare_hitl(raw, output, seed=43)
    mask = Path(manifest["records"][0]["mask_path"])
    mask.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="mask hash mismatch"):
        data.load_prepared(output)


def test_portable_manifest_and_frozen_split_hashes(raw, tmp_path):
    manifest = data.prepare_hitl(raw, tmp_path / "prepared")
    shutil.move(tmp_path / "prepared", tmp_path / "moved")
    loaded = data.load_prepared(tmp_path / "moved")
    assert loaded["manifest_hash"] == manifest["manifest_hash"]
    assert all(Path(row["image_path"]).is_file() for row in loaded["records"])
    split = tmp_path / "moved" / next(iter(loaded["split_hashes"]))
    split.write_text("tampered\n")
    with pytest.raises(ValueError, match="split hash mismatch"):
        data.load_prepared(tmp_path / "moved")


def test_requires_both_subsets_and_rejects_unknown_reservation(raw, tmp_path):
    with pytest.raises(ValueError, match="Unknown reserved IDs"):
        data.prepare_hitl(raw, tmp_path / "prepared", reserved_ids=["unknown"])
    assert not (tmp_path / "prepared").exists()
    shutil.rmtree(raw / "Car parts dataset")
    with pytest.raises(ValueError, match="Both HITL subsets"):
        data.prepare_hitl(raw, tmp_path / "prepared")


def test_orientation_rotates_mask_and_rejects_ambiguous_frame(tmp_path):
    source = Image.new("RGB", (10, 6))
    exif = source.getexif()
    exif[274] = 6
    path = tmp_path / "rotated.jpg"
    source.save(path, exif=exif)
    mask = Image.new("L", (10, 6))
    mask.putpixel((0, 0), 1)
    image, normalized, transform = data._aligned(path, mask)
    assert image.size == normalized.size == (6, 10)
    assert normalized.getpixel((5, 0)) == 1
    assert transform["annotation_frame"] == "stored_pixels"
    exif[274] = 3
    source.save(path, exif=exif)
    with pytest.raises(ValueError, match="Ambiguous EXIF"):
        data._aligned(path, mask)


def test_bad_geometry_and_unmapped_titles_fail():
    annotation = {"size": {"width": 10, "height": 10},
                  "objects": [obj("unmapped", [[0, 0], [1, 0], [1, 1]])]}
    with pytest.raises(ValueError, match="Unmapped"):
        data.rasterize_annotation(annotation, {"a": 1})
    annotation["objects"][0]["classTitle"] = "a"
    annotation["objects"][0]["points"]["exterior"][0] = [-1, 0]
    with pytest.raises(ValueError, match="outside annotation frame"):
        data.rasterize_annotation(annotation, {"a": 1})
