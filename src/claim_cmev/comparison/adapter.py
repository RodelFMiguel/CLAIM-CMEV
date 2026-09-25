"""M8 consolidation: the pure ``consolidate`` entry point (module 08, integration contracts 4 and 8.8).

``consolidate`` performs no I/O, opens no socket and reads no clock: the container
fetches the branch records and the pinned cost table, passes them in with the revision,
timestamp, versions and provenance to stamp, and persists the returned ``Assessment``.
The same request always yields the same assessment, identifiers included.

Per line item the twelve ordered rules R1 to R12 run; the first rule with an outcome
stops the flow and its identifier is recorded (``ConsolidationResult.outcome_rules`` and
each check's ``rule_id``). Each check is stored separately, so a passed photo check can sit
beside a withheld cost check; a check the flow never reached is ``not_evaluated`` with
``check_not_reached``, never ``passed``. Possible additions (A1 to A8) run once per
assessment. No language model takes part.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from claim_cmev.contracts.assessment import Assessment, AssessmentFinding, CheckResult, EvidenceRef
from claim_cmev.contracts.claims import ReusedArtifact
from claim_cmev.contracts.common import (
    COST_BASIS,
    RESOLVED_SIDES,
    ClaimId,
    ContractError,
    ContractModel,
    Currency,
    Provenance,
    Revision,
    UtcDatetime,
    VehicleClass,
    Versions,
    deterministic_id,
    make_job_key,
)
from claim_cmev.contracts.costs import CostCheck
from claim_cmev.contracts.documents import (
    DeclarationCompleteness,
    DocumentPage,
    LineItem,
    PenMark,
    effective_price_for,
)
from claim_cmev.contracts.fixtures import FixtureBundle
from claim_cmev.contracts.imaging import (
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    PartCoverage,
    PartSummary,
)

from ..contracts.review import ReviewEvent

from .additions import addition_rule_id, missing_repairs_check, propose_additions
from .config import RuleConfig
from .cost_check import RangeLookup, compare_amount, cost_check_not_evaluated, cost_key, cost_rule_id
from .inputs import EvidenceIndex, line_item_order, page_states, row_marks, uncertain_fields
from .lineage import with_content_hash
from .reason_codes import REASON_CATALOGUE_VERSION, reason, require_code

TASK = "consolidate"
_COVERAGE_CODES = {"inadequate": "coverage_inadequate", "not_visible": "coverage_not_visible",
                   "unresolved": "coverage_unresolved"}
_BRANCH_CODE = {"failed": "branch_failed", "skipped_no_photos": "branch_missing", "skipped_no_pages": "branch_missing"}


class ConsolidationRequest(ContractModel):
    """Everything one assessment is built from. Records are the shared contract records."""

    claim_id: ClaimId
    input_revision: Revision
    assessment_revision: Revision
    review_revision: int | None = Field(default=None, ge=0)
    trigger: Literal["branches_complete", "reassessment", "retry"] = "branches_complete"
    image_branch_state: Literal["complete", "failed", "skipped_no_photos"]
    document_branch_state: Literal["complete", "failed", "skipped_no_pages"]
    vehicle_class: VehicleClass
    currency: Currency
    part_summaries: list[PartSummary] = Field(default_factory=list)
    coverage: list[PartCoverage] = Field(default_factory=list)
    observations: list[ImageDamageObservation] = Field(default_factory=list)
    identity_confirmations: list[IdentityConfirmation] = Field(default_factory=list)
    coverage_confirmations: list[CoverageConfirmation] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)
    pen_marks: list[PenMark] = Field(default_factory=list)
    accepted_scope: list[ReviewEvent] = Field(default_factory=list)
    declaration: DeclarationCompleteness | None = None
    pages: list[DocumentPage] = Field(default_factory=list)
    cost_table_version: str = Field(min_length=1)
    rules_config_version: str = Field(min_length=1)
    pinned_versions: Versions
    provenance: Provenance
    created_at: UtcDatetime
    reuse_lineage: list[ReusedArtifact] = Field(default_factory=list)
    prior_assessment_revision: Revision | None = None
    superseded: bool = False

    @model_validator(mode="after")
    def _rules(self) -> ConsolidationRequest:
        if any(e.action_type != "accept_addition" or e.claim_id != self.claim_id or
               e.assessment_revision >= self.assessment_revision for e in self.accepted_scope):
            raise ValueError("accepted scope must be recorded accept-addition events for this claim")
        if self.document_branch_state != "complete" and (self.line_items or self.pen_marks):
            raise ValueError("a failed or skipped document branch carries no line items or marks")
        if self.prior_assessment_revision is not None and self.prior_assessment_revision >= self.assessment_revision:
            raise ValueError("prior_assessment_revision precedes assessment_revision")
        return self


@dataclass(frozen=True)
class ConsolidationResult:
    """The immutable assessment plus the counts and rule identifiers the consumer publishes."""

    assessment: Assessment
    job_key: str
    outcome_rules: Mapping[str, str]
    addition_rules: Mapping[str, str]
    trigger: str
    prior_assessment_revision: int | None = None
    reuse_lineage: tuple[ReusedArtifact, ...] = ()

    @property
    def finding_counts(self) -> dict[str, int]:
        return self.assessment.finding_counts()

    @property
    def cost_check_counts(self) -> dict[str, int]:
        return self.assessment.cost_check_counts()

    @property
    def proposed_addition_count(self) -> int:
        return sum(1 for a in self.assessment.possible_additions if a.status == "proposed")

    @property
    def withheld_addition_count(self) -> int:
        return sum(1 for a in self.assessment.possible_additions if a.status == "withheld")

    @property
    def suppressed_addition_count(self) -> int:
        return len(self.assessment.suppressed_additions)

    def ready_payload(self) -> dict[str, Any]:
        """The ``cmev.evt.assessment-ready.v1`` payload for this assessment."""
        a = self.assessment
        return {"assessment_revision": a.assessment_revision, "assessment_state": a.state,
                "finding_counts": self.finding_counts, "proposed_addition_count": self.proposed_addition_count,
                "suppressed_addition_count": self.suppressed_addition_count, "cost_checks": self.cost_check_counts,
                "pinned_versions": dict(a.pinned_versions), "incomplete_reasons": list(a.incomplete_reasons)}


@dataclass(frozen=True)
class _Outcome:
    rule: str
    overall: str
    row_state: str
    documentary: CheckResult
    mark: CheckResult
    photo: CheckResult
    cost: CostCheck
    reasons: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass
class _Context:
    request: ConsolidationRequest
    config: RuleConfig
    ranges: RangeLookup
    index: EvidenceIndex
    pages: dict[str, str]
    job_key: str
    pinned: dict[str, str]

    @property
    def policy(self) -> str:
        return self.config.rules_config_version

    @property
    def table(self) -> str:
        return self.request.cost_table_version


def _check(result: str, codes: Sequence[str], rule: str, **detail: Any) -> CheckResult:
    return CheckResult(result=result, reasons=[reason(c) for c in codes], rule_id=rule, detail=detail)


def _not_reached(rule: str) -> CheckResult:
    return _check("not_evaluated", ["check_not_reached"], rule)


def _obs_refs(observations: Iterable[ImageDamageObservation]) -> list[EvidenceRef]:
    return [EvidenceRef(kind="observation", ref_id=o.observation_id, box_norm=o.bbox_norm,
                        artifact_id=o.damage_mask_ref.artifact_id) for o in observations]


def _unique(values: Iterable[Any]) -> list[Any]:
    seen, out = set(), []
    for value in values:
        key = value.model_dump_json() if hasattr(value, "model_dump_json") else value
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def job_key_for(request: ConsolidationRequest) -> str:
    """``claim:input_revision:consolidate:all:<signature of rules config and cost table>``."""
    return make_job_key(request.claim_id, request.input_revision, TASK,
                        {"rules_config": request.rules_config_version, "cost_table": request.cost_table_version})


def _evaluate(item: LineItem, ctx: _Context) -> _Outcome:
    """Rules R1 to R12 for one line item; the first rule with an outcome stops the flow."""
    req, cfg, index = ctx.request, ctx.config, ctx.index
    marks = row_marks(item.entry_id, req.pen_marks)
    uncertain = uncertain_fields(item)
    refs: list[EvidenceRef] = [
        EvidenceRef(kind="line_item", ref_id=item.entry_id, box_norm=item.row_box_norm),
        EvidenceRef(kind="page_box", ref_id=item.page_id, box_norm=item.amount_box_norm or item.row_box_norm),
        *(EvidenceRef(kind="mark_box", ref_id=m.mark_id, box_norm=m.box_norm) for m in marks.marks)]

    def not_cost(code: str) -> CostCheck:
        return cost_check_not_evaluated(item.entry_id, code, policy_version=ctx.policy, cost_table_version=ctx.table)

    def stop(rule: str, overall: str, codes: Sequence[str], *, documentary: CheckResult | None = None,
             mark: CheckResult | None = None, photo: CheckResult | None = None,
             cost: CostCheck | None = None) -> _Outcome:
        return _Outcome(rule, overall, "active", documentary or _not_reached(rule), mark or _not_reached(rule),
                        photo or _not_reached(rule), cost or not_cost("check_not_reached"), tuple(codes), tuple(refs))

    # R1: the image branch is required for every line item.
    if req.image_branch_state != "complete":
        code = _BRANCH_CODE[req.image_branch_state]
        return stop("R1", "insufficient_evidence", [code],
                    photo=_check("not_evaluated", [code], "R1", image_branch_state=req.image_branch_state))

    # R2: pending, conflicting or unlinked marks withhold the row before anything else.
    price = effective_price_for(item.printed_line_amount, req.pen_marks, entry_id=item.entry_id)
    mark_detail = {"linked_mark_ids": [m.mark_id for m in marks.linked],
                   "unlinked_mark_ids": [m.mark_id for m in marks.unlinked]}
    blocking = marks.blocking_codes
    if blocking:
        codes, cost = list(blocking), None
        if price.effective_price_source == "unresolved" and price.reason != "amount_unreadable":
            cost = not_cost("amount_unresolved")  # a pending repricing: the printed amount is never used
            codes.append("amount_unresolved")
        return stop("R2", "insufficient_evidence", codes, cost=cost,
                    mark=_check("insufficient", blocking, "R2", effective_price_reason=price.reason, **mark_detail))

    # R3: only a confirmed exclusion skips the row; it is a row state, never ok.
    if marks.excluded:
        excluded = _check("not_evaluated", ["exclusion_confirmed"], "R3",
                          exclusion_mark_id=marks.confirmed_exclusion.mark_id)
        return _Outcome("R3", "not_evaluated", "excluded", excluded, excluded, excluded,
                        not_cost("exclusion_confirmed"), ("exclusion_confirmed",), tuple(refs))
    mark_ok = _check("passed", ["marks_clear"] + (["price_change_confirmed"] if marks.confirmed_price_change else []),
                     "R3", **mark_detail)

    # R4: the row must be readable and mapped.
    doc_codes = []
    if item.part_code is None or item.part_mapping_status != "resolved":
        doc_codes.append("row_fields_incomplete")
    if item.operation is None or item.operation_mapping_status != "resolved" or item.operation in ("other", "unknown"):
        doc_codes.append("operation_unresolved")
    flagged = [f for f in ("part_code", "operation") if f in uncertain and getattr(item, f) is not None]
    flagged += [f for f in ("page_id", "row_box_norm") if f in uncertain]
    if flagged:
        doc_codes.append("extraction_incomplete")
    if ctx.pages.get(item.page_id) == "unreadable":
        doc_codes.append("page_unreadable")
    if doc_codes:
        return stop("R4", "insufficient_evidence", doc_codes, mark=mark_ok,
                    documentary=_check("insufficient", doc_codes, "R4", uncertain_fields=sorted(uncertain)))

    # R5: one physical part and side, resolved by the row and by a recorded identity confirmation.
    part, side = item.part_code, item.side
    if side not in RESOLVED_SIDES or "side" in uncertain:
        code = "side_unresolved"
    elif not index.identity_resolved(part, side):
        code = "side_unresolved" if index.has_unsided_evidence(part) else "part_identity_unresolved"
    else:
        code = None
    if code:
        return stop("R5", "insufficient_evidence", [code], mark=mark_ok,
                    documentary=_check("insufficient", [code], "R5", part_code=part, side=side))
    slot = index.slot(part, side)
    identity_ids = index.confirmed_identity_ids(part, side, slot.identity_confirmation_ids)
    refs.append(EvidenceRef(kind="coverage", ref_id=slot.coverage_id))
    refs += [EvidenceRef(kind="confirmation", ref_id=cid) for cid in identity_ids]
    documentary = _check("passed", ["row_identity_resolved"], "R5", part_code=part, side=side,
                         identity_confirmation_ids=identity_ids)

    # R6: adequate views of that physical part.
    if slot.state != cfg.coverage.required_state:
        code = _COVERAGE_CODES[slot.state]
        return stop("R6", "insufficient_evidence", [code], documentary=documentary, mark=mark_ok,
                    photo=_check("insufficient", [code], "R6", coverage_id=slot.coverage_id,
                                 coverage_reasons=list(slot.reasons)))
    refs.append(EvidenceRef(kind="confirmation", ref_id=slot.coverage_confirmation_id))
    refs += [EvidenceRef(kind="photo", ref_id=p) for p in sorted(slot.covering_photo_ids)]

    # R7 and R8: confident, in-scope damage on the same physical part.
    supported, threshold = set(cfg.damage.supported_types), cfg.damage.min_observation_confidence
    summaries = index.resolved_summaries(part, side)
    observations = [o for s in summaries for o in index.members(s)]
    confident = [o for o in observations if o.damage_code in supported and o.damage_confidence >= threshold]
    if confident:
        refs += [EvidenceRef(kind="summary", ref_id=s.summary_id) for s in summaries] + _obs_refs(confident)
        photo = _check("passed", ["damage_supported"], "R8",
                       supporting_observation_ids=[o.observation_id for o in confident],
                       max_confidence=max(o.damage_confidence for o in confident))
    else:
        low = [o for o in observations if o.damage_code in supported]
        out_of_scope = [o for o in observations if o.damage_code not in supported]
        nearby = index.unsided_observations(part)
        codes = (["damage_evidence_uncertain"] if low or nearby else []) + (
            ["damage_type_out_of_scope"] if out_of_scope else [])
        if codes:
            refs += _obs_refs(low + out_of_scope + nearby)
            return stop("R7", "insufficient_evidence", codes, documentary=documentary, mark=mark_ok,
                        photo=_check("insufficient", codes, "R7", threshold=threshold,
                                     low_confidence_observation_ids=[o.observation_id for o in low],
                                     out_of_scope_observation_ids=[o.observation_id for o in out_of_scope],
                                     unresolved_identity_observation_ids=[o.observation_id for o in nearby]))
        if cfg.coverage.require_human_confirmation_for_negative and index.coverage_confirmation(slot) is None:
            return stop("R8", "insufficient_evidence", ["coverage_unresolved"], documentary=documentary, mark=mark_ok,
                        photo=_check("insufficient", ["coverage_unresolved"], "R8",
                                     coverage_confirmation_id=slot.coverage_confirmation_id,
                                     coverage_confirmation="not_supplied_or_not_covering"))
        return stop("R8", "unsupported", ["no_supported_damage_in_adequate_views"], documentary=documentary,
                    mark=mark_ok, photo=_check("failed", ["no_supported_damage_in_adequate_views"], "R8",
                                               covering_photo_ids=sorted(slot.covering_photo_ids),
                                               coverage_confirmation_id=slot.coverage_confirmation_id))

    # R9 to R12: the effective price against the pinned range, photo check retained.
    amount, amount_reason = price.effective_price, (
        "amount_unreadable" if price.reason == "amount_unreadable" else "amount_unresolved")
    if price.effective_price_source == "surveyor_entry":
        entered = marks.confirmed_price_change
        currency, basis = entered.confirmed_currency, entered.confirmed_cost_basis
    else:
        if price.effective_price_source == "printed" and "printed_line_amount" in uncertain:
            amount, amount_reason = None, "amount_unreadable"
        currency = None if "currency" in uncertain else item.currency
        basis = None if "cost_basis" in uncertain else item.cost_basis
    quantity = None if "quantity" in uncertain else item.quantity
    key = cost_key(part, item.operation, req.vehicle_class, currency)
    lookup = ctx.ranges.lookup(key) if key is not None else None
    cost = compare_amount(amount, lookup, quantity=quantity, currency=currency, cost_basis=basis,
                          entry_id=item.entry_id, policy_version=ctx.policy, cost_table_version=ctx.table,
                          operation=item.operation, vehicle_class=req.vehicle_class, amount_reason=amount_reason,
                          quantity_must_equal=cfg.cost.quantity_must_equal, money_places=cfg.cost.money_places,
                          score_places=cfg.cost.normalised_score_places)
    if lookup is not None and lookup.range_id and cost.reason_code not in ("amount_unresolved", "amount_unreadable"):
        refs.append(EvidenceRef(kind="cost_range", ref_id=lookup.range_id))
    overall = {"within_range": "ok", "outside_range": "cost_outlier"}.get(cost.result, "insufficient_evidence")
    return _Outcome(cost_rule_id(cost), overall, "active", documentary, mark_ok, photo, cost,
                    (cost.reason_code,), tuple(refs))


def _finding(item: LineItem, outcome: _Outcome, ctx: _Context, notes: Sequence[Any]) -> AssessmentFinding:
    codes, refs = list(outcome.reasons), list(outcome.evidence)
    if notes:  # A7: the informational damage note attached to the excluded row
        codes.append("addition_suppressed_confirmed_exclusion_same_part")
        refs += [ref for candidate in notes for ref in candidate.evidence_refs]
    compared = outcome.cost.result in ("within_range", "outside_range")
    req = ctx.request
    finding = AssessmentFinding(
        finding_id=deterministic_id("af", ctx.job_key, req.assessment_revision, item.entry_id),
        assessment_revision=req.assessment_revision, entry_id=item.entry_id, documentary_check=outcome.documentary,
        photographic_check=outcome.photo, cost_check=outcome.cost, mark_state_check=outcome.mark,
        overall_result=outcome.overall, row_state=outcome.row_state, reasons=[reason(c) for c in _unique(codes)],
        evidence_refs=_unique(refs), applied_range_id=outcome.cost.range_id if compared else None,
        applied_range_reason=None if compared else outcome.cost.reason_code, pinned_versions=ctx.pinned,
        created_at=req.created_at)
    return with_content_hash(finding)


def _check_inputs(req: ConsolidationRequest) -> None:
    """Reject inconsistent input to the DLQ rather than building an assessment from it."""
    machine = [*req.part_summaries, *req.coverage, *req.observations, *req.line_items, *req.pen_marks, *req.pages,
               *([req.declaration] if req.declaration else [])]
    human = [*req.identity_confirmations, *req.coverage_confirmations]
    reused = {r.source_input_revision for r in req.reuse_lineage}
    for record in machine + human:
        name = type(record).__name__
        if record.claim_id != req.claim_id:
            raise ContractError("claim_mismatch", f"{name} belongs to claim {record.claim_id}")
        if record.input_revision > req.input_revision:
            raise ContractError("mixed_input_revision", f"{name} is from a later input revision")
    for record in machine:  # human confirmations legitimately apply to later revisions
        if record.input_revision < req.input_revision and record.input_revision not in reused:
            raise ContractError("mixed_input_revision", f"{type(record).__name__} from input revision "
                                f"{record.input_revision} is not declared in reuse_lineage")
    entries = [i.entry_id for i in req.line_items]
    if len(entries) != len(set(entries)):
        raise ContractError("duplicate_entry", "entry_id values must be unique")
    for mark in req.pen_marks:
        if (mark.entry_id and mark.entry_id not in entries) or set(mark.candidate_entry_ids) - set(entries):
            raise ContractError("mark_entry_unknown", f"mark {mark.mark_id} references a row not supplied")
    if any(r.provenance.source_kind == "fixture" for r in machine + human) and req.provenance.source_kind != "fixture":
        raise ContractError("fixture_provenance_required", "an assessment built from fixtures is stamped fixture")


def _pinned(req: ConsolidationRequest, config: RuleConfig) -> dict[str, str]:
    pins = {"rules_config": config.rules_config_version, "cost_table": req.cost_table_version,
            "cost_basis": COST_BASIS, "cost_reference": "synthetic", "reason_catalogue": REASON_CATALOGUE_VERSION}
    for key, value in pins.items():
        if req.pinned_versions.get(key, value) != value:
            raise ContractError("pinned_version_conflict", f"pinned_versions[{key!r}] is not {value!r}")
    return dict(sorted({**req.pinned_versions, **pins}.items()))


def consolidate(request: ConsolidationRequest, *, config: RuleConfig, ranges: RangeLookup) -> ConsolidationResult:
    """Build one immutable assessment from the branch records and the pinned cost table.

    Raises ``ContractError`` (the consumer routes it to the DLQ) when the rule config or
    the table differs from the versions the request pins, or the records are inconsistent:
    another claim, a later or undeclared earlier input revision, unknown mark rows,
    missing summary members, or fixture inputs under non-fixture provenance.
    """
    if request.rules_config_version != config.rules_config_version:
        raise ContractError("rules_config_version_mismatch",
                            f"request pins {request.rules_config_version!r}, config is {config.rules_config_version!r}")
    if ranges.table_version != request.cost_table_version:
        raise ContractError("cost_table_version_mismatch",
                            f"request pins {request.cost_table_version!r}, table is {ranges.table_version!r}")
    _check_inputs(request)
    index = EvidenceIndex(part_summaries=request.part_summaries, coverage=request.coverage,
                          observations=request.observations, identity_confirmations=request.identity_confirmations,
                          coverage_confirmations=request.coverage_confirmations)
    ctx = _Context(request, config, ranges, index, page_states(request.pages), job_key_for(request),
                   _pinned(request, config))
    image_ok = request.image_branch_state == "complete"
    document_ok = request.document_branch_state == "complete"
    items = sorted(request.line_items, key=line_item_order)

    candidates = []
    if image_ok and document_ok:
        candidates = propose_additions(request.observations, items, request.pen_marks, request.declaration,
                                       config=config, index=index, assessment_revision=request.assessment_revision,
                                       job_key=ctx.job_key, page_states=ctx.pages,
                                       accepted_parts={(e.new_values["part_code"], e.new_values["side"])
                                                       for e in request.accepted_scope})
        missing = missing_repairs_check(request.declaration, items, candidates, config=config)
    else:
        state = request.image_branch_state if not image_ok else request.document_branch_state
        missing = _check("not_evaluated", [_BRANCH_CODE[state]], "R1", image_branch_state=request.image_branch_state,
                         document_branch_state=request.document_branch_state)
    notes: dict[str, list] = {}
    for candidate in candidates:
        if candidate.status == "suppressed":
            notes.setdefault(candidate.suppressed_by_entry_id, []).append(candidate)

    findings, outcome_rules = [], {}
    for item in items:
        outcome = _evaluate(item, ctx)
        outcome_rules[item.entry_id] = outcome.rule
        findings.append(_finding(item, outcome, ctx, notes.get(item.entry_id, [])))

    incomplete = []
    if request.image_branch_state == "failed":
        incomplete.append("image_branch_failed")
    elif request.image_branch_state == "skipped_no_photos":
        incomplete.append("image_branch_missing")
    if request.document_branch_state == "failed":
        incomplete.append("document_branch_failed")
    elif request.document_branch_state == "skipped_no_pages":
        incomplete.append("document_branch_missing")
    incomplete = [require_code(code) for code in incomplete]

    assessment = Assessment(
        claim_id=request.claim_id, input_revision=request.input_revision,
        assessment_revision=request.assessment_revision, review_revision=request.review_revision,
        state="incomplete" if incomplete else "ready", findings=findings, missing_repairs_check=missing,
        possible_additions=[c for c in candidates if c.status != "suppressed"],
        suppressed_additions=[c for c in candidates if c.status == "suppressed"], incomplete_reasons=incomplete,
        cost_table_version=request.cost_table_version, rules_config_version=config.rules_config_version,
        pinned_versions=ctx.pinned, superseded=request.superseded, provenance=request.provenance,
        created_at=request.created_at)
    return ConsolidationResult(assessment=assessment, job_key=ctx.job_key, outcome_rules=outcome_rules,
                               addition_rules={c.candidate_id: addition_rule_id(c) for c in candidates},
                               trigger=request.trigger, prior_assessment_revision=request.prior_assessment_revision,
                               reuse_lineage=tuple(request.reuse_lineage))


def bundle_versions(bundle: FixtureBundle) -> dict[str, str]:
    """A flat pinned-version map from a fixture bundle's records (taxonomies split by kind)."""
    versions: dict[str, str] = {}
    for record in bundle.records():
        for key, value in (getattr(record, "versions", None) or {}).items():
            if key == "taxonomy":
                key = "taxonomy_damage" if value.startswith("damage-") else "taxonomy_parts"
            versions.setdefault(key, value)
    return dict(sorted(versions.items()))


