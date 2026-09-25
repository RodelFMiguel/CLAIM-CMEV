"""Pure print payload from one frozen assessment and its matching frozen review revision.

Module 09 "Print view and report", application platform section 9.2, data contracts
section 11 and UI specification section 6.13. The payload is built only from records
passed in; it never reads the live database, so a printed report is a rendering of a
stored revision and reprints identically later. Display text comes from M8's reason-code
catalogue or from the reason text M8 stored on the record; nothing is composed here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import importlib
from typing import Any

from ..contracts.assessment import Assessment, AssessmentFinding, CheckResult
from ..contracts.claims import ClaimInput
from ..contracts.common import COST_CURRENCY, SCHEMA_VERSION, ContractError, Reason
from ..contracts.costs import CostCheck
from .config import ReviewConfig, default_review_config
from .state import ReviewState, _Frozen

DISPLAY_LABELS = {
    "ok": "No discrepancy found",
    "unsupported": "No supporting damage detected in adequate views",
    "cost_outlier": "Cost outside reference range",
    "insufficient_evidence": "More information needed",
    "not_evaluated": "Excluded by surveyor",
}
"""Data contracts section 9.1; ``not_evaluated`` carries the exclusion chip, not a result."""

Catalogue = Mapping[str, Any] | Callable[[str], str | None]
_CATALOGUE_NAMES = ("DISPLAY_TEXT", "REASON_DISPLAY_TEXT", "REASON_CODES", "REASON_CATALOGUE", "CATALOGUE")


class PrintRefused(ContractError):
    """The requested pairing cannot be printed; ``http_status`` is 409 or 412."""

    def __init__(self, reason_code: str, message: str, http_status: int):
        super().__init__(reason_code, message)
        self.http_status = http_status


# --- payload shapes ---------------------------------------------------------------------

class ReasonLine(_Frozen):
    code: str
    text: str
    text_source: str
    """``catalogue`` (M8 display table), ``record`` (text M8 stored) or ``code`` (no text known)."""


class CheckView(_Frozen):
    result: str
    reasons: tuple[ReasonLine, ...]


class CostCheckView(_Frozen):
    result: str
    reason: ReasonLine
    amount: str | None
    lower_amount: str | None
    upper_amount: str | None
    direction: str | None
    absolute_deviation: str | None
    range_id: str | None
    independent_base_case_count: int | None
    cost_table_version: str
    synthetic: bool = True


class DismissalView(_Frozen):
    action_id: str
    reason_code: str | None
    note: str | None
    actor: str
    recorded_at: datetime
    review_revision: int


class LineItemRow(_Frozen):
    entry_id: str
    finding_id: str | None
    page_number: int | None
    original_part_text: str | None
    original_operation_text: str | None
    part_code: str | None
    side: str | None
    operation: str | None
    quantity: str | None
    printed_line_amount: str | None
    original_printed_line_amount: str | None = None
    printed_amount_corrected: bool = False
    original_amount_text: str | None = None
    effective_price: str | None
    effective_price_source: str | None
    effective_price_reason: str | None
    currency: str | None
    row_state: str | None
    overall_result: str | None
    result_label: str
    more_information_needed: bool
    uncertain_fields: tuple[str, ...]
    documentary_check: CheckView | None
    mark_state_check: CheckView | None
    photographic_check: CheckView | None
    cost_check: CostCheckView | None
    reasons: tuple[ReasonLine, ...]
    dismissal: DismissalView | None


class MarkDecisionRow(_Frozen):
    mark_id: str
    page_id: str
    entry_id: str | None
    mark_type: str
    state: str
    origin: str
    detection_confidence: float | None
    confirmed_amount: str | None
    confirmed_currency: str | None
    decided_by: str | None
    decided_at: datetime | None
    link_reason: str


class DamageSummaryRow(_Frozen):
    summary_id: str
    identity_status: str
    part_code: str | None
    side: str
    damage_codes: tuple[str, ...]
    views: int
    coverage_state: str | None
    coverage_reasons: tuple[str, ...]
    confirmed_by_surveyor: bool


class AdditionRow(_Frozen):
    candidate_id: str
    part_code: str | None
    side: str
    status: str
    review_state: str
    reason: ReasonLine
    dismissal: DismissalView | None = None


class AcceptedAdditionRow(_Frozen):
    candidate_id: str
    action_id: str
    part_code: str
    side: str
    operation: str
    quantity: str
    amount: str | None
    amount_absent_reason: str | None = None
    currency: str
    cost_basis: str
    actor: str
    recorded_at: datetime


class DeclarationView(_Frozen):
    state: str | None
    source: str | None
    reasons: tuple[str, ...]
    unparsed_region_count: int | None
    missing_repairs_check: CheckView


class NoteView(_Frozen):
    action_id: str
    note: str
    actor: str
    recorded_at: datetime
    finding_id: str | None = None
    entry_id: str | None = None


class ReportHeader(_Frozen):
    claim_id: str
    external_reference: str | None
    make: str
    model: str
    year: int | None
    vehicle_class: str
    currency: str
    reviewed_by: str
    finalized_at: datetime
    input_revision: int
    assessment_revision: int
    review_revision: int


class CostReference(_Frozen):
    synthetic: bool = True
    statement: str
    basis_statement: str
    cost_basis: str
    cost_table_version: str
    currency: str
    currency_supported: bool


class FinalApproval(_Frozen):
    status: str = "not_recorded"
    text: str


class FixtureNotice(_Frozen):
    is_fixture: bool
    notice: str | None


class Footer(_Frozen):
    claim_id: str
    input_revision: int
    assessment_revision: int
    review_revision: int
    review_frozen: bool = True
    pinned_versions: tuple[tuple[str, str], ...]
    rules_config_version: str
    cost_table_version: str
    cost_basis: str
    synthetic_cost_statement: str
    fixture_marker: str | None
    schema_version: str
    printed_at: datetime | None
    printed_by: str | None


class PrintPayload(_Frozen):
    header: ReportHeader
    damage_summary: tuple[DamageSummaryRow, ...]
    unresolved_observation_count: int
    line_items: tuple[LineItemRow, ...]
    excluded_entry_ids: tuple[str, ...]
    mark_decisions: tuple[MarkDecisionRow, ...]
    accepted_additions: tuple[AcceptedAdditionRow, ...]
    findings: tuple[LineItemRow, ...]
    """Every checked row that is not ``ok``, with its reasons and any dismissal."""
    more_information_needed: tuple[LineItemRow, ...]
    """Outstanding "More information needed" rows; printed with reasons, never as passes."""
    open_additions: tuple[AdditionRow, ...]
    withheld_additions: tuple[AdditionRow, ...]
    dismissed_additions: tuple[AdditionRow, ...]
    declaration: DeclarationView
    notes: tuple[NoteView, ...]
    cost_reference: CostReference
    final_approval: FinalApproval
    fixture: FixtureNotice
    no_judgement_statement: str
    footer: Footer


# --- catalogue --------------------------------------------------------------------------

def _entry_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return value.get("display_text") or value.get("display")
    return getattr(value, "display_text", None) or getattr(value, "display", None)


def default_reason_catalogue() -> Catalogue | None:
    """M8's reason-code display catalogue when ``claim_cmev.comparison.reason_codes`` provides one."""
    try:
        module = importlib.import_module("claim_cmev.comparison.reason_codes")
    except ImportError:
        return None
    for name in _CATALOGUE_NAMES:
        value = getattr(module, name, None)
        if isinstance(value, Mapping):
            return value
    for name in ("display_text", "display_text_for"):
        func = getattr(module, name, None)
        if callable(func):
            return func
    return None


