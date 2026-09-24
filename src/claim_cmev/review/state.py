"""Explicit review state for one assessment revision, and the request and plan shapes.

Module 09 "Review lifecycle" and "Review revision model". A ``ReviewState`` is a pure
value: the assessment under review, the records it was built from, the claim's current
input and assessment pointers, the review revision and every committed action. The API
loads it, calls ``apply_review_action`` or ``finalize_review`` and persists whatever the
outcome says. Nothing here reads a database, a clock or the network.

``review_revision`` is the claim-wide review counter: a new assessment's review starts
from the claim's current value, and each saved action advances it by exactly one.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts.assessment import Assessment, AssessmentFinding, ProposedRepairAddition
from ..contracts.claims import ReusedArtifact
from ..contracts.common import BoxNorm, ClaimId, Currency, Identifier, Revision
from ..contracts.documents import DeclarationCompleteness, LineItem, PenMark
from ..contracts.imaging import PartCoverage, PartSummary
from ..contracts.review import Finalization, ReviewActionType, ReviewEvent

ActionClass = Literal["decision_changing", "review_only"]
Stage = Literal["parts", "damage", "summary", "page_read", "line_items", "pen_marks", "consolidate"]
STAGE_ORDER: tuple[str, ...] = ("parts", "damage", "summary", "page_read", "line_items", "pen_marks", "consolidate")
BRANCH_STAGES: tuple[str, ...] = STAGE_ORDER[:-1]
"""The six orchestrator branch stages (application platform section 6.1)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewActionRequest(_Frozen):
    """One review action as submitted by the workbench (typed endpoint or batch item).

    Only the fields that apply to ``action_type`` may be set; the rest stay at their
    defaults. Values are checked by ``apply_review_action`` so that a bad value becomes
    a structured outcome with a reason code rather than an exception.
    """

    action_type: ReviewActionType
    expected_review_revision: int = Field(ge=0)
    expected_input_revision: Revision | None = None
    finding_id: str | None = Field(default=None, max_length=128)
    entry_id: str | None = Field(default=None, max_length=128)
    mark_id: str | None = Field(default=None, max_length=128)
    candidate_id: str | None = Field(default=None, max_length=128)
    target_entry_id: str | None = Field(default=None, max_length=128)
    reason_code: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=10000)
    amount: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=3)
    cost_basis: str | None = Field(default=None, max_length=128)
    corrections: dict[str, str | None] = Field(default_factory=dict)
    part_code: str | None = Field(default=None, max_length=64)
    side: str | None = Field(default=None, max_length=32)
    photo_ids: tuple[str, ...] = ()
    covers_enough: bool | None = None
    completeness_state: str | None = Field(default=None, max_length=32)
    operation: str | None = Field(default=None, max_length=32)
    quantity: str | None = Field(default=None, max_length=40)
    page_id: str | None = Field(default=None, max_length=128)
    box_norm: tuple[float, float, float, float] | None = None
    mark_type: str | None = Field(default=None, max_length=32)


class ReassessmentPlan(_Frozen):
    """What a decision-changing action asks the API to create and publish.

    ``corrections`` are every decision-changing event recorded against the base
    assessment so far, in order, including the trigger: the new input revision is the
    base input revision plus exactly these corrections. ``rerun_stages`` is the union of
    the stages the corrections require; every other branch stage is reused unchanged and
    listed in ``reused_stages`` (the orchestrator ``reuse_hint``) with any known
    artifacts in ``reuse_lineage``. ``publish`` is ``consolidate`` when M8 is the only
    rerun, so the API publishes ``cmev.cmd.consolidate.v1`` directly; otherwise it is
    ``input_revision_created`` and the orchestrator reruns the listed stages.
    """

    claim_id: ClaimId
    trigger: Literal["reassessment"] = "reassessment"
    trigger_action_id: str
    trigger_action_type: ReviewActionType
    base_assessment_revision: Revision
    base_input_revision: Revision
    previous_input_revision: Revision
    new_input_revision: Revision
    review_revision: Revision
    corrections: tuple[ReviewEvent, ...] = Field(min_length=1)
    rerun_stages: tuple[Stage, ...]
    reused_stages: tuple[Stage, ...]
    reuse_lineage: tuple[ReusedArtifact, ...] = ()
    publish: Literal["consolidate", "input_revision_created"]
    neural_rerun: bool
    """True only when an image or document neural stage (M1, M2, M4, M6) must rerun."""

    @property
    def reuse_hint(self) -> tuple[str, ...]:
        return self.reused_stages


class RecordedAction(_Frozen):
    """One committed action: its event, the request payload hash and any reassessment plan."""

    event: ReviewEvent
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan: ReassessmentPlan | None = None


