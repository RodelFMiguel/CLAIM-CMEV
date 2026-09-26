"""M9 review actions: classification, idempotent save, stale conflicts, validation and replay.

Module 09 "Action classification", "Idempotent save", "Review revision model"; application
platform sections 3.3 and 8; data contracts sections 10 and 14.
"""
import pytest

from claim_cmev.contracts.common import ContractError
from claim_cmev.contracts.review import REVIEW_ACTION_TYPES
from claim_cmev.review import (
    apply_review_batch,
    carryable_dismissals,
    classify_review_action,
    finalize_review,
    replay_review_actions,
    replay_review_events,
    rerun_stages,
)
from m9_support import ALL_STAGES_DONE, BASIS, LATER, apply, assessment, finding, request, state

# Module 09 "Action classification" table, written out independently of the contract constant.
EXPECTED_CLASS = {
    "confirm_mark": "decision_changing", "reject_mark": "decision_changing", "add_mark": "decision_changing",
    "correct_mark_link": "decision_changing", "enter_amount": "decision_changing",
    "correct_line_item": "decision_changing", "confirm_identity": "decision_changing",
    "confirm_coverage": "decision_changing", "confirm_declaration_completeness": "decision_changing",
    "accept_addition": "decision_changing", "dismiss_finding": "review_only", "dismiss_addition": "review_only",
    "add_note": "review_only",
}

VALID = {
    "confirm_mark": dict(mark_id="pm2"),
    "reject_mark": dict(mark_id="pm1", note="ink smudge, not a mark"),
    "add_mark": dict(entry_id="li1", mark_type="exclusion"),
    "correct_mark_link": dict(mark_id="pm1", target_entry_id="li1"),
    "enter_amount": dict(mark_id="pm4", amount="600.00"),
    "correct_line_item": dict(entry_id="li1", corrections={"operation": "repair"}, reason_code="mapping_error"),
    "confirm_identity": dict(part_code="fender", side="left", photo_ids=("ph2",), entry_id="li5"),
    "confirm_coverage": dict(part_code="fender", side="left", photo_ids=("ph2", "ph3"), covers_enough=True),
    "confirm_declaration_completeness": dict(completeness_state="complete"),
    "accept_addition": dict(candidate_id="cand1", operation="repair", quantity="1", reason_code="workshop_to_quote"),
    "dismiss_addition": dict(candidate_id="cand1", reason_code="other", note="Same damage as row 2."),
    "dismiss_finding": dict(finding_id="f4", reason_code="parts_price_change", note="Supplier raised the price."),
    "add_note": dict(note="Called the workshop about row 4."),
}


def test_classification_matches_the_specified_split_for_every_type():
    assert set(EXPECTED_CLASS) == set(REVIEW_ACTION_TYPES)
    for action_type, expected in EXPECTED_CLASS.items():
        assert classify_review_action(action_type) == expected
        stages = rerun_stages(action_type)
        assert (stages == ()) == (expected == "review_only")
        assert not {"parts", "damage", "page_read", "pen_marks"} & set(stages), "no action reruns a neural model"
    assert rerun_stages("confirm_identity") == rerun_stages("confirm_coverage") == ("summary", "consolidate")
    assert rerun_stages("enter_amount") == rerun_stages("correct_line_item") == ("consolidate",)


def test_unknown_action_type_is_refused():
    with pytest.raises(ContractError) as exc:
        classify_review_action("approve_claim")
    assert exc.value.reason_code == "action_type_unknown"


@pytest.mark.parametrize("action_type", sorted(VALID))
def test_each_action_type_applies_with_the_right_revision_effect(action_type):
    s = state()
    out = apply(s, request(action_type, **VALID[action_type]))
    assert out.kind == "applied", out.errors
    assert out.classification == EXPECTED_CLASS[action_type] == classify_review_action(out.event)
    assert out.event.action_type == action_type
    assert out.event.expected_review_revision == 0 and out.event.resulting_review_revision == 1
    assert out.review_revision == out.state.review_revision == 1
    assert s.review_revision == 0 and not s.actions, "the input state is never mutated"
    if EXPECTED_CLASS[action_type] == "decision_changing":
        assert out.http_status == 202 and out.plan is not None
        assert out.new_input_revision == out.state.current_input_revision == 2
        assert out.plan.base_assessment_revision == 1 and out.plan.base_input_revision == 1
        assert out.plan.corrections == (out.event,)
        assert out.state.pending_reassessment
    else:
        assert out.http_status == 200 and out.plan is None and out.new_input_revision is None
        assert out.state.current_input_revision == 1 and not out.state.pending_reassessment