class _Texts:
    def __init__(self, catalogue: Catalogue | None):
        self.catalogue = catalogue

    def lookup(self, code: str) -> str | None:
        if self.catalogue is None:
            return None
        if callable(self.catalogue) and not isinstance(self.catalogue, Mapping):
            try:
                return _entry_text(self.catalogue(code))
            except (KeyError, LookupError, ValueError):
                return None
        return _entry_text(self.catalogue.get(code))

    def line(self, code: str, record_text: str | None = None) -> ReasonLine:
        text = self.lookup(code)
        if text:
            return ReasonLine(code=code, text=text, text_source="catalogue")
        if record_text:
            return ReasonLine(code=code, text=record_text, text_source="record")
        return ReasonLine(code=code, text=code, text_source="code")

    def reasons(self, reasons: list[Reason]) -> tuple[ReasonLine, ...]:
        return tuple(self.line(r.code, r.message) for r in reasons)

    def check(self, check: CheckResult) -> CheckView:
        return CheckView(result=check.result, reasons=self.reasons(check.reasons))

    def cost(self, cost: CostCheck) -> CostCheckView:
        return CostCheckView(result=cost.result, reason=self.line(cost.reason_code or cost.result), amount=cost.amount,
                             lower_amount=cost.lower_amount, upper_amount=cost.upper_amount, direction=cost.direction,
                             absolute_deviation=cost.absolute_deviation, range_id=cost.range_id,
                             independent_base_case_count=cost.independent_base_case_count,
                             cost_table_version=cost.cost_table_version)


