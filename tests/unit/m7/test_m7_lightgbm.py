"""M7 learned method: LightGBM quantile bounds under the same support, validation and publication rules."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import textwrap

import pytest
import yaml

from claim_cmev.comparison.cost_check import compare_amount
from claim_cmev.contracts.costs import CostKey
from claim_cmev.costs.reference.build import build_cost_table, run
from claim_cmev.costs.reference.config import DEFAULT_CONFIG_DIR, ConfigError, load_cost_table_config
from claim_cmev.costs.reference.empirical import KeyFit, assemble_rows, fit_conformal, fit_empirical, served
from claim_cmev.costs.reference.lightgbm_quantile import EXCLUDED_FEATURES, FEATURES, MODEL_FILES
from claim_cmev.costs.reference.lookup import load_table
from claim_cmev.costs.reference.validation import BuildValidationError, validate_ranges

from m7_support import SEED, read_json, record

KEY_FIELDS = ("part_code", "operation", "vehicle_class", "currency")
Q = (Decimal("0.05"), Decimal("0.95"))


def _rows(root, manifest):
    return read_json(root / manifest["table_version"] / "ranges.json")


def _by_key(rows):
    return {tuple(r[f] for f in KEY_FIELDS): r for r in rows}


def test_learned_table_publishes_and_loads_with_its_model_files(built_lightgbm):
    root, manifest = built_lightgbm
    directory = root / manifest["table_version"]
    table = load_table(root, manifest["table_version"])  # the runtime loader verifies every file hash
    assert table.manifest["method"] == "lightgbm_quantile" and manifest["synthetic"] is True
    assert manifest["learned_comparator"]["status"] == "run"
    report = read_json(directory / "model.json")
    assert report["features"] == list(FEATURES) and report["excluded_features"] == list(EXCLUDED_FEATURES)
    assert report["target"] == "ln(amount)" and report["fit_partition"] == "train"
    assert not set(report["features"]) & {"model_year", "side", "damage_type", "workshop_id", "synthetic_date"}
    for name in MODEL_FILES.values():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == manifest["files"][name]
    assert all(m["trees_used"] <= report["recipe"]["n_estimators"] for m in report["models"].values())


def test_same_keys_support_and_withholding_as_the_empirical_table(built, built_lightgbm):
    empirical, learned = _by_key(_rows(*built)), _by_key(_rows(*built_lightgbm))
    assert set(empirical) == set(learned) and len(learned) == 216
    for key, row in learned.items():
        assert row["method"] == "lightgbm_quantile"
        assert row["independent_base_case_count"] == empirical[key]["independent_base_case_count"], key
        assert row["record_count"] == empirical[key]["record_count"]
        if empirical[key]["support_status"] == "withheld":  # sparse, empty and ineligible keys stay withheld
            assert (row["support_status"], row["withheld_reason"]) == ("withheld", empirical[key]["withheld_reason"])
            assert row["lower_amount"] is None and row["upper_amount"] is None
        else:
            assert row["support_status"] == "supported" or row["withheld_reason"] == "range_invalid"


def test_no_crossed_negative_or_float_bound_is_published(built_lightgbm):
    root, manifest = built_lightgbm
    supported = [r for r in _rows(root, manifest) if r["support_status"] == "supported"]
    assert len(supported) == manifest["supported_key_count"] > 0
    for row in supported:
        assert isinstance(row["lower_amount"], str) and isinstance(row["upper_amount"], str)
        lower, upper = Decimal(row["lower_amount"]), Decimal(row["upper_amount"])
        assert lower.is_finite() and upper.is_finite() and 0 < lower <= upper
        assert lower.as_tuple().exponent == upper.as_tuple().exponent == -2
        assert row["independent_base_case_count"] >= manifest["min_independent_support"]
    assert manifest["interval_integrity"]["crossed_bounds"] == 0


def test_the_same_seed_rebuilds_the_same_learned_table(built_lightgbm, tmp_path):
    _, manifest = built_lightgbm
    again = run(["--seed", str(SEED), "--out", str(tmp_path / "again"), "--method", "lightgbm_quantile"])
    assert (again["table_version"], again["content_hash"]) == (manifest["table_version"], manifest["content_hash"])
    assert again["files"]["ranges.json"] == manifest["files"]["ranges.json"]


def test_learned_method_is_a_different_table_version_from_the_empirical_one(built, built_lightgbm):
    assert built[1]["table_version"] != built_lightgbm[1]["table_version"]
    assert built[1]["method"] == "empirical_percentile" and built[1]["lightgbm"] is None


def test_m8_cost_check_reads_the_learned_table_unchanged(built_lightgbm):
    root, manifest = built_lightgbm
    table = load_table(root, manifest["table_version"])
    row = next(r for r in _rows(root, manifest) if r["support_status"] == "supported")
    result = table.lookup(CostKey(**{f: row[f] for f in KEY_FIELDS}))
    assert result.support_status == "supported" and result.method == "lightgbm_quantile"
    common = dict(quantity=1, currency="SGD", cost_basis=row["cost_basis"], entry_id="e-1", policy_version="p")
    assert compare_amount(row["upper_amount"], result, **common).result == "within_range"
    above = compare_amount(str(Decimal(row["upper_amount"]) + Decimal("0.01")), result, **common)
    assert (above.result, above.direction, above.absolute_deviation) == ("outside_range", "above", "0.01")


def test_training_rows_are_base_cases_so_repeated_quotes_cannot_move_the_model(needs_lightgbm):
    from claim_cmev.costs.reference.lightgbm_quantile import fit_lightgbm

    config = load_cost_table_config()
    keys = config.grid.eligible_keys()
    once = [record(f"r{i}", f"b{i}", str(600 + i * 7)) for i in range(60)] + \
           [record(f"v{i}", f"vb{i}", str(900 + i * 9), vehicle_class="van_commercial") for i in range(60)]
    flooded = once + [record(f"x{i}", "b9", str(600 + 9 * 7)) for i in range(12)]  # twelve more quotes, one base case
    key = ("front-bumper", "replace", "sedan_standard", "SGD")
    a = fit_lightgbm(once, [], quantiles=Q, recipe=config.lightgbm, eligible_keys=keys)
    b = fit_lightgbm(flooded, [], quantiles=Q, recipe=config.lightgbm, eligible_keys=keys)
    assert (a.fits[key].lower_raw, a.fits[key].upper_raw) == (b.fits[key].lower_raw, b.fits[key].upper_raw)
    assert (b.fits[key].independent_base_case_count, b.fits[key].record_count) == (60, 72)
    empirical = fit_empirical(flooded, quantiles=Q)
    assert {k: (f.independent_base_case_count, f.record_count) for k, f in b.fits.items()} == \
           {k: (f.independent_base_case_count, f.record_count) for k, f in empirical.items()}
    assert a.report["train_rows"] == 120 and a.report["early_stopping_partition"] is None


def test_a_key_outside_the_frozen_grid_is_refused(needs_lightgbm):
    from claim_cmev.costs.reference.lightgbm_quantile import fit_lightgbm

    config = load_cost_table_config()
    rows = [record(f"r{i}", f"b{i}", "100", part="licence-plate") for i in range(30)]
    with pytest.raises(ValueError, match="outside the frozen eligible grid"):
        fit_lightgbm(rows, [], quantiles=Q, recipe=config.lightgbm, eligible_keys=config.grid.eligible_keys())


def test_fit_with_pytorch_loaded_keeps_native_runtimes_separate(needs_lightgbm):
    """The caller used to abort with OMP Error 15 on macOS; contain regressions in a subprocess."""
    if importlib.util.find_spec("torch") is None:
        pytest.skip("vision extra not installed")
    code = textwrap.dedent('''
        import sys
        import torch
        from dataclasses import replace
        from datetime import date
        from decimal import Decimal
        from claim_cmev.costs.reference.config import load_cost_table_config
        from claim_cmev.costs.reference.lightgbm_quantile import fit_lightgbm
        from claim_cmev.costs.reference.records import PriceRecord
        assert torch.ones(2).sum().item() == 2
        config = load_cost_table_config()
        rows = [PriceRecord(f"r{i}", f"b{i}", "w", "front-bumper", "replace", "sedan_standard", "SGD",
                            config.grid.cost_basis, Decimal(600 + i * 7), date(2026, 1, 1), "0" * 64)
                for i in range(60)]
        fit = fit_lightgbm(rows, [], quantiles=config.quantiles,
                           recipe=replace(config.lightgbm, n_estimators=2),
                           eligible_keys=config.grid.eligible_keys())
        assert fit.report["train_rows"] == 60
        assert len(fit.fits) == 1 and set(fit.model_text) == {"lower", "upper"}
        assert "lightgbm" not in sys.modules
        assert torch.ones(2).sum().item() == 2
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_worker_failure_is_reported_without_a_fit(monkeypatch):
    from claim_cmev.costs.reference.lightgbm_quantile import fit_lightgbm

    def aborted(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], -6, stdout="", stderr="native worker aborted")

    monkeypatch.setattr(subprocess, "run", aborted)
    config = load_cost_table_config()
    with pytest.raises(RuntimeError, match="exit -6.*native worker aborted"):
        fit_lightgbm([], [], quantiles=config.quantiles, recipe=config.lightgbm,
                      eligible_keys=config.grid.eligible_keys())


# ---------------------------------------------------------------- crossed bounds (no LightGBM needed)
def test_crossed_fit_is_withheld_as_range_invalid_and_never_served_or_calibrated():
    config = load_cost_table_config()
    good = ("front-bumper", "replace", "sedan_standard", "SGD")
    crossed = ("front-bumper", "replace", "van_commercial", "SGD")
    sparse = ("front-bumper", "replace", "suv_crossover", "SGD")
    fits = {good: KeyFit(good, Decimal("600"), Decimal("900"), 12, 30),
            crossed: KeyFit(crossed, Decimal("950"), Decimal("900"), 12, 30),
            sparse: KeyFit(sparse, Decimal("990"), Decimal("900"), 3, 9)}  # too sparse wins over crossed
    assert fits[crossed].crossed and not fits[good].crossed
    assert set(served(fits, 8)) == {good}
    calibration = [record("c1", "cb1", "2000", vehicle_class="van_commercial"), record("c2", "cb2", "700")]
    assert fit_conformal(fits, calibration, min_support=8, nominal=Decimal("0.90"))["scores"] == 1
    rows = assemble_rows(fits, config.grid, keys_with_records=set(fits), min_support=8, offset=None,
                         table_version="t", config=config, as_of_date=date(2026, 9, 22),
                         provenance={"source_kind": "synthetic", "generator_version": "g", "seed": 1}, validation=[])
    by_key = _by_key(rows)
    assert (by_key[crossed]["support_status"], by_key[crossed]["withheld_reason"]) == ("withheld", "range_invalid")
    assert by_key[crossed]["lower_amount"] is None and by_key[crossed]["independent_base_case_count"] == 12
    assert by_key[sparse]["withheld_reason"] == "insufficient_support"
    validate_ranges(rows, min_support=8)  # a range_invalid row is a valid withheld row

    from claim_cmev.costs.reference.lookup import PinnedCostTable

    table = PinnedCostTable.from_rows("t", rows, min_independent_support=8)
    result = table.lookup(CostKey(part_code=crossed[0], operation=crossed[1], vehicle_class=crossed[2], currency="SGD"))
    assert (result.support_status, result.reason_code) == ("withheld", "range_invalid")
    check = compare_amount("980.00", result, quantity=1, currency="SGD", cost_basis=config.grid.cost_basis,
                           entry_id="e-1", policy_version="p")
    assert (check.result, check.reason_code, check.absolute_deviation) == ("not_evaluated", "range_invalid", None)


# ---------------------------------------------------------------- configuration
def test_recipe_is_the_proposed_specification_recipe():
    config = load_cost_table_config()
    recipe = config.lightgbm
    assert (recipe.n_estimators, recipe.num_leaves, recipe.min_data_in_leaf, recipe.early_stopping_rounds) == \
           (400, 15, 20, 50)
    assert (recipe.learning_rate, recipe.lambda_l2, recipe.feature_fraction, recipe.seed) == (0.05, 1.0, 1.0, 20260922)
    params = recipe.params()
    assert params["deterministic"] is True and params["force_row_wise"] is True and params["num_threads"] == 1


def _config_copy(tmp_path, mutate):
    copy = tmp_path / "costs"
    shutil.copytree(DEFAULT_CONFIG_DIR, copy)
    data = yaml.safe_load((copy / "cost_table.yaml").read_text())
    mutate(data)
    (copy / "cost_table.yaml").write_text(yaml.safe_dump(data))
    return copy


def test_learned_method_without_a_recipe_or_with_an_unimplemented_target_is_refused(tmp_path):
    def no_recipe(data):
        data["method"] = "lightgbm_quantile"
        del data["lightgbm"]
    with pytest.raises(ConfigError, match="recipe"):
        load_cost_table_config(_config_copy(tmp_path / "a", no_recipe))
    with pytest.raises(ConfigError, match="log_amount"):
        load_cost_table_config(_config_copy(tmp_path / "b", lambda d: d["lightgbm"].update(target="amount")))
    with pytest.raises(ConfigError, match="quoted decimal"):
        load_cost_table_config(_config_copy(tmp_path / "c", lambda d: d["lightgbm"].update(learning_rate=0.05)))


def test_learned_build_still_refuses_an_injected_anomaly_file(built, tmp_path):
    root, _ = built
    injected = next((root / "_generated").rglob("injected_anomaly/prices.csv"))
    config = load_cost_table_config().with_policy(method="lightgbm_quantile")
    with pytest.raises(BuildValidationError, match="injected-anomaly"):
        build_cost_table(injected, config=config, out_dir=tmp_path / "registry")
