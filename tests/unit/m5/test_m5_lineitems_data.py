"""Label alignment and tensors of the M5 training data. Hand-built pages only: no dataset, no network."""
from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pytest

from m5_support import CLAIM_ID, make_page
from pipelines.documents.lineitems_data import (
    IGNORE_INDEX,
    LabelPolicy,
    PageWindowDataset,
    align_page,
    align_pages,
    assert_claims_in_one_split,
    bio_labels,
    char_error_rate,
    collate_windows,
    edit_distance,
    field_name,
    field_type,
    ink_over,
    overlap_of_smaller,
    overlap_samples,
    region_label_names,
    row_markings,
    row_slot,
    share_inside,
    text_quality_rows,
)
from pipelines.documents.m4_cache import CachedPage
from test_m5_layoutlmv3 import TINY_LABELS, long_cells, page_image, tiny_checkpoint

# Ground-truth field boxes of the hand-built estimate page, in "dataset" pixels (page is 1000 x 1400).
REGIONS = [
    {"label": "claim_number", "text": "MC2026000001", "bbox": [300, 100, 480, 120]},
    {"label": "part_1_quantity", "text": "1", "bbox": [140, 205, 150, 218]},
    {"label": "part_1_description", "text": "Rear door panel LH", "bbox": [200, 200, 460, 222]},
    {"label": "part_1_amount", "text": "715.00", "bbox": [800, 200, 870, 220]},
    {"label": "part_1_remarks", "text": "Nett", "bbox": [900, 200, 940, 220]},
    {"label": "part_2_description", "text": "Front bumper", "bbox": [200, 260, 380, 282]},
    {"label": "part_2_amount", "text": "415.00", "bbox": [800, 260, 870, 280]},
]
HANDWRITING = [{"text": "SGD", "bbox": [900, 222, 940, 240]}, {"text": "585.00", "bbox": [945, 222, 995, 240]}]


def cached(cells, *, image_id="P-1", split="train", claim="C-1", page_type="repair_estimate", rectified=None,
           record=None) -> CachedPage:
    record = record or make_page(cells, granularity="line", file_id=image_id)
    status = record.quality.state
    return CachedPage(image_id=image_id, dataset_claim_id=claim, claim_id=CLAIM_ID, split=split, page_type=page_type,
                      page_number=2, processing_status="succeeded", page_status=status, page=record,
                      rectified_path=rectified, source_path=None, entry=MappingProxyType({}))


def annotation(image_id="P-1", split="train", regions=REGIONS, handwriting=(), **extra):
    return {"image_id": image_id, "split": split, "page_type": "repair_estimate", "regions": list(regions),
            "handwritten_words": list(handwriting), "marks": [], "row_decisions": [], **extra}


def labels_of(alignment):
    return {box.text: (box.outcome, box.label, box.reason) for box in alignment.boxes}


def test_overlap_measures():
    box, inside, brushing, apart = [0, 0, 10, 10], [2, 2, 4, 4], [8, 0, 18, 10], [20, 20, 30, 30]
    values = overlap_of_smaller([box], [inside, brushing, apart, box])
    assert values.tolist() == [[1.0, pytest.approx(0.2), 0.0, 1.0]]
    assert share_inside([box], [brushing]).tolist() == [[pytest.approx(0.2)]]
    assert overlap_of_smaller(np.zeros((0, 4)), [box]).shape == (0, 1)


def test_one_field_static_text_and_missed_field():
    cells = [("Claim Number", (60, 100, 200, 120), 0.98),            # static form text
             ("MC2026000001", (298, 98, 484, 123), 0.99),            # the claim number value
             ("Rear door panel LH", (198, 198, 464, 224), 0.95),
             ("715.00", (797, 197, 873, 222), 0.99),
             ("Nett", (898, 198, 942, 222), 0.99)]
    alignment = align_page(cached(cells), annotation())
    assert labels_of(alignment) == {
        "Claim Number": ("other", "O", "static_text"),
        "MC2026000001": ("field", "B-claim_number", "single_field"),
        "Rear door panel LH": ("field", "B-part_1_description", "single_field"),
        "715.00": ("field", "B-part_1_amount", "single_field"),
        "Nett": ("field", "B-part_1_remarks", "single_field")}
    status = {match.label: (match.status, len(match.box_ids)) for match in alignment.fields}
    assert status["part_1_amount"] == ("matched", 1)
    assert status["part_1_quantity"] == ("missed", 0)  # OCR returned no box for the single digit
    assert status["part_2_amount"] == ("missed", 0)