# --- builder ----------------------------------------------------------------------------

def _refuse_mismatch(claim: ClaimInput, assessment: Assessment, review: ReviewState) -> None:
    f = review.finalization
    if f is None:
        raise PrintRefused("not_finalized", "Finalize this review before printing.", 412)
    if review.claim_id != assessment.claim_id or review.assessment_revision != assessment.assessment_revision:
        raise PrintRefused("review_revision_mismatch",
                           "The frozen review belongs to another assessment revision.", 409)
    if f.assessment_revision != assessment.assessment_revision or f.review_revision != review.review_revision:
        raise PrintRefused("review_revision_mismatch",
                           "The finalization does not freeze this assessment and review revision.", 409)
    if f.input_revision != assessment.input_revision:
        raise PrintRefused("review_revision_mismatch", "The finalization names another input revision.", 409)
    if review.assessment != assessment:
        raise PrintRefused("assessment_mismatch", "The review was made against different assessment content.", 409)
    if claim.claim_id != assessment.claim_id or claim.input_revision != assessment.input_revision:
        raise PrintRefused("claim_snapshot_mismatch",
                           "The claim snapshot is not the input revision this assessment was built from.", 409)
    if assessment.superseded:
        raise PrintRefused("assessment_superseded", "A superseded assessment is never printed.", 409)
    from .finalize import assessment_processing_finished
    if not assessment_processing_finished(assessment):
        raise PrintRefused("assessment_incomplete", "An incomplete assessment is never printed as finished.", 409)


def _dismissal(event: Any) -> DismissalView:
    return DismissalView(action_id=event.action_id, reason_code=event.reason_code, note=event.note, actor=event.actor,
                         recorded_at=event.recorded_at, review_revision=event.resulting_review_revision)


def _row(texts: _Texts, entry_id: str, item: Any, finding: AssessmentFinding | None,
         dismissal: Any) -> LineItemRow:
    overall = finding.overall_result if finding else None
    label = DISPLAY_LABELS.get(overall, "No finding recorded")
    reasons = texts.reasons(finding.reasons) if finding else (texts.line("finding_missing"),)
    return LineItemRow(
        entry_id=entry_id, finding_id=finding.finding_id if finding else None,
        page_number=item.page_number if item else None,
        original_part_text=item.original_part_text if item else None,
        original_operation_text=item.original_operation_text if item else None,
        part_code=item.part_code if item else None, side=item.side if item else None,
        operation=item.operation if item else None, quantity=item.quantity if item else None,
        printed_line_amount=item.printed_line_amount if item else None,
        original_printed_line_amount=item.original_printed_line_amount if item else None,
        printed_amount_corrected=item.printed_amount_corrected if item else False,
        original_amount_text=item.original_amount_text if item else None,
        effective_price=item.effective_price if item else None,
        effective_price_source=item.effective_price_source if item else None,
        effective_price_reason=item.effective_price_reason if item else None,
        currency=item.currency if item else None,
        row_state=finding.row_state if finding else None, overall_result=overall, result_label=label,
        more_information_needed=overall == "insufficient_evidence",
        uncertain_fields=tuple(u.field for u in item.field_uncertainty) if item else (),
        documentary_check=texts.check(finding.documentary_check) if finding else None,
        mark_state_check=texts.check(finding.mark_state_check) if finding else None,
        photographic_check=texts.check(finding.photographic_check) if finding else None,
        cost_check=texts.cost(finding.cost_check) if finding else None,
        reasons=reasons, dismissal=_dismissal(dismissal) if dismissal else None)


