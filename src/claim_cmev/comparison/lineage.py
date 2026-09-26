"""Finding content hash and the dismissal carry-forward rule (module 08 "Reassessment and lineage").

``content_hash`` covers a finding's row state, overall result, four checks (including
the cost check's pinned table and policy versions), reasons, evidence references and
applied range. It excludes identifiers, revision numbers and timestamps, so the same
decision on the same evidence hashes the same in a later assessment.

A dismissal belongs to the finding it was made against. It carries forward (proposed
rule) only to the same entry's finding in a later assessment of the same claim whose
recomputed content hash is identical; any change in a check, a reason, the evidence or
the pinned cost table stops it. A stored hash that does not match its content is treated
as changed evidence. The API records a carry-forward as its own event with ``carried_from``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json

from claim_cmev.contracts.assessment import Assessment, AssessmentFinding
from claim_cmev.contracts.common import ContractError

HASHED_FIELDS = ("row_state", "overall_result", "documentary_check", "mark_state_check", "photographic_check",
                 "cost_check", "reasons", "evidence_refs", "applied_range_id", "applied_range_reason")


def content_hash(finding: AssessmentFinding) -> str:
    """sha256 over the canonical JSON of the hashed fields; recomputed, never read from the record."""
    material = finding.model_dump(mode="json", include=set(HASHED_FIELDS))
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def with_content_hash(finding: AssessmentFinding) -> AssessmentFinding:
    return finding.model_copy(update={"content_hash": content_hash(finding)})


def _trusted_hash(finding: AssessmentFinding) -> str | None:
    computed = content_hash(finding)
    return computed if finding.content_hash in (None, computed) else None


def dismissal_carries_forward(prior: AssessmentFinding, current: AssessmentFinding) -> bool:
    """True only for the same entry with an unchanged, verifiable content hash."""
    if prior.entry_id != current.entry_id or current.assessment_revision <= prior.assessment_revision:
        return False
    prior_hash, current_hash = _trusted_hash(prior), _trusted_hash(current)
    return prior_hash is not None and prior_hash == current_hash


@dataclass(frozen=True)
class CarriedDismissal:
    """A dismissal re-applied to a new finding, with its source recorded."""

    finding_id: str
    carried_from: str
    entry_id: str
    reason_code: str
    prior_assessment_revision: int
    assessment_revision: int


def carry_forward_dismissals(prior: Assessment, current: Assessment,
                             dismissed: Mapping[str, str]) -> list[CarriedDismissal]:
    """Dismissals (prior finding_id -> reason code) that may carry into ``current``.

    Anything not returned is active again in the new assessment.
    """
    if prior.claim_id != current.claim_id:
        raise ContractError("claim_mismatch", "dismissals carry only within one claim")
    by_entry = {f.entry_id: f for f in current.findings}
    carried = []
    for finding in sorted(prior.findings, key=lambda f: f.finding_id):
        if finding.finding_id not in dismissed:
            continue
        successor = by_entry.get(finding.entry_id)
        if successor is not None and dismissal_carries_forward(finding, successor):
            carried.append(CarriedDismissal(successor.finding_id, finding.finding_id, finding.entry_id,
                                            dismissed[finding.finding_id], prior.assessment_revision,
                                            current.assessment_revision))
    return carried


__all__ = ["HASHED_FIELDS", "CarriedDismissal", "carry_forward_dismissals", "content_hash",
           "dismissal_carries_forward", "with_content_hash"]