def request_from_bundle(bundle: FixtureBundle, *, assessment_revision: int, created_at: datetime,
                        pinned_versions: Mapping[str, str] | None = None, provenance: Provenance | None = None,
                        cost_table_version: str | None = None, rules_config_version: str | None = None,
                        review_revision: int | None = None,
                        trigger: Literal["branches_complete", "reassessment", "retry"] = "branches_complete",
                        reuse_lineage: Sequence[ReusedArtifact] = (), superseded: bool = False) -> ConsolidationRequest:
    """A ``ConsolidationRequest`` from a validated fixture bundle (provenance stays fixture).

    ``cost_table_version`` and ``rules_config_version`` default to ``pinned_versions``
    entries ``cost_table`` and ``rules_config``; ``pinned_versions`` defaults to the
    bundle's own record versions, which then requires both versions explicitly.
    """
    versions = dict(pinned_versions) if pinned_versions is not None else bundle_versions(bundle)
    table = cost_table_version or versions.get("cost_table")
    rules = rules_config_version or versions.get("rules_config")
    if not table or not rules:
        raise ValueError("cost_table_version and rules_config_version are required (directly or in pinned_versions)")
    stamp = provenance or Provenance(source_kind="fixture", runtime_profile="lean",
                                     producer_service="cmev-consolidator", source_dataset_id=f"fixture:{bundle.scenario}")
    image = bundle.image_branch_state == "complete"
    document = bundle.document_branch_state == "complete"
    return ConsolidationRequest(
        claim_id=bundle.claim.claim_id, input_revision=bundle.claim.input_revision,
        assessment_revision=assessment_revision, review_revision=review_revision, trigger=trigger,
        image_branch_state=bundle.image_branch_state, document_branch_state=bundle.document_branch_state,
        vehicle_class=bundle.vehicle_class, currency=bundle.currency,
        part_summaries=bundle.part_summaries if image else [], coverage=bundle.coverage if image else [],
        observations=bundle.observations if image else [],
        identity_confirmations=bundle.identity_confirmations, coverage_confirmations=bundle.coverage_confirmations,
        line_items=bundle.line_items if document else [], pen_marks=bundle.pen_marks if document else [],
        declaration=bundle.declaration if document else None, pages=bundle.pages if document else [],
        cost_table_version=table, rules_config_version=rules, pinned_versions=versions, provenance=stamp,
        created_at=created_at, reuse_lineage=list(reuse_lineage), superseded=superseded)


__all__ = ["ConsolidationRequest", "ConsolidationResult", "bundle_versions", "consolidate", "job_key_for",
           "request_from_bundle"]
