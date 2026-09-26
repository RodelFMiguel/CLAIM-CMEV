"""apply_mark_action: every legal and illegal transition, human-added marks, relinking and replay."""
import pytest

from claim_cmev.contracts.common import COST_BASIS, ContractError, deterministic_id
from claim_cmev.contracts.documents import TrocrSuggestion
from claim_cmev.contracts.review import ReviewEvent
from claim_cmev.documents.pen_marks import (
    ACTION_ERROR_CODES,
    Detection,
    MarkAction,
    apply_mark_action,
    apply_review_event,
    mark_action_from_review_event,
    transition_mark,
)
from m6_support import (
    ACTOR, API_PROV, CLAIM, HUMAN_VERSIONS, at, det, line_item, link, norm, ruled_rows, worked_example_rows,
)

ROWS = ruled_rows(4) + ruled_rows(2, page_id="dp2", prefix="p", page_number=2)
AMOUNT = {"confirmed_amount": "980.00", "confirmed_currency": "SGD", "confirmed_cost_basis": COST_BASIS}


def detected():
    """X on e0 (EX-1), price change over e1's amount (PC-1), price change between e2 and e3 (PC-3)."""
    marks = list(link([det((1850, 1002, 2300, 1052), "exclusion", 0.88),
                       det((2030, 1058, 2280, 1104), "price_change", 0.8),
                       det((2005, 1152, 2210, 1196), "price_change", 0.7)], ROWS).marks)
    x, pc, between = marks
    assert (x.entry_id, pc.entry_id, between.entry_id) == ("e0", "e1", None)
    assert set(between.candidate_entry_ids) == {"e2", "e3"}
    return marks


def act(marks, action_type, action_id="ra1", *, minutes=1, revision=2, rows=ROWS, **fields):
    return apply_mark_action(marks, MarkAction(action_type, action_id, **fields), actor=ACTOR, recorded_at=at(minutes),
                             review_revision=revision, rows=rows, provenance=API_PROV, versions=HUMAN_VERSIONS)


def fails(code, marks, action_type, action_id="ra9", **fields):
    assert code in ACTION_ERROR_CODES
    with pytest.raises(ContractError) as err:
        act(marks, action_type, action_id, **fields)
    assert err.value.reason_code == code, err.value
    return err.value


def only_changed(before, after, mark_id):
    assert len(before) == len(after)
    for old, new in zip(before, after):
        if old.mark_id != mark_id:
            assert new is old  # confirming one mark never touches another


# --- confirm_mark ----------------------------------------------------------------------------
def test_confirm_exclusion_records_the_decision_and_nothing_else():
    marks = detected()
    x = marks[0]
    after = act(marks, "confirm_mark", mark_id=x.mark_id, minutes=5, revision=3)
    only_changed(marks, after, x.mark_id)
    new = after[0]
    assert (new.state, new.decision_action_id, new.decided_by, new.decided_at, new.review_revision) == (
        "confirmed", "ra1", ACTOR, at(5), 3)
    assert new.entry_id == "e0" and new.confirmed_amount is None and new.origin == "detector"
    assert x.state == "pending" and marks[0] is x  # input records are never mutated


def test_confirm_price_change_requires_an_exact_positive_typed_amount():
    marks = detected()
    pc = marks[1]
    fails("amount_required", marks, "confirm_mark", mark_id=pc.mark_id)
    fails("amount_required", marks, "confirm_mark", mark_id=pc.mark_id, confirmed_amount="980.00")
    for bad in ["0.00", "0", "-5.00", "980.005", "1,200.00", "980.0O", "", 980.0]:
        fails("amount_invalid", marks, "confirm_mark", mark_id=pc.mark_id, **{**AMOUNT, "confirmed_amount": bad})
    fails("currency_mismatch", marks, "confirm_mark", mark_id=pc.mark_id, **{**AMOUNT, "confirmed_currency": "MYR"})
    fails("cost_basis_mismatch", marks, "confirm_mark", mark_id=pc.mark_id,
          **{**AMOUNT, "confirmed_cost_basis": "with_tax_v1"})
    after = act(marks, "confirm_mark", mark_id=pc.mark_id, **AMOUNT)
    only_changed(marks, after, pc.mark_id)
    assert (after[1].state, after[1].confirmed_amount, after[1].confirmed_currency, after[1].confirmed_cost_basis) == (
        "confirmed", "980.00", "SGD", COST_BASIS)