def test_price_correction_reruns_no_neural_stage_and_reuses_every_branch_result():
    out = apply(state(), request("confirm_mark", mark_id="pm1", amount="300.00"))
    plan = out.plan
    assert plan.publish == "consolidate" and plan.rerun_stages == ("consolidate",)
    assert plan.reused_stages == ("parts", "damage", "summary", "page_read", "line_items", "pen_marks")
    assert plan.reuse_hint == plan.reused_stages and not plan.neural_rerun
    assert [a.kind for a in plan.reuse_lineage] == list(plan.reused_stages)
    assert out.event.new_values == {"state": "confirmed", "entry_id": "li2", "mark_type": "price_change",
                                    "amount": "300.00", "currency": "SGD", "cost_basis": BASIS}
    assert out.event.original_values == {"state": "pending", "entry_id": "li2"}


def test_coverage_confirmation_reruns_summary_and_chained_corrections_accumulate():
    first = apply(state(), request("confirm_coverage", part_code="fender", side="left", photo_ids=("ph2",),
                                   covers_enough=False, reason_code="panel_edge_cropped"))
    assert first.plan.rerun_stages == ("summary", "consolidate")
    assert first.plan.publish == "input_revision_created" and "summary" not in first.plan.reused_stages
    assert not first.plan.neural_rerun
    second = apply(first.state, request("correct_line_item", 1, entry_id="li1", corrections={"quantity": "2"},
                                        reason_code="ocr_error"), 2)
    plan = second.plan
    assert (plan.base_input_revision, plan.previous_input_revision, plan.new_input_revision) == (1, 2, 3)
    assert [e.action_type for e in plan.corrections] == ["confirm_coverage", "correct_line_item"]
    assert plan.rerun_stages == ("summary", "consolidate"), "the new input still needs the earlier summary rerun"


def test_stale_review_revision_is_a_conflict_that_overwrites_nothing():
    s1 = apply(state(), request("dismiss_finding", finding_id="f4", reason_code="parts_price_change")).state
    out = apply(s1, request("dismiss_finding", 0, finding_id="f5", reason_code="inadequate_photograph"), 2)
    assert (out.kind, out.http_status, out.reason_code) == ("conflict", 409, "stale_revision")
    c = out.conflict
    assert (c.current_review_revision, c.submitted_review_revision) == (1, 0)
    assert c.actions_since == ("act-0001",)
    assert c.submitted_values["finding_id"] == "f5" and c.submitted_values["reason_code"] == "inadequate_photograph"
    assert out.state == s1 and list(out.state.dismissals()) == ["f4"]
    ahead = apply(s1, request("add_note", 5, note="from the future"), 3)
    assert ahead.reason_code == "stale_revision"


def test_stale_input_revision_is_a_conflict():
    s1 = apply(state(), request("confirm_mark", mark_id="pm2")).state
    out = apply(s1, request("reject_mark", 1, expected_input_revision=1, mark_id="pm1"), 2)
    assert (out.kind, out.reason_code) == ("conflict", "stale_input_revision")
    assert out.conflict.current_input_revision == 2 and out.conflict.submitted_input_revision == 1
    ok = apply(s1, request("reject_mark", 1, expected_input_revision=2, mark_id="pm1"), 2)
    assert ok.kind == "applied" and ok.state.current_input_revision == 3


def test_frozen_review_refuses_every_new_action():
    s = state("clean")
    frozen = finalize_review(s, presented_review_revision=0, stage_states=ALL_STAGES_DONE, actor="surveyor:rm",
                             finalized_at=LATER, finalization_id="fin-1").state
    for action_type, fields in (("add_note", VALID["add_note"]), ("dismiss_finding", VALID["dismiss_finding"])):
        out = apply(frozen, request(action_type, **fields))
        assert (out.kind, out.http_status, out.reason_code) == ("refused", 409, "review_finalized")
        assert out.state == frozen


