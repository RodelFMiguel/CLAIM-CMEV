"""Row mark states, helpers, and the no-printed-fallback invariant after every action sequence."""
import pytest

from claim_cmev.contracts.common import COST_BASIS
from claim_cmev.contracts.documents import effective_price_for, with_effective_price
from claim_cmev.contracts.fixtures import fixture_bundle
from claim_cmev.documents.pen_marks import (
    MarkAction,
    apply_mark_action,
    marks_for_entry,
    row_mark_states,
    unresolved_marks,
)
from m6_support import (
    ACTOR, API_PROV, CLAIM, HUMAN_VERSIONS, assert_no_printed_fallback, at, det, link, norm, ruled_rows,
)

ROWS = ruled_rows(4)
AMOUNT = {"confirmed_amount": "980.00", "confirmed_currency": "SGD", "confirmed_cost_basis": COST_BASIS}
X_E0 = det((1850, 1002, 2300, 1052), "exclusion", 0.88)
PC_E1 = det((2030, 1058, 2280, 1104), "price_change", 0.8)
PC_E1_LEFT = det((2040, 1064, 2150, 1110), "price_change", 0.8)
PC_E1_RIGHT = det((2170, 1064, 2290, 1110), "price_change", 0.8)
X_E1 = det((1850, 1062, 2300, 1112), "exclusion", 0.9)
PC_BETWEEN_E2_E3 = det((2005, 1152, 2210, 1196), "price_change", 0.7)
X_BETWEEN_E0_E1 = det((1850, 1030, 2320, 1080), "exclusion", 0.9)


def detect(*detections):
    return list(link(list(detections), ROWS).marks)


def find(marks, entry_id=None, mark_type=None, unlinked=False):
    hits = [m for m in marks if (m.entry_id is None if unlinked else m.entry_id == entry_id)
            and (mark_type is None or m.mark_type == mark_type)]
    return hits


def states(marks):
    return {e: s.state for e, s in row_mark_states(ROWS, marks).items()}


def apply(marks, action_type, action_id, **fields):
    return apply_mark_action(marks, MarkAction(action_type, action_id, **fields), actor=ACTOR, recorded_at=at(1),
                             review_revision=2, rows=ROWS, provenance=API_PROV, versions=HUMAN_VERSIONS)


# --- row states ------------------------------------------------------------------------------
def test_each_row_state_and_its_consequence():
    marks = detect(X_E0, PC_E1, PC_BETWEEN_E2_E3)
    view = row_mark_states(ROWS, marks)
    assert {e: s.state for e, s in view.items()} == {
        "e0": "pending", "e1": "pending", "e2": "unlinked_candidate", "e3": "unlinked_candidate"}
    assert all(s.withholds_decision and not s.excludes_row for s in view.values())
    assert view["e2"].unlinked_candidate_mark_ids == (find(marks, unlinked=True)[0].mark_id,)
    assert view["e2"].reason == "mark_unlinked" and view["e0"].reason == "mark_pending"

    x, pc, between = marks
    marks = apply(marks, "confirm_mark", "ra1", mark_id=x.mark_id)
    marks = apply(marks, "confirm_mark", "ra2", mark_id=pc.mark_id, **AMOUNT)
    marks = apply(marks, "reject_mark", "ra3", mark_id=between.mark_id)
    view = row_mark_states(ROWS, marks)
    assert {e: s.state for e, s in view.items()} == {
        "e0": "confirmed_exclusion", "e1": "confirmed_price_change", "e2": "none", "e3": "none"}
    assert view["e0"].excludes_row and not view["e0"].withholds_decision
    assert not view["e1"].excludes_row and not view["e1"].withholds_decision
    rejected = apply(detect(X_E0), "reject_mark", "ra4", mark_id=detect(X_E0)[0].mark_id)
    only = row_mark_states(ROWS, rejected)["e0"]
    assert only.state == "rejected" and only.active_mark_ids == () and not only.withholds_decision


@pytest.mark.parametrize("detections, reason", [
    ((PC_E1, X_E1), "exclusion_and_price_change"),
    ((PC_E1_LEFT, PC_E1_RIGHT), "multiple_price_changes"),
])
def test_conflicting_rows_withhold_and_outrank_every_other_state(detections, reason):
    marks = detect(*detections, X_BETWEEN_E0_E1)  # e1 is also a candidate of an unlinked exclusion
    view = row_mark_states(ROWS, marks)["e1"]
    assert (view.state, view.conflict_reason, view.reason) == ("conflicting", reason, reason)
    assert view.withholds_decision and len(view.active_mark_ids) == 2 and view.unlinked_candidate_mark_ids


def test_an_unlinked_candidate_outranks_a_confirmed_exclusion():
    marks = detect(X_E1, X_BETWEEN_E0_E1)
    linked = find(marks, "e1")[0]
    marks = apply(marks, "confirm_mark", "ra1", mark_id=linked.mark_id)
    assert states(marks)["e1"] == "unlinked_candidate"  # never silently excluded while a mark is unresolved


def test_rows_may_be_ids_line_items_or_mappings():
    marks = detect(X_E0)
    expected = row_mark_states(ROWS, marks)
    assert row_mark_states([r.entry_id for r in ROWS], marks) == expected
    assert row_mark_states([{"entry_id": r.entry_id} for r in ROWS], marks) == expected