def _fixture(claim: ClaimInput, review: ReviewState) -> bool:
    records = [claim, review.assessment, *review.line_items, *review.marks, *review.part_summaries,
               *review.coverage]
    if review.completeness is not None:
        records.append(review.completeness)
    return any(r.provenance.source_kind == "fixture" for r in records)


def build_print_payload(
    claim_snapshot: ClaimInput,
    assessment: Assessment,
    review: ReviewState,
    *,
    reason_catalogue: Catalogue | None = None,
    printed_at: datetime | None = None,
    printed_by: str | None = None,
    config: ReviewConfig | None = None,
) -> PrintPayload:
    """The print payload of one frozen review of one assessment (pure).

    Raises ``PrintRefused`` (409 or 412) unless ``review`` is finalized against exactly
    ``assessment`` at its current review revision and ``claim_snapshot`` is the input
    revision the assessment was built from. ``reason_catalogue`` maps a reason code to
    its display text (a mapping, or a callable); when omitted, M8's catalogue is used if
    ``claim_cmev.comparison.reason_codes`` provides one, else the text stored on the record.
    """
    config = config or default_review_config()
    _refuse_mismatch(claim_snapshot, assessment, review)
    texts = _Texts(reason_catalogue if reason_catalogue is not None else default_reason_catalogue())
    f = review.finalization
    dismissals = review.dismissals()
    findings_by_entry = {x.entry_id: x for x in assessment.findings}
    entries = {i.entry_id: i for i in review.line_items}
    order = [i.entry_id for i in review.line_items] + [x.entry_id for x in assessment.findings
                                                        if x.entry_id not in entries]
    rows = []
    for entry_id in order:
        finding = findings_by_entry.get(entry_id)
        rows.append(_row(texts, entry_id, entries.get(entry_id), finding,
                         dismissals.get(finding.finding_id) if finding else None))
    rows = tuple(rows)

    coverage = {(c.part_code, c.side): c for c in review.coverage}
    damage = []
    for s in review.part_summaries:
        if s.identity_status == "unresolved":
            continue
        slot = coverage.get((s.part_code, s.side))
        damage.append(DamageSummaryRow(
            summary_id=s.summary_id, identity_status=s.identity_status, part_code=s.part_code, side=s.side,
            damage_codes=tuple(s.damage_codes), views=len(s.supporting_photo_ids),
            coverage_state=slot.state if slot else None, coverage_reasons=tuple(slot.reasons) if slot else (),
            confirmed_by_surveyor=bool(s.identity_confirmation_ids or (slot and slot.coverage_confirmation_id))))

    decided = review.addition_decisions()

    def addition(c: Any) -> AdditionRow:
        event = decided.get(c.candidate_id)
        dismissed = event if event is not None and event.action_type == "dismiss_addition" else None
        return AdditionRow(candidate_id=c.candidate_id, part_code=c.part_code, side=c.side, status=c.status,
                           review_state=c.review_state, reason=texts.line(c.reason.code, c.reason.message),
                           dismissal=_dismissal(dismissed) if dismissed else None)

    additions = assessment.possible_additions
    completeness = review.completeness
    currency = claim_snapshot.currency
    fixture = _fixture(claim_snapshot, review)
    text = config.print
    return PrintPayload(
        header=ReportHeader(
            claim_id=assessment.claim_id, external_reference=claim_snapshot.external_reference,
            make=claim_snapshot.make, model=claim_snapshot.model, year=claim_snapshot.year,
            vehicle_class=claim_snapshot.vehicle_class, currency=currency, reviewed_by=f.actor,
            finalized_at=f.finalized_at, input_revision=assessment.input_revision,
            assessment_revision=assessment.assessment_revision, review_revision=f.review_revision),
        damage_summary=tuple(damage),
        unresolved_observation_count=sum(s.observation_count for s in review.part_summaries
                                         if s.identity_status == "unresolved"),
        line_items=rows,
        excluded_entry_ids=tuple(r.entry_id for r in rows if r.row_state == "excluded"),
        mark_decisions=tuple(MarkDecisionRow(
            mark_id=m.mark_id, page_id=m.page_id, entry_id=m.entry_id, mark_type=m.mark_type, state=m.state,
            origin=m.origin, detection_confidence=m.detection_confidence, confirmed_amount=m.confirmed_amount,
            confirmed_currency=m.confirmed_currency, decided_by=m.decided_by, decided_at=m.decided_at,
            link_reason=m.link_reason) for m in review.marks),
        accepted_additions=tuple(AcceptedAdditionRow(
            candidate_id=e.candidate_id, action_id=e.action_id, part_code=e.new_values["part_code"],
            side=e.new_values["side"], operation=e.new_values["operation"], quantity=e.new_values["quantity"],
            amount=e.new_values.get("amount"), amount_absent_reason=e.new_values.get("amount_absent_reason"),
            currency=e.new_values.get("currency", currency), cost_basis=e.new_values.get("cost_basis", review.cost_basis),
            actor=e.actor, recorded_at=e.recorded_at)
            for e in (*review.accepted_scope, *review.events) if e.action_type == "accept_addition"),
        findings=tuple(r for r in rows if r.row_state == "active" and r.overall_result != "ok"),
        more_information_needed=tuple(r for r in rows if r.more_information_needed),
        open_additions=tuple(addition(c) for c in additions
                             if c.status == "proposed" and c.review_state == "open" and c.candidate_id not in decided),
        withheld_additions=tuple(addition(c) for c in additions if c.status == "withheld"),
        dismissed_additions=tuple(addition(c) for c in additions
                                  if c.review_state == "dismissed"
                                  or (c.candidate_id in decided
                                      and decided[c.candidate_id].action_type == "dismiss_addition")),
        declaration=DeclarationView(
            state=completeness.state if completeness else None, source=completeness.source if completeness else None,
            reasons=tuple(completeness.reasons) if completeness else ("declaration_not_recorded",),
            unparsed_region_count=completeness.unparsed_region_count if completeness else None,
            missing_repairs_check=texts.check(assessment.missing_repairs_check)),
        notes=tuple(NoteView(action_id=e.action_id, note=e.note or "", actor=e.actor, recorded_at=e.recorded_at,
                             finding_id=e.finding_id, entry_id=e.entry_id) for e in review.notes()),
        cost_reference=CostReference(
            statement=text.synthetic_cost_statement, basis_statement=text.cost_basis_statement,
            cost_basis=claim_snapshot.cost_basis, cost_table_version=assessment.cost_table_version,
            currency=currency, currency_supported=currency == COST_CURRENCY),
        final_approval=FinalApproval(text=text.final_approval_not_recorded),
        fixture=FixtureNotice(is_fixture=fixture, notice=text.fixture_notice if fixture else None),
        no_judgement_statement=text.no_judgement_statement,
        footer=Footer(
            claim_id=assessment.claim_id, input_revision=assessment.input_revision,
            assessment_revision=assessment.assessment_revision, review_revision=f.review_revision,
            pinned_versions=tuple(sorted(assessment.pinned_versions.items())),
            rules_config_version=assessment.rules_config_version, cost_table_version=assessment.cost_table_version,
            cost_basis=claim_snapshot.cost_basis, synthetic_cost_statement=text.synthetic_cost_statement,
            fixture_marker=text.fixture_notice if fixture else None, schema_version=SCHEMA_VERSION,
            printed_at=printed_at, printed_by=printed_by))