def test_non_current_assessment_is_refused():
    out = apply(state(claim_assessment_revision=2), request("add_note", note="late"))
    assert (out.kind, out.http_status, out.reason_code) == ("refused", 409, "assessment_not_current")


def test_idempotent_replay_returns_the_original_result_even_after_later_actions():
    req = request("confirm_mark", mark_id="pm1", amount="300.00")
    first = apply(state(), req, key="client-action-0001")
    later = apply(first.state, request("add_note", 1, note="next"), 2).state
    again = apply(later, req, 3, key="client-action-0001")
    assert again.kind == "replayed" and again.replayed and again.response()["replayed"] is True
    assert (again.action_id, again.review_revision, again.new_input_revision) == ("act-0001", 1, 2)
    assert again.event == first.event and again.plan == first.plan and again.http_status == first.http_status
    assert again.state == later and len(later.actions) == 2, "a replay never adds a second row"


def test_same_key_with_a_different_payload_is_an_idempotency_conflict():
    first = apply(state(), request("add_note", note="first text"), key="client-action-0002")
    out = apply(first.state, request("add_note", note="changed text"), 2, key="client-action-0002")
    assert (out.kind, out.http_status, out.reason_code) == ("conflict", 409, "idempotency_conflict")
    assert out.state == first.state


def test_idempotency_key_and_action_id_are_checked():
    assert apply(state(), request("add_note", note="x"), key="short").reason_code == "idempotency_key_invalid"
    s1 = apply(state(), request("add_note", note="x")).state
    reused = apply(s1, request("add_note", 1, note="y"), 1, key="another-key-01")
    assert (reused.kind, reused.reason_code) == ("invalid", "action_id_reused")


@pytest.mark.parametrize("amount, code", [
    (None, "amount_required"), ("", "amount_invalid"), ("abc", "amount_invalid"), ("-5.00", "amount_invalid"),
    ("1,000.00", "amount_invalid"), ("1e3", "amount_invalid"), (" 300.00", "amount_invalid"),
    ("300.005", "amount_too_precise"), ("0", "amount_not_positive"), ("0.00", "amount_not_positive"),
    ("1000000.00", "amount_too_large"),
])
def test_invalid_amounts_are_rejected_without_a_state_change(amount, code):
    s = state()
    out = apply(s, request("confirm_mark", mark_id="pm1", amount=amount))
    assert (out.kind, out.http_status, out.reason_code) == ("invalid", 400, code)
    assert out.errors[0].field == "amount" and out.state == s


def test_amount_currency_and_basis_must_match_the_claim():
    out = apply(state(), request("confirm_mark", mark_id="pm1", amount="300.00", currency="MYR"))
    assert (out.http_status, out.reason_code) == (422, "currency_mismatch")
    out = apply(state(), request("enter_amount", mark_id="pm4", amount="600.00", cost_basis="with_tax_v1"))
    assert out.reason_code == "basis_mismatch"


@pytest.mark.parametrize("fields, code, status", [
    (dict(finding_id="f4", reason_code="too_expensive"), "dismissal_reason_invalid", 400),
    (dict(finding_id="f4"), "reason_required", 400),
    (dict(finding_id="f4", reason_code="other"), "note_required", 400),
    (dict(finding_id="f4", reason_code="other", note="x" * 301), "note_too_long", 400),
    (dict(finding_id="f3", reason_code="system_error"), "row_excluded", 422),
    (dict(finding_id="nope", reason_code="system_error"), "finding_not_found", 404),
])
def test_dismissal_validation(fields, code, status):
    s = state("clean")
    out = apply(s, request("dismiss_finding", **fields))
    assert (out.kind, out.http_status, out.reason_code) == ("invalid", status, code)


