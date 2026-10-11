"""Contract safety for the optional adapter, without importing neural dependencies."""
import pytest

from claim_cmev.contracts.common import ContractError
from claim_cmev.documents.line_items.layoutlmv3 import WordPrediction, adapt_predictions
from m5_support import (BASIS, CLAIM_ID, CONFIG, JOB_KEY, PROVENANCE, VOCABULARY,
                        make_page, row_cells)


def adapt(pages, predictions):
    return adapt_predictions(
        pages, predictions, config=CONFIG, vocabulary=VOCABULARY,
        family_id="family-a-ruled-grid", job_key=JOB_KEY, claim_id=CLAIM_ID,
        input_revision=1, currency="SGD", cost_basis=BASIS,
        model_version="test-weights", provenance=PROVENANCE)


def test_exact_money_missing_quantity_stable_ids_and_source_links():
    page = make_page(row_cells({"description": "FRT BUMPER", "amount": "980.00"}, 200))
    predictions = {page.page_id: [WordPrediction(page.text_boxes[0].box_id, "B-part_1_description", .9),
                                  WordPrediction(page.text_boxes[1].box_id, "B-part_1_amount", .9)]}
    result = adapt([page], predictions)
    item = result.line_items[0]
    assert item.printed_line_amount == "980.00"
    assert item.quantity is None and item.unit_price is None
    assert item.part_code == "front-bumper"
    assert item.source_box_ids == [box.box_id for box in page.text_boxes]
    assert result.completeness.state == "partial"
    assert result.versions["extraction_method"] == "layoutlmv3"
    assert result.line_item_ids == adapt([page], predictions).line_item_ids


@pytest.mark.parametrize("amount,score,expected", [("48O.OO", .9, None), ("980.00", .1, None),
                                                  ("0.00", .9, "0.00")])
def test_unreadable_and_low_confidence_amounts_are_not_zero(amount, score, expected):
    page = make_page(row_cells({"description": "FRT BUMPER", "amount": amount}, 200))
    result = adapt([page], {page.page_id: [
        WordPrediction(page.text_boxes[0].box_id, "B-part_1_description", .9),
        WordPrediction(page.text_boxes[1].box_id, "B-part_1_amount", score)]})
    assert result.line_items[0].printed_line_amount == expected
    assert result.rows[0].field("amount").original_text == amount
    if score < .5:
        assert result.rows[0].field("amount").reason == "model_or_ocr_confidence_low"
        assert any(u.field == "printed_line_amount" and u.reason == "model_or_ocr_confidence_low"
                   for u in result.line_items[0].field_uncertainty)


def test_repeated_page_slots_keep_separate_ids():
    pages = [make_page(row_cells({"description": "FRT BUMPER"}, 200), page_number=n)
             for n in (1, 2)]
    predictions = {page.page_id: [WordPrediction(page.text_boxes[0].box_id,
                                               "B-part_1_description", .9)] for page in pages}
    assert len(set(adapt(pages, predictions).line_item_ids)) == 2


def test_no_rows_and_unreadable_are_never_explicitly_empty():
    page = make_page(row_cells({"description": "OTHER TEXT"}, 200))
    result = adapt([page], {page.page_id: [WordPrediction(page.text_boxes[0].box_id, "O", .9)]})
    assert result.completeness.state == "partial"
    assert "no_rows_matched" in result.completeness.reasons
    assert result.unparsed_regions[0].box_ids == (page.text_boxes[0].box_id,)
    unreadable = make_page([])
    assert adapt([unreadable], {}).completeness.state == "unreadable"


def test_incomplete_predictions_fail_instead_of_silent_omission():
    page = make_page(row_cells({"description": "FRT BUMPER"}, 200))
    with pytest.raises(ContractError, match="prediction_alignment_mismatch"):
        adapt([page], {})