def test_label_follows_the_field_not_the_text_a_struck_amount_keeps_its_label():
    """Ink changed what OCR read, not which field the box is."""
    alignment = align_page(cached([("150", (805, 201, 866, 216), 0.91)]), annotation())
    assert labels_of(alignment) == {"150": ("field", "B-part_1_amount", "single_field")}


def test_handwriting_is_other_and_a_replacement_amount_is_never_the_printed_amount():
    cells = [("715.00", (797, 197, 873, 222), 0.99),
             ("SGD 585.00", (896, 221, 998, 242), 0.89)]  # handwriting under the printed remark
    alignment = align_page(cached(cells), annotation(handwriting=HANDWRITING))
    assert labels_of(alignment)["SGD 585.00"] == ("other", "O", "handwriting")
    assert labels_of(alignment)["715.00"][1] == "B-part_1_amount"
    assert all(box.label != "B-part_1_amount" for box in alignment.boxes if "585" in box.text)


def test_print_merged_with_handwriting_is_masked_and_the_field_is_ambiguous():
    cells = [("Nett585.00", (898, 198, 998, 242), 0.82)]  # one OCR line over printed "Nett" and the handwriting
    alignment = align_page(cached(cells), annotation(handwriting=HANDWRITING))
    box = alignment.boxes[0]
    assert (box.outcome, box.label, box.field, box.reason) == ("masked", None, "part_1_remarks", "print_and_handwriting")
    assert alignment.field("part_1_remarks").status == "ambiguous"
    assert alignment.label_ids({"O": 0}) == [IGNORE_INDEX]


def test_a_line_over_two_fields_is_masked_never_guessed():
    cells = [("715.00 Nett", (797, 197, 942, 222), 0.9)]
    alignment = align_page(cached(cells), annotation())
    assert labels_of(alignment) == {"715.00 Nett": ("masked", None, "merged_fields")}
    assert {alignment.field(name).status for name in ("part_1_amount", "part_1_remarks")} == {"ambiguous"}


def test_several_boxes_in_one_field_begin_then_continue_in_reading_order():
    cells = [("Rear door", (198, 198, 300, 224), 0.9), ("panel LH", (310, 198, 464, 224), 0.9)]
    alignment = align_page(cached(cells), annotation())
    assert [box.label for box in alignment.boxes] == ["B-part_1_description", "I-part_1_description"]
    assert alignment.field("part_1_description").box_ids == tuple(box.box_id for box in alignment.boxes)


