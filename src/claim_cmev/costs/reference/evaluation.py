"""M7 experiments: the method comparison, RQ4 (fewer reference cases) and experiment C.

Everything here is offline analysis of synthetic records. It publishes no table and
changes no policy; it reports what each method does under one split.

Order of evidence, kept strict so a result cannot steer a choice it should not:

1. Both methods are fitted on train. The support threshold is the frozen configuration
   value; the conformal option is selected per method on validation.
2. The method is selected by a rule fixed in advance, using validation numbers only.
3. Only then is the reserved test partition read, once per method, to report.

Ordinary-price exceedance (test partition) and injected anomalies (a separate generator
run with its own labels) are different populations and are reported in separate tables.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
from typing import Any, Mapping, Sequence

from .build import fit_policy
from .config import CostTableConfig
from .empirical import Bounds, evaluate_bounds, fit_conformal, conformal_selection, served, width_summary
from .records import PriceRecord, read_records
from .splits import SplitResult, membership_hash, reserve_test_membership, screen_records, split_records
from .vocabulary import METHODS, SYNTHETIC_NOTICE, CostKeyTuple, key_text

FALLBACK_METHOD = "empirical_percentile"
SELECTION_RULE = ("Among methods whose validation coverage is inside the target band, select the narrower median "
                  f"served range. On a tie, or if no method qualifies, keep {FALLBACK_METHOD}.")
DEFAULT_CAPS: tuple[int | None, ...] = (None, 16, 12, 8, 5, 3)


def load_split(records_path: Path, config: CostTableConfig) -> SplitResult:
    """Screen and split ordinary records exactly as a build does."""
    included, _ = screen_records(read_records(records_path), grid=config.grid, cutoff_date=config.split.cutoff_date)
    return split_records(included, config.split)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _summary(bounds: Bounds, records: Sequence[PriceRecord], config: CostTableConfig) -> dict[str, Any]:
    """Coverage, width and availability of served bounds on one partition, with counts."""
    result = evaluate_bounds(bounds, records, config.support_group_edges)
    eligible = len(config.grid.eligible_keys())
    return {
        "coverage": result["coverage"], "covered": result["covered"], "evaluated_records": result["evaluated_records"],
        "below": result["below"], "above": result["above"], "exceedance_rate": result["exceedance_rate"],
        **width_summary(bounds),
        "supported_keys": len(bounds), "eligible_keys": eligible,
        "withheld_key_fraction": _rate(eligible - len(bounds), eligible),
        "records_total": result["records_total"], "records_without_range": result["records_without_range"],
        "records_without_cost_check_fraction": _rate(result["records_without_range"], result["records_total"]),
        "by_support_group": result["by_support_group"],
    }


def _method_config(config: CostTableConfig, method: str) -> CostTableConfig:
    """The comparison configuration: the frozen support threshold, conformal selected on validation."""
    return config.with_policy(method=method, conformal=None)


def _fit(partitions: Mapping[str, Sequence[PriceRecord]], config: CostTableConfig, method: str):
    method_config = _method_config(config, method)
    fits, policy, learned = fit_policy(partitions, method_config)
    bounds = served(fits, policy["min_support"], policy["offset"], config.places)
    return method_config, fits, policy, learned, bounds


def select_method(validation: Mapping[str, Mapping[str, Any]], band: tuple[Decimal, Decimal]) -> dict[str, Any]:
    """Apply ``SELECTION_RULE`` to validation summaries (method -> summary). Reads no test number."""
    low, high = band
    candidates = []
    for method in METHODS:
        summary = validation[method]
        evaluated = summary["evaluated_records"]
        coverage = None if not evaluated else Decimal(summary["covered"]) / Decimal(evaluated)
        width = summary["median_width"]
        candidates.append({"method": method, "validation_coverage": summary["coverage"],
                           "validation_median_width": width,
                           "qualifies": coverage is not None and width is not None and low <= coverage <= high})
    qualifying = [c for c in candidates if c["qualifies"]]
    if not qualifying:
        selected, reason = FALLBACK_METHOD, "no method's validation coverage is inside the target band"
    else:
        best = min(qualifying, key=lambda c: (Decimal(c["validation_median_width"]), c["method"] != FALLBACK_METHOD))
        selected = best["method"]
        tied = [c for c in qualifying if Decimal(c["validation_median_width"]) == Decimal(best["validation_median_width"])]
        reason = ("only qualifying method" if len(qualifying) == 1 else
                  "tie on median width; fallback kept" if len(tied) > 1 else "narrower validation median width")
    return {"rule": SELECTION_RULE, "selected_on": "validation",
            "target_coverage_band": [format(low, "f"), format(high, "f")], "candidates": candidates,
            "selected": selected, "reason": reason}


def _environment() -> dict[str, Any]:
    try:
        library_version = version("lightgbm")
    except PackageNotFoundError:
        library_version = None
    return {"python": platform.python_version(), "platform": platform.platform(), "lightgbm": library_version}


def _policy_note(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {"min_independent_base_cases": policy["min_support"], "conformal": policy["conformal"],
            "conformal_offset": None if policy["offset"] is None else format(policy["offset"], "f"),
            "conformal_validation_candidates": policy["conformal_selection"]["candidates"],
            "support_sweep_selected": policy["sweep"]["selected"]}


def compare_methods(records_path: Path, *, config: CostTableConfig) -> dict[str, Any]:
    """Both methods on one split: select on validation, then report the reserved test partition."""
    split = load_split(records_path, config)
    reservation = reserve_test_membership(split)
    fitted = {method: _fit(split.partitions, config, method) for method in METHODS}
    validation = {m: _summary(f[4], split.partitions["validation"], config) for m, f in fitted.items()}
    selection = select_method(validation, config.target_band)  # frozen before the test partition is read
    test = split.partitions["test"]
    if membership_hash(test) != reservation["membership_sha256"]:
        raise ValueError("test membership changed after it was reserved")
    methods = {}
    for method, (_, fits, policy, learned, bounds) in fitted.items():
        methods[method] = {
            "policy": _policy_note(policy), "validation": validation[method],
            "final_test": _summary(bounds, test, config) | {"evaluated_after_selection": True},
            "crossed_keys": [key_text(k) for k, f in sorted(fits.items()) if f.crossed],
            "model": None if learned is None else dict(learned.report)}
    low, high = config.target_band
    for entry in methods.values():
        coverage = entry["final_test"]["coverage"]
        entry["final_test"]["target_status"] = ("not_evaluated" if coverage is None else
                                                "met" if float(low) <= coverage <= float(high) else "unmet")
    per_key = {}
    for key in sorted(set().union(*(f[4] for f in fitted.values()))):
        per_key[key_text(key)] = {m: None if key not in f[4] else
                                  {"lower": format(f[4][key][0], "f"), "upper": format(f[4][key][1], "f"),
                                   "width": format(f[4][key][1] - f[4][key][0], "f"),
                                   "independent_base_case_count": f[4][key][2]} for m, f in fitted.items()}
    return {
        "experiment": "m7-method-comparison", "synthetic": True, "notice": SYNTHETIC_NOTICE,
        "config_version": config.config_version, "split_version": config.split.split_version,
        "split_config_hash": split.config_hash, "partitions": split.summary(),
        "test_membership_sha256": reservation["membership_sha256"],
        "nominal_coverage": format(config.nominal_coverage, "f"),
        "comparison_policy": "support threshold frozen in the configuration for both methods; conformal option "
                             "selected per method on validation; the same partitions for both",
        "selection": selection, "methods": methods, "ranges_by_key": per_key, "environment": _environment(),
        "limits": "Synthetic generator only. The selection uses validation numbers; test numbers are reported "
                  "after it and do not change it. Not evidence about real repair prices.",
    }


# ------------------------------------------------------------------ experiment C: injected anomalies

def read_injected(records_path: Path, config: CostTableConfig) -> list[dict[str, Any]]:
    """Labelled records of a separate injected-anomaly run, restricted to the frozen eligible grid."""
    eligible = set(config.grid.eligible_keys())
    records = []
    for raw in read_records(records_path):
        if raw.get("run_kind") != "injected_anomaly":
            raise ValueError("experiment C reads an injected-anomaly run; ordinary records carry no labels")
        key = (raw["part_code"], raw["operation"], raw["vehicle_class"], raw["currency"])
        if key not in eligible or raw["cost_basis"] != config.grid.cost_basis or raw["quantity"] != "1":
            continue
        records.append({"key": key, "amount": Decimal(raw["amount"]), "label": raw["anomaly_label"] == "true",
                        "magnitude": raw["anomaly_magnitude"] or None})
    return records


def score_injected(bounds: Bounds, injected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Flag = amount outside the served range. Labels come from the generator run, never from a range.

    A record whose key has no served range receives no cost check: it is neither a flag
    nor a pass, and is counted separately so withholding stays visible.
    """
    counts: dict[str, int] = defaultdict(int)
    by_magnitude: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in injected:
        labelled = record["label"]
        counts["labelled" if labelled else "unlabelled"] += 1
        magnitude = record["magnitude"] if labelled else None
        if labelled:
            by_magnitude[magnitude]["labelled"] += 1
        entry = bounds.get(record["key"])
        if entry is None:
            counts["labelled_without_range" if labelled else "unlabelled_without_range"] += 1
            continue
        flagged = not entry[0] <= record["amount"] <= entry[1]
        if labelled:
            by_magnitude[magnitude]["checked"] += 1
            by_magnitude[magnitude]["flagged"] += int(flagged)
            counts["true_positive" if flagged else "false_negative"] += 1
        else:
            counts["false_positive" if flagged else "true_negative"] += 1
    tp, fp, fn, tn = (counts[k] for k in ("true_positive", "false_positive", "false_negative", "true_negative"))
    total = counts["labelled"] + counts["unlabelled"]
    return {
        "records": total, "labelled_anomalies": counts["labelled"], "prevalence": _rate(counts["labelled"], total),
        "checked_records": tp + fp + fn + tn,
        "records_without_cost_check": counts["labelled_without_range"] + counts["unlabelled_without_range"],
        "labelled_without_cost_check": counts["labelled_without_range"],
        "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
        "precision": _rate(tp, tp + fp), "recall_among_checked": _rate(tp, tp + fn),
        "recall_among_all_labelled": _rate(tp, counts["labelled"]),
        "false_flag_rate_unlabelled": _rate(fp, fp + tn),
        "by_magnitude": [{"magnitude": m, "labelled": v["labelled"], "checked": v["checked"], "flagged": v["flagged"],
                          "recall_among_checked": _rate(v["flagged"], v["checked"])}
                         for m, v in sorted(by_magnitude.items(), key=lambda i: Decimal(i[0]))],
    }