class MarkView(_Frozen):
    """A pen mark as the review currently sees it: the assessment's mark plus this review's decisions."""

    mark_id: str
    page_id: str
    box_norm: BoxNorm
    mark_type: Literal["exclusion", "price_change"]
    state: Literal["pending", "confirmed", "rejected"]
    entry_id: str | None
    candidate_entry_ids: tuple[str, ...] = ()
    confirmed_amount: str | None = None
    confirmed_currency: str | None = None
    confirmed_cost_basis: str | None = None
    origin: Literal["detector", "human_added"]
    decided_in_review: bool = False

    @classmethod
    def of(cls, mark: PenMark) -> MarkView:
        return cls(mark_id=mark.mark_id, page_id=mark.page_id, box_norm=mark.box_norm, mark_type=mark.mark_type,
                   state=mark.state, entry_id=mark.entry_id, candidate_entry_ids=tuple(mark.candidate_entry_ids),
                   confirmed_amount=mark.confirmed_amount, confirmed_currency=mark.confirmed_currency,
                   confirmed_cost_basis=mark.confirmed_cost_basis, origin=mark.origin)


class ReviewState(_Frozen):
    """The review of one assessment revision. Build the first one with ``open_review``."""

    assessment: Assessment
    current_input_revision: Revision
    """The claim's current input revision; ahead of the assessment while a reassessment is pending."""
    claim_assessment_revision: Revision | None
    """The claim's current assessment pointer; it moves only when a newer assessment is ready."""
    currency: Currency
    cost_basis: Identifier
    line_items: tuple[LineItem, ...] = ()
    marks: tuple[PenMark, ...] = ()
    completeness: DeclarationCompleteness | None = None
    part_summaries: tuple[PartSummary, ...] = ()
    coverage: tuple[PartCoverage, ...] = ()
    photo_ids: frozenset[str] | None = None
    """Known photo ids of the assessed input; None skips the existence check."""
    page_ids: frozenset[str] | None = None
    """Known estimate page ids of the assessed input; None skips the existence check."""
    base_stage_artifacts: dict[str, tuple[ReusedArtifact, ...]] = Field(default_factory=dict)
    """Stage results of the assessed input revision, used to fill ``reuse_lineage``."""
    initial_review_revision: int = Field(ge=0)
    review_revision: int = Field(ge=0)
    actions: tuple[RecordedAction, ...] = ()
    inherited_notes: tuple[ReviewEvent, ...] = ()
    inherited_dismissals: dict[str, ReviewEvent] = Field(default_factory=dict)
    inherited_addition_decisions: dict[str, ReviewEvent] = Field(default_factory=dict)
    accepted_scope: tuple[ReviewEvent, ...] = ()
    finalization: Finalization | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ReviewState:
        a = self.assessment
        if self.current_input_revision < a.input_revision:
            raise ValueError("the claim's current input revision cannot precede the assessed input revision")
        if self.review_revision != self.initial_review_revision + len(self.actions):
            raise ValueError("review_revision is the initial revision plus one per committed action")
        expected = self.initial_review_revision
        for recorded in self.actions:
            event = recorded.event
            if event.claim_id != a.claim_id or event.assessment_revision != a.assessment_revision:
                raise ValueError("every recorded action belongs to this claim and assessment revision")
            if event.expected_review_revision != expected:
                raise ValueError("recorded actions form one unbroken chain of review revisions")
            expected = event.resulting_review_revision
        unknown = set(self.base_stage_artifacts) - set(BRANCH_STAGES)
        if unknown:
            raise ValueError(f"base_stage_artifacts names unknown stages {sorted(unknown)}")
        f = self.finalization
        if f is not None and (f.claim_id != a.claim_id or f.assessment_revision != a.assessment_revision
                              or f.review_revision != self.review_revision):
            raise ValueError("a finalization freezes this assessment at the current review revision")
        return self

    # --- identity and derived views -------------------------------------------------

    @property
    def claim_id(self) -> str:
        return self.assessment.claim_id

    @property
    def assessment_revision(self) -> int:
        return self.assessment.assessment_revision

    @property
    def events(self) -> tuple[ReviewEvent, ...]:
        return tuple(r.event for r in self.actions)

    @property
    def finalized(self) -> bool:
        return self.finalization is not None

    @property
    def status(self) -> Literal["unreviewed", "in_review", "finalized"]:
        """Application platform section 7.4."""
        if self.finalized:
            return "finalized"
        return "in_review" if self.actions else "unreviewed"

    @property
    def pending_reassessment(self) -> bool:
        """True while a decision-changing edit has no ready assessment yet."""
        return self.current_input_revision != self.assessment.input_revision

    def recorded(self, idempotency_key: str) -> RecordedAction | None:
        return next((r for r in self.actions if r.event.idempotency_key == idempotency_key), None)

    def finding(self, finding_id: str) -> AssessmentFinding | None:
        return next((f for f in self.assessment.findings if f.finding_id == finding_id), None)

    def finding_for_entry(self, entry_id: str) -> AssessmentFinding | None:
        return next((f for f in self.assessment.findings if f.entry_id == entry_id), None)

    def line_item(self, entry_id: str) -> LineItem | None:
        return next((i for i in self.line_items if i.entry_id == entry_id), None)

    def candidate(self, candidate_id: str) -> ProposedRepairAddition | None:
        return next((c for c in self.assessment.possible_additions if c.candidate_id == candidate_id), None)

    def dismissals(self) -> dict[str, ReviewEvent]:
        """Finding id to the dismissal recorded against it in this review."""
        return {**self.inherited_dismissals,
                **{e.finding_id: e for e in self.events if e.action_type == "dismiss_finding" and e.finding_id}}

    def addition_decisions(self) -> dict[str, ReviewEvent]:
        """Candidate id to the accept or dismiss action recorded in this review."""
        return {**self.inherited_addition_decisions, **{e.candidate_id: e for e in self.events
                if e.action_type in ("accept_addition", "dismiss_addition") and e.candidate_id}}

    def notes(self) -> tuple[ReviewEvent, ...]:
        return self.inherited_notes + tuple(e for e in self.events if e.action_type == "add_note")

    def decision_events(self) -> tuple[ReviewEvent, ...]:
        """Decision-changing events recorded against this assessment, in order."""
        return tuple(e for e in self.events if e.decision_changing)

    def effective_marks(self) -> dict[str, MarkView]:
        """The assessment's marks with this review's mark decisions applied, in first-seen order."""
        views = {m.mark_id: MarkView.of(m) for m in self.marks}
        for event in self.events:
            new: dict[str, Any] = event.new_values
            mark = views.get(event.mark_id) if event.mark_id else None
            if event.action_type == "add_mark" and event.mark_id:
                views[event.mark_id] = MarkView(
                    mark_id=event.mark_id, page_id=new["page_id"], box_norm=tuple(new["box_norm"]),
                    mark_type=new["mark_type"], state="confirmed", entry_id=new["entry_id"],
                    confirmed_amount=new.get("amount"), confirmed_currency=new.get("currency"),
                    confirmed_cost_basis=new.get("cost_basis"), origin="human_added", decided_in_review=True)
            elif mark is None:
                continue
            elif event.action_type == "confirm_mark":
                views[mark.mark_id] = mark.model_copy(update=dict(
                    state="confirmed", entry_id=new["entry_id"], confirmed_amount=new.get("amount"),
                    confirmed_currency=new.get("currency"), confirmed_cost_basis=new.get("cost_basis"),
                    decided_in_review=True))
            elif event.action_type == "reject_mark":
                views[mark.mark_id] = mark.model_copy(update=dict(state="rejected", decided_in_review=True))
            elif event.action_type == "correct_mark_link":
                views[mark.mark_id] = mark.model_copy(update=dict(entry_id=new["entry_id"], decided_in_review=True))
            elif event.action_type == "enter_amount":
                views[mark.mark_id] = mark.model_copy(update=dict(
                    confirmed_amount=new["amount"], confirmed_currency=new["currency"],
                    confirmed_cost_basis=new["cost_basis"], decided_in_review=True))
        return views


