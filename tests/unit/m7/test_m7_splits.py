"""M7 screening and grouped splits: base cases never straddle partitions; exclusions carry reasons."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import csv

import pytest

from claim_cmev.costs.reference.config import load_cost_table_config, load_generator_config, load_grid
from claim_cmev.costs.reference.generator import generate_prices
from claim_cmev.costs.reference.records import read_records
from claim_cmev.costs.reference.splits import (
    _allocate, membership_hash, reserve_test_membership, screen_records, split_records,
)

from m7_support import read_json

CUTOFF = date(2026, 6, 30)


def raw(**overrides):
    row = {"record_id": "r1", "base_case_id": "b1", "workshop_id": "ws-01", "part_code": "front-bumper",
           "operation": "replace", "vehicle_class": "sedan_standard", "currency": "SGD",
           "cost_basis": "single_part_pre_tax_no_discount_v1", "quantity": "1", "amount": "800.00",
           "synthetic_date": "2026-01-15", "generator_version": "g", "seed": "1", "run_kind": "ordinary",
           "source_kind": "synthetic", "anomaly_label": "", "anomaly_magnitude": ""}
    return row | overrides


@pytest.mark.parametrize("overrides, reason", [
    ({"part_code": "spoiler"}, "unknown_part"),
    ({"part_code": "front-wheel"}, "non_cost_part"),
    ({"operation": "refit"}, "unknown_operation"),
    ({"operation": "other"}, "ineligible_operation"),
    ({"operation": "unknown"}, "ineligible_operation"),
    ({"vehicle_class": "unknown"}, "unknown_vehicle_class"),
    ({"currency": "MYR"}, "currency_unsupported"),
    ({"cost_basis": "single_part_with_tax_v1"}, "basis_mismatch"),
    ({"quantity": "2"}, "quantity_not_one"),
    ({"quantity": ""}, "quantity_not_one"),
    ({"amount": "NaN"}, "amount_invalid"),
    ({"amount": "-5.00"}, "amount_invalid"),
    ({"amount": "0.00"}, "amount_invalid"),
    ({"amount": "620.0e1"}, "amount_invalid"),
    ({"synthetic_date": "2026-07-01"}, "after_cutoff"),
    ({"part_code": "windshield", "operation": "paint"}, "unsupported_combination"),
    ({"run_kind": "injected_anomaly"}, "injected_anomaly"),
    ({"anomaly_label": "true"}, "injected_anomaly"),
    ({"source_kind": "real"}, "source_not_eligible"),
    ({"base_case_id": ""}, "identifier_missing"),
])
def test_ineligible_records_are_excluded_with_a_reason_never_mapped_to_a_neighbour(overrides, reason):
    included, excluded = screen_records([raw(**overrides)], grid=load_grid(), cutoff_date=CUTOFF)
    assert included == [] and [e.reason_code for e in excluded] == [reason]


def test_valid_record_is_typed_with_an_exact_decimal_and_duplicates_are_refused():
    included, excluded = screen_records([raw(), raw(amount="801.00")], grid=load_grid(), cutoff_date=CUTOFF)
    assert len(included) == 1 and included[0].amount == Decimal("800.00")
    assert included[0].key == ("front-bumper", "replace", "sedan_standard", "SGD")
    assert [e.reason_code for e in excluded] == ["duplicate_record"]


def test_a_base_case_spanning_two_keys_is_excluded_whole():
    rows = [raw(), raw(record_id="r2", operation="paint"), raw(record_id="r3", base_case_id="b2")]
    included, excluded = screen_records(rows, grid=load_grid(), cutoff_date=CUTOFF)
    assert [r.record_id for r in included] == ["r3"]
    assert {e.record_id: e.reason_code for e in excluded} == \
           {"r1": "base_case_spans_keys", "r2": "base_case_spans_keys"}


@pytest.mark.parametrize("n, expected", [(1, ["train"]), (2, ["train", "test"]),
                                         (4, ["train", "train", "validation", "test"])])
def test_largest_remainder_allocation(n, expected):
    ratios = load_cost_table_config().split.ratios
    assert _allocate(n, ratios) == expected
    assert len(_allocate(27, ratios)) == 27


@pytest.fixture(scope="module")
def split(tmp_path_factory):
    config = load_cost_table_config()
    records = read_records(generate_prices(config=load_generator_config(seed=7),
                                           out_dir=tmp_path_factory.mktemp("prices")).records_path)
    included, _ = screen_records(records, grid=config.grid, cutoff_date=config.split.cutoff_date)
    return included, split_records(included, config.split), config


def test_every_quote_of_a_base_case_stays_in_one_partition(split):
    included, result, _ = split
    partition_of = {}
    for name, records in result.partitions.items():
        for record in records:
            assert partition_of.setdefault(record.base_case_id, name) == name
    assert sum(len(r) for r in result.partitions.values()) == len(included)
    assert set(partition_of) == {r.base_case_id for r in included} == set(result.base_case_partition)


def test_split_is_deterministic_and_hashes_membership(split):
    included, result, config = split
    again = split_records(list(reversed(included)), config.split)
    assert again.hashes == result.hashes and again.base_case_partition == result.base_case_partition
    assert result.hashes["test"] == membership_hash(result.partitions["test"])
    counts = result.support_after_split()
    assert all(set(v) == {"train", "validation", "calibration", "test"} for v in counts.values())


def test_test_reservation_names_the_hashed_membership(split):
    _, result, _ = split
    reservation = reserve_test_membership(result)
    assert reservation["reserved_before_fitting"] is True
    assert reservation["membership_sha256"] == result.hashes["test"]
    assert reservation["base_case_ids"] == sorted({r.base_case_id for r in result.partitions["test"]})


def test_published_test_membership_matches_the_reservation_recorded_before_fitting(built):
    root, manifest = built
    directory = root / manifest["table_version"]
    reservation = read_json(directory / "test_reservation.json")
    with open(directory / "members.csv", encoding="utf-8") as handle:
        test_cases = {row["base_case_id"] for row in csv.DictReader(handle) if row["split"] == "test"}
    assert sorted(test_cases) == reservation["base_case_ids"]
    assert manifest["splits"]["test_ref"]["sha256"] == reservation["membership_sha256"]
    final = read_json(directory / "metrics.json")["final_test"]
    assert final["test_membership_sha256"] == reservation["membership_sha256"]
