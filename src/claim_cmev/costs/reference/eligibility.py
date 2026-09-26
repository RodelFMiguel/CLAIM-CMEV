"""Reference-build eligibility and membership (data contracts section 8.4).

``approval_eligibility`` is the pure filter for ``ApprovalRecord`` inputs. Approval import
itself is stretch S3 and out of scope; in the core build the approval list is empty and
the members are documented synthetic seed records. Deduplication keeps one member per
source record and one approval per reviewed-entry lineage; independent support counts
base cases only. Member rows validate as the shared ``ReferenceBuildMember`` record.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from claim_cmev.contracts.common import SCHEMA_VERSION
from claim_cmev.contracts.costs import CostKey, ReferenceBuildMember

from .records import PriceRecord
from .splits import Exclusion
from .vocabulary import BASIS_MISMATCH, COST_BASIS, CURRENCY, CURRENCY_UNSUPPORTED, CostKeyTuple

MEMBER_FIELDS = ("schema_version", "build_id", "table_version", "source_record_id", "source_kind", "source_hash",
                 "included", "inclusion_reason", "cutoff_date", "split", "base_case_id", "weight",
                 "part_code", "operation", "vehicle_class", "currency")
APPROVAL_EXCLUSION_REASONS = frozenset({
    "identifier_missing", "duplicate_record", "approval_rejected", "approval_pending", "approval_superseded",
    "approval_not_final", "source_not_eligible", "source_not_documented", "own_claim_approval", "amount_invalid",
    CURRENCY_UNSUPPORTED, BASIS_MISMATCH, "after_cutoff", "duplicate_lineage",
})


def _get(obj: Any, *names: str) -> Any:
    """First present attribute or key among ``names``."""
    for name in names:
        value = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.max, tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return _moment(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class EligibilityDecision:
    approval_id: str | None
    eligible: bool
    reason_code: str
    lineage_key: str | None
    detail: str = ""


def _lineage(approval: Any) -> str | None:
    claim = _get(approval, "claim_id")
    entries = _get(approval, "reviewed_entry_ids", "entry_id")
    if isinstance(entries, str):
        entries = [entries]
    return None if claim is None or not entries else f"{claim}|{','.join(sorted(entries))}"


def _source_problem(approval: Any) -> tuple[str, str] | None:
    kind = _get(_get(approval, "provenance") or {}, "source_kind")
    if kind in ("fixture", "explainer"):
        return "source_not_eligible", f"{kind} records are never cost evidence"
    if _get(approval, "synthetic") is True:
        if kind in (None, "synthetic"):
            return None
        return "source_not_documented", "the synthetic marker contradicts the provenance"
    if kind == "real" and _get(approval, "source_record_id"):
        return None
    return "source_not_documented", "only synthetic or documented-source final approvals are eligible"


def _approval_reason(approval: Any, *, superseded_ids: set, seen: set, assessed_claim_id: str | None,
                     cutoff: datetime, currency: str, cost_basis: str) -> tuple[str, str] | None:
    approval_id, status = _get(approval, "approval_id"), _get(approval, "status")
    if not approval_id or _lineage(approval) is None:
        return "identifier_missing", "approval_id, claim_id and the reviewed entry lineage are required"
    if approval_id in seen:
        return "duplicate_record", "approval_id already considered"
    if status == "rejected":
        return "approval_rejected", "rejected approvals never train a reference"
    if status == "pending":
        return "approval_pending", "only final approvals are eligible"
    if status == "superseded" or approval_id in superseded_ids:
        return "approval_superseded", "a later approval supersedes this one"
    if status != "approved":
        return "approval_not_final", f"status {status!r} is not a final approval"
    source = _source_problem(approval)
    if source:
        return source
    if assessed_claim_id is not None and _get(approval, "claim_id") == assessed_claim_id:
        return "own_claim_approval", "a claim's own final approval is never used in its own assessment"
    amount = _get(approval, "approved_amount", "final_approved_amount")
    try:
        valid = isinstance(amount, (str, Decimal)) and Decimal(amount).is_finite() and Decimal(amount) > 0
    except InvalidOperation:
        valid = False
    if not valid:
        return "amount_invalid", "the final approved amount must be a positive exact decimal"
    if _get(approval, "currency") != currency:
        return CURRENCY_UNSUPPORTED, "no currency conversion"
    if _get(approval, "cost_basis") != cost_basis:
        return BASIS_MISMATCH, "bases never pool"
    available = _moment(_get(approval, "effective_at", "approved_at"))
    if available is None or available > cutoff:
        return "after_cutoff", "the approval was not available at the build cutoff"
    return None


def approval_eligibility(approvals: Iterable[Any], *, cutoff: datetime | date, assessed_claim_id: str | None = None,
                         currency: str = CURRENCY, cost_basis: str = COST_BASIS) -> list[EligibilityDecision]:
    """Pure filter: only final, non-superseded, synthetic-or-documented approvals before the cutoff.

    Rejected, pending and superseded records (including any named by a later record's
    ``supersedes_approval_id``) are excluded with a reason, as are fixture sources and the
    assessed claim's own approval. Several eligible approvals of one reviewed-entry lineage
    keep only the latest available one; the rest are ``duplicate_lineage``.
    """
    records = list(approvals)
    limit = _moment(cutoff)
    superseded_ids = {_get(a, "supersedes_approval_id") for a in records} - {None}
    decisions, seen = [], set()
    for approval in records:
        problem = _approval_reason(approval, superseded_ids=superseded_ids, seen=seen,
                                   assessed_claim_id=assessed_claim_id, cutoff=limit, currency=currency,
                                   cost_basis=cost_basis)
        approval_id = _get(approval, "approval_id")
        if approval_id:
            seen.add(approval_id)
        decisions.append(EligibilityDecision(approval_id, problem is None, problem[0] if problem else "eligible",
                                             _lineage(approval), problem[1] if problem else ""))
    by_lineage: dict[str, list[int]] = defaultdict(list)
    for index, decision in enumerate(decisions):
        if decision.eligible:
            by_lineage[decision.lineage_key].append(index)
    for indexes in by_lineage.values():
        ordered = sorted(indexes, key=lambda i: (_moment(_get(records[i], "effective_at", "approved_at")),
                                                 decisions[i].approval_id))
        for i in ordered[:-1]:
            d = decisions[i]
            decisions[i] = EligibilityDecision(d.approval_id, False, "duplicate_lineage", d.lineage_key,
                                               f"later approval used: {decisions[ordered[-1]].approval_id}")
    return decisions


def independent_support(records: Iterable[PriceRecord]) -> dict[CostKeyTuple, int]:
    """Distinct base cases per key; repeated quotes of one base case count once."""
    cases: dict[CostKeyTuple, set] = defaultdict(set)
    for record in records:
        cases[record.key].add(record.base_case_id)
    return {key: len(v) for key, v in cases.items()}


def _excluded(row: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return dict(row, included=False, inclusion_reason=reason, split=None, weight=0.0)


def deduplicate_members(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Keep one member per source record; a repeated source record or source hash is excluded."""
    kept, dropped, ids, hashes = [], [], set(), set()
    for row in rows:
        if row["source_record_id"] in ids or row["source_hash"] in hashes:
            dropped.append(_excluded(row, "duplicate_record"))
            continue
        ids.add(row["source_record_id"])
        hashes.add(row["source_hash"])
        kept.append(dict(row))
    return kept, dropped


