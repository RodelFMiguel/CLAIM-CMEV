"""Scores of the M5 line-item model on hand-built pages: every denominator is checked by hand."""
from __future__ import annotations

import json

import pytest

from claim_cmev.contracts.common import make_job_key
from claim_cmev.documents.line_items.layoutlmv3 import WordPrediction, adapt_predictions
from m5_support import BASIS, CLAIM_ID, CONFIG, PROVENANCE, VOCABULARY
from pipelines.documents.lineitems_data import align_pages
from pipelines.documents.lineitems_eval import (
    box_rows,
    box_summary,
    entity_scores,
    expected_calibration_error,
    label_metrics,
    line_item_rows,
    line_item_summary,
    position_baseline,
    prf,
    reliability,
    row_rows,
    row_summary,
    slot_confusion,
    type_confusion,
    type_metrics,
    validation_report,
)
from test_m5_lineitems_data import HANDWRITING, REGIONS, annotation, cached

# One estimate page: row 1 complete except its quantity (OCR found no box), row 2 with description and amount.
CELLS = [("Claim Number", (60, 100, 200, 120), 0.98),            # 0  O
         ("MC2026000001", (298, 98, 484, 123), 0.99),            # 1  claim_number
         ("Rear door", (198, 198, 300, 224), 0.95),              # 2  B-part_1_description
         ("panel LH", (310, 198, 464, 224), 0.95),               # 3  I-part_1_description
         ("715.00", (797, 197, 873, 222), 0.99),                 # 4  part_1_amount
         ("Nett585.00", (898, 198, 998, 242), 0.82),             # 5  masked (print and handwriting)
         ("Front bumper", (198, 258, 384, 284), 0.97),           # 6  part_2_description
         ("415.00", (797, 257, 873, 282), 0.99)]                 # 7  part_2_amount
TRUTH = ["O", "B-claim_number", "B-part_1_description", "I-part_1_description", "B-part_1_amount", None,
         "B-part_2_description", "B-part_2_amount"]
RECORD = annotation(handwriting=HANDWRITING, row_decisions=[
    {"row_id": "part_1", "synthetic_action": "price_change", "mark_ids": ["m1"]},
    {"row_id": "part_2", "synthetic_action": "accepted", "mark_ids": []}],
    marks=[{"mark_id": "m1", "class": "price_change", "style": "amount_cross", "target_row_id": "part_1",
            "component_bboxes": [[795, 205, 875, 212]]}])


@pytest.fixture()
def page():
    return cached(CELLS)


@pytest.fixture()
def alignments(page):
    result = align_pages([page], {"P-1": RECORD})
    assert [box.label for box in result["P-1"].boxes] == TRUTH
    return result


def predict(page, labels, confidence=0.9):
    return {"P-1": [WordPrediction(box.box_id, label, confidence) for box, label in zip(page.page.text_boxes, labels)]}


def perfect(page, masked="O"):
    return predict(page, [label if label is not None else masked for label in TRUTH])


def test_prf_keeps_counts_and_undefined_values():
    assert prf(3, 4, 6) == {"true_positive": 3, "predicted": 4, "support": 6, "precision": 0.75, "recall": 0.5,
                            "f1": pytest.approx(0.6)}
    assert prf(0, 0, 5)["precision"] is None and prf(0, 0, 5)["f1"] == 0.0   # never predicted: it counts as 0
    assert prf(0, 0, 0)["f1"] is None


def test_every_box_needs_a_prediction(page, alignments):
    with pytest.raises(ValueError, match="one prediction per text box"):
        box_rows(alignments, {"P-1": perfect(page)["P-1"][:-1]})


