"""M9 finalize gate: F1 to F6 each on its own, proposed P1 and P2, and freezing.

Module 09 "Finalize preconditions"; application platform section 9.1; data contracts
section 11. ``insufficient_evidence`` never blocks and is never turned into a pass.
"""
from claim_cmev.contracts import PenMark
from claim_cmev.contracts.review import REQUIRED_FINALIZATION_PRECONDITIONS
from claim_cmev.review import default_review_config, evaluate_finalize_preconditions, finalize_review
from m9_support import (
    ALL_STAGES_DONE,
    LATER,
    apply,
    assessment,
    completeness,
    findings,
    mark,
    marks,
    request,
    state,
)


def gate(s, presented=0, stages=None, config=None):
    return evaluate_finalize_preconditions(s, presented_review_revision=presented,
                                           stage_states=ALL_STAGES_DONE if stages is None else stages, config=config)


def failing(result):
    return {c.condition for c in result.conditions if not c.passed}


def enforce(p1=False, p2=False):
    config = default_review_config()
    return config.model_copy(update={"finalize": config.finalize.model_copy(update={
        "enforce_p1_assessment_not_superseded": p1, "enforce_p2_completeness_confirmed": p2})})


def test_clean_review_passes_with_insufficient_evidence_rows_left_as_they_are():
    s = state("clean")
    result = gate(s)
    assert result.allowed and not result.blockers and failing(result) == set()
    assert result.non_blocking.insufficient_evidence_entry_ids == ("li2", "li5")
    assert result.non_blocking.cost_outlier_entry_ids == ("li4",)
    assert result.non_blocking.open_addition_ids == ("cand1",)
    out = finalize_review(s, presented_review_revision=0, stage_states=ALL_STAGES_DONE, actor="surveyor:rm",
                          finalized_at=LATER, finalization_id="fin-1")
    assert (out.kind, out.http_status) == ("finalized", 200)
    f = out.finalization
    assert (f.assessment_revision, f.review_revision, f.input_revision) == (1, 0, 1)
    assert REQUIRED_FINALIZATION_PRECONDITIONS <= {p.code for p in f.preconditions}
    assert all(p.passed for p in f.preconditions)
    assert out.state.review_revision == s.review_revision, "freezing does not advance the review revision"
    assert out.state.assessment == s.assessment
    results = {x.entry_id: x.overall_result for x in out.state.assessment.findings}
    assert results["li2"] == results["li5"] == "insufficient_evidence", "never converted into a pass"


def test_f1_failed_unfinished_or_unknown_stage_blocks():
    s = state("clean")
    for stages, stage, word in ((dict(ALL_STAGES_DONE, line_items="failed"), "line_items", "failed"),
                                (dict(ALL_STAGES_DONE, pen_marks="dead_lettered"), "pen_marks", "failed"),
                                (dict(ALL_STAGES_DONE, summary="running"), "summary", "not finished"),
                                ({k: v for k, v in ALL_STAGES_DONE.items() if k != "damage"}, "damage", "No state")):
        result = gate(s, stages=stages)
        assert failing(result) == {"F1"} and not result.allowed
        (blocker,) = result.blockers
        assert (blocker.code, blocker.stage) == ("processing_incomplete", stage) and word in blocker.message
    not_required = dict(ALL_STAGES_DONE, page_read="not_required", line_items="not_required", pen_marks="not_required")
    assert gate(s, stages=not_required).allowed


def test_f1_incomplete_assessment_blocks():
    s = state("clean", assessment=assessment(findings("clean"), state="incomplete", incomplete_reasons=["branch_failed"]))
    result = gate(s)
    assert failing(result) == {"F1"} and "branch_failed" in result.blockers[0].message


def test_f2_unfinished_reassessment_blocks_and_names_the_change():
    s = apply(state("clean"), request("correct_line_item", entry_id="li1", corrections={"quantity": "2"},
                                      reason_code="ocr_error")).state
    result = gate(s, presented=1)
    assert failing(result) == {"F2"}
    codes = {(b.code, b.entry_id, b.action_id) for b in result.blockers}
    assert ("reassessment_pending", "li1", "act-0001") in codes
    newer = gate(state("clean", claim_assessment_revision=2))
    assert failing(newer) == {"F2"} and newer.blockers[0].code == "reassessment_pending"


def test_f3_pending_mark_blocks_with_its_row():
    s = state("clean", marks=marks("clean")[1:] + [mark("pm1", "li2", "price_change", index=1)])
    result = gate(s)
    assert failing(result) == {"F3"}
    (blocker,) = result.blockers
    assert (blocker.code, blocker.mark_id, blocker.entry_id) == ("marks_pending", "pm1", "li2")


