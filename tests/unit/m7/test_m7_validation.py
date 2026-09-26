"""M7 build validation: a malformed interval, mixed basis/currency or quote-counted support never publishes."""
from __future__ import annotations

import pytest

from claim_cmev.costs.reference.validation import BuildValidationError, validate_ranges

from m7_support import record, row, withheld


def check(rows, **kwargs):
    validate_ranges(rows, min_support=kwargs.pop("min_support", 8), **kwargs)


def test_valid_rows_pass_including_a_zero_width_interval():
    check([row(), row(range_id="rng-2", vehicle_class="van_commercial", lower_amount="900.00", upper_amount="900.00"),
           withheld(range_id="rng-3", vehicle_class="suv_crossover")])


@pytest.mark.parametrize("bad, message", [
    (row(lower_amount="1020.01"), "crossed bounds"),
    (row(upper_amount="NaN"), "non-finite"),
    (row(lower_amount="Infinity"), "non-finite"),
    (row(lower_amount="-1.00"), "negative"),
    (row(lower_amount=620.0), "decimal string"),
    (row(lower_amount="0.00", upper_amount="0.00"), "zero range"),
    (withheld(lower_amount="0.00", upper_amount="0.00"), "null bounds"),
    (withheld(withheld_reason=None), "needs a reason"),
    (row(currency="USD"), "basis or currency mixing"),
    (row(cost_basis="single_part_with_tax_v1"), "basis or currency mixing"),
    (row(side="left"), "v1 key fields"),
    (row(independent_base_case_count=5, record_count=15), "below the minimum"),
    (row(independent_base_case_count=20, record_count=10), "exceeds the record count"),
    (row(operation="other"), "never has a range"),
    (row(vehicle_class="unknown"), "never has a range"),
    (row(synthetic=False), "synthetic"),
    (row(support_status="partial"), "support_status"),
])
def test_invalid_rows_fail_the_build(bad, message):
    with pytest.raises(BuildValidationError, match=message):
        check([bad])


def test_zero_width_is_refused_when_the_build_does_not_permit_it():
    with pytest.raises(BuildValidationError, match="zero-width"):
        check([row(lower_amount="900.00", upper_amount="900.00")], zero_width_allowed=False)


def test_duplicate_keys_are_refused():
    with pytest.raises(BuildValidationError, match="duplicate"):
        check([row(), row()])


def test_support_counted_by_quote_instead_of_base_case_is_refused():
    train = [record(f"r{c}{q}", f"b{c}", "800.00") for c in range(3) for q in range(5)]  # 3 cases, 15 quotes
    quote_counted = row(independent_base_case_count=15, record_count=15)
    with pytest.raises(BuildValidationError, match="support counts base cases, never quotes"):
        check([quote_counted], train_records=train)
    check([row(independent_base_case_count=3, record_count=15)], min_support=3, train_records=train)


def test_basis_or_currency_mixing_among_training_records_is_refused():
    train = [record(f"r{i}", f"b{i}", "800.00") for i in range(8)]
    train[0] = record("r0", "b0", "800.00", basis="single_part_with_tax_v1")
    with pytest.raises(BuildValidationError, match="mixing among training records"):
        check([row(independent_base_case_count=8, record_count=8)], train_records=train)
