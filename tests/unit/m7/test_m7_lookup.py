"""M7 read-only loader and lookup: verified pinned tables, no extrapolation, no fallback."""
from __future__ import annotations

from decimal import Decimal
import os
import stat

import pytest

from claim_cmev.contracts.common import ContractError
from claim_cmev.contracts.costs import CostKey, ReferenceCostRange
from claim_cmev.costs.reference import (
    LOOKUP_REASON_CODES, CostTableError, PinnedCostTable, active_table_version, list_tables, load_table, lookup_range,
)

from m7_support import read_json, rewrite, row, withheld


def key(part="front-bumper", operation="replace", vehicle_class="sedan_standard", currency="SGD", **extra):
    return {"part_code": part, "operation": operation, "vehicle_class": vehicle_class, "currency": currency} | extra


@pytest.fixture(scope="module")
def table(built):
    root, manifest = built
    return load_table(root, manifest["table_version"])


def rows_where(table, **match):
    return [r for r in table.rows() if all(r[k] == v for k, v in match.items())]


def test_supported_lookup_returns_exact_pinned_bounds_and_the_contract_row(table):
    target = rows_where(table, support_status="supported")[0]
    result = table.lookup(CostKey(part_code=target["part_code"], operation=target["operation"],
                                  vehicle_class=target["vehicle_class"], currency="SGD"))
    assert result.supported and result.reason_code is None
    assert (result.lower_amount, result.upper_amount) == (Decimal(target["lower_amount"]),
                                                          Decimal(target["upper_amount"]))
    assert isinstance(result.lower_amount, Decimal) and result.lower_amount <= result.upper_amount
    assert result.independent_base_case_count >= table.min_independent_support
    assert result.table_version == table.table_version and result.synthetic is True
    assert isinstance(result.range, ReferenceCostRange) and result.range.range_id == target["range_id"]
    assert result.to_dict()["lower_amount"] == target["lower_amount"]


def test_every_non_supported_outcome_has_null_bounds_and_a_spec_reason(table):
    insufficient = rows_where(table, withheld_reason="insufficient_support")[0]
    no_records = rows_where(table, withheld_reason="no_records")[0]
    cases = {
        "insufficient_support": key(insufficient["part_code"], insufficient["operation"],
                                    insufficient["vehicle_class"]),
        "no_records": key(no_records["part_code"], no_records["operation"], no_records["vehicle_class"]),
        "pair": key("windshield", "paint"),
        "other": key(operation="other"), "unknown_op": key(operation="unknown"),
        "wheel": key("front-wheel"), "spoiler": key("spoiler"),
        "class": key(vehicle_class="unknown"), "truck": key(vehicle_class="truck"),
        "currency": key(currency="MYR"), "basis": key(cost_basis="single_part_with_tax_v1"),
    }
    expected = {"insufficient_support": ("withheld", "insufficient_support"),
                "no_records": ("withheld", "insufficient_support"),
                "pair": ("withheld", "unsupported_combination"), "other": ("absent", "unsupported_combination"),
                "unknown_op": ("absent", "unsupported_combination"), "wheel": ("absent", "unsupported_combination"),
                "spoiler": ("absent", "no_key"), "class": ("absent", "unknown_vehicle_class"),
                "truck": ("absent", "unknown_vehicle_class"), "currency": ("absent", "currency_unsupported"),
                "basis": ("absent", "basis_mismatch")}
    for name, lookup_key in cases.items():
        result = lookup_range(table, lookup_key)
        assert (result.support_status, result.reason_code) == expected[name], name
        assert result.lower_amount is None and result.upper_amount is None, name
        assert result.reason_code in LOOKUP_REASON_CODES
    assert lookup_range(table, cases["no_records"]).withheld_reason == "no_records"
    assert lookup_range(table, cases["insufficient_support"]).independent_base_case_count == \
           insufficient["independent_base_case_count"]
    assert table.lookup(key(), cost_basis="single_part_with_tax_v1").reason_code == "basis_mismatch"