def test_confirm_exclusion_rejects_an_amount_and_unlinked_marks_need_a_row():
    marks = detected()
    fails("amount_not_allowed", marks, "confirm_mark", mark_id=marks[0].mark_id, **AMOUNT)
    fails("mark_unlinked", marks, "confirm_mark", mark_id=marks[2].mark_id, **AMOUNT)


def test_confirm_can_relink_an_unlinked_mark_in_the_same_action():
    marks = detected()
    between = marks[2]
    after = act(marks, "confirm_mark", mark_id=between.mark_id, target_entry_id="e3", **AMOUNT)
    new = after[2]
    assert (new.entry_id, new.link_reason, new.model_entry_id, new.state) == ("e3", "human_link", None, "confirmed")
    assert new.candidate_entry_ids == between.candidate_entry_ids and new.rule_id == "PC-3"
    fails("entry_on_other_page", marks, "confirm_mark", mark_id=between.mark_id, target_entry_id="p0", **AMOUNT)
    fails("entry_not_found", marks, "confirm_mark", mark_id=between.mark_id, target_entry_id="zz", **AMOUNT)
    with pytest.raises(ContractError) as err:
        act(marks, "confirm_mark", mark_id=between.mark_id, target_entry_id="e3", rows=None, **AMOUNT)
    assert err.value.reason_code == "rows_required"


def test_decided_marks_cannot_be_confirmed_or_rejected_again_but_replays_are_idempotent():
    marks = act(detected(), "confirm_mark", "ra1", mark_id=detected()[0].mark_id)
    x = marks[0]
    fails("mark_already_decided", marks, "confirm_mark", "ra2", mark_id=x.mark_id)
    fails("mark_already_decided", marks, "reject_mark", "ra2", mark_id=x.mark_id)
    replay = transition_mark(marks, MarkAction("confirm_mark", "ra1", mark_id=x.mark_id), actor=ACTOR,
                             recorded_at=at(99), review_revision=9)
    assert replay.replay and not replay.changed and list(replay.marks) == marks and replay.after is x
    rejected = act(marks, "reject_mark", "ra3", mark_id=marks[1].mark_id)
    fails("mark_already_decided", rejected, "confirm_mark", "ra4", mark_id=marks[1].mark_id, **AMOUNT)


# --- reject_mark -----------------------------------------------------------------------------
def test_reject_retains_the_record_and_its_link():
    marks = detected()
    after = act(marks, "reject_mark", mark_id=marks[2].mark_id, reason_code="not_a_mark")
    only_changed(marks, after, marks[2].mark_id)
    new = after[2]
    assert new.state == "rejected" and new.entry_id is None and new.candidate_entry_ids == marks[2].candidate_entry_ids
    assert new.detection_confidence == marks[2].detection_confidence and len(after) == len(marks)


def test_reject_not_a_row_needs_a_note_and_takes_no_amount_or_link():
    marks = detected()
    fails("note_required", marks, "reject_mark", mark_id=marks[2].mark_id, reason_code="not_a_row")
    fails("note_required", marks, "reject_mark", mark_id=marks[2].mark_id, reason_code="not_a_row", note="  ")
    ok = act(marks, "reject_mark", mark_id=marks[2].mark_id, reason_code="not_a_row", note="margin doodle")
    assert ok[2].state == "rejected"
    fails("field_not_allowed", marks, "reject_mark", mark_id=marks[1].mark_id, **AMOUNT)
    fails("field_not_allowed", marks, "reject_mark", mark_id=marks[1].mark_id, target_entry_id="e2")


# --- correct_mark_link ----------------------------------------------------------------------
def test_relink_a_pending_mark_keeps_it_pending_and_keeps_the_model_link():
    marks = detected()
    pc = marks[1]
    after = act(marks, "correct_mark_link", mark_id=pc.mark_id, target_entry_id="e2")
    only_changed(marks, after, pc.mark_id)
    new = after[1]
    assert (new.entry_id, new.link_reason, new.model_entry_id, new.state) == ("e2", "human_link", "e1", "pending")
    assert (new.decision_action_id, new.decided_by) == (None, None)  # the review event carries the actor
    again = act(after, "correct_mark_link", "ra2", mark_id=pc.mark_id, target_entry_id="e3")
    assert (again[1].entry_id, again[1].model_entry_id) == ("e3", "e1")
    same = transition_mark(again, MarkAction("correct_mark_link", "ra3", mark_id=pc.mark_id, target_entry_id="e3"),
                           actor=ACTOR, recorded_at=at(3), review_revision=4, rows=ROWS)
    assert not same.changed and same.after is again[1]


