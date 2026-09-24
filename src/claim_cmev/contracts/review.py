"""Human review actions and finalization (data contracts sections 10 and 11).

Machine records are immutable. A decision-changing action creates a new input and
assessment revision; the rest create a review revision only. A surveyor's edit is not
an approval.
"""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import Field, model_validator

from .common import (
    SCHEMA_VERSION,
    ClaimId,
    ContractModel,
    ReasonCode,
    RecordId,
    Revision,
    SchemaVersion,
    UtcDatetime,
)

ReviewActionType = Literal[
    "confirm_mark", "reject_mark", "add_mark", "correct_mark_link", "enter_amount", "correct_line_item",
    "confirm_identity", "confirm_coverage", "confirm_declaration_completeness", "accept_addition",
    "dismiss_addition", "dismiss_finding", "add_note",
]
REVIEW_ACTION_TYPES: tuple[str, ...] = get_args(ReviewActionType)
DECISION_CHANGING_ACTIONS = frozenset(REVIEW_ACTION_TYPES) - {"dismiss_addition", "dismiss_finding", "add_note"}
DismissalReason = Literal["hidden_damage_after_dismantling", "adas_calibration_or_specialist_procedure",
                          "parts_price_change", "inadequate_photograph", "system_error", "other"]
DISMISSAL_REASONS: tuple[str, ...] = get_args(DismissalReason)

_REQUIRED_TARGET = {
    "confirm_mark": "mark_id", "reject_mark": "mark_id", "correct_mark_link": "mark_id", "enter_amount": "mark_id",
    "add_mark": "entry_id", "correct_line_item": "entry_id", "accept_addition": "candidate_id",
    "dismiss_addition": "candidate_id", "dismiss_finding": "finding_id",
}


class ReviewEvent(ContractModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    event_id: RecordId
    action_id: RecordId
    claim_id: ClaimId
    assessment_revision: Revision
    expected_review_revision: int = Field(ge=0)
    resulting_review_revision: Revision
    actor: str = Field(min_length=1)
    recorded_at: UtcDatetime
    action_type: ReviewActionType
    finding_id: RecordId | None = None
    entry_id: RecordId | None = None
    mark_id: RecordId | None = None
    candidate_id: RecordId | None = None
    reason_code: ReasonCode | None = None
    note: str | None = None
    original_values: dict[str, Any] = Field(default_factory=dict)
    new_values: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @property
    def decision_changing(self) -> bool:
        return self.action_type in DECISION_CHANGING_ACTIONS

    @model_validator(mode="after")
    def _rules(self) -> ReviewEvent:
        if self.resulting_review_revision != self.expected_review_revision + 1:
            raise ValueError("a saved action advances the review revision by exactly one")
        target = _REQUIRED_TARGET.get(self.action_type)
        if target and getattr(self, target) is None:
            raise ValueError(f"{self.action_type} needs {target}")
        if self.action_type == "dismiss_finding" and self.reason_code not in DISMISSAL_REASONS:
            raise ValueError(f"a dismissal reason is one of {list(DISMISSAL_REASONS)}")
        if self.action_type == "add_note" and not (self.note and self.note.strip()):
            raise ValueError("add_note needs a note")
        return self


class FinalizationPrecondition(ContractModel):
    code: ReasonCode
    passed: bool
    detail: str | None = None


REQUIRED_FINALIZATION_PRECONDITIONS = frozenset({
    "no_pending_marks", "no_unlinked_marks", "reassessment_complete", "no_failed_stages", "review_revision_current",
})


class Finalization(ContractModel):
    """Freezes one completed assessment with its matching review revision. Not a claim approval."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    finalization_id: RecordId
    claim_id: ClaimId
    input_revision: Revision
    assessment_revision: Revision
    review_revision: int = Field(ge=0)
    actor: str = Field(min_length=1)
    finalized_at: UtcDatetime
    preconditions: list[FinalizationPrecondition] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> Finalization:
        codes = {p.code for p in self.preconditions}
        missing = REQUIRED_FINALIZATION_PRECONDITIONS - codes
        if missing:
            raise ValueError(f"finalization records every required precondition; missing {sorted(missing)}")
        failed = [p.code for p in self.preconditions if not p.passed]
        if failed:
            raise ValueError(f"finalize is refused while preconditions fail: {failed}")
        return self
