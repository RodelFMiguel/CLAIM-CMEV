"""Run the M7 experiments on synthetic records and write their reports. Offline only.

    python pipelines/costs/eval_cost_table.py compare      --seed 20260924 --out artifacts/evaluation/m7-method-comparison
    python pipelines/costs/eval_cost_table.py rq4          --seed 20260924 --out artifacts/evaluation/rq4-reduced-support
    python pipelines/costs/eval_cost_table.py experiment-c --seed 20260924 --out artifacts/evaluation/experiment-c

Each run regenerates its inputs from the seed under ``<out>/inputs/`` and writes
``report.json`` and ``report.md``. Nothing is published and no table changes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from claim_cmev.costs.reference.config import DEFAULT_CONFIG_DIR, load_cost_table_config, load_generator_config
from claim_cmev.costs.reference.evaluation import compare_methods, experiment_c, reduced_support
from claim_cmev.costs.reference.generator import generate_injected_anomalies, generate_prices
from claim_cmev.costs.reference.vocabulary import METHODS

LABELS = {"empirical_percentile": "Empirical percentiles", "lightgbm_quantile": "LightGBM quantiles"}


def _cell(value: Any) -> str:
    return "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _counted(summary: dict) -> str:
    return f"{_cell(summary['coverage'])} ({summary['covered']} of {summary['evaluated_records']})"


def render_compare(report: dict) -> str:
    selection, methods = report["selection"], report["methods"]
    rows = []
    for partition in ("validation", "final_test"):
        for method in METHODS:
            s = methods[method][partition]
            rows.append([partition.replace("_", " "), LABELS[method], _counted(s), s["median_width"], s["mean_width"],
                         f"{s['supported_keys']} of {s['eligible_keys']}",
                         _cell(s["records_without_cost_check_fraction"])])
    policy = [[LABELS[m], methods[m]["policy"]["min_independent_base_cases"], methods[m]["policy"]["conformal"],
               methods[m]["policy"]["conformal_offset"], len(methods[m]["crossed_keys"])] for m in METHODS]
    model = methods["lightgbm_quantile"]["model"]["models"]
    return "\n\n".join([
        "# M7 method comparison (synthetic prices)",
        report["notice"],
        f"Configuration `{report['config_version']}`, split `{report['split_version']}`. Nominal coverage "
        f"{report['nominal_coverage']}, target band {' to '.join(selection['target_coverage_band'])}.",
        "## Selection (validation only)",
        f"Rule: {selection['rule']}",
        f"Selected: **{selection['selected']}** ({selection['reason']}).",
        _table(["Method", "Validation coverage", "Validation median width", "Qualifies"],
               [[LABELS[c["method"]], c["validation_coverage"], c["validation_median_width"], c["qualifies"]]
                for c in selection["candidates"]]),
        "## Results",
        "Test numbers were read after the selection and did not change it.",
        _table(["Partition", "Method", "Coverage (covered of evaluated)", "Median width", "Mean width",
                "Keys with a range", "Records with no cost check"], rows),
        "## Policy per method",
        _table(["Method", "Minimum support", "Conformal", "Offset (log scale)", "Crossed keys"], policy),
        "## LightGBM fit",
        _table(["Bound", "Quantile", "Trees used", "Train pinball loss", "Validation pinball loss"],
               [[name, m["quantile"], m["trees_used"], m["train_pinball_loss"], m["validation_pinball_loss"]]
                for name, m in model.items()]),
        f"Limits: {report['limits']}",
    ]) + "\n"


def render_experiment_c(report: dict) -> str:
    methods = report["methods"]
    ordinary = [[LABELS[m], _counted(methods[m]["ordinary_prices"]), methods[m]["ordinary_prices"]["below"],
                 methods[m]["ordinary_prices"]["above"], _cell(methods[m]["ordinary_prices"]["exceedance_rate"])]
                for m in METHODS]
    injected = []
    for m in METHODS:
        a = methods[m]["injected_anomalies"]
        injected.append([LABELS[m], a["labelled_anomalies"], a["labelled_without_cost_check"], a["true_positive"],
                         a["false_negative"], a["false_positive"], _cell(a["precision"]),
                         _cell(a["recall_among_checked"]), _cell(a["recall_among_all_labelled"])])
    magnitude = [[LABELS[m], row["magnitude"], row["labelled"], row["checked"], row["flagged"],
                  _cell(row["recall_among_checked"])]
                 for m in METHODS for row in methods[m]["injected_anomalies"]["by_magnitude"]]
    first = methods[METHODS[0]]["injected_anomalies"]
    return "\n\n".join([
        "# Experiment C: ordinary exceedance and injected anomalies (synthetic prices)",
        report["notice"],
        "## Ordinary prices: reserved test partition",
        "A calibrated 0.90 interval leaves about 10 percent of ordinary prices outside it. These are not anomalies.",
        _table(["Method", "Coverage (covered of evaluated)", "Below", "Above", "Exceedance rate"], ordinary),
        "## Injected anomalies: separate labelled run",
        f"{first['records']} records, {first['labelled_anomalies']} labelled (prevalence "
        f"{_cell(first['prevalence'])}). A record on a key with no served range gets no cost check.",
        _table(["Method", "Labelled", "Labelled with no cost check", "Flagged (TP)", "Missed (FN)",
                "False flags (FP)", "Precision", "Recall among checked", "Recall among all labelled"], injected),
        "## Recall by injected magnitude",
        _table(["Method", "Magnitude", "Labelled", "Checked", "Flagged", "Recall among checked"], magnitude),
        report["interpretation"],
    ]) + "\n"


def render_rq4(report: dict) -> str:
    sections = ["# RQ4: fewer independent reference cases (synthetic prices)", report["notice"],
                f"Cap rule: {report['cap_rule']}. Validation, calibration and test partitions are fixed."]
    for view, title in (("served", "Served ranges (frozen support threshold, as published)"),
                        ("model_only_not_served", "Model only, not served (support threshold of one base case)")):
        rows = []
        for step in report["steps"]:
            for method in METHODS:
                v = step["methods"][method][view]
                t, a = v["final_test"], v["injected_anomalies"]
                rows.append(["all" if step["cap_base_cases_per_key"] is None else step["cap_base_cases_per_key"],
                             step["train_base_cases"], LABELS[method], _counted(t) if t["evaluated_records"] else "n/a",
                             t["median_width"], _cell(t["withheld_key_fraction"]),
                             _cell(t["records_without_cost_check_fraction"]), v["conformal"],
                             _cell(a["precision"]) if a else "n/a", _cell(a["recall_among_checked"]) if a else "n/a",
                             _cell(a["recall_among_all_labelled"]) if a else "n/a"])
        sections += [f"## {title}", _table(
            ["Cap per key", "Train base cases", "Method", "Test coverage (covered of evaluated)", "Median width",
             "Keys withheld", "Test records with no cost check", "Conformal", "Anomaly precision",
             "Anomaly recall (checked)", "Anomaly recall (all labelled)"], rows)]
    sections.append(f"Limits: {report['limits']}")
    return "\n\n".join(sections) + "\n"


RENDERERS = {"compare": render_compare, "rq4": render_rq4, "experiment-c": render_experiment_c}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("experiment", choices=sorted(RENDERERS))
    parser.add_argument("--out", type=Path, required=True, help="report directory, e.g. artifacts/evaluation/<name>")
    parser.add_argument("--seed", type=int, default=None, help="generator seed (default: configs/costs/generator.yaml)")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    args = parser.parse_args(argv)
    generator_config = load_generator_config(args.config_dir, seed=args.seed)
    inputs = args.out / "inputs" / generator_config.generator_version / f"seed-{generator_config.seed}"
    ordinary = generate_prices(config=generator_config, out_dir=inputs / "ordinary")
    injected = generate_injected_anomalies(config=generator_config, out_dir=inputs / "injected_anomaly")
    config = load_cost_table_config(args.config_dir)
    if args.experiment == "compare":
        report = compare_methods(ordinary.records_path, config=config)
    elif args.experiment == "rq4":
        report = reduced_support(ordinary.records_path, config=config, injected_path=injected.records_path)
    else:
        report = experiment_c(ordinary.records_path, injected.records_path, config=config)
    report["inputs"] = {"generator_version": generator_config.generator_version, "seed": generator_config.seed,
                        "ordinary_sha256": ordinary.data["output"]["sha256"],
                        "injected_sha256": injected.data["output"]["sha256"],
                        "injected_seed": injected.data["seed"]}
    (args.out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
                                          encoding="utf-8")
    (args.out / "report.md").write_text(RENDERERS[args.experiment](report), encoding="utf-8")
    print(json.dumps({"experiment": args.experiment, "report": str(args.out / "report.md")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