def test_relink_validates_the_target_row():
    marks = detected()
    pc = marks[1]
    fails("field_required", marks, "correct_mark_link", mark_id=pc.mark_id)
    fails("entry_not_found", marks, "correct_mark_link", mark_id=pc.mark_id, target_entry_id="nope")
    fails("entry_on_other_page", marks, "correct_mark_link", mark_id=pc.mark_id, target_entry_id="p1")
    with pytest.raises(ContractError) as err:
        act(marks, "correct_mark_link", mark_id=pc.mark_id, target_entry_id="e2", rows=None)
    assert err.value.reason_code == "rows_required"


def test_relink_a_confirmed_mark_keeps_it_confirmed_and_records_the_new_decision():
    marks = act(detected(), "confirm_mark", "ra1", mark_id=detected()[1].mark_id, **AMOUNT)
    after = act(marks, "correct_mark_link", "ra2", mark_id=marks[1].mark_id, target_entry_id="e2", minutes=7,
                revision=5)
    new = after[1]
    assert (new.state, new.entry_id, new.confirmed_amount, new.decision_action_id, new.review_revision) == (
        "confirmed", "e2", "980.00", "ra2", 5)
    rows = ROWS[:2] + [line_item("e2", 1120, 1174, currency="MYR")] + ROWS[3:]
    with pytest.raises(ContractError) as err:
        act(marks, "correct_mark_link", "ra3", mark_id=marks[1].mark_id, target_entry_id="e2", rows=rows)
    assert err.value.reason_code == "currency_mismatch"


def test_rejected_marks_cannot_be_relinked():
    marks = act(detected(), "reject_mark", "ra1", mark_id=detected()[2].mark_id)
    fails("mark_rejected", marks, "correct_mark_link", mark_id=marks[2].mark_id, target_entry_id="e2")


# --- enter_amount ----------------------------------------------------------------------------
def test_enter_amount_replaces_the_amount_of_a_confirmed_price_change_only():
    marks = detected()
    fails("mark_not_confirmed", marks, "enter_amount", mark_id=marks[1].mark_id, **AMOUNT)
    fails("mark_type_mismatch", marks, "enter_amount", mark_id=marks[0].mark_id, **AMOUNT)
    confirmed = act(marks, "confirm_mark", "ra1", mark_id=marks[1].mark_id, **AMOUNT)
    fails("amount_invalid", confirmed, "enter_amount", mark_id=marks[1].mark_id,
          **{**AMOUNT, "confirmed_amount": "abc"})
    step = transition_mark(confirmed, MarkAction("enter_amount", "ra2", mark_id=marks[1].mark_id,
                                                 **{**AMOUNT, "confirmed_amount": "975.50"}),
                           actor=ACTOR, recorded_at=at(8), review_revision=6, rows=ROWS)
    assert step.after.confirmed_amount == "975.50" and step.after.decision_action_id == "ra2"
    assert step.original_values["confirmed_amount"] == "980.00" and step.new_values["confirmed_amount"] == "975.50"
    rejected = act(marks, "reject_mark", "ra3", mark_id=marks[1].mark_id)
    fails("mark_not_confirmed", rejected, "enter_amount", mark_id=marks[1].mark_id, **AMOUNT)


def test_a_trocr_suggestion_is_never_used_as_the_amount():
    suggestion = TrocrSuggestion(text="980", confidence=0.61)
    detection = Detection(page_id="dp1", box_norm=norm((2030, 1058, 2280, 1104)), mark_type="price_change",
                          score=0.8, trocr_suggestion=suggestion)
    marks = list(link([detection], ROWS).marks)
    fails("amount_required", marks, "confirm_mark", mark_id=marks[0].mark_id)
    after = act(marks, "confirm_mark", mark_id=marks[0].mark_id, **{**AMOUNT, "confirmed_amount": "975.00"})
    assert after[0].confirmed_amount == "975.00" and after[0].trocr_suggestion == suggestion