@pytest.mark.parametrize("action_type, fields, code, status", [
    ("confirm_mark", dict(mark_id="pm9"), "mark_not_found", 404),
    ("confirm_mark", dict(mark_id="pm4", amount="1.00"), "mark_already_resolved", 422),
    ("confirm_mark", dict(mark_id="pm2", amount="5.00"), "amount_not_applicable", 400),
    ("reject_mark", dict(mark_id="pm4"), "mark_already_resolved", 422),
    ("enter_amount", dict(mark_id="pm1", amount="300.00"), "mark_not_confirmed", 422),
    ("enter_amount", dict(mark_id="pm2", amount="300.00"), "mark_not_price_change", 422),
    ("enter_amount", dict(mark_id="pm4", amount="610.0"), "no_change", 422),
    ("correct_mark_link", dict(mark_id="pm1"), "target_entry_required", 400),
    ("correct_mark_link", dict(mark_id="pm1", target_entry_id="li2"), "no_change", 422),
    ("correct_mark_link", dict(mark_id="pm1", target_entry_id="li9"), "entry_not_found", 404),
    ("add_mark", dict(entry_id="li1", mark_type="tick"), "mark_type_invalid", 400),
    ("add_mark", dict(entry_id="li1", mark_type="price_change"), "amount_required", 400),
    ("correct_line_item", dict(entry_id="li1", corrections={"part_code": "spoiler"}, reason_code="ocr_error"),
     "value_outside_taxonomy", 422),
    ("correct_line_item", dict(entry_id="li1", corrections={"operation": "replace"}, reason_code="ocr_error"),
     "no_change", 422),
    ("correct_line_item", dict(entry_id="li1", corrections={"colour": "red"}, reason_code="ocr_error"),
     "correction_field_unknown", 400),
    ("correct_line_item", dict(entry_id="li1", corrections={"quantity": "-1"}, reason_code="ocr_error"),
     "quantity_invalid", 400),
    ("confirm_identity", dict(part_code="fender", side="unknown", photo_ids=("ph2",)), "side_unresolved", 422),
    ("confirm_identity", dict(part_code="fender", side="left", photo_ids=("ph7",)), "photo_not_found", 404),
    ("confirm_coverage", dict(part_code="fender", side="left", photo_ids=("ph2",), covers_enough=False),
     "reason_required", 400),
    ("confirm_declaration_completeness", dict(completeness_state="partial"), "reason_required", 400),
    ("confirm_declaration_completeness", dict(completeness_state="done"), "completeness_state_invalid", 400),
    ("accept_addition", dict(candidate_id="cand2", operation="repair", quantity="1", amount="10.00"),
     "addition_not_proposed", 422),
    ("accept_addition", dict(candidate_id="cand1", quantity="1", amount="10.00"), "operation_required", 400),
    ("accept_addition", dict(candidate_id="cand1", operation="repair", quantity="1"),
     "amount_absent_reason_required", 400),
    ("dismiss_addition", dict(candidate_id="cand7", reason_code="system_error"), "addition_not_found", 404),
    ("add_note", dict(note="   "), "note_required", 400),
    ("add_note", dict(note="x", amount="5.00"), "field_not_applicable", 400),
])
def test_per_type_validation(action_type, fields, code, status):
    s = state()
    out = apply(s, request(action_type, **fields))
    assert (out.kind, out.http_status, out.reason_code) == ("invalid", status, code), out.errors
    assert out.state == s


def test_mark_decisions_within_one_review_are_not_repeated():
    s1 = apply(state(), request("confirm_mark", mark_id="pm1", amount="300.00")).state
    twice = apply(s1, request("reject_mark", 1, mark_id="pm1"), 2)
    assert twice.reason_code == "mark_already_resolved"
    amended = apply(s1, request("enter_amount", 1, mark_id="pm1", amount="310.00"), 2)
    assert amended.kind == "applied" and amended.event.original_values["amount"] == "300.00"
    assert amended.state.effective_marks()["pm1"].confirmed_amount == "310.00"
    assert s1.marks[0].state == "pending", "the machine mark record is never rewritten"


def test_unlinked_mark_needs_a_link_before_confirmation():
    from m9_support import mark, marks

    s = state(marks=marks("review") + [mark("pm3", None, "exclusion", candidates=["li4", "li5"], index=3)])
    assert apply(s, request("confirm_mark", mark_id="pm3")).reason_code == "mark_unlinked"
    linked = apply(s, request("confirm_mark", mark_id="pm3", target_entry_id="li5"))
    assert linked.kind == "applied" and linked.event.new_values["link_reason"] == "human_link"
    added = apply(s, request("add_mark", entry_id="li1", mark_type="price_change", amount="900.00"))
    view = added.state.effective_marks()[added.event.mark_id]
    assert (view.origin, view.state, view.confirmed_amount) == ("human_added", "confirmed", "900.00")