def experiment_c(records_path: Path, injected_path: Path, *, config: CostTableConfig) -> dict[str, Any]:
    """Ordinary exceedance (test partition) and injected-anomaly detection, as separate tables."""
    split = load_split(records_path, config)
    injected = read_injected(injected_path, config)
    methods = {}
    for method in METHODS:
        _, _, policy, _, bounds = _fit(split.partitions, config, method)
        ordinary = _summary(bounds, split.partitions["test"], config)
        methods[method] = {
            "policy": _policy_note(policy),
            "ordinary_prices": {k: ordinary[k] for k in ("evaluated_records", "covered", "below", "above", "coverage",
                                                         "exceedance_rate", "records_without_range")}
                               | {"population": "reserved test partition of the ordinary run",
                                  "expectation": "about 0.10 exceedance for a calibrated 0.90 interval"},
            "injected_anomalies": score_injected(bounds, injected)
                                  | {"population": "separate injected run; labels set by the generator"}}
    return {
        "experiment": "experiment-c", "synthetic": True, "notice": SYNTHETIC_NOTICE,
        "config_version": config.config_version, "split_config_hash": split.config_hash,
        "nominal_coverage": format(config.nominal_coverage, "f"), "methods": methods,
        "environment": _environment(),
        "interpretation": "An ordinary exceedance is an unusual synthetic price, not an anomaly. Anomaly precision "
                          "and recall hold only at the stated prevalence and magnitudes of the injected run.",
    }