# --- add_mark --------------------------------------------------------------------------------
def test_add_mark_creates_a_confirmed_human_mark():
    marks = detected()
    box = norm((1850, 1182, 2300, 1232))
    after = act(marks, "add_mark", "ra7", mark_type="exclusion", page_id="dp1", box_norm=box, target_entry_id="e3")
    assert after[:3] == marks and len(after) == 4
    new = after[3]
    assert new.mark_id == deterministic_id("pm", "human_added", "ra7")
    assert (new.origin, new.state, new.detection_confidence, new.entry_id, new.link_reason, new.rule_id) == (
        "human_added", "confirmed", None, "e3", "human_link", "human")
    assert new.candidate_entry_ids == ["e3"] and new.model_entry_id is None
    assert new.provenance == API_PROV and new.versions == HUMAN_VERSIONS
    assert (new.claim_id, new.input_revision, new.decision_action_id) == (CLAIM, 1, "ra7")
    replay = act(after, "add_mark", "ra7", mark_type="exclusion", page_id="dp1", box_norm=box, target_entry_id="e3")
    assert replay == after
    fails("mark_id_conflict", after, "add_mark", "ra8", mark_id=new.mark_id, mark_type="exclusion", page_id="dp1",
          box_norm=box, target_entry_id="e3")


def test_add_mark_validation():
    marks = detected()
    base = {"mark_type": "price_change", "page_id": "dp1", "box_norm": norm((2030, 1178, 2280, 1224)),
            "target_entry_id": "e3"}
    fails("amount_required", marks, "add_mark", **base)
    priced = act(marks, "add_mark", **base, **AMOUNT)
    assert priced[-1].confirmed_amount == "980.00" and priced[-1].state == "confirmed"
    fails("amount_not_allowed", marks, "add_mark", **{**base, "mark_type": "exclusion"}, **AMOUNT)
    for missing in ("mark_type", "page_id", "box_norm", "target_entry_id"):
        fails("field_required", marks, "add_mark", **{k: v for k, v in base.items() if k != missing}, **AMOUNT)
    fails("field_not_allowed", marks, "add_mark", **{**base, "mark_type": "tick"}, **AMOUNT)
    fails("entry_on_other_page", marks, "add_mark", **{**base, "target_entry_id": "p0"}, **AMOUNT)
    with pytest.raises(ContractError) as err:
        apply_mark_action(marks, MarkAction("add_mark", "ra5", **base, **AMOUNT), actor=ACTOR, recorded_at=at(1),
                          review_revision=2, rows=ROWS)
    assert err.value.reason_code == "context_required"
    with pytest.raises(ContractError) as err:
        act(marks, "add_mark", **{**base, "box_norm": (0.9, 0.5, 0.1, 0.6)}, **AMOUNT)
    assert err.value.reason_code == "mark_record_invalid"


def test_add_mark_on_a_claim_without_marks_takes_identity_from_the_rows():
    after = act([], "add_mark", mark_type="exclusion", page_id="dp1", box_norm=norm((1850, 1002, 2300, 1052)),
                target_entry_id="e0")
    assert (after[0].claim_id, after[0].input_revision, after[0].origin) == (CLAIM, 1, "human_added")


# --- general rules ---------------------------------------------------------------------------
def test_unknown_marks_and_malformed_actions_raise_stable_codes():
    marks = detected()
    fails("mark_not_found", marks, "confirm_mark", mark_id="pm-missing")
    fails("field_required", marks, "confirm_mark")
    fails("action_unsupported", marks, "dismiss_finding", mark_id=marks[0].mark_id)
    fails("field_not_allowed", marks, "correct_mark_link", mark_id=marks[0].mark_id, target_entry_id="e1",
          confirmed_amount="1.00")
    fails("duplicate_mark_id", marks + marks[:1], "confirm_mark", mark_id=marks[0].mark_id)
    with pytest.raises(ContractError) as err:
        apply_mark_action(marks, MarkAction("confirm_mark", "ra1", mark_id=marks[0].mark_id), actor=ACTOR,
                          recorded_at=at(1), review_revision=2, claim_id="01JAX7Q0VN4Z3K9F2M8R6T1C5E")
    assert err.value.reason_code == "claim_mismatch"
    with pytest.raises(ContractError) as err:
        apply_mark_action(marks, MarkAction("confirm_mark", "ra1", mark_id=marks[0].mark_id), actor="",
                          recorded_at=at(1), review_revision=2)
    assert err.value.reason_code == "field_required"


