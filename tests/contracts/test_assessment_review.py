"""Assessment findings, possible additions, review actions and finalization."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from claim_cmev.contracts import (
    DECISION_CHANGING_ACTIONS,
    Assessment,
    AssessmentFinding,
    Finalization,
    ProposedRepairAddition,
    ReviewEvent,
)
from claim_cmev.contracts.review import REVIEW_ACTION_TYPES
from contract_factories import CLAIM, NOW, PROV, check, cost_check, excluded_finding, finding, not_evaluated_cost


def test_ok_finding_round_trip():
    ok = AssessmentFinding(**finding())
    assert AssessmentFinding.model_validate_json(ok.model_dump_json()) == ok
    assert ok.checks == {"documentary": "passed", "mark_state": "passed", "photographic": "passed", "cost": "within_range"}


def test_excluded_row_is_not_evaluated_never_ok():
    excluded = AssessmentFinding(**excluded_finding())
    assert excluded.overall_result == "not_evaluated" and excluded.row_state == "excluded"
    with pytest.raises(ValidationError):
        AssessmentFinding(**{**excluded_finding(), "overall_result": "ok"})
    with pytest.raises(ValidationError):
        AssessmentFinding(**{**excluded_finding(), "photographic_check": check()})  # a check that 'passed'
    with pytest.raises(ValidationError):
        AssessmentFinding(**finding(overall_result="not_evaluated"))  # not_evaluated on an active row


@pytest.mark.parametrize("overrides", [
    {"photographic_check": check("not_evaluated", "check_not_reached")},  # a skipped check is not a pass
    {"cost_check": not_evaluated_cost(reason_code="insufficient_support"), "applied_range_id": None,
     "applied_range_reason": "insufficient_support"},  # a withheld cost check is not a pass
    {"mark_state_check": check("insufficient", "mark_pending")},
    {"documentary_check": check("insufficient", "row_fields_incomplete")},
])
def test_ok_requires_every_check_passed_and_cost_within_range(overrides):
    with pytest.raises(ValidationError, match="'ok'"):
        AssessmentFinding(**finding(**overrides))
    # the same checks are valid under insufficient_evidence
    assert AssessmentFinding(**finding(overall_result="insufficient_evidence", **overrides))


def test_passed_photo_check_beside_withheld_cost_check():
    kept = AssessmentFinding(**finding(overall_result="insufficient_evidence",
                                       cost_check=not_evaluated_cost(reason_code="insufficient_support"),
                                       applied_range_id=None, applied_range_reason="insufficient_support"))
    assert kept.photographic_check.result == "passed" and kept.cost_check.result == "not_evaluated"


@pytest.mark.parametrize("overrides", [
    {"overall_result": "unsupported"},  # photographic check passed
    {"overall_result": "cost_outlier"},  # cost within range
    {"applied_range_id": None},  # null without its reason
    {"applied_range_id": "other-range"},
    {"cost_check": cost_check(entry_id="li2")},
    {"reasons": []},
    {"documentary_check": {"result": "passed", "reasons": []}},  # every check carries a reason
    {"explanation_sentence": {"text": "fine", "provenance": PROV}},  # explainer output must say so
    {"overall_result": "approved"},
])
def test_invalid_findings(overrides):
    with pytest.raises(ValidationError):
        AssessmentFinding(**finding(**overrides))


def test_unsupported_and_cost_outlier_shapes():
    unsupported = finding(overall_result="unsupported",
                          photographic_check=check("failed", "no_supported_damage_in_adequate_views"),
                          cost_check=not_evaluated_cost(), applied_range_id=None, applied_range_reason="check_not_reached")
    assert AssessmentFinding(**unsupported).overall_result == "unsupported"
    outlier = finding(overall_result="cost_outlier",
                      cost_check=cost_check(result="outside_range", amount="1150.00", direction="above",
                                            absolute_deviation="130.00", normalised_score="0.3250",
                                            reason_code="amount_above_range"))
    assert AssessmentFinding(**outlier).cost_check.direction == "above"
    explained = AssessmentFinding(**finding(explanation_sentence={
        "text": "Within the reference range.",
        "provenance": {**PROV, "source_kind": "explainer", "producer_service": "cmev-explainer"}}))
    assert explained.overall_result == "ok"


def addition(**kw) -> dict:
    data = {"candidate_id": "pa1", "assessment_revision": 1, "observation_ids": ["ob3"], "part_code": "hood",
            "side": "not_applicable", "status": "proposed",
            "reason": {"code": "addition_proposed", "message": "Damage with no matching estimate row"}}
    data.update(kw)
    return data


def test_possible_additions_carry_no_operation_or_amount():
    assert ProposedRepairAddition(**addition()).review_state == "open"
    for field, value in (("operation", "repair"), ("amount", "100.00"), ("entry_id", "li1")):
        with pytest.raises(ValidationError):
            ProposedRepairAddition(**addition(**{field: value}))
    with pytest.raises(ValidationError):
        ProposedRepairAddition(**addition(side="unknown"))  # proposed needs a resolved side
    withheld = ProposedRepairAddition(**addition(side="unknown", status="withheld", reason={
        "code": "addition_withheld_identity_unresolved", "message": "Damage found, part not identified"}))
    assert withheld.status == "withheld"
    suppressed = ProposedRepairAddition(**addition(status="suppressed", suppressed_by_entry_id="li1", part_code="back-door",
                                                   side="left", reason={
        "code": "addition_suppressed_confirmed_exclusion_same_part", "message": "Damage noted on an excluded row"}))
    assert suppressed.suppressed_by_entry_id == "li1"
    with pytest.raises(ValidationError):
        ProposedRepairAddition(**addition(status="suppressed"))  # without the excluding row


def assessment(**kw) -> dict:
    data = {"claim_id": CLAIM, "input_revision": 1, "assessment_revision": 1, "state": "ready",
            "findings": [finding(), excluded_finding(finding_id="f2", entry_id="li2")],
            "missing_repairs_check": check("passed", "addition_proposed"),
            "possible_additions": [addition()], "suppressed_additions": [], "incomplete_reasons": [],
            "cost_table_version": "2026.09.1", "rules_config_version": "rc-0.1.0",
            "pinned_versions": {"rules_config": "rc-0.1.0", "cost_table": "2026.09.1"}, "provenance": PROV,
            "created_at": NOW}
    data.update(kw)
    return data


def test_assessment_counts_and_round_trip():
    a = Assessment(**assessment())
    assert a.finding_counts() == {"ok": 1, "unsupported": 0, "cost_outlier": 0, "insufficient_evidence": 0, "excluded": 1}
    assert a.cost_check_counts()["not_evaluated"] == 1
    assert Assessment.model_validate_json(a.model_dump_json()) == a


@pytest.mark.parametrize("overrides", [
    {"state": "incomplete"},  # incomplete without reasons
    {"incomplete_reasons": ["branch_failed"]},  # ready with reasons
    {"assessment_revision": 2},  # findings from another revision
    {"possible_additions": [addition(status="suppressed", suppressed_by_entry_id="li2",
                                     reason={"code": "x", "message": "y"})]},
    {"suppressed_additions": [addition()]},
    {"findings": [finding(), finding()]},
    {"schema_version": "0.1.0"},
])
def test_invalid_assessments(overrides):
    with pytest.raises(ValidationError):
        Assessment(**assessment(**overrides))


# --- review -------------------------------------------------------------------------------------
def event(**kw) -> dict:
    data = {"event_id": "ev1", "action_id": "ra1", "claim_id": CLAIM, "assessment_revision": 1,
            "expected_review_revision": 0, "resulting_review_revision": 1, "actor": "surveyor:t", "recorded_at": NOW,
            "action_type": "confirm_mark", "mark_id": "pm1", "idempotency_key": "idem-0001",
            "new_values": {"confirmed_amount": "980.00", "confirmed_currency": "SGD"}}
    data.update(kw)
    return data


def test_review_action_types_and_decision_changing_set():
    assert len(REVIEW_ACTION_TYPES) == 13
    assert DECISION_CHANGING_ACTIONS == frozenset(REVIEW_ACTION_TYPES) - {"dismiss_addition", "dismiss_finding", "add_note"}
    assert ReviewEvent(**event()).decision_changing
    note = ReviewEvent(**event(action_type="add_note", mark_id=None, note="Checked with the workshop"))
    assert not note.decision_changing


@pytest.mark.parametrize("overrides", [
    {"resulting_review_revision": 3},  # a stale or skipping edit
    {"mark_id": None},
    {"action_type": "dismiss_finding", "finding_id": "f1", "reason_code": "looks_fine"},
    {"action_type": "add_note", "note": " "},
    {"action_type": "approve_claim"},  # a surveyor edit is not an approval
    {"idempotency_key": "x"},
])
def test_invalid_review_events(overrides):
    with pytest.raises(ValidationError):
        ReviewEvent(**event(**overrides))


def test_dismissal_reason_from_the_v2_list():
    dismissal = ReviewEvent(**event(action_type="dismiss_finding", mark_id=None, finding_id="f1",
                                    reason_code="parts_price_change"))
    assert not dismissal.decision_changing


def finalization(**kw) -> dict:
    codes = ["no_pending_marks", "no_unlinked_marks", "reassessment_complete", "no_failed_stages",
             "review_revision_current"]
    data = {"finalization_id": "fz1", "claim_id": CLAIM, "input_revision": 2, "assessment_revision": 2,
            "review_revision": 4, "actor": "surveyor:t", "finalized_at": NOW,
            "preconditions": [{"code": c, "passed": True} for c in codes]}
    data.update(kw)
    return data


def test_finalization_requires_every_precondition_passed():
    assert Finalization(**finalization()).review_revision == 4
    failing = finalization()
    failing["preconditions"][0]["passed"] = False
    with pytest.raises(ValidationError, match="refused"):
        Finalization(**failing)
    with pytest.raises(ValidationError, match="missing"):
        Finalization(**finalization(preconditions=[{"code": "no_pending_marks", "passed": True}]))
