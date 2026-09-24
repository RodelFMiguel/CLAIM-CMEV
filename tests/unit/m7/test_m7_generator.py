"""M7 synthetic price generator: seeded, base cases separate from quotes, sparsity, separate runs."""
from __future__ import annotations

from collections import defaultdict
import re

import pytest

from claim_cmev.costs.reference.build import build_cost_table
from claim_cmev.costs.reference.config import load_cost_table_config, load_generator_config
from claim_cmev.costs.reference.generator import generate_injected_anomalies, generate_prices
from claim_cmev.costs.reference.records import RECORD_FIELDS, read_records
from claim_cmev.costs.reference.validation import BuildValidationError


@pytest.fixture(scope="module")
def ordinary(tmp_path_factory):
    config = load_generator_config(seed=20260924)
    manifest = generate_prices(config=config, out_dir=tmp_path_factory.mktemp("ordinary"))
    return config, manifest, read_records(manifest.records_path)


def test_same_seed_reproduces_bytes_and_a_new_seed_differs(ordinary, tmp_path):
    config, manifest, _ = ordinary
    again = generate_prices(config=config, out_dir=tmp_path / "again")
    other = generate_prices(config=config.with_seed(1), out_dir=tmp_path / "other")
    assert again.records_path.read_bytes() == manifest.records_path.read_bytes()
    assert again.data == manifest.data
    assert other.data["output"]["sha256"] != manifest.data["output"]["sha256"]
    assert manifest.data["seed"] == 20260924 and other.data["seed"] == 1


def test_record_and_base_case_identifiers_are_separate_and_quotes_correlate(ordinary):
    _, manifest, rows = ordinary
    assert len({r["record_id"] for r in rows}) == len(rows)
    cases = defaultdict(list)
    for row in rows:
        cases[row["base_case_id"]].append(row)
    assert len(cases) < len(rows)
    assert max(len(c) for c in cases.values()) == 5 and min(len(c) for c in cases.values()) == 1
    for quotes in cases.values():
        assert len({(q["part_code"], q["operation"], q["vehicle_class"], q["synthetic_date"]) for q in quotes}) == 1
        assert len({q["workshop_id"] for q in quotes}) == len(quotes)
    assert manifest.data["base_case_count"] == len(cases)


def test_records_are_exact_synthetic_single_part_sgd_without_side_damage_or_year(ordinary):
    config, manifest, rows = ordinary
    assert 5000 <= len(rows) <= 10000 and manifest.data["record_count"] == len(rows)
    assert set(rows[0]) == set(RECORD_FIELDS)
    assert not {"side", "damage_type", "model_year", "year"} & set(rows[0])
    for row in rows:
        assert re.fullmatch(r"\d+\.\d{2}", row["amount"]) and float(row["amount"]) > 0
        assert (row["currency"], row["cost_basis"], row["quantity"]) == \
               ("SGD", "single_part_pre_tax_no_discount_v1", "1")
        assert row["source_kind"] == "synthetic" and row["run_kind"] == "ordinary"
        assert (row["part_code"], row["operation"]) in config.grid.eligible_pairs
    assert manifest.data["excluded_from_generation"] == ["model_year", "side", "damage_type"]
    assert manifest.data["cost_key_fields"] == ["part_code", "operation", "vehicle_class", "currency"]


def test_sparsity_plan_is_recorded_and_applied(ordinary):
    config, manifest, rows = ordinary
    data = manifest.data
    eligible = len(config.grid.eligible_keys())
    assert len(data["sparse_keys"]) == round(eligible * 0.20) and len(data["empty_keys"]) == round(eligible * 0.05)
    counts = data["base_case_count_per_key"]
    minimum = load_cost_table_config().min_independent_base_cases
    assert all(counts[k] == 0 for k in data["empty_keys"])
    assert all(1 <= counts[k] < minimum for k in data["sparse_keys"])
    present = {f"{r['part_code']}/{r['operation']}/{r['vehicle_class']}/{r['currency']}" for r in rows}
    assert not present & set(data["empty_keys"])
    assert set(data["base_price_table"]) == set(data["eligible_key_list"])


def test_injected_anomalies_are_a_separate_run_with_their_own_manifest(ordinary, tmp_path):
    config, manifest, rows = ordinary
    injected = generate_injected_anomalies(config=config, out_dir=tmp_path / "injected")
    data = injected.data
    injected_rows = read_records(injected.records_path)
    assert data["run_kind"] == "injected_anomaly" and data["seed"] == config.seed + 1
    assert not {r["base_case_id"] for r in injected_rows} & {r["base_case_id"] for r in rows}
    labelled = [r for r in injected_rows if r["anomaly_label"] == "true"]
    assert {r["anomaly_label"] for r in injected_rows} == {"true", "false"}
    assert len(labelled) == data["labelled_base_case_count"] == round(len(injected_rows) * 0.10)
    assert sum(data["labelled_by_magnitude"].values()) == len(labelled)
    assert {r["anomaly_magnitude"] for r in labelled} == {"0.25", "0.50", "-0.30"}


def test_an_injected_run_never_builds_a_reference_table(ordinary, tmp_path):
    config, _, _ = ordinary
    injected = generate_injected_anomalies(config=config, out_dir=tmp_path / "injected")
    with pytest.raises(BuildValidationError, match="injected-anomaly run never builds"):
        build_cost_table(injected.records_path, config=load_cost_table_config(), out_dir=tmp_path / "registry")
    assert not (tmp_path / "registry").exists()


def test_a_records_file_that_no_longer_matches_its_manifest_is_refused(ordinary, tmp_path):
    config, _, _ = ordinary
    manifest = generate_prices(config=config, out_dir=tmp_path / "run")
    with open(manifest.records_path, "a", encoding="utf-8") as handle:
        handle.write("extra\n")
    with pytest.raises(BuildValidationError, match="does not match its generator manifest"):
        build_cost_table(manifest.records_path, config=load_cost_table_config(), out_dir=tmp_path / "registry")
