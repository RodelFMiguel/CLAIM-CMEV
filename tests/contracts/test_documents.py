"""Document records: line items, the effective-price rule, completeness and pen marks."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from claim_cmev.contracts import (
    DeclarationCompleteness,
    DocumentPage,
    LineItem,
    PageTransform,
    PenMark,
    effective_price_for,
    with_effective_price,
)
from contract_factories import artifact, completeness, decided, line_item, pen_mark, SCOPE


def mark(**kw) -> PenMark:
    return PenMark(**pen_mark(**kw))


# --- effective-price rule (data contracts section 7.2) ------------------------------------
def test_pending_price_change_never_yields_the_printed_amount():
    result = effective_price_for("1150.00", [mark()])
    assert result == (None, "unresolved", "price_change_pending")
    item = with_effective_price(LineItem(**line_item(printed_line_amount="1150.00", effective_price="1150.00")), [mark()])
    assert item.effective_price is None and item.printed_line_amount == "1150.00"
    assert item.effective_price_source == "unresolved" and item.effective_price_reason == "price_change_pending"


def test_confirmed_price_change_uses_the_surveyor_amount_not_the_printed_one():
    confirmed = mark(state="confirmed", confirmed_amount="980.00", confirmed_currency="SGD",
                     confirmed_cost_basis="single_part_pre_tax_no_discount_v1", **decided())
    assert effective_price_for("1150.00", [confirmed]) == ("980.00", "surveyor_entry", None)


def test_conflicting_marks_leave_the_price_unresolved():
    other = mark(mark_id="pm2")
    assert effective_price_for("1150.00", [mark(), other]).reason == "mark_conflicting"
    exclusion = mark(mark_id="pm3", mark_type="exclusion")
    assert effective_price_for("1150.00", [mark(), exclusion]).effective_price_source == "unresolved"


def test_rejected_marks_and_other_rows_do_not_count():
    rejected = mark(state="rejected", **decided())
    assert effective_price_for("980.00", [rejected]) == ("980.00", "printed", None)
    elsewhere = mark(entry_id="li9", candidate_entry_ids=["li9"])
    assert effective_price_for("980.00", [elsewhere], entry_id="li1") == ("980.00", "printed", None)


def test_unlinked_price_change_naming_the_row_blocks_the_printed_amount():
    unlinked = mark(entry_id=None, candidate_entry_ids=["li1", "li2"], link_reason="mark_between_rows")
    assert effective_price_for("980.00", [unlinked], entry_id="li1") == (None, "unresolved", "price_change_unlinked")


def test_unreadable_printed_amount_is_unresolved_not_zero():
    assert effective_price_for(None, []) == (None, "unresolved", "amount_unreadable")


# --- LineItem -------------------------------------------------------------------------------
def test_line_item_valid_and_exact_round_trip():
    item = LineItem(**line_item(unit_price="980.10", printed_line_amount="980.10", effective_price="980.10"))
    again = LineItem.model_validate_json(item.model_dump_json())
    assert again == item and again.printed_line_amount == "980.10"


@pytest.mark.parametrize("overrides", [
    {"printed_line_amount": 980.0},  # money as a JSON number
    {"quantity": 1},  # quantity as a number
    {"quantity": None},  # a missing quantity needs a reason and is never one
    {"part_code": None},  # null part without mapping status and reason
    {"part_code": None, "part_mapping_status": "ambiguous"},  # still needs a field_uncertainty reason
    {"operation": "other", "operation_mapping_status": "ambiguous"},  # mapped value with a non-resolved status
    {"side": "left", "side_source": "absent"},  # side without a source
    {"side": "unknown", "side_source": "document_text"},
    {"effective_price": "1150.00"},  # 'printed' effective price differing from the printed amount
    {"effective_price": None},  # printed source with no amount
    {"effective_price": None, "effective_price_source": "unresolved"},  # unresolved without a reason
    {"effective_price_source": "unresolved", "effective_price_reason": "price_change_pending"},  # amount kept
    {"field_uncertainty": [{"field": "colour", "reason": "x"}]},
    {"quantity": "-1"},
    {"side": "LH"},
])
def test_line_item_invalid(overrides):
    with pytest.raises(ValidationError):
        LineItem(**line_item(**overrides))


def test_line_item_null_fields_carry_reasons():
    item = LineItem(**line_item(part_code=None, part_mapping_status="ambiguous", operation=None,
                                operation_mapping_status="unmapped", quantity=None, unit_price=None,
                                printed_line_amount=None, effective_price=None, effective_price_source="unresolved",
                                effective_price_reason="amount_unreadable", amount_box_norm=None,
                                side="unknown", side_source="absent",
                                field_uncertainty=[{"field": f, "reason": "column_not_located"} for f in (
                                    "part_code", "operation", "quantity", "unit_price", "printed_line_amount",
                                    "amount_box_norm")]))
    assert item.quantity is None and item.printed_line_amount is None


# --- DeclarationCompleteness ------------------------------------------------------------------
def test_zero_parsed_rows_is_never_explicitly_empty():
    with pytest.raises(ValidationError, match="only a surveyor confirmation"):
        DeclarationCompleteness(**completeness(state="explicitly_empty", reasons=["no_rows_matched"]))
    parsed = DeclarationCompleteness(**completeness(state="partial", reasons=["no_rows_matched"]))
    assert parsed.state == "partial"
    human = DeclarationCompleteness(**completeness(state="explicitly_empty", reasons=["surveyor_confirmed_empty"],
                                                   source="human_confirmation", confirmed_by="surveyor:t",
                                                   confirmed_at="2026-09-22T03:20:00Z", review_revision=2))
    assert human.state == "explicitly_empty"


@pytest.mark.parametrize("overrides", [
    {"state": "partial", "reasons": []},
    {"layout_family": None},
    {"source": "human_confirmation"},
    {"confirmed_by": "surveyor:t"},
    {"unparsed_region_count": -1},
    {"state": "empty"},
])
def test_completeness_invalid(overrides):
    with pytest.raises(ValidationError):
        DeclarationCompleteness(**completeness(**overrides))


# --- PenMark ------------------------------------------------------------------------------------
def test_pen_mark_valid_states():
    assert mark().state == "pending"
    assert mark(state="rejected", **decided()).state == "rejected"
    human = mark(origin="human_added", detection_confidence=None, state="confirmed", mark_type="exclusion",
                 link_reason="human_link", **decided())
    assert human.origin == "human_added"
    unlinked = mark(entry_id=None, candidate_entry_ids=[], link_reason="no_candidate_row")
    assert unlinked.entry_id is None
    suggested = mark(trocr_suggestion={"text": "980", "confidence": 0.61})
    assert suggested.confirmed_amount is None and suggested.trocr_suggestion.text == "980"


@pytest.mark.parametrize("overrides", [
    {"state": "confirmed", **decided()},  # confirmed price change without an amount
    {"origin": "human_added", "detection_confidence": None},  # human-added but pending
    {"origin": "human_added", "state": "confirmed", "mark_type": "exclusion", **decided()},  # has a confidence
    {"detection_confidence": None},  # detector mark without its confidence
    {"entry_id": None, "candidate_entry_ids": [], "link_reason": "mark_between_rows"},  # candidates dropped
    {"entry_id": None, "link_reason": "unambiguous_row_overlap"},  # claims a link it lacks
    {"link_reason": "mark_between_rows"},  # linked mark with an unlinked reason
    {"confirmed_amount": "980.00", "confirmed_currency": "SGD",
     "confirmed_cost_basis": "single_part_pre_tax_no_discount_v1"},  # amount on a pending mark
    {"state": "confirmed", "confirmed_amount": "980.00", **decided()},  # amount without currency and basis
    {"decided_by": "surveyor:t"},  # decision fields on a pending mark
    {"state": "rejected"},  # decided without decision fields
    {"mark_type": "tick"},
    {"state": "confirmed", "mark_type": "exclusion", "confirmed_amount": "1.00", "confirmed_currency": "SGD",
     "confirmed_cost_basis": "x", **decided()},  # an exclusion carries no amount
])
def test_pen_mark_invalid(overrides):
    with pytest.raises(ValidationError):
        mark(**overrides)


# --- DocumentPage -------------------------------------------------------------------------------
def page(**kw):
    box = {"box_id": "bx1", "order_index": 0, "text": "FRT BUMPER", "confidence": 0.97,
           "box_norm": (0.08, 0.25, 0.37, 0.27), "quad_rectified": ((201, 872), (900, 872), (900, 920), (201, 920)),
           "quad_original": ((201, 872), (900, 872), (900, 920), (201, 920)), "granularity": "line"}
    data = {**SCOPE, "versions": {"ocr": "paddleocr/2.7.3"}, "page_id": "dp1", "file_id": "pg1", "page_number": 1,
            "corrected_render_ref": artifact("r1"), "page_reading_ref": artifact("j1", "application/json"),
            "transform": {"source_width": 2480, "source_height": 3508, "corrected_width": 2480,
                          "corrected_height": 3508, "geometry_correction": "none", "correction_reason": "flat_scan"},
            "text_granularity": "line", "text_boxes": [box], "mean_text_confidence": 0.97,
            "quality": {"state": "complete"}}
    data.update(kw)
    return DocumentPage(**data)


def test_document_page_rules():
    assert page().transform.perspective_applied is False
    assert page(text_boxes=[], mean_text_confidence=None, mean_text_confidence_reason="ocr_engine_error",
                quality={"state": "unreadable", "reasons": ["ocr_engine_error"]}).text_boxes == []
    with pytest.raises(ValidationError, match="unreadable"):
        page(text_boxes=[], mean_text_confidence=None, mean_text_confidence_reason="no_boxes")
    with pytest.raises(ValidationError, match="granularity"):
        page(text_granularity="word")  # never invent word boxes from line boxes
    with pytest.raises(ValidationError):
        page(mean_text_confidence=None)  # null without its reason
    with pytest.raises(ValidationError):
        page(quality={"state": "partial"})
    with pytest.raises(ValidationError):
        page(page_number=0)


def test_perspective_page_transform_round_trip():
    # A real perspective warp: a 3024x4032 photo of a page mapped to a 2480x3508 render.
    import numpy as np
    import cv2

    src = np.float32([[412, 388], [2688, 452], [2790, 3702], [301, 3615]])
    dst = np.float32([[0, 0], [2480, 0], [2480, 3508], [0, 3508]])
    h = cv2.getPerspectiveTransform(src, dst)
    t = PageTransform(source_width=3024, source_height=4032, corrected_width=2480, corrected_height=3508,
                      geometry_correction="perspective", correction_reason="boundary_reliable",
                      homography=h.tolist(), homography_inverse=np.linalg.inv(h).tolist())
    for (sx, sy), (cx, cy) in zip(src.tolist(), dst.tolist()):
        x, y = t.source_to_corrected(sx, sy)
        assert abs(x - cx) < 1e-3 and abs(y - cy) < 1e-3
        bx, by = t.corrected_to_source(x, y)
        assert abs(bx - sx) < 1e-3 and abs(by - sy) < 1e-3
    quad = t.norm_box_to_source_quad((0.0, 0.0, 1.0, 1.0))
    assert all(abs(a - b) < 1e-2 for p, q in zip(quad, src.tolist()) for a, b in zip(p, q))
    pdf = PageTransform(source_width=2480, source_height=3508, corrected_width=2480, corrected_height=3508,
                        render_scale=300 / 72, geometry_correction="none", correction_reason="pdf_render")
    assert pdf.corrected_to_pdf_points(2480, 3508) == pytest.approx((595.2, 841.92))