def test_marks_for_entry_and_unresolved_marks():
    marks = detect(X_E0, PC_E1, PC_BETWEEN_E2_E3)
    x, pc, between = marks
    assert marks_for_entry(marks, "e0") == [x]
    assert marks_for_entry(marks, "e2") == [between]
    assert marks_for_entry(marks, "e2", include_unlinked_candidates=False) == []
    assert unresolved_marks(marks) == marks
    marks = apply(marks, "confirm_mark", "ra1", mark_id=x.mark_id)
    marks = apply(marks, "confirm_mark", "ra2", mark_id=pc.mark_id, **AMOUNT)
    assert unresolved_marks(marks) == [marks[2]]
    marks = apply(marks, "reject_mark", "ra3", mark_id=between.mark_id)
    assert unresolved_marks(marks) == []
    # A confirmed mark on a conflicting row is still unresolved.
    conflict = detect(PC_E1, X_E1)
    conflict = apply(conflict, "confirm_mark", "ra4", mark_id=find(conflict, "e1", "exclusion")[0].mark_id)
    assert {m.mark_id for m in unresolved_marks(conflict)} == {m.mark_id for m in conflict}


def test_fixture_bundles_have_the_expected_row_states():
    partial = fixture_bundle("partial_extraction", CLAIM)
    view = row_mark_states(partial.line_items, partial.pen_marks)
    assert [s.state for s in view.values()] == ["unlinked_candidate", "unlinked_candidate", "none"]
    supported = fixture_bundle("exclusion_and_supported", CLAIM)
    view = row_mark_states(supported.line_items, supported.pen_marks)
    assert [s.state for s in view.values()] == ["confirmed_exclusion", "confirmed_price_change", "none"]


# --- the safety invariant across action sequences --------------------------------------------
def _seq_relink_then_confirm():
    marks = detect(PC_BETWEEN_E2_E3)
    yield marks
    marks = apply(marks, "correct_mark_link", "ra1", mark_id=marks[0].mark_id, target_entry_id="e3")
    yield marks
    yield apply(marks, "confirm_mark", "ra2", mark_id=marks[0].mark_id, **AMOUNT)


def _seq_two_price_changes():
    marks = detect(PC_E1_LEFT, PC_E1_RIGHT)
    yield marks
    marks = apply(marks, "confirm_mark", "ra1", mark_id=marks[0].mark_id, **AMOUNT)
    yield marks
    marks = apply(marks, "enter_amount", "ra2", mark_id=marks[0].mark_id, **{**AMOUNT, "confirmed_amount": "990.00"})
    yield marks
    yield apply(marks, "reject_mark", "ra3", mark_id=marks[1].mark_id)


def _seq_exclusion_and_price_change():
    marks = detect(PC_E1, X_E1)
    pc, x = find(marks, "e1", "price_change")[0], find(marks, "e1", "exclusion")[0]
    yield marks
    marks = apply(marks, "confirm_mark", "ra1", mark_id=x.mark_id)
    yield marks
    marks = apply(marks, "confirm_mark", "ra2", mark_id=pc.mark_id, **AMOUNT)
    yield marks  # both confirmed: still conflicting, the price stays unresolved
    marks = apply(marks, "correct_mark_link", "ra3", mark_id=x.mark_id, target_entry_id="e2")
    yield marks


def _seq_unlinked_then_rejected():
    marks = detect(PC_BETWEEN_E2_E3, X_E0)
    yield marks
    yield apply(marks, "reject_mark", "ra1", mark_id=find(marks, unlinked=True)[0].mark_id)


def _seq_human_added_on_pending_row():
    marks = detect(PC_E1)
    yield marks
    marks = apply(marks, "add_mark", "ra1", mark_type="price_change", page_id="dp1",
                  box_norm=norm((2295, 1064, 2335, 1086)), target_entry_id="e1", **AMOUNT)
    yield marks  # a human price change beside a pending detected one: conflicting
    yield apply(marks, "reject_mark", "ra2", mark_id=marks[0].mark_id)


def _seq_pending_moved_to_another_row():
    marks = detect(PC_E1)
    yield marks
    yield apply(marks, "correct_mark_link", "ra1", mark_id=marks[0].mark_id, target_entry_id="e2")


SEQUENCES = [_seq_relink_then_confirm, _seq_two_price_changes, _seq_exclusion_and_price_change,
             _seq_unlinked_then_rejected, _seq_human_added_on_pending_row, _seq_pending_moved_to_another_row]


@pytest.mark.parametrize("sequence", SEQUENCES, ids=lambda s: s.__name__)
def test_unresolved_repricing_never_uses_the_printed_price(sequence):
    for marks in sequence():
        assert_no_printed_fallback(ROWS, marks)
        for row in ROWS:  # the contract helper produces a valid LineItem for every row
            with_effective_price(row, marks)


def test_expected_prices_along_the_sequences():
    printed = {r.entry_id: r.printed_line_amount for r in ROWS}

    def prices(marks):
        return {r.entry_id: effective_price_for(r.printed_line_amount, marks, entry_id=r.entry_id)[:2] for r in ROWS}

    steps = list(_seq_relink_then_confirm())
    assert prices(steps[0])["e2"] == prices(steps[0])["e3"] == (None, "unresolved")
    assert prices(steps[1])["e2"] == (printed["e2"], "printed") and prices(steps[1])["e3"] == (None, "unresolved")
    assert prices(steps[2])["e3"] == ("980.00", "surveyor_entry")
    steps = list(_seq_two_price_changes())
    assert [prices(s)["e1"] for s in steps] == [(None, "unresolved")] * 3 + [("990.00", "surveyor_entry")]
    steps = list(_seq_exclusion_and_price_change())
    assert [prices(s)["e1"] for s in steps] == [(None, "unresolved")] * 3 + [("980.00", "surveyor_entry")]
    assert states(steps[-1])["e2"] == "confirmed_exclusion"
    steps = list(_seq_pending_moved_to_another_row())
    assert prices(steps[1])["e1"] == (printed["e1"], "printed") and prices(steps[1])["e2"] == (None, "unresolved")