def test_new_input_revision_is_stamped_on_every_returned_mark():
    marks = detected()
    after = apply_mark_action(marks, MarkAction("confirm_mark", "ra1", mark_id=marks[0].mark_id), actor=ACTOR,
                              recorded_at=at(1), review_revision=2, input_revision=2)
    assert {m.input_revision for m in after} == {2} and {m.input_revision for m in marks} == {1}
    assert [m.state for m in after] == ["confirmed", "pending", "pending"]


def test_transition_reports_original_and_new_values_for_the_review_event():
    marks = detected()
    step = transition_mark(marks, MarkAction("correct_mark_link", "ra1", mark_id=marks[2].mark_id,
                                             target_entry_id="e2"),
                           actor=ACTOR, recorded_at=at(1), review_revision=2, rows=ROWS)
    assert step.changed and not step.replay and step.decision_changing
    assert step.original_values == {"entry_id": None, "link_reason": "mark_between_rows"}
    assert step.new_values == {"entry_id": "e2", "link_reason": "human_link"}


# --- review events and the worked example ---------------------------------------------------
def event(action_type, action_id, *, resulting, **kw):
    return ReviewEvent(event_id=f"ev-{action_id}", action_id=action_id, claim_id=CLAIM, assessment_revision=1,
                       expected_review_revision=resulting - 1, resulting_review_revision=resulting, actor=ACTOR,
                       recorded_at=at(resulting), action_type=action_type, idempotency_key=f"idem-{action_id}", **kw)


def test_review_events_drive_the_worked_example_review():
    """Spec worked example: relink B to e-005, confirm it at 980.00, confirm A."""
    rows = worked_example_rows()
    marks = list(link([det((1850, 1300, 2320, 1362), "exclusion", 0.88),
                       det((2005, 1381, 2210, 1441), "price_change", 0.79)], rows).marks)
    a, b = marks
    step = apply_review_event(marks, event("correct_mark_link", "ra1", resulting=1, mark_id=b.mark_id,
                                           entry_id="e-005"), rows=rows)
    step = apply_review_event(step.marks, event("confirm_mark", "ra2", resulting=2, mark_id=b.mark_id,
                                                entry_id="e-005", new_values=AMOUNT), rows=rows)
    step = apply_review_event(step.marks, event("confirm_mark", "ra3", resulting=3, mark_id=a.mark_id), rows=rows,
                              input_revision=2)
    a2, b2 = step.marks
    assert (b2.entry_id, b2.model_entry_id, b2.state, b2.confirmed_amount, b2.confirmed_currency,
            b2.confirmed_cost_basis, b2.review_revision) == ("e-005", None, "confirmed", "980.00", "SGD", COST_BASIS, 2)
    assert (a2.entry_id, a2.state, a2.decided_by, a2.review_revision, a2.input_revision) == (
        "e-003", "confirmed", ACTOR, 3, 2)


def test_mark_action_from_review_event_maps_fields_and_rejects_other_actions():
    action = mark_action_from_review_event(event(
        "add_mark", "ra1", resulting=1, entry_id="e0",
        new_values={"mark_type": "exclusion", "page_id": "dp1", "box_norm": [0.1, 0.2, 0.3, 0.4]}))
    assert (action.action_type, action.target_entry_id, action.box_norm, action.mark_id) == (
        "add_mark", "e0", (0.1, 0.2, 0.3, 0.4), None)
    rejected = mark_action_from_review_event(event("reject_mark", "ra2", resulting=1, mark_id="pm1", entry_id="e0",
                                                   reason_code="not_a_mark"))
    assert rejected.target_entry_id is None and rejected.reason_code == "not_a_mark"
    with pytest.raises(ContractError) as err:
        mark_action_from_review_event(event("add_note", "ra3", resulting=1, note="check later"))
    assert err.value.reason_code == "action_unsupported"
