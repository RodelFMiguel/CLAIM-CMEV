"""Pen-mark training data: pages from an M4 cache, exact-box augmentation and the torch dataset.

The dataset and the cache here are a few drawn rectangles in a temporary folder. The cache is
a stand-in with the attributes ``load_penmark_pages`` reads; the page transforms are real
``PageTransform`` records, so the frame mapping is the one M4 pages go through.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")

from claim_cmev.contracts.documents import PageTransform  # noqa: E402
from pipelines.documents import penmarks_data as pd_  # noqa: E402

W, H = 320, 400
PAPER, INK = (254, 254, 252), (44, 84, 170)
CLAIM_IDS = {"SYN-1": "01JAX7Q0VN4Z3K9F2M8R6T1C5A", "SYN-2": "01JAX7Q0VN4Z3K9F2M8R6T1C5B",
             "SYN-3": "01JAX7Q0VN4Z3K9F2M8R6T1C5C", "SYN-4": "01JAX7Q0VN4Z3K9F2M8R6T1C5D"}
SPLITS = {"SYN-1": "train", "SYN-2": "train", "SYN-3": "validation", "SYN-4": "test"}


def _mark(image_id, n, cls, style, box, row=None, components=None):
    return {"mark_id": f"{image_id}-mark-{n:02d}", "class": cls, "style": style, "bbox": list(box),
            "component_bboxes": [list(c) for c in (components or [box])], "target_row_id": row,
            "target_entry_id": f"{image_id}:{row}" if row else None, "text": None}


def _estimate(image_id):
    """Two rows; an X on row 1, a price change on row 2 and a tick that is not a target."""
    marks = [_mark(image_id, 1, "exclusion", "x", (40, 110, 60, 132), "part_1"),
             _mark(image_id, 2, "price_change", "amount_strike", (200, 165, 300, 190), "part_2",
                   [(200, 170, 240, 176), (250, 165, 300, 190)]),
             _mark(image_id, 3, "acceptance_tick", "tick", (150, 112, 172, 132), "part_1")]
    rows = [{"row_id": "part_1", "row_bbox": [20, 100, 300, 150], "synthetic_action": "exclusion"},
            {"row_id": "part_2", "row_bbox": [20, 150, 300, 200], "synthetic_action": "price_change"}]
    regions = [{"label": "part_1_amount", "text": "100.00", "bbox": [200, 115, 240, 130]},
               {"label": "part_2_amount", "text": "250.00", "bbox": [200, 166, 240, 180]},
               {"label": "part_1_description", "text": "Door", "bbox": [70, 115, 140, 130]}]
    return marks, rows, regions


def _draw(path: Path, marks) -> None:
    pixels = np.full((H, W, 3), PAPER, dtype=np.uint8)
    for mark in marks:
        for x0, y0, x1, y1 in mark["component_bboxes"]:
            pixels[y0:y1, x0:x1] = INK
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels).save(path)


def _build(tmp_path: Path, *, homography=None):
    """A four-claim dataset folder and a stand-in for its M4 cache."""
    dataset, cache_dir = tmp_path / "dataset", tmp_path / "cache"
    dataset.mkdir()
    records, pages, coco = [], [], {s: {"images": [], "annotations": [], "categories": [
        {"id": 1, "name": "exclusion"}, {"id": 2, "name": "price_change"}]} for s in set(SPLITS.values())}
    for claim, split in SPLITS.items():
        for number, page_type in ((1, "survey_assessment"), (2, "repair_estimate")):
            image_id = f"{claim}_P{number}"
            if page_type == "repair_estimate":
                marks, rows, regions = _estimate(image_id)
            else:
                marks, rows, regions = [_mark(image_id, 1, "review_note", "handwritten_note", (30, 300, 120, 318))], [], []
            records.append({"image_id": image_id, "claim_id": claim, "page_number": number, "page_type": page_type,
                            "split": split, "file_name": f"images/{split}/{image_id}.png", "width": W, "height": H,
                            "regions": regions, "marks": marks, "row_decisions": rows,
                            "font_file": f"/fonts/Writer {number}.ttf"})
            rectified = cache_dir / "pages" / image_id / "rectified.png"
            _draw(rectified, marks)
            matrix = homography if (homography is not None and image_id == "SYN-1_P2") else np.eye(3)
            transform = PageTransform(
                source_width=W, source_height=H, corrected_width=W, corrected_height=H, exif_orientation=1,
                geometry_correction="none", correction_reason="skew_below_threshold",
                homography=[[float(v) for v in row] for row in matrix],
                homography_inverse=[[float(v) for v in row] for row in np.linalg.inv(matrix)])
            pages.append(SimpleNamespace(
                image_id=image_id, dataset_claim_id=claim, claim_id=CLAIM_IDS[claim], split=split,
                page_type=page_type, page_number=number, page_status="complete",
                page=SimpleNamespace(page_id=f"pg-{image_id}", transform=transform), rectified_path=rectified))
            images = coco[split]["images"]
            images.append({"id": len(images) + 1, "file_name": f"images/{split}/{image_id}.png", "width": W, "height": H})
            for mark in marks:
                if mark["class"] in ("exclusion", "price_change"):
                    x0, y0, x1, y1 = mark["bbox"]
                    coco[split]["annotations"].append({
                        "id": len(coco[split]["annotations"]) + 1, "image_id": len(images),
                        "category_id": 1 if mark["class"] == "exclusion" else 2, "bbox": [x0, y0, x1 - x0, y1 - y0],
                        "area": (x1 - x0) * (y1 - y0), "iscrowd": 0, "mark_id": mark["mark_id"]})
    annotations = dataset / "annotations.jsonl"
    annotations.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    for split, content in coco.items():
        (dataset / f"marks_coco_{split}.json").write_text(json.dumps(content), encoding="utf-8")
    manifest = {"schema": "cmev-m4-cache/1",
                "dataset": {"name": "toy", "manifest_sha256": "a" * 64,
                            "annotations_sha256": hashlib.sha256(annotations.read_bytes()).hexdigest()},
                "m4": {"versions": {"m4_code": "m4-text-layout/test"}, "m4_source_sha256": "b" * 64}}
    (cache_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    cache = SimpleNamespace(pages=tuple(pages), manifest=manifest, root=cache_dir,
                            split=lambda name: tuple(p for p in pages if p.split == name))
    return cache, dataset


# --------------------------------------------------------------------------- pages

def test_pages_hold_targets_distractors_and_rows_and_exclude_the_test_split(tmp_path):
    cache, dataset = _build(tmp_path)
    pages = pd_.load_penmark_pages(cache, dataset)
    assert [p.image_id for p in pages] == ["SYN-1_P1", "SYN-1_P2", "SYN-2_P1", "SYN-2_P2", "SYN-3_P1", "SYN-3_P2"]
    assert {p.split for p in pages} == {"train", "validation"}
    estimate, assessment = pages[1], pages[0]
    assert (estimate.page_id, estimate.claim_id, estimate.writer) == ("pg-SYN-1_P2", CLAIM_IDS["SYN-1"], "Writer 2")
    assert [(m.mark_class, m.style, m.row_id) for m in estimate.marks] == [
        ("exclusion", "x", "part_1"), ("price_change", "amount_strike", "part_2")]
    assert [m.mark_class for m in estimate.distractors] == ["acceptance_tick"]
    assert estimate.boxes.tolist() == [[40, 110, 60, 132], [200, 165, 300, 190]] and estimate.labels.tolist() == [1, 2]
    assert estimate.marks[1].entry_id == "SYN-1_P2:part_2" and len(estimate.marks[1].components) == 2
    # The row box is the union of the row's printed field boxes (module-06 step 4), not the table band.
    assert [(r.entry_id, r.box, r.amount_box, r.action) for r in estimate.rows] == [
        ("SYN-1_P2:part_1", (70.0, 115.0, 240.0, 130.0), (200.0, 115.0, 240.0, 130.0), "exclusion"),
        ("SYN-1_P2:part_2", (200.0, 166.0, 240.0, 180.0), (200.0, 166.0, 240.0, 180.0), "price_change")]
    assert [r.band for r in estimate.rows] == [(20.0, 100.0, 300.0, 150.0), (20.0, 150.0, 300.0, 200.0)]
    assert estimate.rows[0].cells == ((70.0, 115.0, 140.0, 130.0), (200.0, 115.0, 240.0, 130.0))
    assert estimate.rows_source == pd_.ROWS_CELL_UNION == "oracle_cell_union"
    assert not assessment.marks and not assessment.rows and assessment.boxes.shape == (0, 4)
    assert [m.mark_class for m in assessment.distractors] == ["review_note"]
    assert (estimate.width, estimate.height) == (W, H) and estimate.image_path.is_file()


def test_the_test_split_is_loaded_only_on_request(tmp_path):
    cache, dataset = _build(tmp_path)
    with pytest.raises(ValueError, match="held-out"):
        pd_.load_penmark_pages(cache, dataset, splits=("test",))
    (page, estimate) = pd_.load_penmark_pages(cache, dataset, splits=("test",), include_test=True)
    assert page.split == estimate.split == "test" and len(estimate.marks) == 2


def test_boxes_are_mapped_into_the_corrected_frame_with_the_page_transform(tmp_path):
    shrink = np.array([[0.5, 0.0, 10.0], [0.0, 0.5, 20.0], [0.0, 0.0, 1.0]])
    cache, dataset = _build(tmp_path, homography=shrink)
    pages = {p.image_id: p for p in pd_.load_penmark_pages(cache, dataset)}
    warped, plain = pages["SYN-1_P2"], pages["SYN-2_P2"]
    assert plain.boxes[0].tolist() == [40, 110, 60, 132]
    np.testing.assert_allclose(warped.boxes[0], [30.0, 75.0, 40.0, 86.0])
    np.testing.assert_allclose(warped.rows[0].band, (20.0, 70.0, 160.0, 95.0))
    np.testing.assert_allclose(warped.rows[0].box, (45.0, 77.5, 130.0, 85.0))  # union of the mapped field boxes
    np.testing.assert_allclose(warped.rows[0].amount_box, (110.0, 77.5, 130.0, 85.0))
    np.testing.assert_allclose(warped.distractors[0].box, (85.0, 76.0, 96.0, 86.0))


def test_the_row_box_is_the_union_of_the_rows_printed_field_boxes():
    """Hand-worked: quantity, description, action, amount and remarks cells of one row."""
    cells = [(138, 514, 148, 528), (215, 505, 454, 525), (691, 509, 774, 528), (831, 508, 884, 523),
             (998, 509, 1078, 527)]
    assert pd_.union_box(cells) == (138.0, 505.0, 1078.0, 528.0)  # 23 px tall; its table band is 79 px
    assert pd_.union_box([(5, 6, 7, 8)]) == (5.0, 6.0, 7.0, 8.0)
    with pytest.raises(ValueError, match="no boxes"):
        pd_.union_box([])


def test_only_the_rows_own_printed_fields_enter_its_row_box(tmp_path):
    cache, dataset = _build(tmp_path)
    path = dataset / "annotations.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    for record in records:
        if record["image_id"] == "SYN-1_P2":
            record["regions"] += [{"label": "part_1_quantity", "text": "1", "bbox": [30, 118, 38, 131]},
                                  {"label": "part_1_remarks", "text": "Check", "bbox": [250, 112, 290, 128]},
                                  {"label": "part_10_amount", "text": "9.00", "bbox": [200, 300, 240, 320]},  # no such row
                                  {"label": "parts_subtotal", "text": "350.00", "bbox": [200, 350, 240, 365]}]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    cache.manifest["dataset"]["annotations_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    first, second = pd_.records_by_id(pd_.load_penmark_pages(cache, dataset))["SYN-1_P2"].rows
    assert first.box == (30.0, 112.0, 290.0, 131.0) and len(first.cells) == 4  # quantity, description, amount, remarks
    assert first.amount_box == (200.0, 115.0, 240.0, 130.0), "the amount box is unchanged"
    assert second.box == (200.0, 166.0, 240.0, 180.0)


def test_a_row_without_a_printed_field_is_refused_not_given_its_table_band(tmp_path):
    cache, dataset = _build(tmp_path)
    path = dataset / "annotations.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    for record in records:
        record["regions"] = [r for r in record["regions"] if not r["label"].startswith("part_2_")]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    cache.manifest["dataset"]["annotations_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(pd_.PenMarkDataError, match="part_2 has no printed field box"):
        pd_.load_penmark_pages(cache, dataset)


def test_table_band_rows_are_a_named_contrast_that_leaves_the_pages_untouched(tmp_path):
    cache, dataset = _build(tmp_path)
    pages = pd_.load_penmark_pages(cache, dataset)
    bands = pd_.with_table_band_rows(pages)
    estimate, contrast = pages[1], bands[1]
    assert [r.box for r in contrast.rows] == [(20.0, 100.0, 300.0, 150.0), (20.0, 150.0, 300.0, 200.0)]
    assert contrast.rows_source == pd_.ROWS_TABLE_BAND == "oracle_table_band"
    assert [r.amount_box for r in contrast.rows] == [r.amount_box for r in estimate.rows]
    assert contrast.marks == estimate.marks and estimate.rows[0].box == (70.0, 115.0, 240.0, 130.0)
    by_hand = pd_.RowGeometry("part_1", "P:part_1", (0.0, 0.0, 1.0, 1.0), None, "unmarked")  # no band recorded
    with pytest.raises(pd_.PenMarkDataError, match="no table band"):
        pd_.with_table_band_rows([pd_.PenMarkPage(**{**estimate.__dict__, "rows": (by_hand,)})])


def test_labels_that_disagree_between_the_coco_file_and_the_annotations_are_refused(tmp_path):
    cache, dataset = _build(tmp_path)
    path = dataset / "marks_coco_train.json"
    coco = json.loads(path.read_text())
    coco["annotations"][0]["bbox"][0] += 5
    path.write_text(json.dumps(coco))
    with pytest.raises(pd_.PenMarkDataError, match="SYN-1_P2-mark-01"):
        pd_.load_penmark_pages(cache, dataset)
    coco["annotations"].pop(0)
    path.write_text(json.dumps(coco))
    with pytest.raises(pd_.PenMarkDataError, match="SYN-1_P2"):
        pd_.load_penmark_pages(cache, dataset)


def test_annotations_other_than_the_ones_the_cache_was_built_from_are_refused(tmp_path):
    cache, dataset = _build(tmp_path)
    with (dataset / "annotations.jsonl").open("a") as stream:
        stream.write("\n")
    with pytest.raises(pd_.PenMarkDataError, match="annotations.jsonl"):
        pd_.load_penmark_pages(cache, dataset)


def test_a_claim_in_two_partitions_is_refused(tmp_path):
    cache, _ = _build(tmp_path)
    pd_.assert_claim_grouped(cache.pages)
    cache.pages[1].split = "validation"  # SYN-1_P2 leaves its claim's partition
    with pytest.raises(pd_.PenMarkDataError, match="SYN-1"):
        pd_.assert_claim_grouped(cache.pages)


def test_split_rows_count_pages_marks_and_styles(tmp_path):
    cache, dataset = _build(tmp_path)
    rows = pd_.split_rows(pd_.load_penmark_pages(cache, dataset))
    train = next(r for r in rows if r["split"] == "train")
    assert (train["pages"], train["claims"], train["pages_without_target"]) == (4, 2, 2)
    assert (train["exclusion"], train["price_change"], train["acceptance_tick"], train["review_note"]) == (2, 2, 2, 2)
    styles = pd_.style_rows(pd_.load_penmark_pages(cache, dataset))
    assert {(r["mark_class"], r["style"], r["train"], r["validation"]) for r in styles} >= {
        ("exclusion", "x", 2, 1), ("price_change", "amount_strike", 2, 1)}


def test_the_data_record_names_hashes_m4_versions_and_counts_without_reading_test_labels(tmp_path):
    cache, dataset = _build(tmp_path)
    record = pd_.data_record(cache, dataset, pd_.load_penmark_pages(cache, dataset))
    assert record["dataset"]["name"] == "toy" and record["split_version"] == "toy@manifest-aaaaaaaaaaaa"
    assert record["m4_cache"]["versions"] == {"m4_code": "m4-text-layout/test"}
    assert record["m4_cache"]["manifest_sha256"] == hashlib.sha256((cache.root / "manifest.json").read_bytes()).hexdigest()
    assert set(record["split_files"]) == {"marks_coco_train.json", "marks_coco_validation.json",
                                          "marks_coco_test.json", "annotations.jsonl"}
    assert record["partitions"]["train"]["marks"] == {"exclusion": 2, "price_change": 2}
    assert record["partitions"]["train"]["styles"] == {"exclusion/x": 2, "price_change/amount_strike": 2}
    assert record["partitions"]["test"] == {"pages": 2, "claims": 1, "marks": None,
                                            "marks_reason": "the labels of this partition were not read"}
    json.dumps(record)


# --------------------------------------------------------------------------- ink

def test_ink_is_found_inside_a_box_and_its_extent_is_measured():
    pixels = np.full((60, 80, 3), PAPER, dtype=np.uint8)
    pixels[20:30, 10:50] = INK
    pixels[40:50, 12:48] = (20, 20, 20)  # printed text and black pens are ink as well
    pixels[55:58, :] = (200, 205, 210)  # a pale table line is not
    mask = pd_.ink_mask(pixels)
    assert mask[20:30, 10:50].all() and mask[40:50, 12:48].all() and not mask[:20].any() and not mask[55:].any()
    assert pd_.ink_extent(mask, (8, 18, 52, 32)) == (10, 20, 50, 30)
    assert pd_.ink_extent(mask, (8, 38, 52, 52)) == (12, 40, 48, 50)
    assert pd_.ink_extent(mask, (60, 0, 80, 18)) is None


# --------------------------------------------------------------------------- augmentation

def _ink_box(pixels: np.ndarray):
    ys, xs = np.nonzero(pd_.ink_mask(pixels))
    return np.array([xs.min(), ys.min(), xs.max() + 1, ys.max() + 1], dtype=float)


@pytest.mark.parametrize("seed", range(6))
def test_geometric_augmentation_moves_each_box_with_its_ink(seed):
    pixels = np.full((H, W, 3), PAPER, dtype=np.uint8)
    pixels[150:180, 90:230] = INK
    config = pd_.AugmentConfig(scale=0.10, translate_px=15.0, rotation_degrees=3.0, brightness=0.0, contrast=0.0,
                               blur_probability=0.0, jpeg_probability=0.0, shadow_probability=0.0)
    out, boxes, labels, info = pd_.augment_page(pixels, np.array([[90.0, 150.0, 230.0, 180.0]]), np.array([2]),
                                                np.random.default_rng(seed), config)
    assert out.shape == pixels.shape and out.dtype == np.uint8 and labels.tolist() == [2]
    assert info["scale"] != 1.0 and info["rotation_degrees"] != 0.0
    # The rotated rectangle's bound is the exact box of its ink, up to resampling at the edges.
    np.testing.assert_allclose(boxes[0], _ink_box(out), atol=2.0)


def test_photometric_augmentation_leaves_the_boxes_alone_and_is_repeatable():
    pixels = np.full((H, W, 3), PAPER, dtype=np.uint8)
    pixels[150:180, 90:230] = INK
    boxes = np.array([[90.0, 150.0, 230.0, 180.0]])
    config = pd_.AugmentConfig(scale=0.0, translate_px=0.0, rotation_degrees=0.0, brightness=0.2, contrast=0.2,
                               blur_probability=1.0, jpeg_probability=1.0, shadow_probability=1.0)
    out, new_boxes, _, info = pd_.augment_page(pixels, boxes, np.array([1]), np.random.default_rng(3), config)
    again, *_ = pd_.augment_page(pixels, boxes, np.array([1]), np.random.default_rng(3), config)
    other, *_ = pd_.augment_page(pixels, boxes, np.array([1]), np.random.default_rng(4), config)
    np.testing.assert_array_equal(new_boxes, boxes)
    assert np.array_equal(out, again) and not np.array_equal(out, other) and not np.array_equal(out, pixels)
    assert info["jpeg_quality"] is not None and info["blur_radius"] > 0 and info["shadow"] is not None
    assert not info["geometric"]


def test_a_box_pushed_off_the_page_is_clipped_or_dropped():
    matrix = pd_.affine_matrix(W, H, scale=1.0, degrees=0.0, translate=(-100.0, 0.0))
    boxes = np.array([[90.0, 150.0, 230.0, 180.0], [20.0, 10.0, 110.0, 30.0], [10.0, 300.0, 60.0, 320.0]])
    moved, keep = pd_.affine_boxes(boxes, matrix, W, H, min_visible=0.5)
    assert keep.tolist() == [True, False, False]  # 10 % and nothing of the last two stay on the page
    np.testing.assert_allclose(moved, [[0.0, 150.0, 130.0, 180.0]])
    np.testing.assert_allclose(pd_.affine_matrix(W, H, scale=1.0, degrees=0.0, translate=(0.0, 0.0)),
                               [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], atol=1e-12)


# --------------------------------------------------------------------------- torch dataset

def test_the_dataset_returns_a_list_friendly_image_and_target_and_handles_empty_pages(tmp_path):
    cache, dataset_dir = _build(tmp_path)
    pages = pd_.load_penmark_pages(cache, dataset_dir, splits=("train",))
    dataset = pd_.PenMarkDataset(pages)
    assert len(dataset) == 4
    image, target = dataset[1]
    assert image.shape == (3, H, W) and image.dtype == torch.float32 and 0.0 <= image.min() and image.max() <= 1.0
    stored = np.asarray(Image.open(pages[1].image_path).convert("RGB"))
    np.testing.assert_array_equal((image.permute(1, 2, 0) * 255).round().numpy().astype(np.uint8), stored)
    assert target["boxes"].dtype == torch.float32 and target["boxes"].tolist() == pages[1].boxes.tolist()
    assert target["labels"].dtype == torch.int64 and target["labels"].tolist() == [1, 2]
    assert int(target["image_id"]) == 1
    _, empty = dataset[0]
    assert empty["boxes"].shape == (0, 4) and empty["labels"].shape == (0,) and empty["labels"].dtype == torch.int64
    images, targets = pd_.collate([dataset[0], dataset[1]])
    assert len(images) == len(targets) == 2 and images[1] is not None


def test_training_augmentation_changes_with_the_epoch_and_repeats_for_one_epoch(tmp_path):
    cache, dataset_dir = _build(tmp_path)
    pages = pd_.load_penmark_pages(cache, dataset_dir, splits=("train",))
    dataset = pd_.PenMarkDataset(pages, augment=pd_.AugmentConfig(), seed=5)
    first, target = dataset[1]
    assert torch.equal(first, dataset[1][0]) and len(target["boxes"]) == 2
    dataset.set_epoch(1)
    assert not torch.equal(first, dataset[1][0])
    assert (target["boxes"][:, 2] > target["boxes"][:, 0]).all() and (target["boxes"][:, 3] > target["boxes"][:, 1]).all()


# --------------------------------------------------------------------------- resizing and anchors

@pytest.mark.parametrize("size", [(1754, 1240), (400, 320), (300, 900)])
def test_resized_size_is_what_torchvision_does_to_a_page(size):
    from torchvision.models.detection.transform import GeneralizedRCNNTransform

    height, width = size
    transform = GeneralizedRCNNTransform(800, 1333, [0.0] * 3, [1.0] * 3)
    images, _ = transform.eval()([torch.zeros(3, height, width)])
    scale, resized, padded = pd_.resized_size(height, width, min_size=800, max_size=1333)
    assert resized == tuple(images.image_sizes[0]) and padded == tuple(images.tensors.shape[-2:])
    assert scale == pytest.approx(min(800 / min(size), 1333 / max(size)))


def test_best_anchor_iou_is_the_largest_overlap_any_anchor_reaches():
    anchors = torch.tensor([[0.0, 0.0, 10.0, 10.0], [0.0, 0.0, 40.0, 10.0], [100.0, 100.0, 120.0, 120.0]])
    boxes = np.array([[0.0, 0.0, 40.0, 10.0], [0.0, 0.0, 20.0, 10.0], [200.0, 200.0, 210.0, 210.0]])
    np.testing.assert_allclose(pd_.best_anchor_iou(boxes, anchors), [1.0, 0.5, 0.0])
    np.testing.assert_allclose(pd_.best_anchor_iou(boxes, anchors, chunk=1), [1.0, 0.5, 0.0])
    assert pd_.best_anchor_iou(np.zeros((0, 4)), anchors).shape == (0,)
