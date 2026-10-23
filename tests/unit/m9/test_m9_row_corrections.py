"""Row corrections as the consolidator replays them: never an invalid row, never an inferred side."""
import pytest

from claim_cmev.contracts.fixtures import fixture_bundle
from claim_cmev.orchestration.corrections import correct_line_item

CLAIM = "01K50000000000000000000001"


def bumper():
    items = fixture_bundle("exclusion_and_supported", CLAIM, 1).line_items
    item = next(i for i in items if i.part_code == "front-bumper")
    assert (item.side, item.side_source) == ("not_applicable", "absent")  # unsided part, no printed side
    return item


@pytest.mark.parametrize("part", ["fender", "unknown"])
def test_moving_to_a_sided_or_unknown_part_leaves_the_side_unknown(part):
    row = correct_line_item(bumper(), {"part_code": part}, input_revision=2, action_id="ra-1")
    assert (row.side, row.side_source) == ("unknown", "absent")
    assert row.part_code == (None if part == "unknown" else part)


def test_moving_to_another_unsided_part_keeps_not_applicable():
    row = correct_line_item(bumper(), {"part_code": "hood"})
    assert (row.part_code, row.side, row.side_source) == ("hood", "not_applicable", "absent")


def test_an_explicit_side_is_a_human_correction():
    row = correct_line_item(bumper(), {"part_code": "fender", "side": "left"})
    assert (row.side, row.side_source) == ("left", "human_correction")


def test_apply_corrections_with_mapped_entry_id():
    from datetime import datetime, timezone
    from claim_cmev.contracts.review import ReviewEvent
    from claim_cmev.orchestration.corrections import apply_corrections

    bundle = fixture_bundle("exclusion_and_supported", CLAIM, 1)
    item = bundle.line_items[0]
    new_item = item.model_copy(update={"entry_id": "li-new123456789"})

    ev = ReviewEvent(
        event_id="rve-1", action_id="ra-1", claim_id=CLAIM, assessment_revision=1,
        expected_review_revision=0, resulting_review_revision=1, actor="Surveyor",
        recorded_at=datetime.now(timezone.utc), action_type="correct_line_item",
        entry_id=item.entry_id, new_values={"printed_line_amount": "999.00"},
        idempotency_key="idem-12345678"
    )
    claim_input = {"corrections": [{"kind": "review_event", "event": ev.model_dump(mode="json")}]}
    entry_map = {item.entry_id: new_item.entry_id}

    res_items, _, _ = apply_corrections({"line_item": [new_item]}, [], claim_input, 2, entry_map=entry_map)
    assert len(res_items) == 1
    assert res_items[0].entry_id == "li-new123456789"
    assert res_items[0].effective_price == "999.00"
    assert res_items[0].printed_amount_corrected is True


def reextracted_rows():
    """The fixture page's three full-width rows, re-extracted under new IDs with new OCR text."""
    items = fixture_bundle("exclusion_and_supported", CLAIM, 1).line_items
    texts = {"back-door": "REAR DR L/H", "front-bumper": "FRNT BUMPR", "fender": "FNDR L/H"}
    return [i.model_copy(update={"entry_id": f"li-new{n:020d}", "original_part_text": texts[i.part_code]})
            for n, i in enumerate(items)]


def old_row(rows, of_part, /, **changes):
    old = next(r for r in rows if r.part_code == of_part).model_dump(mode="json")
    return {**old, "entry_id": "li-old" + of_part, **changes}


def test_a_row_is_matched_by_its_vertical_band_not_its_horizontal_extent():
    from claim_cmev.orchestration.corrections import _match_line_item
    rows = reextracted_rows()
    # Only the left part of the bumper row was boxed before, and its amount and part were misread:
    # box IoU is below 0.3, so only the row band (y 0.266-0.280) identifies it.
    old = old_row(rows, "front-bumper", original_part_text="FRT BUMPER", printed_line_amount="1105.00",
                  part_code=None, row_box_norm=[0.08, 0.266, 0.30, 0.280])
    assert _match_line_item(old, rows).part_code == "front-bumper"


def test_a_row_with_no_counterpart_is_not_matched_to_another_row():
    from claim_cmev.orchestration.corrections import _match_line_item
    rows = reextracted_rows()
    old = old_row(rows, "fender", original_part_text="BONNET", part_code="hood", printed_line_amount="700.00",
                  row_box_norm=[0.08, 0.60, 0.92, 0.62])
    assert _match_line_item(old, rows) is None


