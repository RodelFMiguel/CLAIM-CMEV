"""M7 empirical method: type-7 percentiles, per-base-case aggregation, support, sweep, conformal."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import random

import numpy as np
import pytest

from claim_cmev.costs.reference.config import load_cost_table_config
from claim_cmev.costs.reference.empirical import (
    assemble_rows, conformal_selection, fit_conformal, fit_empirical, percentile, served, support_sweep,
)

from m7_support import record

Q = (Decimal("0.05"), Decimal("0.95"))


@pytest.mark.parametrize("n", [1, 2, 3, 8, 15, 47])
def test_percentile_is_type7_linear_interpolation(n):
    rng = random.Random(n)
    values = sorted(Decimal(f"{rng.uniform(100, 900):.2f}") for _ in range(n))
    for q in (Decimal("0.05"), Decimal("0.5"), Decimal("0.95")):
        expected = np.percentile([float(v) for v in values], float(q) * 100, method="linear")
        assert abs(float(percentile(values, q)) - expected) < 1e-9


def test_support_counts_distinct_base_cases_never_quotes():
    records = [record(f"r{c}{i}", f"b{c}", str(500 + c * 10)) for c in range(3) for i in range(5)]
    fit = fit_empirical(records, quantiles=Q)[("front-bumper", "replace", "sedan_standard", "SGD")]
    assert (fit.independent_base_case_count, fit.record_count) == (3, 15)


def test_repeated_quotes_of_one_base_case_cannot_dominate_the_interval():
    once = [record(f"r{i}", f"b{i}", str(600 + i * 20)) for i in range(10)]
    flooded = once + [record(f"x{i}", "b9", "780") for i in range(8)]  # eight more quotes of one base case
    key = ("front-bumper", "replace", "sedan_standard", "SGD")
    a, b = fit_empirical(once, quantiles=Q)[key], fit_empirical(flooded, quantiles=Q)[key]
    assert (a.lower_raw, a.upper_raw, a.independent_base_case_count) == \
           (b.lower_raw, b.upper_raw, b.independent_base_case_count)
    assert b.record_count == 18


def test_rows_cover_the_grid_and_withhold_with_reasons_and_null_bounds():
    config = load_cost_table_config()
    grid = config.grid
    supported_key = ("front-bumper", "replace", "sedan_standard", "SGD")
    sparse_key = ("front-bumper", "replace", "van_commercial", "SGD")
    records = [record(f"s{i}", f"sb{i}", str(700 + i * 10)) for i in range(10)] + \
              [record(f"v{i}", f"vb{i}", "900", vehicle_class="van_commercial") for i in range(3)]
    fits = fit_empirical(records, quantiles=Q)
    rows = assemble_rows(fits, grid, keys_with_records={supported_key, sparse_key}, min_support=8, offset=None,
                         table_version="t", config=config, as_of_date=date(2026, 9, 22),
                         provenance={"source_kind": "synthetic", "generator_version": "g", "seed": 1}, validation=[])
    by_key = {(r["part_code"], r["operation"], r["vehicle_class"], r["currency"]): r for r in rows}
    assert len(rows) == len(grid.keys()) == 216
    assert by_key[supported_key]["support_status"] == "supported"
    assert isinstance(by_key[supported_key]["lower_amount"], str)
    assert (by_key[sparse_key]["withheld_reason"], by_key[sparse_key]["independent_base_case_count"]) == \
           ("insufficient_support", 3)
    assert by_key[("hood", "repair", "suv_crossover", "SGD")]["withheld_reason"] == "no_records"
    assert by_key[("windshield", "paint", "sedan_standard", "SGD")]["withheld_reason"] == "unsupported_combination"
    for r in rows:
        if r["support_status"] == "withheld":
            assert r["lower_amount"] is None and r["upper_amount"] is None and r["withheld_reason"]


def _two_key_fits():
    small = [record(f"a{i}", f"ab{i}", str(100 + i)) for i in range(4)]
    large = [record(f"c{i}", f"cb{i}", str(500 + 5 * i), part="hood") for i in range(12)]
    return fit_empirical(small + large, quantiles=Q)


def test_support_sweep_uses_validation_only_and_refuses_thin_candidates():
    config = load_cost_table_config().with_policy(sweep_candidates=(3, 5, 20), sweep_min_evaluated=2)
    fits = _two_key_fits()
    validation = [record("v1", "vb1", "102"), record("v2", "vb2", "530", part="hood"),
                  record("v3", "vb3", "999", part="hood")]
    sweep = support_sweep(fits, validation, config=config, eligible_key_count=2)
    by_threshold = {c["min_independent_base_cases"]: c for c in sweep["candidates"]}
    assert by_threshold[3]["supported_keys"] == 2 and by_threshold[3]["evaluated_records"] == 3
    assert by_threshold[5]["supported_keys"] == 1 and by_threshold[5]["evaluated_records"] == 2
    assert by_threshold[20]["selectable"] is False
    assert sweep["selected"] in (3, 5) and sweep["partition"] == "validation" and sweep["synthetic"] is True


def test_conformal_offset_is_fitted_on_calibration_and_widens_bounds():
    fits = _two_key_fits()
    key = ("hood", "replace", "sedan_standard", "SGD")
    fit = fits[key]
    calibration = [record(f"k{i}", f"kb{i}", str(480 + 10 * i), part="hood") for i in range(12)]
    result = fit_conformal(fits, calibration, min_support=5, nominal=Decimal("0.90"))
    assert result["partition"] == "calibration" and result["scores"] == 12 and result["rank"] == 12
    offset = Decimal(result["offset"])
    assert offset > 0
    lower, upper = fit.bounds(offset)
    assert lower < fit.bounds()[0] and upper > fit.bounds()[1]
    assert fit_conformal(fits, calibration[:3], min_support=5, nominal=Decimal("0.90"))["offset"] is None
    choice = conformal_selection(fits, calibration, result, min_support=5, config=load_cost_table_config())
    assert choice["partition"] == "validation" and {c["conformal"] for c in choice["candidates"]} == {"none", "cqr"}


def test_published_bounds_are_rounded_once_to_cents():
    fits = _two_key_fits()
    for lower, upper, _ in served(fits, 1, Decimal("0.0123")).values():
        assert lower.as_tuple().exponent == -2 and upper.as_tuple().exponent == -2 and lower <= upper