def test_dismissal_survives_reload_by_deterministic_replay():
    initial = state()
    s1 = apply(initial, request("dismiss_finding", finding_id="f4", reason_code="parts_price_change",
                                note="Supplier raised the price."), key="client-dismiss-01").state
    s2 = apply(s1, request("add_note", 1, note="Checked with the workshop."), 2).state
    s3 = apply(s2, request("confirm_mark", 2, mark_id="pm2"), 3).state
    reloaded = replay_review_actions(initial, s3.actions)
    assert reloaded == s3
    hashes = {r.event.idempotency_key: r.payload_hash for r in s3.actions}
    assert replay_review_events(initial, s3.events, hashes) == s3
    assert reloaded.dismissals()["f4"].reason_code == "parts_price_change"
    assert reloaded.finding("f4").overall_result == "cost_outlier", "a dismissal never changes the finding"
    retry = apply(reloaded, request("dismiss_finding", finding_id="f4", reason_code="parts_price_change",
                                    note="Supplier raised the price."), 9, key="client-dismiss-01")
    assert retry.kind == "replayed" and retry.review_revision == 1
    again = apply(reloaded, request("dismiss_finding", 3, finding_id="f4", reason_code="system_error"), 4)
    assert again.reason_code == "already_dismissed"


def test_reapplying_the_same_requests_is_deterministic():
    sequence = [request("add_note", 0, note="a"), request("confirm_mark", 1, mark_id="pm1", amount="300.00"),
                request("dismiss_finding", 2, finding_id="f4", reason_code="parts_price_change"),
                request("confirm_coverage", 3, part_code="fender", side="left", photo_ids=("ph2",),
                        covers_enough=True)]

    def run():
        s = state()
        for n, req in enumerate(sequence, 1):
            s = apply(s, req, n).state
        return s

    assert run() == run()


def test_replay_refuses_out_of_order_or_foreign_events():
    s = apply(state(), request("add_note", note="a")).state
    with pytest.raises(ContractError) as exc:
        replay_review_actions(state(review_revision=5), s.actions)
    assert exc.value.reason_code == "replay_out_of_order"
    with pytest.raises(ContractError):
        replay_review_events(state(), s.events, {})


def test_batch_is_atomic_and_replays_as_a_whole():
    s = state()
    good = [request("add_note", 0, note="a"), request("confirm_mark", 0, mark_id="pm1", amount="300.00")]
    out = apply_review_batch(s, good, actor="surveyor:rm", recorded_at=LATER, idempotency_key="batch-key-0001",
                             action_ids=["b-1", "b-2"])
    assert out.kind == "applied" and out.http_status == 202 and out.review_revision == 2
    assert out.state.current_input_revision == 2
    again = apply_review_batch(out.state, good, actor="surveyor:rm", recorded_at=LATER,
                               idempotency_key="batch-key-0001", action_ids=["b-1", "b-2"])
    assert again.kind == "replayed" and again.state == out.state
    bad = [request("add_note", 0, note="a"), request("dismiss_finding", 0, finding_id="f4", reason_code="nope")]
    failed = apply_review_batch(s, bad, actor="surveyor:rm", recorded_at=LATER, idempotency_key="batch-key-0002",
                                action_ids=["c-1", "c-2"])
    assert (failed.kind, failed.failed_index, failed.reason_code) == ("invalid", 1, "dismissal_reason_invalid")
    assert failed.state == s and not failed.state.actions


def test_dismissal_carries_only_to_an_unchanged_finding():
    s = apply(state(), request("dismiss_finding", finding_id="f4", reason_code="parts_price_change")).state
    same = assessment([finding("f4", "li4", "cost_outlier", revision=2)], revision=2)
    changed = assessment([finding("f4", "li4", "cost_outlier", revision=2, content_hash="0" * 64)], revision=2)
    assert [fid for fid, _ in carryable_dismissals(s, same)] == ["f4"]
    assert carryable_dismissals(s, changed) == ()
