"""Reference-build eligibility: final approvals only, never the assessed claim's own, deduplicated."""
from __future__ import annotations

from datetime import date, datetime, timezone

from claim_cmev.contracts.common import Provenance
from claim_cmev.contracts.costs import ApprovalRecord
from claim_cmev.costs.reference.eligibility import (
    approval_eligibility, deduplicate_members, independent_support, member_record, synthetic_members,
)
from claim_cmev.costs.reference.splits import Exclusion

from m7_support import record

CLAIM_A, CLAIM_B = "01J8Z3K4M5N6P7Q8R9S0T1V2W3", "01J8Z3K4M5N6P7Q8R9S0T1V2W4"
CUTOFF = date(2026, 6, 30)


def approval(approval_id: str, **overrides) -> ApprovalRecord:
    fields = {"approval_id": approval_id, "source_record_id": f"src-{approval_id}", "claim_id": CLAIM_A,
              "reviewed_entry_ids": ["e-1"], "status": "approved", "approved_amount": "800.00", "currency": "SGD",
              "cost_basis": "single_part_pre_tax_no_discount_v1", "approver": "synthetic-seed",
              "approved_at": datetime(2026, 5, 1, tzinfo=timezone.utc), "synthetic": True,
              "provenance": Provenance(source_kind="synthetic", runtime_profile="lean", producer_service="test")}
    return ApprovalRecord(**(fields | overrides))


def reasons(decisions):
    return {d.approval_id: d.reason_code for d in decisions}


def test_only_final_non_superseded_synthetic_approvals_before_the_cutoff_are_eligible():
    fixture = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="test")
    real = Provenance(source_kind="real", runtime_profile="lean", producer_service="import")
    approvals = [
        approval("a-ok", claim_id=CLAIM_B, reviewed_entry_ids=["e-9"]),
        approval("a-rejected", status="rejected", reviewed_entry_ids=["e-2"]),
        approval("a-pending", status="pending", approved_amount=None, currency=None, cost_basis=None,
                 approved_at=None, reviewed_entry_ids=["e-3"]),
        approval("a-marked-superseded", status="superseded", reviewed_entry_ids=["e-4"]),
        approval("a-old", reviewed_entry_ids=["e-5"]),
        approval("a-new", reviewed_entry_ids=["e-5b"], supersedes_approval_id="a-old"),
        approval("a-late", reviewed_entry_ids=["e-6"], approved_at=datetime(2026, 7, 2, tzinfo=timezone.utc)),
        approval("a-usd", reviewed_entry_ids=["e-7"], currency="USD"),
        approval("a-basis", reviewed_entry_ids=["e-8"], cost_basis="single_part_with_tax_v1"),
        approval("a-fixture", reviewed_entry_ids=["e-10"], provenance=fixture),
        approval("a-real-documented", reviewed_entry_ids=["e-11"], synthetic=False, provenance=real),
        approval("a-real-marked-synthetic", reviewed_entry_ids=["e-12"], provenance=real),
    ]
    result = reasons(approval_eligibility(approvals, cutoff=CUTOFF))
    assert result == {
        "a-ok": "eligible", "a-rejected": "approval_rejected", "a-pending": "approval_pending",
        "a-marked-superseded": "approval_superseded", "a-old": "approval_superseded", "a-new": "eligible",
        "a-late": "after_cutoff", "a-usd": "currency_unsupported", "a-basis": "basis_mismatch",
        "a-fixture": "source_not_eligible", "a-real-documented": "eligible",
        "a-real-marked-synthetic": "source_not_documented",
    }


def test_a_claims_own_approval_is_never_used_in_its_own_assessment():
    decisions = approval_eligibility([approval("a-own"), approval("a-other", claim_id=CLAIM_B)], cutoff=CUTOFF,
                                     assessed_claim_id=CLAIM_A)
    assert reasons(decisions) == {"a-own": "own_claim_approval", "a-other": "eligible"}


def test_one_lineage_keeps_only_its_latest_available_approval():
    first = approval("a-1", approved_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
    second = approval("a-2", approved_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
    decisions = approval_eligibility([second, first, approval("a-1")], cutoff=CUTOFF)
    assert [(d.approval_id, d.reason_code) for d in decisions] == [
        ("a-2", "eligible"), ("a-1", "duplicate_lineage"), ("a-1", "duplicate_record")]


def test_members_deduplicate_by_source_and_support_counts_base_cases():
    records = [record(f"r{c}{q}", f"b{c}", "800.00") for c in range(3) for q in range(4)]
    assert independent_support(records) == {("front-bumper", "replace", "sedan_standard", "SGD"): 3}
    members, excluded = synthetic_members({"train": records + records[:2]},
                                          [Exclusion("x1", "b9", "after_cutoff", "late", "0" * 64)],
                                          cutoff_date=CUTOFF)
    assert len(members) == 12
    assert sorted(e["inclusion_reason"] for e in excluded) == ["after_cutoff", "duplicate_record", "duplicate_record"]
    for member in (*members, *excluded):
        contract = member_record(member | {"build_id": "build-1", "table_version": "t-1"})
        assert contract.included is (member in members)
    kept, dropped = deduplicate_members([members[0], dict(members[0], source_record_id="other")])
    assert len(kept) == 1 and dropped[0]["inclusion_reason"] == "duplicate_record"
