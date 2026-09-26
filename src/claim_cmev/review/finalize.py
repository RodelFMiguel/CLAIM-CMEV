"""Pure finalize gate: conditions F1 to F6, proposed P1 and P2, and freezing.

Module 09 "Finalize preconditions" and application platform section 9.1. Each
condition is reported separately with blockers that name the stage, row or mark to go
to. ``insufficient_evidence`` findings, undismissed discrepancy flags and open possible
additions never block, and nothing here changes a finding: finalization freezes the
review revision the surveyor presented against the assessment it belongs to. It is not
a claim approval.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import Field

from ..contracts.common import to_decimal
from ..contracts.documents import effective_price_for
from ..contracts.review import Finalization, FinalizationPrecondition
from .config import ReviewConfig, default_review_config
from .state import ReviewState, _Frozen

ConditionId = Literal["F1", "F2", "F3", "F4", "F5", "F6", "P1", "P2"]

# condition -> (contract precondition code, failure reason code)
CONDITIONS: dict[str, tuple[str, str]] = {
    "F1": ("no_failed_stages", "processing_incomplete"),
    "F2": ("reassessment_complete", "reassessment_pending"),
    "F3": ("no_pending_marks", "marks_pending"),
    "F4": ("no_unlinked_marks", "marks_unlinked"),
    "F5": ("confirmed_amounts_present", "amount_missing"),
    "F6": ("review_revision_current", "stale_review_revision"),
    "P1": ("assessment_not_superseded", "assessment_superseded"),
    "P2": ("declaration_completeness_confirmed", "completeness_unconfirmed"),
}
_FAILED_STAGE_STATES = frozenset({"failed", "dead_lettered"})


class Blocker(_Frozen):
    """One reason finalize is refused, with a target the UI can jump to."""

    condition: ConditionId
    code: str
    message: str
    entry_id: str | None = None
    mark_id: str | None = None
    stage: str | None = None
    action_id: str | None = None


class ConditionResult(_Frozen):
    condition: ConditionId
    precondition: str
    reason_code: str
    passed: bool
    enforced: bool
    blockers: tuple[Blocker, ...] = ()


class NonBlocking(_Frozen):
    """What stays in the report and does not block (module 09 "What does not block finalize")."""

    insufficient_evidence_entry_ids: tuple[str, ...] = ()
    unsupported_entry_ids: tuple[str, ...] = ()
    cost_outlier_entry_ids: tuple[str, ...] = ()
    dismissed_finding_ids: tuple[str, ...] = ()
    open_addition_ids: tuple[str, ...] = ()


class FinalizePreconditions(_Frozen):
    claim_id: str
    assessment_revision: int
    assessment_input_revision: int
    current_input_revision: int
    review_revision: int
    presented_review_revision: int
    allowed: bool
    conditions: tuple[ConditionResult, ...]
    blockers: tuple[Blocker, ...]
    """Blockers of enforced conditions; finalize is refused while any exists."""
    advisories: tuple[Blocker, ...]
    """Failures of proposed conditions (P1, P2) that are evaluated but not enforced."""
    non_blocking: NonBlocking
    config_version: str

    def condition(self, condition_id: str) -> ConditionResult:
        return next(c for c in self.conditions if c.condition == condition_id)

    def contract_preconditions(self) -> list[FinalizationPrecondition]:
        """The enforced conditions in the ``Finalization`` record shape."""
        return [FinalizationPrecondition(code=c.precondition, passed=c.passed, detail=c.condition)
                for c in self.conditions if c.enforced]


def _row_label(state: ReviewState, entry_id: str | None) -> str:
    item = state.line_item(entry_id) if entry_id else None
    if item is None:
        return f"Row {entry_id}" if entry_id else "A pen mark"
    return f"Page {item.page_number}, {item.original_part_text}".strip(", ")


def assessment_processing_finished(assessment) -> bool:
    # A deliberately absent evidence branch is not unfinished processing. Its
    # insufficient-evidence results and incomplete status remain visible in print.
    return assessment.state == "ready" or (
        assessment.state == "incomplete" and bool(assessment.incomplete_reasons)
        and set(assessment.incomplete_reasons) <= {"image_branch_missing", "document_branch_missing"})


def _f1(state: ReviewState, stage_states: Mapping[str, str], config: ReviewConfig) -> list[Blocker]:
    code = CONDITIONS["F1"][1]
    blockers = []
    a = state.assessment
    if not assessment_processing_finished(a):
        reasons = ", ".join(a.incomplete_reasons) or "not ready"
        blockers.append(Blocker(condition="F1", code=code, stage="consolidate",
                                message=f"Assessment revision {a.assessment_revision} is incomplete: {reasons}."))
    passing = set(config.finalize.passing_stage_states)
    names = list(config.finalize.required_stages) + sorted(set(stage_states) - set(config.finalize.required_stages))
    for stage in names:
        value = stage_states.get(stage)
        if value is None:
            blockers.append(Blocker(condition="F1", code=code, stage=stage,
                                    message=f"No state is recorded for the {stage} stage of the current input."))
        elif value in _FAILED_STAGE_STATES:
            blockers.append(Blocker(condition="F1", code=code, stage=stage,
                                    message=f"The {stage} stage failed ({value}). Retry it before finalizing."))
        elif value not in passing:
            blockers.append(Blocker(condition="F1", code=code, stage=stage,
                                    message=f"The {stage} stage has not finished ({value})."))
    return blockers


def _f2(state: ReviewState) -> list[Blocker]:
    code = CONDITIONS["F2"][1]
    a = state.assessment
    blockers = []
    if state.claim_assessment_revision != a.assessment_revision:
        blockers.append(Blocker(condition="F2", code=code,
                                message=f"Assessment revision {state.claim_assessment_revision} is current, "
                                        f"not {a.assessment_revision}. Reload it."))
    if state.current_input_revision != a.input_revision:
        blockers.append(Blocker(condition="F2", code=code, stage="consolidate",
                                message=f"Input revision {state.current_input_revision} is still recomputing; "
                                        f"assessment revision {a.assessment_revision} is for input revision "
                                        f"{a.input_revision}."))
        for event in state.decision_events():
            blockers.append(Blocker(condition="F2", code=code, entry_id=event.entry_id, mark_id=event.mark_id,
                                    action_id=event.action_id,
                                    message=f"Your {event.action_type} change has no ready assessment yet."))
    return blockers


def _f3_f4_f5(state: ReviewState) -> tuple[list[Blocker], list[Blocker], list[Blocker]]:
    entries = {i.entry_id: i for i in state.line_items}
    pending, unlinked, amounts = [], [], []
    for mark in state.marks:
        kind = "exclusion" if mark.mark_type == "exclusion" else "price change"
        where = _row_label(state, mark.entry_id)
        if mark.state == "pending":
            pending.append(Blocker(condition="F3", code=CONDITIONS["F3"][1], mark_id=mark.mark_id,
                                   entry_id=mark.entry_id,
                                   message=f"{where}: the {kind} mark is still pending. Confirm or reject it."))
        if mark.state != "rejected" and (mark.entry_id is None or mark.entry_id not in entries):
            unlinked.append(Blocker(condition="F4", code=CONDITIONS["F4"][1], mark_id=mark.mark_id,
                                    message=f"A {kind} mark on page {mark.page_id} is not linked to any row. "
                                            "Link it or reject it."))
        if mark.mark_type == "price_change" and mark.state == "confirmed":
            values = (mark.confirmed_amount, mark.confirmed_currency, mark.confirmed_cost_basis)
            try:
                typed = all(v is not None for v in values) and to_decimal(mark.confirmed_amount) is not None
            except ValueError:
                typed = False
            if not typed:
                amounts.append(Blocker(condition="F5", code=CONDITIONS["F5"][1], mark_id=mark.mark_id,
                                       entry_id=mark.entry_id,
                                       message=f"{where}: the confirmed price change has no typed amount with "
                                               "its currency and cost basis."))
    for entry_id, item in entries.items():
        if effective_price_for(item.printed_line_amount, state.marks, entry_id=entry_id).reason == "mark_conflicting":
            unlinked.append(Blocker(condition="F4", code="marks_conflicting", entry_id=entry_id,
                                    message=f"{_row_label(state, entry_id)}: pen marks on this row conflict. "
                                            "Reject or relink one of them."))
    return pending, unlinked, amounts


def _p2(state: ReviewState) -> list[Blocker]:
    code = CONDITIONS["P2"][1]
    c = state.completeness
    if c is None:
        return [Blocker(condition="P2", code=code, message="Declaration completeness is not recorded.")]
    if c.state == "complete" or c.source == "human_confirmation":
        return []
    return [Blocker(condition="P2", code=code,
                    message=f"The estimate was read as {c.state} ({', '.join(c.reasons)}). Confirm its completeness.")]


def evaluate_finalize_preconditions(
    state: ReviewState,
    *,
    presented_review_revision: int,
    stage_states: Mapping[str, str],
    config: ReviewConfig | None = None,
) -> FinalizePreconditions:
    """Evaluate F1 to F6 (and P1, P2 as configured) for the review in ``state``.

    ``stage_states`` maps each stage of the claim's current input revision to its job or
    branch state (``succeeded``, ``done``, ``not_required``, ``running``, ``failed`` ...).
    ``presented_review_revision`` is the ``expected_review_revision`` the client holds.
    """
    config = config or default_review_config()
    a = state.assessment
    pending, unlinked, amounts = _f3_f4_f5(state)
    found = {
        "F1": _f1(state, stage_states, config),
        "F2": _f2(state),
        "F3": pending,
        "F4": unlinked,
        "F5": amounts,
        "F6": [] if presented_review_revision == state.review_revision else [Blocker(
            condition="F6", code=CONDITIONS["F6"][1],
            message=f"Review revision {state.review_revision} is current; you presented "
                    f"{presented_review_revision}. Reload before finalizing.")],
        "P1": [Blocker(condition="P1", code=CONDITIONS["P1"][1],
                       message=f"Assessment revision {a.assessment_revision} is superseded by a newer input.")]
        if a.superseded else [],
        "P2": _p2(state),
    }
    enforced = {"P1": config.finalize.enforce_p1_assessment_not_superseded,
                "P2": config.finalize.enforce_p2_completeness_confirmed or bool(
                    state.completeness and (state.completeness.state == "unreadable" or
                    "page_unreadable" in state.completeness.reasons))}
    conditions = tuple(
        ConditionResult(condition=cid, precondition=CONDITIONS[cid][0], reason_code=CONDITIONS[cid][1],
                        passed=not found[cid], enforced=enforced.get(cid, True), blockers=tuple(found[cid]))
        for cid in CONDITIONS)
    blockers = tuple(b for c in conditions if c.enforced for b in c.blockers)
    advisories = tuple(b for c in conditions if not c.enforced for b in c.blockers)
    dismissed = state.dismissals()
    decided = state.addition_decisions()
    active = [f for f in a.findings if f.row_state == "active"]
    non_blocking = NonBlocking(
        insufficient_evidence_entry_ids=tuple(f.entry_id for f in active if f.overall_result == "insufficient_evidence"),
        unsupported_entry_ids=tuple(f.entry_id for f in active if f.overall_result == "unsupported"),
        cost_outlier_entry_ids=tuple(f.entry_id for f in active if f.overall_result == "cost_outlier"),
        dismissed_finding_ids=tuple(dismissed),
        open_addition_ids=tuple(c.candidate_id for c in a.possible_additions if c.status == "proposed"
                                and c.review_state == "open" and c.candidate_id not in decided))
    return FinalizePreconditions(
        claim_id=state.claim_id, assessment_revision=a.assessment_revision, assessment_input_revision=a.input_revision,
        current_input_revision=state.current_input_revision, review_revision=state.review_revision,
        presented_review_revision=presented_review_revision, allowed=not blockers, conditions=conditions,
        blockers=blockers, advisories=advisories, non_blocking=non_blocking, config_version=config.config_version)


class FinalizeOutcome(_Frozen):
    """``finalized`` (200), ``replayed`` (200, the same frozen revision), ``conflict`` (409)
    or ``blocked`` (412 with the failing conditions)."""

    kind: Literal["finalized", "replayed", "conflict", "blocked"]
    http_status: int
    reason_code: str | None = None
    message: str | None = None
    finalization: Finalization | None = None
    preconditions: FinalizePreconditions | None = None
    state: ReviewState = Field(exclude=True)


def finalize_review(
    state: ReviewState,
    *,
    presented_review_revision: int,
    stage_states: Mapping[str, str],
    actor: str,
    finalized_at: datetime,
    finalization_id: str,
    config: ReviewConfig | None = None,
) -> FinalizeOutcome:
    """Freeze the current review revision against its assessment when every enforced condition passes.

    Idempotent: finalizing an already finalized review with the frozen revision returns
    the same ``Finalization``. The review revision is not advanced by freezing.
    """
    if state.finalization is not None:
        if presented_review_revision == state.finalization.review_revision:
            return FinalizeOutcome(kind="replayed", http_status=200, finalization=state.finalization, state=state)
        return FinalizeOutcome(kind="conflict", http_status=409, reason_code="review_finalized",
                               message=f"Review revision {state.finalization.review_revision} is already frozen.",
                               finalization=state.finalization, state=state)
    gate = evaluate_finalize_preconditions(state, presented_review_revision=presented_review_revision,
                                           stage_states=stage_states, config=config)
    if not gate.condition("F6").passed:
        return FinalizeOutcome(kind="conflict", http_status=409, reason_code="stale_review_revision",
                               message=gate.condition("F6").blockers[0].message, preconditions=gate, state=state)
    if not gate.allowed:
        return FinalizeOutcome(kind="blocked", http_status=412, reason_code="finalize_blocked",
                               message=f"{len(gate.blockers)} things must be resolved first.",
                               preconditions=gate, state=state)
    a = state.assessment
    finalization = Finalization(
        finalization_id=finalization_id, claim_id=state.claim_id, input_revision=a.input_revision,
        assessment_revision=a.assessment_revision, review_revision=state.review_revision, actor=actor,
        finalized_at=finalized_at, preconditions=gate.contract_preconditions())
    return FinalizeOutcome(kind="finalized", http_status=200, finalization=finalization, preconditions=gate,
                           state=state.model_copy(update={"finalization": finalization}))