def open_review(
    assessment: Assessment,
    *,
    currency: str,
    cost_basis: str,
    review_revision: int = 0,
    current_input_revision: int | None = None,
    claim_assessment_revision: int | None = None,
    line_items: tuple[LineItem, ...] | list[LineItem] = (),
    marks: tuple[PenMark, ...] | list[PenMark] = (),
    completeness: DeclarationCompleteness | None = None,
    part_summaries: tuple[PartSummary, ...] | list[PartSummary] = (),
    coverage: tuple[PartCoverage, ...] | list[PartCoverage] = (),
    photo_ids: frozenset[str] | set[str] | None = None,
    page_ids: frozenset[str] | set[str] | None = None,
    base_stage_artifacts: dict[str, tuple[ReusedArtifact, ...]] | None = None,
) -> ReviewState:
    """The initial review state of an assessment, before any action.

    ``review_revision`` is the claim's current review counter. The claim pointers
    default to "this assessment is current and its input revision is the claim's".
    """
    return ReviewState(
        assessment=assessment,
        current_input_revision=current_input_revision or assessment.input_revision,
        claim_assessment_revision=(assessment.assessment_revision if claim_assessment_revision is None
                                   else claim_assessment_revision),
        currency=currency, cost_basis=cost_basis,
        line_items=tuple(line_items), marks=tuple(marks), completeness=completeness,
        part_summaries=tuple(part_summaries), coverage=tuple(coverage),
        photo_ids=None if photo_ids is None else frozenset(photo_ids),
        page_ids=None if page_ids is None else frozenset(page_ids),
        base_stage_artifacts=dict(base_stage_artifacts or {}),
        initial_review_revision=review_revision, review_revision=review_revision,
    )