def synthetic_members(partitions: Mapping[str, Sequence[PriceRecord]], exclusions: Sequence[Exclusion], *,
                      cutoff_date: date) -> tuple[list[dict], list[dict]]:
    """ReferenceBuildMember rows for documented synthetic seed records (build fields stamped later)."""
    common = {"schema_version": SCHEMA_VERSION, "build_id": "", "table_version": "", "source_kind": "synthetic_seed",
              "cutoff_date": cutoff_date.isoformat()}
    no_key = dict.fromkeys(("part_code", "operation", "vehicle_class", "currency"))
    included = [dict(common, source_record_id=r.record_id, source_hash=r.source_hash, included=True,
                     inclusion_reason="eligible_synthetic_seed", split=partition, base_case_id=r.base_case_id,
                     weight=1.0, part_code=r.part_code, operation=r.operation, vehicle_class=r.vehicle_class,
                     currency=r.currency)
                for partition, records in partitions.items() for r in records]
    excluded = [_excluded(dict(common, **no_key, source_record_id=e.record_id, source_hash=e.source_hash,
                               base_case_id=e.base_case_id), e.reason_code) for e in exclusions]
    kept, duplicates = deduplicate_members(sorted(included, key=lambda m: m["source_record_id"]))
    return kept, excluded + duplicates


def member_record(row: Mapping[str, Any]) -> ReferenceBuildMember:
    """Validate one membership row as the shared contract record."""
    key = None if row.get("part_code") is None else CostKey(
        part_code=row["part_code"], operation=row["operation"], vehicle_class=row["vehicle_class"],
        currency=row["currency"])
    fields = {k: row[k] for k in MEMBER_FIELDS if k not in ("part_code", "operation", "vehicle_class", "currency")}
    return ReferenceBuildMember.model_validate(fields | {"cost_key": key})