# ------------------------------------------------------------------ RQ4: fewer reference cases

def cap_train(train: Sequence[PriceRecord], cap: int | None, seed: int) -> list[PriceRecord]:
    """Keep at most ``cap`` independent base cases per key, chosen by a seeded hash order."""
    if cap is None:
        return list(train)
    cases: dict[CostKeyTuple, set[str]] = defaultdict(set)
    for record in train:
        cases[record.key].add(record.base_case_id)
    order = lambda case: hashlib.sha256(f"{seed}|{case}".encode()).hexdigest()
    kept = {case for key_cases in cases.values() for case in sorted(key_cases, key=order)[:cap]}
    return [r for r in train if r.base_case_id in kept]


def _view(fits, partitions, config: CostTableConfig, min_support: int, injected) -> dict[str, Any]:
    """One serving view: conformal fitted on calibration, selected on validation, then reported."""
    conformal = fit_conformal(fits, partitions["calibration"], min_support=min_support,
                              nominal=config.nominal_coverage)
    selected = conformal_selection(fits, partitions["validation"], conformal, min_support=min_support,
                                   config=config)["selected"]
    offset = Decimal(conformal["offset"]) if selected == "cqr" else None
    bounds = served(fits, min_support, offset, config.places)
    summary = _summary(bounds, partitions["test"], config)
    summary.pop("by_support_group")
    anomalies = None if injected is None else {
        k: v for k, v in score_injected(bounds, injected).items() if k != "by_magnitude"}
    return {"min_independent_base_cases": min_support, "conformal": selected,
            "conformal_offset": None if offset is None else format(offset, "f"),
            "final_test": summary, "injected_anomalies": anomalies}


