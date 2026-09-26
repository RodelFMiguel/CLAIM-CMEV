"""Cost records: the four-field key, ranges, exact comparison arithmetic and base-case support."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from claim_cmev.contracts import (
    ApprovalRecord,
    ContractError,
    CostCheck,
    CostKey,
    ReferenceBuildMember,
    ReferenceCostRange,
    check_cost_key_fields,
    count_independent_base_cases,
)
from contract_factories import CLAIM, NOW, PROV, cost_check, cost_range, sha

KEY = {"part_code": "front-bumper", "operation": "replace", "vehicle_class": "sedan_standard", "currency": "SGD"}


def test_cost_key_is_exactly_four_fields():
    assert tuple(CostKey.model_fields) == ("part_code", "operation", "vehicle_class", "currency")
    assert CostKey(**KEY).currency == "SGD"


@pytest.mark.parametrize("extra", [{"side": "left"}, {"damage_type": "dent"}, {"model_year": 2019},
                                   {"make_model_year": "toyota-corolla-2019"}])
def test_v1_cost_key_fields_are_rejected(extra):
    with pytest.raises(ValidationError):
        CostKey(**KEY, **extra)


@pytest.mark.parametrize("field,value", [("operation", "other"), ("operation", "unknown"),
                                         ("vehicle_class", "unknown"), ("part_code", "wing-mirror")])
def test_cost_key_rejects_ineligible_vocabulary(field, value):
    with pytest.raises(ValidationError):
        CostKey(**{**KEY, field: value})


def test_v1_cost_table_manifest_is_not_loadable():
    check_cost_key_fields(["part_code", "operation", "vehicle_class", "currency"])
    with pytest.raises(ContractError) as err:
        check_cost_key_fields(["part_code", "side", "operation", "damage_type", "vehicle_class", "currency"])
    assert err.value.reason_code == "schema_unsupported"


def test_supported_and_withheld_ranges():
    supported = ReferenceCostRange(**cost_range())
    assert supported.key == CostKey(**KEY)
    assert ReferenceCostRange.model_validate_json(supported.model_dump_json()) == supported
    withheld = ReferenceCostRange(**cost_range(range_id="r2", support_status="withheld", lower_amount=None,
                                               upper_amount=None, withheld_reason="insufficient_support",
                                               independent_base_case_count=3, record_count=9))
    assert withheld.lower_amount is None and withheld.upper_amount is None
    zero_width = ReferenceCostRange(**cost_range(lower_amount="500.00", upper_amount="500.00"))
    assert zero_width.lower_amount == zero_width.upper_amount


@pytest.mark.parametrize("overrides", [
    {"lower_amount": "1020.01"},  # crossed bounds
    {"upper_amount": None},  # a supported range needs both bounds
    {"support_status": "withheld", "withheld_reason": "insufficient_support",
     "lower_amount": "0", "upper_amount": "0"},  # a missing range is null, never [0, 0]
    {"support_status": "withheld", "lower_amount": None, "upper_amount": None},  # withheld without a reason
    {"synthetic": False},
    {"cost_basis": "single_part_incl_tax_v1"},
    {"record_count": 10},  # support counted by quote would exceed records
    {"lower_amount": 620.0},  # money as a JSON number
    {"lower_amount": "-1.00"},
    {"nominal_coverage": "1.00"},
    {"side": "left"},
    {"method": "mean"},
])
def test_invalid_ranges(overrides):
    with pytest.raises(ValidationError):
        ReferenceCostRange(**cost_range(**overrides))


# --- CostCheck arithmetic (data contracts 8.3, M8 cases 25-29) ---------------------------------
@pytest.mark.parametrize("amount", ["620.00", "1020.00", "980.00"])
def test_equality_at_a_bound_is_within_range(amount):
    assert CostCheck(**cost_check(amount=amount)).result == "within_range"


def test_outside_range_direction_and_exact_deviation():
    below = CostCheck(**cost_check(result="outside_range", amount="619.99", direction="below",
                                   absolute_deviation="0.01", reason_code="amount_below_range"))
    above = CostCheck(**cost_check(result="outside_range", amount="1020.01", direction="above",
                                   absolute_deviation="0.01", reason_code="amount_above_range"))
    assert below.absolute_deviation == above.absolute_deviation == "0.01"


def test_zero_width_interval_has_no_normalised_score():
    ok = CostCheck(**cost_check(amount="500.00", lower_amount="500.00", upper_amount="500.00", normalised_score=None,
                                normalised_score_reason="zero_width_interval"))
    assert ok.normalised_score is None
    with pytest.raises(ValidationError):
        CostCheck(**cost_check(amount="500.00", lower_amount="500.00", upper_amount="500.00"))


@pytest.mark.parametrize("overrides", [
    {"amount": "619.99"},  # below L but stored within_range
    {"absolute_deviation": "1.00"},
    {"direction": "above"},
    {"result": "outside_range", "direction": "below", "absolute_deviation": "0.01"},  # a is inside
    {"result": "outside_range", "amount": "619.99", "direction": "above", "absolute_deviation": "0.01"},
    {"result": "outside_range", "amount": "619.99", "direction": "below", "absolute_deviation": "0.02"},
    {"result": "not_evaluated", "reason_code": None, "lower_amount": None, "upper_amount": None, "range_id": None,
     "direction": None, "absolute_deviation": None, "normalised_score": None},  # skipped without a reason
    {"result": "not_evaluated", "reason_code": "quantity_not_one"},  # a deviation for an incompatible amount
    {"range_id": None},
    {"amount": 980.0},
])
def test_invalid_cost_checks(overrides):
    with pytest.raises(ValidationError):
        CostCheck(**cost_check(**overrides))


def test_not_evaluated_and_insufficient_support_carry_reasons_and_no_deviation():
    skipped = CostCheck(entry_id="li1", result="not_evaluated", amount="980.00", reason_code="quantity_not_one",
                        policy_version="rc-0.1.0", cost_table_version="2026.09.1")
    assert skipped.absolute_deviation is None
    withheld = CostCheck(entry_id="li1", result="insufficient_support", reason_code="insufficient_support",
                         independent_base_case_count=3, policy_version="rc-0.1.0", cost_table_version="2026.09.1")
    assert withheld.independent_base_case_count == 3


# --- approvals and reference build membership ---------------------------------------------------
def approval(**kw):
    data = {"approval_id": "ap1", "source_record_id": "ext-1", "claim_id": CLAIM, "reviewed_entry_ids": ["li1"],
            "status": "approved", "approved_amount": "950.00", "currency": "SGD",
            "cost_basis": "single_part_pre_tax_no_discount_v1", "approver": "insurer:t", "approved_at": NOW,
            "synthetic": True, "provenance": PROV}
    data.update(kw)
    return ApprovalRecord(**data)


def test_approval_eligibility_and_lineage():
    assert approval().is_cost_eligible
    for status in ("rejected", "pending", "superseded"):
        assert not approval(status=status).is_cost_eligible
    with pytest.raises(ValidationError):
        approval(approved_amount=None, currency=None, cost_basis=None)
    with pytest.raises(ValidationError):
        approval(supersedes_approval_id="ap1")


def member(base_case: str, index: int, **kw) -> ReferenceBuildMember:
    data = {"build_id": "b1", "table_version": "2026.09.1", "source_record_id": f"rec-{index}",
            "source_kind": "synthetic_seed", "source_hash": sha(f"rec-{index}"), "included": True,
            "inclusion_reason": "eligible_synthetic_seed", "cutoff_date": date(2026, 6, 30), "split": "train",
            "base_case_id": base_case, "cost_key": KEY}
    data.update(kw)
    return ReferenceBuildMember(**data)


def test_support_counts_independent_base_cases_not_quotes():
    members = [member("bc-1", 0), member("bc-1", 1), member("bc-1", 2), member("bc-2", 3),
               member("bc-3", 4, included=False, inclusion_reason="after_cutoff", split=None)]
    assert count_independent_base_cases(members) == 2
    assert count_independent_base_cases(members, key=CostKey(**KEY), split="train") == 2
    with pytest.raises(ValidationError):
        member("bc-4", 5, split=None)  # an included member has a split