def test_outside_is_reported_apart_and_masked_boxes_are_counted_not_scored(page, alignments):
    labels = [label or "B-part_1_remarks" for label in TRUTH]
    labels[0] = "B-claim_number"          # the static label is called a field: a false positive
    labels[7] = "B-part_1_amount"         # right field type, wrong row slot
    rows = box_rows(alignments, predict(page, labels))
    summary = box_summary(rows)
    assert (summary["boxes"], summary["scored_boxes"], summary["masked_boxes"]) == (8, 7, 1)
    assert summary["field_micro"]["support"] == 6          # six field boxes; O is not in the headline
    assert summary["field_micro"]["true_positive"] == 5 and summary["field_micro"]["predicted"] == 7
    assert summary["outside"]["support"] == 1 and summary["outside"]["recall"] == 0.0
    assert summary["accuracy_all_scored"] == pytest.approx(5 / 7)
    by_label = {m["label"]: m for m in label_metrics(rows)}
    assert by_label["B-part_2_amount"]["recall"] == 0.0 and by_label["B-part_1_amount"]["precision"] == 0.5
    by_type = {m["label"]: m for m in type_metrics(rows)}
    assert by_type["amount"]["recall"] == 1.0              # the type was right; only the slot was wrong
    matrix, names = slot_confusion(rows)
    assert names == ["1", "2", "no row"]
    assert matrix.tolist() == [[3, 0, 0], [1, 1, 0]]
    types, type_names = type_confusion(rows)
    assert types[type_names.index("O"), type_names.index("page_field")] == 1


def test_entity_needs_the_exact_box_set_and_ignores_masked_boxes(page, alignments):
    assert entity_scores(alignments, perfect(page, masked="B-part_1_remarks")) == {
        "true_positive": 5, "predicted": 5, "support": 5, "precision": 1.0, "recall": 1.0, "f1": 1.0,
        "fields_without_box": 2}                           # part_1_quantity (missed) and part_1_remarks (ambiguous)
    half = [label or "O" for label in TRUTH]
    half[3] = "O"                                          # the second half of the wrapped description is lost
    scores = entity_scores(alignments, predict(page, half))
    assert (scores["true_positive"], scores["predicted"], scores["support"]) == (4, 5, 5)


def test_row_labelling_counts_delivered_fields_strays_and_unreadable_pages(page, alignments):
    rows = {row["slot"]: row for row in row_rows(alignments, perfect(page), {"P-1": RECORD})}
    assert rows[1]["quantity_status"] == "missed" and rows[1]["quantity_right"] is None
    assert rows[1]["complete_available"] is True and rows[1]["complete_all_four"] is False
    assert rows[1]["fields_delivered"] == 2 and rows[1]["row_style"] == "amount_cross"
    assert rows[2]["complete_available"] is True and rows[2]["row_style"] == "unmarked"

    stray = [label or "O" for label in TRUTH]
    stray[0] = "B-part_1_quantity"                         # a label for a cell OCR never delivered
    with_stray = {row["slot"]: row for row in row_rows(alignments, predict(page, stray), {"P-1": RECORD})}
    assert with_stray[1]["stray_labels"] == 1 and with_stray[1]["complete_available"] is False

    failed = cached([], image_id="P-2", claim="C-2")
    both = {**alignments, **align_pages([failed], {"P-2": annotation("P-2")})}
    summary = row_summary(row_rows(both, perfect(page), {"P-1": RECORD, "P-2": annotation("P-2")}))
    assert summary["rows"] == 4 and summary["rows_on_unreadable_pages"] == 2
    assert summary["complete_available"] == 2 and summary["complete_all_four"] == 0
    assert summary["amount_delivered"] == 2 and summary["quantity_delivered"] == 0


def test_reliability_bins_and_calibration_error(page, alignments):
    labels = [label or "O" for label in TRUTH]
    labels[7] = "O"
    rows = box_rows(alignments, predict(page, labels, confidence=0.95))
    table = reliability(rows, bins=10)
    assert sum(row["boxes"] for row in table) == 7 and table[-1]["boxes"] == 7
    assert table[-1]["accuracy"] == pytest.approx(6 / 7)
    assert expected_calibration_error(table) == pytest.approx(0.95 - 6 / 7)
    assert expected_calibration_error(reliability([], bins=5)) is None