def test_no_fallback_to_another_class_and_operations_never_pool(table):
    for row_ in rows_where(table, withheld_reason="insufficient_support"):
        pair = (row_["part_code"], row_["operation"])
        siblings = [r for r in table.rows()
                    if (r["part_code"], r["operation"]) == pair and r["support_status"] == "supported"]
        if siblings:
            result = table.lookup(key(row_["part_code"], row_["operation"], row_["vehicle_class"]))
            assert not result.supported and result.lower_amount is None
            break
    else:
        pytest.skip("no withheld key with a supported sibling class in this build")
    ids = {table.lookup(key(operation=op)).range_id for op in ("repair", "replace", "paint")}
    assert len(ids) == 3


def test_v1_shaped_keys_are_refused(table):
    with pytest.raises(ContractError) as error:
        table.lookup(key(side="left"))
    assert error.value.reason_code == "schema_unsupported"


def test_load_verifies_hashes_and_refuses_tampering(writable_copy):
    root, version = writable_copy("hash")
    rows = read_json(root / version / "ranges.json")
    next(r for r in rows if r["support_status"] == "supported")["lower_amount"] = "1.00"
    rewrite(root / version, "ranges.json", rows, rehash=False)
    with pytest.raises(CostTableError) as error:
        load_table(root, version)
    assert error.value.reason_code == "hash_mismatch"
    (root / version / "members.csv").unlink()
    with pytest.raises(CostTableError, match="members.csv"):
        load_table(root, version)


def test_a_rehashed_crossed_bound_is_still_never_served(writable_copy):
    root, version = writable_copy("crossed")
    rows = read_json(root / version / "ranges.json")
    target = next(r for r in rows if r["support_status"] == "supported")
    target["lower_amount"], target["upper_amount"] = target["upper_amount"], target["lower_amount"]
    rewrite(root / version, "ranges.json", rows)
    with pytest.raises(CostTableError) as error:
        load_table(root, version)
    assert error.value.reason_code == "range_invalid"


@pytest.mark.parametrize("field, value", [("cost_key_fields", ["part_code", "side", "operation", "damage_type",
                                                                "vehicle_class", "model_year", "currency"]),
                                          ("cost_key_fields", ["part_code", "operation", "vehicle_class"]),
                                          ("schema_version", "0.1.0")])
def test_v1_shaped_table_is_refused_with_schema_unsupported(writable_copy, field, value):
    root, version = writable_copy("v1")
    manifest = read_json(root / version / "build_manifest.json")
    manifest[field] = value
    rewrite(root / version, "build_manifest.json", manifest, rehash=False)
    with pytest.raises(CostTableError) as error:
        load_table(root, version)
    assert error.value.reason_code == "schema_unsupported"


def test_missing_and_unsafe_versions_are_operational_errors(built):
    root, _ = built
    for version in ("ct-does-not-exist", "../etc", ""):
        with pytest.raises(CostTableError) as error:
            load_table(root, version)
        assert error.value.reason_code == "table_missing"


def test_loading_is_read_only(built):
    root, manifest = built
    directory = root / manifest["table_version"]
    before = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    mode = directory.stat().st_mode
    os.chmod(directory, stat.S_IRUSR | stat.S_IXUSR)
    try:
        load_table(root, manifest["table_version"]).lookup(key())
    finally:
        os.chmod(directory, mode)
    assert {p.name: p.stat().st_mtime_ns for p in directory.iterdir()} == before
    assert all(not p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) for p in directory.iterdir())


def test_registry_lists_the_active_table(built):
    root, manifest = built
    assert active_table_version(root) == manifest["table_version"]
    listed = list_tables(root)
    assert [t["table_version"] for t in listed] == [manifest["table_version"]] and listed[0]["active"]
    assert listed[0]["synthetic"] is True and listed[0]["release_status"] == "candidate"


def test_in_memory_table_applies_the_same_validation():
    table = PinnedCostTable.from_rows("t-1", [row(), withheld(range_id="rng-2", vehicle_class="van_commercial")],
                                      min_independent_support=8)
    assert table.lookup(key()).supported
    assert table.lookup(key(vehicle_class="van_commercial")).reason_code == "insufficient_support"
    assert table.lookup(key(vehicle_class="suv_crossover")).reason_code == "no_key"
    with pytest.raises(CostTableError) as error:
        PinnedCostTable.from_rows("t-1", [row(lower_amount="2000.00")], min_independent_support=8)
    assert error.value.reason_code == "range_invalid"
    with pytest.raises(CostTableError):
        PinnedCostTable.from_rows("t-1", [row(table_version="t-other")], min_independent_support=8)
