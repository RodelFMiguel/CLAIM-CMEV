"""M7 experiments: the pre-declared method selection rule, injected-anomaly scoring and the RQ4 cap."""
from __future__ import annotations

from decimal import Decimal

import pytest

from claim_cmev.costs.reference.config import load_cost_table_config
from claim_cmev.costs.reference.evaluation import (
    cap_train, compare_methods, experiment_c, read_injected, reduced_support, score_injected, select_method,
)

from m7_support import record

BAND = (Decimal("0.85"), Decimal("0.95"))
KEY = ("front-bumper", "replace", "sedan_standard", "SGD")


def _validation(covered, width, evaluated=1000):
    return {"covered": covered, "evaluated_records": evaluated, "coverage": covered / evaluated if evaluated else None,
            "median_width": width}


def _select(empirical, learned):
    return select_method({"empirical_percentile": empirical, "lightgbm_quantile": learned}, BAND)


def test_selection_takes_the_narrower_method_among_those_inside_the_band():
    assert _select(_validation(906, "207.41"), _validation(887, "189.33"))["selected"] == "lightgbm_quantile"
    assert _select(_validation(906, "180.00"), _validation(887, "189.33"))["selected"] == "empirical_percentile"


def test_selection_ignores_a_narrower_method_outside_the_band():
    outcome = _select(_validation(906, "207.41"), _validation(849, "150.00"))  # 0.849 is below 0.85
    assert outcome["selected"] == "empirical_percentile" and outcome["reason"] == "only qualifying method"
    assert _select(_validation(960, "207.41"), _validation(850, "300.00"))["selected"] == "lightgbm_quantile"


def test_selection_keeps_the_fallback_on_a_tie_or_when_nothing_qualifies():
    tie = _select(_validation(906, "200.00"), _validation(887, "200.00"))
    assert tie["selected"] == "empirical_percentile" and "tie" in tie["reason"]
    none = _select(_validation(700, "100.00"), _validation(990, "100.00"))
    assert none["selected"] == "empirical_percentile" and "no method" in none["reason"]
    empty = _select(_validation(0, None, evaluated=0), _validation(0, None, evaluated=0))
    assert empty["selected"] == "empirical_percentile"
    assert tie["selected_on"] == "validation"


def test_injected_scoring_counts_flags_misses_and_unchecked_records_separately():
    bounds = {KEY: (Decimal("600.00"), Decimal("900.00"), 12)}
    other = ("hood", "replace", "sedan_standard", "SGD")
    injected = [
        {"key": KEY, "amount": Decimal("1200.00"), "label": True, "magnitude": "0.50"},   # flagged
        {"key": KEY, "amount": Decimal("880.00"), "label": True, "magnitude": "0.25"},    # missed
        {"key": other, "amount": Decimal("5000.00"), "label": True, "magnitude": "0.50"},  # no range: no check
        {"key": KEY, "amount": Decimal("900.00"), "label": False, "magnitude": None},     # equality is inside
        {"key": KEY, "amount": Decimal("900.01"), "label": False, "magnitude": None},     # false flag
        {"key": other, "amount": Decimal("10.00"), "label": False, "magnitude": None},
    ]
    scores = score_injected(bounds, injected)
    assert (scores["true_positive"], scores["false_negative"], scores["false_positive"], scores["true_negative"]) == \
           (1, 1, 1, 1)
    assert scores["records_without_cost_check"] == 2 and scores["labelled_without_cost_check"] == 1
    assert (scores["precision"], scores["recall_among_checked"]) == (0.5, 0.5)
    assert scores["recall_among_all_labelled"] == round(1 / 3, 4)
    assert [(m["magnitude"], m["labelled"], m["checked"], m["flagged"]) for m in scores["by_magnitude"]] == \
           [("0.25", 1, 1, 0), ("0.50", 2, 1, 1)]


def test_cap_keeps_whole_base_cases_per_key_and_is_deterministic():
    train = [record(f"r{c}-{q}", f"b{c:02d}", "700") for c in range(10) for q in range(3)] + \
            [record(f"v{c}-{q}", f"vb{c}", "900", vehicle_class="van_commercial") for c in range(2) for q in range(2)]
    capped = cap_train(train, 4, seed=7)
    cases = {}
    for r in capped:
        cases.setdefault(r.key, set()).add(r.base_case_id)
    assert {k: len(v) for k, v in cases.items()} == {KEY: 4, ("front-bumper", "replace", "van_commercial", "SGD"): 2}
    assert len([r for r in capped if r.key == KEY]) == 12  # every quote of a kept base case stays
    assert capped == cap_train(train, 4, seed=7) and cap_train(train, None, seed=7) == train
    assert {r.base_case_id for r in cap_train(train, 4, seed=8)} != {r.base_case_id for r in capped}


def test_experiment_c_refuses_ordinary_records_as_the_injected_run(built):
    root, _ = built
    ordinary = next((root / "_generated").rglob("ordinary/prices.csv"))
    with pytest.raises(ValueError, match="injected-anomaly run"):
        read_injected(ordinary, load_cost_table_config())


def test_experiments_run_end_to_end_on_one_split(built, built_lightgbm):
    root, manifest = built
    ordinary = next((root / "_generated").rglob("ordinary/prices.csv"))
    injected = next((root / "_generated").rglob("injected_anomaly/prices.csv"))
    config = load_cost_table_config()

    comparison = compare_methods(ordinary, config=config)
    methods = comparison["methods"]
    assert comparison["test_membership_sha256"] == manifest["splits"]["test_ref"]["sha256"]
    assert comparison["selection"]["selected"] in methods and comparison["selection"]["selected_on"] == "validation"
    assert methods["empirical_percentile"]["final_test"]["coverage"] == manifest["final_test_summary"]["coverage"]
    assert methods["lightgbm_quantile"]["final_test"]["coverage"] == built_lightgbm[1]["final_test_summary"]["coverage"]
    assert {m["final_test"]["supported_keys"] for m in methods.values()} == {manifest["supported_key_count"]}
    assert all(m["policy"]["min_independent_base_cases"] == 8 for m in methods.values())

    c = experiment_c(ordinary, injected, config=config)
    for method in methods:
        anomalies = c["methods"][method]["injected_anomalies"]
        assert anomalies["labelled_anomalies"] == sum(m["labelled"] for m in anomalies["by_magnitude"])
        assert anomalies["true_positive"] + anomalies["false_negative"] + anomalies["labelled_without_cost_check"] == \
               anomalies["labelled_anomalies"]
        assert c["methods"][method]["ordinary_prices"]["coverage"] == methods[method]["final_test"]["coverage"]

    rq4 = reduced_support(ordinary, config=config, injected_path=injected, caps=(None, 5))
    full, capped = rq4["steps"]
    assert capped["train_base_cases"] < full["train_base_cases"]
    for method in methods:
        assert full["methods"][method]["served"]["final_test"]["coverage"] == methods[method]["final_test"]["coverage"]
        served_view = capped["methods"][method]["served"]["final_test"]
        assert served_view["supported_keys"] == 0 and served_view["withheld_key_fraction"] == 1.0  # 5 < frozen 8
        assert capped["methods"][method]["model_only_not_served"]["final_test"]["supported_keys"] > 0