def reduced_support(records_path: Path, *, config: CostTableConfig, injected_path: Path | None = None,
                    caps: Sequence[int | None] = DEFAULT_CAPS) -> dict[str, Any]:
    """RQ4: both methods with at most ``cap`` train base cases per key; other partitions fixed.

    Two views per step. ``served`` applies the frozen support threshold, which is what a
    published table does. ``model_only_not_served`` lowers the threshold to one base case
    to show what each method would produce for sparse keys; those ranges are never published.
    """
    split = load_split(records_path, config)
    injected = None if injected_path is None else read_injected(injected_path, config)
    steps = []
    for cap in caps:
        partitions = dict(split.partitions)
        partitions["train"] = tuple(cap_train(split.partitions["train"], cap, config.split.seed))
        cases = {r.base_case_id for r in partitions["train"]}
        step = {"cap_base_cases_per_key": cap, "train_base_cases": len(cases),
                "train_records": len(partitions["train"]), "methods": {}}
        for method in METHODS:
            method_config = _method_config(config, method)
            fits, policy, _ = fit_policy(partitions, method_config)
            step["methods"][method] = {
                "served": _view(fits, partitions, method_config, policy["min_support"], injected),
                "model_only_not_served": _view(fits, partitions, method_config, 1, injected)}
        steps.append(step)
    return {
        "experiment": "rq4-reduced-support", "synthetic": True, "notice": SYNTHETIC_NOTICE,
        "config_version": config.config_version, "split_config_hash": split.config_hash,
        "partitions_fixed": {p: split.summary()[p] for p in ("validation", "calibration", "test")},
        "caps": list(caps), "cap_rule": "per key, keep the first N base cases in a seeded hash order",
        "views": {"served": "frozen support threshold, as published",
                  "model_only_not_served": "support threshold of one base case; diagnostic only, never published"},
        "steps": steps, "environment": _environment(),
        "limits": "Synthetic generator only. The reserved test partition is read at every step to report, never "
                  "to select a policy. The injected set has few records per key; small counts are shown as counts.",
    }


__all__ = ["DEFAULT_CAPS", "SELECTION_RULE", "cap_train", "compare_methods", "experiment_c", "read_injected",
           "reduced_support", "score_injected", "select_method"]