def test_validation_report_is_json_ready_and_counts_unreadable_pages(page, alignments):
    failed = cached([], image_id="P-2", claim="C-2")
    both = {**alignments, **align_pages([failed], {"P-2": annotation("P-2")})}
    report = validation_report(both, {**perfect(page), "P-2": ()}, {"P-1": RECORD, "P-2": annotation("P-2")})
    assert (report["pages"], report["pages_unreadable"]) == (2, 1)
    assert report["entities"]["f1"] == 1.0 and report["entities"]["fields_without_box"] == 2 + len(REGIONS)
    assert json.loads(json.dumps(report))["rows"]["rows"] == 4


def test_line_items_from_the_real_adapter_are_scored_against_the_claim(page, alignments):
    """Labels go through ``adapt_predictions``; the printed amount is compared exactly, a missing quantity stays null."""
    claim = {"parts": [{"quantity": 1, "action": "replace", "line_total": 715.0, "part_name": "Rear door panel LH"},
                       {"quantity": 2, "action": "repair", "line_total": 415.0, "part_name": "Front bumper"}]}
    record = page.page
    result = adapt_predictions(
        [record], {record.page_id: perfect(page)["P-1"]}, config=CONFIG, vocabulary=VOCABULARY,
        family_id="family-a-ruled-grid", job_key=make_job_key(CLAIM_ID, 1, "line_items_extract", {"m": "t"}),
        claim_id=CLAIM_ID, input_revision=1, currency="SGD", cost_basis=BASIS, model_version="sha256:test",
        provenance=PROVENANCE)
    rows = line_item_rows(result, record.page_id, alignments["P-1"], RECORD, claim)
    first, second = rows
    assert first["found"] and first["amount"] == "715.00" and first["amount_exact"] is True
    assert first["quantity"] is None and first["quantity_match"] is False      # OCR had no quantity box: null, never 1
    assert first["description"] == "Rear door panel LH" and first["description_similarity"] == 1.0
    assert first["ink_over_amount"] is True and second["ink_over_amount"] is False
    assert second["amount_exact"] is True and second["operation"] is None and second["operation_match"] is False
    summary = line_item_summary(rows)
    assert summary["true_rows"] == 2 and summary["line_items_found"] == 2 and summary["amount_exact"] == 2
    assert summary["amount_exact_clean"] == {"rows": 1, "amount_exact": 1}
    assert summary["amount_exact_ink_over"] == {"rows": 1, "amount_exact": 1}
    assert summary["quantity_match"] == 0 and summary["extra_line_items"] == 0

    # A row the model did not label is a miss, not a silently shorter table.
    lost = [label or "O" for label in TRUTH]
    lost[6] = lost[7] = "O"
    partial = adapt_predictions(
        [record], {record.page_id: predict(page, lost)["P-1"]}, config=CONFIG, vocabulary=VOCABULARY,
        family_id="family-a-ruled-grid", job_key=make_job_key(CLAIM_ID, 1, "line_items_extract", {"m": "t"}),
        claim_id=CLAIM_ID, input_revision=1, currency="SGD", cost_basis=BASIS, model_version="sha256:test",
        provenance=PROVENANCE)
    assert partial.completeness.state == "partial"
    missed = line_item_summary(line_item_rows(partial, record.page_id, alignments["P-1"], RECORD, claim))
    assert missed["true_rows"] == 2 and missed["line_items_found"] == 1 and missed["amount_exact"] == 1


