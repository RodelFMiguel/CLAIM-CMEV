"""Assessment records written by M8 (data contracts section 9).

Every check is stored separately. A skipped check is ``not_evaluated``, never
``passed``; a confirmed exclusion is a row state with every check ``not_evaluated``,
never ``ok``; ``ok`` requires every check to pass and the cost to be within range.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import Field, model_validator

from .common import (
    RESOLVED_SIDES,
    SCHEMA_VERSION,
    BoxNorm,
    ClaimId,
    ContractModel,
    PartCode,
    Provenance,
    Reason,
    ReasonCode,
    RecordId,
    Revision,
    SchemaVersion,
    Side,
    UtcDatetime,
    Versions,
    require_reasons,
)
from .costs import CostCheck

CheckStatus = Literal["passed", "failed", "insufficient", "not_evaluated"]
OverallResult = Literal["ok", "unsupported", "cost_outlier", "insufficient_evidence", "not_evaluated"]
DISPLAY_RESULTS = ("ok", "unsupported", "cost_outlier", "insufficient_evidence")
"""The four display labels of v2 section 7.2; ``not_evaluated`` has no result chip."""


class CheckResult(ContractModel):
    result: CheckStatus
    reasons: list[Reason] = Field(min_length=1)
    detail: dict[str, Any] = Field(default_factory=dict)
    rule_id: str | None = None


class EvidenceRef(ContractModel):
    kind: Literal["photo", "mask", "observation", "summary", "coverage", "page_box", "mark_box",
                  "line_item", "cost_range", "confirmation"]
    ref_id: str = Field(min_length=1)
    box_norm: BoxNorm | None = None
    artifact_id: str | None = None


class ExplanationSentence(ContractModel):
    """Stretch ``cmev-explainer`` phrasing of an already computed finding. Never a result."""

    text: str = Field(min_length=1)
    provenance: Provenance

    @model_validator(mode="after")
    def _explainer(self) -> ExplanationSentence:
        if self.provenance.source_kind != "explainer":
            raise ValueError("an explanation sentence carries provenance.source_kind 'explainer'")
        return self


class AssessmentFinding(ContractModel):
    finding_id: RecordId
    assessment_revision: Revision
    entry_id: RecordId
    documentary_check: CheckResult
    photographic_check: CheckResult
    cost_check: CostCheck
    mark_state_check: CheckResult
    overall_result: OverallResult
    row_state: Literal["active", "excluded"]
    reasons: list[Reason] = Field(min_length=1)
    evidence_refs: list[EvidenceRef]
    applied_range_id: RecordId | None
    applied_range_reason: ReasonCode | None = None
    pinned_versions: Versions
    created_at: UtcDatetime
    content_hash: str | None = None
    explanation_sentence: ExplanationSentence | None = None

    @property
    def checks(self) -> dict[str, str]:
        return {"documentary": self.documentary_check.result, "mark_state": self.mark_state_check.result,
                "photographic": self.photographic_check.result, "cost": self.cost_check.result}

    @model_validator(mode="after")
    def _rules(self) -> AssessmentFinding:
        require_reasons(self.__dict__, [("applied_range_id", "applied_range_reason")])
        if self.cost_check.entry_id != self.entry_id:
            raise ValueError("the cost check belongs to the same entry as the finding")
        compared = self.cost_check.result in ("within_range", "outside_range")
        if compared and self.applied_range_id != self.cost_check.range_id:
            raise ValueError("applied_range_id is the range the cost check applied")
        if not compared and self.applied_range_id is not None:
            raise ValueError("no range is applied when the cost check did not compare")
        excluded = self.row_state == "excluded"
        if excluded != (self.overall_result == "not_evaluated"):
            raise ValueError("'not_evaluated' is stored exactly for a confirmed-exclusion row")
        if excluded and any(v != "not_evaluated" for v in self.checks.values()):
            raise ValueError("an excluded row has every per-check result 'not_evaluated'")
        if self.overall_result == "ok" and self.checks != {
                "documentary": "passed", "mark_state": "passed", "photographic": "passed", "cost": "within_range"}:
            raise ValueError("'ok' requires every check passed and the cost within range")
        if self.overall_result == "unsupported" and self.photographic_check.result != "failed":
            raise ValueError("'unsupported' requires a failed photographic check")
        if self.overall_result == "cost_outlier" and (
                self.photographic_check.result != "passed" or self.cost_check.result != "outside_range"):
            raise ValueError("'cost_outlier' requires a passed photographic check and an outside_range cost")
        return self


class ProposedRepairAddition(ContractModel):
    """A possible missing repair. No entry, operation or amount until the surveyor supplies them."""

    candidate_id: RecordId
    assessment_revision: Revision
    summary_ids: list[RecordId] = Field(default_factory=list)
    observation_ids: list[RecordId] = Field(default_factory=list)
    part_code: PartCode | None
    side: Side
    status: Literal["proposed", "withheld", "suppressed"]
    reason: Reason
    suppressed_by_entry_id: RecordId | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    review_state: Literal["open", "accepted", "dismissed"] = "open"

    @model_validator(mode="after")
    def _rules(self) -> ProposedRepairAddition:
        if not self.summary_ids and not self.observation_ids:
            raise ValueError("an addition references its summaries or observations")
        if self.status == "proposed" and (self.part_code is None or self.side not in RESOLVED_SIDES):
            raise ValueError("a proposed addition has a resolved part and side")
        if (self.status == "suppressed") != (self.suppressed_by_entry_id is not None):
            raise ValueError("a suppressed addition names the excluded row that suppressed it, and only then")
        return self


class Assessment(ContractModel):
    """The persisted immutable assessment for one claim input revision."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    claim_id: ClaimId
    input_revision: Revision
    assessment_revision: Revision
    review_revision: int | None = Field(default=None, ge=0)
    state: Literal["ready", "incomplete"]
    findings: list[AssessmentFinding]
    missing_repairs_check: CheckResult
    possible_additions: list[ProposedRepairAddition]
    suppressed_additions: list[ProposedRepairAddition]
    incomplete_reasons: list[ReasonCode]
    cost_table_version: str = Field(min_length=1)
    rules_config_version: str = Field(min_length=1)
    pinned_versions: Versions
    superseded: bool = False
    provenance: Provenance
    created_at: UtcDatetime

    def finding_counts(self) -> dict[str, int]:
        """Counts in the ``assessment-ready`` shape: excluded rows are counted separately."""
        counts = Counter(f.overall_result for f in self.findings)
        return {"ok": counts["ok"], "unsupported": counts["unsupported"], "cost_outlier": counts["cost_outlier"],
                "insufficient_evidence": counts["insufficient_evidence"], "excluded": counts["not_evaluated"]}

    def cost_check_counts(self) -> dict[str, int]:
        counts = Counter(f.cost_check.result for f in self.findings)
        return {k: counts[k] for k in ("within_range", "outside_range", "insufficient_support", "not_evaluated")}

    @model_validator(mode="after")
    def _rules(self) -> Assessment:
        if (self.state == "ready") != (not self.incomplete_reasons):
            raise ValueError("incomplete_reasons are empty exactly when the assessment is ready")
        revisions = {f.assessment_revision for f in self.findings}
        revisions |= {a.assessment_revision for a in self.possible_additions + self.suppressed_additions}
        if revisions - {self.assessment_revision}:
            raise ValueError("every finding and addition belongs to this assessment revision")
        for name, values in (("finding_id", [f.finding_id for f in self.findings]),
                             ("entry_id", [f.entry_id for f in self.findings]),
                             ("candidate_id", [a.candidate_id for a in self.possible_additions + self.suppressed_additions])):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} values are unique within an assessment")
        if any(a.status == "suppressed" for a in self.possible_additions):
            raise ValueError("suppressed additions are stored in suppressed_additions")
        if any(a.status != "suppressed" for a in self.suppressed_additions):
            raise ValueError("suppressed_additions holds only suppressed candidates")
        return self
