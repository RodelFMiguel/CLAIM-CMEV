"""M7 publication: atomic, immutable, pinned. A newer table never changes an older pinned lookup."""
from __future__ import annotations

import csv
import hashlib
import time

import pytest

from claim_cmev.contracts.costs import ReferenceCostRange
from claim_cmev.costs.reference import build as build_module
from claim_cmev.costs.reference import active_table_version, list_tables, load_table
from claim_cmev.costs.reference.build import run
from claim_cmev.costs.reference.eligibility import member_record
from claim_cmev.costs.reference.publish import TableExistsError
from claim_cmev.costs.reference.validation import BuildValidationError

from m7_support import read_json

REQUIRED_MANIFEST = {  # integration contracts 9.3 plus the M7 manifest list
    "schema_version", "table_version", "status", "method", "nominal_coverage", "quantiles", "cost_key_fields",
    "cost_basis", "currency", "source", "splits", "cutoff_date", "min_independent_support", "support_selection",
    "calibration", "key_count", "withheld_key_count", "members_ref", "exclusions", "validation_results",
    "interval_integrity", "synthetic", "built_at", "built_by", "code_revision", "generator_version",
    "generator_seed", "conformal", "min_independent_base_cases", "split_config_hash", "partition_hashes",
    "taxonomy_version", "eligible_keys_hash", "supported_key_count", "metrics_ref", "exclusions_ref",
}


def snapshot(table):
    return [(r.range_id, table.lookup(r.key).to_dict()) for r in table.ranges()]


def file_hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}


def test_manifest_is_complete_synthetic_and_keyed_without_side_damage_or_year(built):
    root, manifest = built
    directory = root / manifest["table_version"]
    stored = read_json(directory / "build_manifest.json")
    assert REQUIRED_MANIFEST <= set(stored)
    assert stored["cost_key_fields"] == ["part_code", "operation", "vehicle_class", "currency"]
    assert stored["excluded_key_fields"] == ["model_year", "side", "damage_type"]
    assert stored["synthetic"] is True and stored["source"]["kind"] == "synthetic"
    assert stored["support_selection"]["selected_on"] == "validation"
    assert stored["interval_integrity"]["crossed_bounds"] == 0
    assert stored["min_independent_support"] == 8 and stored["support_selection"]["frozen_in_config"]
    assert stored["calibration"]["method"] == "cqr_log_ratio" and stored["calibration"]["applied"]
    assert set(stored["files"]) == {p.name for p in directory.iterdir()} - {"build_manifest.json"}
    assert stored["grid_key_count"] == 216 and stored["key_count"] + sum(stored["withheld_by_reason"].values()) == 216


def test_every_published_row_is_a_valid_contract_row_with_its_lineage(built):
    root, manifest = built
    rows = read_json(root / manifest["table_version"] / "ranges.json")
    for row in rows:
        record = ReferenceCostRange.model_validate(row)
        assert record.table_version == manifest["table_version"] and record.synthetic
        assert record.cutoff_date.isoformat() == manifest["cutoff_date"] and record.method == manifest["method"]
        assert record.provenance.seed == 20260924
        if record.support_status == "withheld":
            assert record.lower_amount is None and record.upper_amount is None and record.withheld_reason


def test_membership_and_exclusions_are_contract_rows(built):
    root, manifest = built
    directory = root / manifest["table_version"]
    for name in ("members.csv", "exclusions.csv"):
        with open(directory / name, encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows
        for row in rows[:200]:
            converted = row | {"included": row["included"] == "True", "split": row["split"] or None,
                               "weight": float(row["weight"])}
            converted |= {k: None for k in ("part_code", "operation", "vehicle_class", "currency") if not row[k]}
            assert member_record(converted).included is (name == "members.csv")
    assert {e["reason_code"] for e in manifest["exclusions"]} == {"after_cutoff"}


def test_metrics_report_counts_and_denominators_once_on_the_test_partition(built):
    root, manifest = built
    metrics = read_json(root / manifest["table_version"] / "metrics.json")
    final = metrics["final_test"]
    assert final["partition"] == "test" and final["evaluated_once"] and final["synthetic"]
    coverage = final["coverage"]
    assert coverage["covered"] + coverage["below"] + coverage["above"] == coverage["evaluated_records"] > 0
    assert final["available_range_rate"]["test_records"]["total"] >= coverage["evaluated_records"]
    assert sum(g["evaluated_records"] for g in final["by_support_group"]) == coverage["evaluated_records"]
    assert final["target_status"] in ("met", "unmet") and "not proof" in final["interpretation"]
    assert metrics["learned_comparator"]["status"] == "not_run"
    assert read_json(root / manifest["table_version"] / "support_sweep.json")["synthetic"] is True


def test_rebuilding_is_deterministic_and_idempotent(built, tmp_path):
    root, manifest = built
    again = run(["--seed", "20260924", "--out", str(root)])
    assert again["reused_existing"] and again["table_version"] == manifest["table_version"]
    assert [t["table_version"] for t in list_tables(root)] == [manifest["table_version"]]
    elsewhere = run(["--seed", "20260924", "--out", str(tmp_path / "other")])
    assert elsewhere["table_version"] == manifest["table_version"]
    assert (tmp_path / "other" / elsewhere["table_version"] / "ranges.json").read_bytes() == \
           (root / manifest["table_version"] / "ranges.json").read_bytes()


def test_a_newer_table_leaves_the_older_pinned_version_unchanged(tmp_path):
    root = tmp_path / "registry"
    first = run(["--seed", "11", "--out", str(root), "--promote"])
    pinned = first["table_version"]  # what an existing assessment stored
    before_lookups = snapshot(load_table(root, pinned))
    before_files = file_hashes(root / pinned)
    second = run(["--seed", "12", "--out", str(root), "--promote"])
    assert second["table_version"] != pinned
    assert snapshot(load_table(root, pinned)) == before_lookups
    assert file_hashes(root / pinned) == before_files
    assert snapshot(load_table(root, second["table_version"])) != before_lookups
    registry = read_json(root / "registry.json")
    assert registry["active_version"] == second["table_version"]
    assert registry["history"][-1] == registry["history"][-1] | {"from": pinned, "to": second["table_version"]}
    assert active_table_version(root) == second["table_version"]


def test_a_version_is_never_overwritten_with_different_content(tmp_path):
    root = tmp_path / "registry"
    run(["--seed", "11", "--out", str(root), "--table-version", "ct-fixed"])
    before = file_hashes(root / "ct-fixed")
    with pytest.raises(TableExistsError):
        run(["--seed", "12", "--out", str(root), "--table-version", "ct-fixed"])
    assert file_hashes(root / "ct-fixed") == before
    assert not list(root.glob(".staging-*"))


def test_a_failed_candidate_publishes_nothing_and_keeps_the_active_table(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    first = run(["--seed", "11", "--out", str(root), "--promote"])

    def refuse(*args, **kwargs):
        raise BuildValidationError(["crossed bounds 900 > 800"])

    monkeypatch.setattr(build_module, "validate_ranges", refuse)
    with pytest.raises(BuildValidationError):
        run(["--seed", "12", "--out", str(root), "--promote"])
    assert sorted(p.name for p in root.iterdir() if p.name.startswith("ct-")) == [first["table_version"]]
    assert not list(root.glob(".staging-*")) and active_table_version(root) == first["table_version"]


def test_demo_build_runs_well_under_a_minute(tmp_path):
    started = time.monotonic()
    run(["--seed", "20260924", "--out", str(tmp_path / "timed")])
    assert time.monotonic() - started < 30