def test_f4_unlinked_mark_blocks():
    unlinked = mark("pm5", None, "exclusion", "rejected", candidates=["li4", "li5"])
    assert gate(state("clean", marks=marks("clean") + [unlinked])).allowed, "a rejected unlinked mark does not block"
    confirmed_unknown_row = mark("pm6", "li9", "exclusion", "confirmed")
    result = gate(state("clean", marks=marks("clean") + [confirmed_unknown_row]))
    assert failing(result) == {"F4"} and result.blockers[0].mark_id == "pm6"
    pending_unlinked = mark("pm7", None, "price_change", candidates=["li4", "li5"])
    result = gate(state("clean", marks=marks("clean") + [pending_unlinked]))
    assert failing(result) == {"F3", "F4"}
    assert {(b.condition, b.code, b.mark_id) for b in result.blockers} == {
        ("F3", "marks_pending", "pm7"), ("F4", "marks_unlinked", "pm7")}


def test_f4_conflicting_marks_on_one_row_block():
    extra = mark("pm8", "li4", "exclusion", "confirmed", index=3)
    result = gate(state("clean", marks=marks("clean") + [extra]))
    assert failing(result) == {"F4"}
    (blocker,) = result.blockers
    assert (blocker.code, blocker.entry_id) == ("marks_conflicting", "li4")


def test_f5_confirmed_price_change_without_a_typed_amount_blocks():
    # The PenMark contract already refuses this record, so it is injected without validation
    # to prove the gate does not rely on upstream validation alone.
    good = marks("clean")[0]
    broken = PenMark.model_construct(**{**dict(good), "confirmed_amount": None, "confirmed_currency": None,
                                        "confirmed_cost_basis": None})
    s = state("clean")
    result = gate(s.model_copy(update={"marks": (broken, *s.marks[1:])}))
    assert failing(result) == {"F5"}
    (blocker,) = result.blockers
    assert (blocker.code, blocker.mark_id, blocker.entry_id) == ("amount_missing", "pm1", "li2")


def test_f6_stale_presented_review_revision_is_a_conflict():
    s = apply(state("clean"), request("add_note", note="checked")).state
    result = gate(s, presented=0)
    assert failing(result) == {"F6"} and result.blockers[0].code == "stale_review_revision"
    out = finalize_review(s, presented_review_revision=0, stage_states=ALL_STAGES_DONE, actor="surveyor:rm",
                          finalized_at=LATER, finalization_id="fin-1")
    assert (out.kind, out.http_status, out.reason_code) == ("conflict", 409, "stale_review_revision")
    assert out.state == s and out.finalization is None
    ok = finalize_review(s, presented_review_revision=1, stage_states=ALL_STAGES_DONE, actor="surveyor:rm",
                         finalized_at=LATER, finalization_id="fin-1")
    assert ok.kind == "finalized" and ok.finalization.review_revision == 1


def test_p1_and_p2_are_advisory_until_the_team_adopts_them():
    s = state("clean", assessment=assessment(findings("clean"), superseded=True),
              completeness=completeness("partial"))
    default = gate(s)
    assert default.allowed and failing(default) == {"P1", "P2"}
    assert {b.code for b in default.advisories} == {"assessment_superseded", "completeness_unconfirmed"}
    assert not default.condition("P1").enforced and not default.condition("P2").enforced
    strict = gate(s, config=enforce(p1=True, p2=True))
    assert not strict.allowed and {b.code for b in strict.blockers} == {"assessment_superseded",
                                                                        "completeness_unconfirmed"}
    confirmed = completeness("partial", source="human_confirmation", confirmed_by="surveyor:rm", confirmed_at=LATER,
                             review_revision=1)
    assert gate(state("clean", completeness=confirmed), config=enforce(p2=True)).allowed
    assert not gate(state("clean", completeness=None), config=enforce(p2=True)).allowed


def test_blocked_finalize_returns_412_with_every_blocker_and_changes_nothing():
    s = state("review")
    out = finalize_review(s, presented_review_revision=0, stage_states=ALL_STAGES_DONE, actor="surveyor:rm",
                          finalized_at=LATER, finalization_id="fin-1")
    assert (out.kind, out.http_status, out.reason_code) == ("blocked", 412, "finalize_blocked")
    assert {(b.code, b.mark_id) for b in out.preconditions.blockers} == {("marks_pending", "pm1"),
                                                                         ("marks_pending", "pm2")}
    assert out.state == s and not out.state.finalized


def test_finalize_is_idempotent():
    s = state("clean")
    first = finalize_review(s, presented_review_revision=0, stage_states=ALL_STAGES_DONE, actor="surveyor:rm",
                            finalized_at=LATER, finalization_id="fin-1")
    again = finalize_review(first.state, presented_review_revision=0, stage_states={}, actor="other",
                            finalized_at=LATER, finalization_id="fin-2")
    assert again.kind == "replayed" and again.finalization == first.finalization
    other = finalize_review(first.state, presented_review_revision=3, stage_states={}, actor="other",
                            finalized_at=LATER, finalization_id="fin-2")
    assert (other.kind, other.reason_code) == ("conflict", "review_finalized")