def test_a_misread_quantity_that_is_a_well_formed_number_is_counted_as_wrong_not_as_missing():
    """OCR read the printed ``1`` as ``7``: every format check passes, so only the comparison with the truth shows it."""
    cells = CELLS + [("7", (139, 204, 151, 219), 0.66)]                       # the quantity cell of row 1, misread
    page = cached(cells)
    alignments = align_pages([page], {"P-1": RECORD})
    assert alignments["P-1"].boxes[-1].label == "B-part_1_quantity"
    labels = [label or "O" for label in TRUTH] + ["B-part_1_quantity"]
    claim = {"parts": [{"quantity": 1, "action": "replace", "line_total": 715.0, "part_name": "Rear door panel LH"},
                       {"quantity": 2, "action": "repair", "line_total": 415.0, "part_name": "Front bumper"}]}
    record = page.page
    result = adapt_predictions(
        [record], {record.page_id: predict(page, labels)["P-1"]}, config=CONFIG, vocabulary=VOCABULARY,
        family_id="family-a-ruled-grid", job_key=make_job_key(CLAIM_ID, 1, "line_items_extract", {"m": "t"}),
        claim_id=CLAIM_ID, input_revision=1, currency="SGD", cost_basis=BASIS, model_version="sha256:test",
        provenance=PROVENANCE)
    first, second = line_item_rows(result, record.page_id, alignments["P-1"], RECORD, claim)
    assert first["quantity"] == "7" and first["quantity_match"] is False      # accepted, and wrong
    assert first["quantity_delivered"] is True and first["quantity_text"] == "7"
    assert first["quantity_ocr_confidence"] == pytest.approx(0.66) and first["quantity_reason"] is None
    assert second["quantity"] is None and second["quantity_delivered"] is False and second["quantity_text"] is None
    assert second["quantity_reason"] == "value_missing"                        # null, and the record says why
    summary = line_item_summary([first, second])
    assert summary["quantity_boxes_delivered"] == 1 and summary["quantity_match"] == 0
    assert summary["quantity_wrong_accepted"] == 1 and summary["quantity_null"] == 1


def test_annotations_and_claim_record_must_agree(page, alignments):
    claim = {"parts": [{"quantity": 1, "action": "replace", "line_total": 999.0}]}
    record = page.page
    result = adapt_predictions(
        [record], {record.page_id: perfect(page)["P-1"]}, config=CONFIG, vocabulary=VOCABULARY,
        family_id="family-a-ruled-grid", job_key=make_job_key(CLAIM_ID, 1, "line_items_extract", {"m": "t"}),
        claim_id=CLAIM_ID, input_revision=1, currency="SGD", cost_basis=BASIS, model_version="sha256:test",
        provenance=PROVENANCE)
    with pytest.raises(ValueError, match="disagree"):
        line_item_rows(result, record.page_id, alignments["P-1"], RECORD, claim)


def test_position_baseline_copies_the_label_of_the_nearest_training_box_and_reads_no_text(page, alignments):
    """Four coordinates per box, nothing else: the lookup that shows what a fixed layout gives away."""
    moved = [(f"other text {index}", (x0 - 2, y0 + 1, x1 - 2, y1 + 1), 0.5) for index, (_, (x0, y0, x1, y1), _) in enumerate(CELLS)]
    held_out = cached(moved, image_id="P-9", split="validation", claim="C-9")
    failed = cached([], image_id="P-8", split="validation", claim="C-8")
    predictions = position_baseline([page], alignments, [held_out, failed])
    assert predictions["P-8"] == ()                                      # M4 read nothing: nothing to look up
    assert [p.box_id for p in predictions["P-9"]] == [box.box_id for box in held_out.page.text_boxes]
    # Box 5 lies on the masked training box, which has no label to copy: it takes the nearest *scored* box instead.
    assert [p.label for p in predictions["P-9"]] == TRUTH[:5] + ["B-part_1_amount"] + TRUTH[6:]
    assert all(p.confidence == 1.0 for p in predictions["P-9"])          # a lookup has no score of its own

    record = annotation("P-9", "validation", handwriting=HANDWRITING)
    scored = box_summary(box_rows(align_pages([held_out], {"P-9": record}), {"P-9": predictions["P-9"]}))
    assert (scored["scored_boxes"], scored["masked_boxes"], scored["accuracy_all_scored"]) == (7, 1, 1.0)
    assert scored["field_micro"]["true_positive"] == 6 and scored["outside"]["true_positive"] == 1

    # Far from every training box the nearest label is still copied, right or wrong.
    lost = cached([("715.00", (20, 1300, 90, 1320), 0.99)], image_id="P-7", split="validation", claim="C-7")
    assert [p.label for p in position_baseline([page], alignments, [lost])["P-7"]] == ["B-part_2_description"]
    with pytest.raises(ValueError, match="no labelled training box"):
        position_baseline([failed], align_pages([failed], {"P-8": annotation("P-8")}), [held_out])