def _event_on(entry_id):
    return {"kind": "review_event", "event": {"action_type": "correct_line_item", "entry_id": entry_id}}


def test_two_old_rows_matching_one_new_row_are_both_left_unmapped(monkeypatch):
    from claim_cmev.orchestration import corrections
    rows = reextracted_rows()
    bumper_row = next(r for r in rows if r.part_code == "front-bumper")
    old = {f"li-old{n}": old_row(rows, "front-bumper", entry_id=f"li-old{n}",
                                 original_part_text=bumper_row.original_part_text) for n in (1, 2)}
    monkeypatch.setattr(corrections, "_find_old_line_items", lambda _s, _c, ids: {i: old[i] for i in ids})
    mapping = corrections.map_line_item_ids(object(), CLAIM, rows,
                                            {"corrections": [_event_on("li-old1"), _event_on("li-old2")]})
    assert "li-old1" not in mapping and "li-old2" not in mapping


def test_an_old_row_is_not_mapped_onto_a_row_that_already_has_its_own_correction(monkeypatch):
    from claim_cmev.orchestration import corrections
    rows = reextracted_rows()
    bumper_row = next(r for r in rows if r.part_code == "front-bumper")
    old = {"li-old1": old_row(rows, "front-bumper", entry_id="li-old1",
                              original_part_text=bumper_row.original_part_text)}
    monkeypatch.setattr(corrections, "_find_old_line_items", lambda _s, _c, ids: {i: old[i] for i in ids})
    mapping = corrections.map_line_item_ids(object(), CLAIM, rows, {"corrections": [
        _event_on("li-old1"), _event_on(bumper_row.entry_id)]})
    assert "li-old1" not in mapping
    assert mapping[bumper_row.entry_id] == bumper_row.entry_id


def test_a_matched_old_row_is_mapped(monkeypatch):
    from claim_cmev.orchestration import corrections
    rows = reextracted_rows()
    bumper_row = next(r for r in rows if r.part_code == "front-bumper")
    old = {"li-old1": old_row(rows, "front-bumper", entry_id="li-old1",
                              original_part_text=bumper_row.original_part_text)}
    monkeypatch.setattr(corrections, "_find_old_line_items", lambda _s, _c, ids: {i: old[i] for i in ids})
    mapping = corrections.map_line_item_ids(object(), CLAIM, rows, {"corrections": [_event_on("li-old1")]})
    assert mapping["li-old1"] == bumper_row.entry_id


def test_apply_corrections_with_mapped_add_mark():
    from datetime import datetime, timezone
    from claim_cmev.contracts.review import ReviewEvent
    from claim_cmev.orchestration.corrections import apply_corrections

    bundle = fixture_bundle("exclusion_and_supported", CLAIM, 1)
    item = bundle.line_items[0]
    new_item = item.model_copy(update={"entry_id": "li-new987654321"})

    ev = ReviewEvent(
        event_id="rve-2", action_id="ra-2", claim_id=CLAIM, assessment_revision=1,
        expected_review_revision=0, resulting_review_revision=1, actor="Surveyor",
        recorded_at=datetime.now(timezone.utc), action_type="add_mark",
        entry_id=item.entry_id,
        new_values={
            "mark_id": "hmark-1", "entry_id": item.entry_id, "page_id": item.page_id,
            "box_norm": [0.1, 0.2, 0.8, 0.3], "box_source": "surveyor_box",
            "mark_type": "price_change", "state": "confirmed", "origin": "human_added",
            "link_reason": "human_link", "amount": "150.00", "currency": "SGD",
            "cost_basis": "single_part_pre_tax_no_discount_v1"
        },
        idempotency_key="idem-addmark-1"
    )
    claim_input = {"corrections": [{"kind": "review_event", "event": ev.model_dump(mode="json")}]}
    entry_map = {item.entry_id: new_item.entry_id}

    res_items, res_marks, _ = apply_corrections({"line_item": [new_item]}, [], claim_input, 2, entry_map=entry_map)
    assert len(res_items) == 1
    assert len(res_marks) == 1
    assert res_marks[0].entry_id == "li-new987654321"
    assert res_marks[0].confirmed_amount == "150.00"
    assert res_items[0].effective_price == "150.00"
    assert res_items[0].effective_price_source == "surveyor_entry"

