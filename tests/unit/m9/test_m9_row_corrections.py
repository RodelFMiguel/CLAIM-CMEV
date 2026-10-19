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

