"""Possible additions: the reverse direction, rules A1 to A8 (module 08 "Possible additions").

One candidate per M3 part summary, evaluated once per assessment after the per-item rules.
The first rule that applies decides the candidate. A proposed addition carries its
observations and evidence but never an operation or an amount. A confirmed exclusion
suppresses only an addition for the same physical part **and side**; an unresolved side
never satisfies that comparison.

Decision recorded for review: integration contracts 4.7 item 2 ("no pen mark is pending
or unlinked anywhere in the document") is stricter than module 08 A5 ("... that could be
this part"). The stricter reading is implemented for marks: any non-rejected unlinked
mark withholds every otherwise eligible candidate (``addition_withheld_unlinked_mark``),
and so does any pending mark of either type (``addition_withheld_pending_exclusion``),
because a pending mark's class is still a detector proposal.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from claim_cmev.contracts.assessment import CheckResult, EvidenceRef, ProposedRepairAddition
from claim_cmev.contracts.common import RESOLVED_SIDES, ContractError, deterministic_id
from claim_cmev.contracts.documents import DeclarationCompleteness, LineItem, PenMark
from claim_cmev.contracts.imaging import ImageDamageObservation, PartSummary

from .config import RuleConfig
from .inputs import EvidenceIndex, row_identity, row_marks
from .reason_codes import reason

ADDITION_RULES = {
    "addition_withheld_identity_unresolved": "A2", "addition_withheld_evidence_uncertain": "A3",
    "addition_withheld_coverage": "A4", "addition_withheld_ambiguous_row": "A5",
    "addition_withheld_unlinked_mark": "A5", "addition_withheld_pending_exclusion": "A5",
    "addition_suppressed_confirmed_exclusion_same_part": "A7", "addition_proposed": "A8",
}
_WITHHELD_ORDER = ("addition_withheld_identity_unresolved", "addition_withheld_evidence_uncertain",
                   "addition_withheld_coverage", "addition_withheld_ambiguous_row",
                   "addition_withheld_unlinked_mark", "addition_withheld_pending_exclusion")


def declaration_gate(completeness: DeclarationCompleteness | None, line_items: Sequence[LineItem], *,
                     config: RuleConfig) -> str | None:
    """A1: ``declaration_incomplete`` unless the declaration is sufficiently complete.

    ``explicitly_empty`` counts only with a recorded human confirmation, and a parser state
    of ``complete`` with zero rows is treated as unreadable, never as an empty scope.
    """
    if completeness is None or completeness.state not in config.additions.require_completeness:
        return "declaration_incomplete"
    if completeness.state == "explicitly_empty":
        confirmed = (completeness.source == "human_confirmation" and completeness.confirmed_by
                     and completeness.confirmed_at and completeness.review_revision)
        return None if confirmed else "declaration_incomplete"
    if not line_items:
        return "declaration_incomplete"
    return None


def _refs(observations: Sequence[ImageDamageObservation], summary: PartSummary) -> list[EvidenceRef]:
    refs = [EvidenceRef(kind="summary", ref_id=summary.summary_id)]
    for obs in observations:
        refs.append(EvidenceRef(kind="observation", ref_id=obs.observation_id, box_norm=obs.bbox_norm,
                                artifact_id=obs.damage_mask_ref.artifact_id))
    refs += [EvidenceRef(kind="photo", ref_id=p) for p in sorted({o.photo_id for o in observations})]
    return refs


def propose_additions(observations: Sequence[ImageDamageObservation], line_items: Sequence[LineItem],
                      pen_marks: Sequence[PenMark], completeness: DeclarationCompleteness | None, *,
                      config: RuleConfig, index: EvidenceIndex, assessment_revision: int, job_key: str,
                      page_states: Mapping[str, str] | None = None) -> list[ProposedRepairAddition]:
    """Rules A1 to A8 over every part summary; proposed, withheld and suppressed candidates.

    Returns an empty list when A1 fires (the whole check is ``not_evaluated``); see
    ``missing_repairs_check``. ``observations`` must be the observations ``index`` holds.
    """
    if {o.observation_id for o in observations} != set(index.observations):
        raise ContractError("observation_set_mismatch", "the evidence index holds a different observation set")
    if declaration_gate(completeness, line_items, config=config):
        return []
    states = page_states or {}
    rows = [(item, row_identity(item, states.get(item.page_id)), row_marks(item.entry_id, pen_marks))
            for item in line_items]
    unlinked_anywhere = any(m.state != "rejected" and m.entry_id is None for m in pen_marks)
    pending_anywhere = any(m.state == "pending" for m in pen_marks)
    supported = set(config.damage.supported_types)
    threshold = config.additions.min_observation_confidence
    candidates = []
    for summary in index.summaries:
        members = index.members(summary)
        part, side = summary.part_code, summary.side
        confident = [o for o in members if o.damage_code in supported and o.damage_confidence >= threshold]
        code, shown, suppressed_by = None, members, None
        if part is None or side not in RESOLVED_SIDES or not index.summary_resolved(summary):
            code = "addition_withheld_identity_unresolved"                                   # A2
        elif not confident:
            code = "addition_withheld_evidence_uncertain"                                    # A3
        elif (slot := index.slot(part, side)) is None or slot.state != "adequate":
            code = "addition_withheld_coverage"                                              # A4
        elif any(identity.could_be(part, side) for _, identity, _ in rows):
            code = "addition_withheld_ambiguous_row"                                         # A5
        elif unlinked_anywhere:
            code = "addition_withheld_unlinked_mark"                                         # A5
        elif pending_anywhere:
            code = "addition_withheld_pending_exclusion"                                     # A5
        elif any(identity.matches(part, side) and not marks.excluded for _, identity, marks in rows):
            continue                                                                         # A6: matched
        else:
            shown = confident
            excluded = [item for item, identity, marks in rows if marks.excluded and identity.matches(part, side)]
            if excluded:                                                                     # A7
                code, suppressed_by = "addition_suppressed_confirmed_exclusion_same_part", excluded[0].entry_id
            else:                                                                            # A8
                code = "addition_proposed"
        status = ("suppressed" if suppressed_by else "proposed" if code == "addition_proposed" else "withheld")
        candidates.append(ProposedRepairAddition(
            candidate_id=deterministic_id("pa", job_key, assessment_revision, summary.summary_id),
            assessment_revision=assessment_revision, summary_ids=[summary.summary_id],
            observation_ids=[o.observation_id for o in shown], part_code=part, side=side, status=status,
            reason=reason(code), suppressed_by_entry_id=suppressed_by, evidence_refs=_refs(shown, summary)))
    return candidates


def missing_repairs_check(completeness: DeclarationCompleteness | None, line_items: Sequence[LineItem],
                          candidates: Sequence[ProposedRepairAddition], *, config: RuleConfig) -> CheckResult:
    """The assessment-level missing-repairs check. Proposed additions make it ``failed``
    (a possible omission, reported separately from discrepancy flags); withheld ones make
    it ``insufficient``; otherwise ``passed``. A1 makes it ``not_evaluated``."""
    gate = declaration_gate(completeness, line_items, config=config)
    if gate:
        state = completeness.state if completeness else None
        return CheckResult(result="not_evaluated", reasons=[reason(gate)], rule_id="A1",
                           detail={"completeness_state": state, "line_item_count": len(line_items)})
    counts = {s: sum(1 for c in candidates if c.status == s) for s in ("proposed", "withheld", "suppressed")}
    if counts["proposed"]:
        result, codes = "failed", ["addition_proposed"]
    elif counts["withheld"]:
        present = {c.reason.code for c in candidates if c.status == "withheld"}
        result, codes = "insufficient", [c for c in _WITHHELD_ORDER if c in present]
    else:
        result, codes = "passed", ["no_additions_found"]
    return CheckResult(result=result, reasons=[reason(c) for c in codes], rule_id="A2-A8", detail=counts)


def addition_rule_id(candidate: ProposedRepairAddition) -> str:
    return ADDITION_RULES[candidate.reason.code]


__all__ = ["ADDITION_RULES", "addition_rule_id", "declaration_gate", "missing_repairs_check", "propose_additions"]