def test_field_regions_are_mapped_into_the_corrected_frame_before_comparing():
    """A page M4 shifted by 100 px: the dataset box only matches after the mapping."""
    record = make_page([("715.00", (897, 197, 973, 222), 0.99)], granularity="line", file_id="P-1")
    shift = ((1.0, 0.0, 100.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    back = ((1.0, 0.0, -100.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    moved = record.model_copy(update={"transform": record.transform.model_copy(
        update={"homography": shift, "homography_inverse": back, "geometry_correction": "perspective"})})
    assert labels_of(align_page(cached(None, record=moved), annotation()))["715.00"][1] == "B-part_1_amount"
    assert labels_of(align_page(cached(None, record=record), annotation()))["715.00"][1] != "B-part_1_amount"


def test_unreadable_page_has_no_boxes_and_its_fields_are_counted_not_dropped():
    alignment = align_page(cached([]), annotation())
    assert alignment.page_status == "unreadable" and alignment.boxes == ()
    assert {match.status for match in alignment.fields} == {"page_unreadable"}
    assert len(alignment.fields) == len(REGIONS)


def test_thresholds_come_from_the_policy():
    cells = [("715.00", (840, 197, 895, 222), 0.99)]  # overlaps the amount field at about 0.44
    assert labels_of(align_page(cached(cells), annotation()))["715.00"][1] == "O"
    loose = LabelPolicy(field_overlap_min=0.3)
    assert labels_of(align_page(cached(cells), annotation(), loose))["715.00"][1] == "B-part_1_amount"
    assert loose.to_dict()["status"] == "proposed" and loose.to_dict()["version"].startswith("m5-box-label-alignment/")


def test_overlap_samples_accept_training_pages_only():
    page = cached([("715.00", (797, 197, 873, 222), 0.99)])
    samples = overlap_samples([page], {"P-1": annotation(handwriting=HANDWRITING)})
    assert samples["field_overlap"].tolist() == [1.0]
    held_out = cached([("715.00", (797, 197, 873, 222), 0.99)], split="validation")
    with pytest.raises(ValueError, match="training split only"):
        overlap_samples([held_out], {"P-1": annotation(split="validation")})


def test_label_vocabulary_is_built_from_train_and_validation_names_only():
    annotations = {"a": annotation("a", "train"), "b": annotation("b", "validation", regions=REGIONS[:2]),
                   "c": annotation("c", "test", regions=[{"label": "only_in_test", "text": "x", "bbox": [0, 0, 1, 1]}])}
    names = region_label_names(annotations)
    assert "only_in_test" not in names and names == sorted(names)
    labels = bio_labels(names)
    assert labels[0] == "O" and labels[1:3] == ["B-claim_number", "I-claim_number"]
    assert len(labels) == 1 + 2 * len(names)
    assert (field_name("I-part_2_amount"), field_name("O")) == ("part_2_amount", None)
    assert [field_type(n) for n in ("part_2_amount", "claim_number", None)] == ["amount", "page_field", "O"]
    assert (row_slot("part_8_remarks"), row_slot("claim_number")) == (8, None)


def test_claims_must_not_cross_splits():
    pages = [cached([], image_id="A_P1", claim="A"), cached([], image_id="A_P2", claim="A"),
             cached([], image_id="B_P1", claim="B", split="validation")]
    assert assert_claims_in_one_split(pages) == {"train": 1, "validation": 1}
    with pytest.raises(ValueError, match="more than one split"):
        assert_claims_in_one_split(pages + [cached([], image_id="B_P2", claim="B", split="train")])


def test_character_error_rate():
    assert edit_distance("715.00", "715.00") == 0
    assert edit_distance("415.00", "150") == 3
    assert char_error_rate("Rr bpr reinf", "Rr bpr rein") == pytest.approx(1 / 12)
    assert char_error_rate("", "x") == 1.0


def test_text_quality_separates_reading_from_labelling_and_flags_ink_over_the_field():
    marks = [{"mark_id": "m1", "class": "price_change", "style": "amount_cross", "target_row_id": "part_1",
              "component_bboxes": [[795, 205, 875, 212], [900, 222, 995, 240]]}]
    decisions = [{"row_id": "part_1", "synthetic_action": "price_change", "mark_ids": ["m1"]},
                 {"row_id": "part_2", "synthetic_action": "accepted", "mark_ids": []}]
    record = annotation(marks=marks, row_decisions=decisions)
    cells = [("7I5.00", (797, 197, 873, 222), 0.87), ("415.00", (797, 257, 873, 282), 0.99)]
    rows = {row["label"]: row for row in text_quality_rows(align_pages([cached(cells)], {"P-1": record}), {"P-1": record})}
    assert rows["part_1_amount"]["exact"] is False and rows["part_1_amount"]["cer"] == pytest.approx(1 / 6)
    assert rows["part_1_amount"]["ink_over_field"] is True and rows["part_1_amount"]["row_style"] == "amount_cross"
    assert rows["part_2_amount"]["exact"] is True and rows["part_2_amount"]["ink_over_field"] is False
    assert rows["part_2_amount"]["row_style"] == "unmarked"
    assert row_markings(record)["part_1"] == {"action": "price_change", "style": "amount_cross"}
    assert ink_over([0, 0, 5, 5], record) is False


# --------------------------------------------------------------------------- tensors

@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    from claim_cmev.documents.line_items.layoutlmv3 import serving_preprocessing

    root = tmp_path_factory.mktemp("lineitems-data")
    labels = bio_labels(region_label_names({"a": annotation("a")}))
    return {"dir": tiny_checkpoint(root / "model", labels=labels, preprocessing=serving_preprocessing("line")),
            "image": page_image(root), "labels": labels, "label2id": {label: i for i, label in enumerate(labels)}}


def test_training_tensors_equal_the_serving_adapters_tensors_for_the_same_page(tiny):
    torch = pytest.importorskip("torch")
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    cells = [("Claim Number", (60, 100, 200, 120), 0.98), ("MC2026000001", (298, 98, 484, 123), 0.99),
             (" Rear door panel LH", (198, 198, 464, 224), 0.95), ("715.00", (797, 197, 873, 222), 0.99),
             ("Nett585.00", (898, 198, 998, 242), 0.82)]
    page = cached(cells, rectified=tiny["image"])
    record = annotation(handwriting=HANDWRITING)
    served = LocalLayoutLMv3(tiny["dir"])
    dataset = PageWindowDataset([page], served.processor, alignments=align_pages([page], {"P-1": record}),
                                label2id=tiny["label2id"])
    words, encoding = served.encode(page.page, tiny["image"])
    assert len(dataset) == encoding["input_ids"].shape[0] == 1
    for key in ("input_ids", "attention_mask", "bbox", "pixel_values"):
        assert torch.equal(dataset[0][key], encoding[key][0]), key
    assert dataset.words[0] == words

    # A label sits on the first subword of each box; the masked box and every other token are ignored.
    item = dataset[0]
    position = {int(word): token for token, word in enumerate(item["word_index"]) if int(word) >= 0}
    assert sorted(position) == [0, 1, 2, 3, 4]
    expected = ["O", "B-claim_number", "B-part_1_description", "B-part_1_amount", None]
    for word, label in enumerate(expected):
        value = int(item["labels"][position[word]])
        assert value == (IGNORE_INDEX if label is None else tiny["label2id"][label])
    assert int((item["labels"] != IGNORE_INDEX).sum()) == 4
    batch = collate_windows([item, item])
    assert tuple(batch["input_ids"].shape) == (2, 512) and tuple(batch["pixel_values"].shape) == (2, 3, 224, 224)
    assert dataset.token_counts() == [int(encoding["attention_mask"][0].sum())]


def test_long_pages_become_several_windows_and_unreadable_pages_are_listed(tiny):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    long_page = cached(long_cells(150), image_id="LONG", rectified=tiny["image"])
    failed = cached([], image_id="FAILED", rectified=tiny["image"])
    served = LocalLayoutLMv3(tiny["dir"])
    dataset = PageWindowDataset([long_page, failed], served.processor)
    assert [page.image_id for page in dataset.pages] == ["LONG"]
    assert dataset.skipped == [("FAILED", "unreadable")]
    assert len(dataset) >= 3 and dataset.page_windows == [list(range(len(dataset)))]
    # Unlabelled pages carry no training label; every box has a decision token in some window.
    seen = set()
    for item in dataset.windows:
        assert int((item["labels"] != IGNORE_INDEX).sum()) == 0
        seen |= {int(word) for word in item["word_index"] if int(word) >= 0}
    assert seen == set(range(150))


def test_labels_and_alignments_must_come_together(tiny):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    with pytest.raises(ValueError, match="together"):
        PageWindowDataset([], LocalLayoutLMv3(tiny["dir"]).processor, label2id=tiny["label2id"])
    assert TINY_LABELS[0] == "O"
