"""Empirical percentile ranges, support counts, the support sweep, conformal calibration and metrics.

Per key, each base case is first reduced to the median of its quotes, so repeated quotes
cannot dominate; the 5th and 95th percentiles (linear interpolation, type 7) of those
per-base-case values give the interval. Support is the number of distinct base cases.
All arithmetic is ``Decimal``; bounds are rounded once, ROUND_HALF_UP, at publication.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
import hashlib
from typing import Iterable, Mapping, Sequence

from claim_cmev.contracts.common import SCHEMA_VERSION

from .config import CostTableConfig, Grid
from .records import PriceRecord
from .vocabulary import INSUFFICIENT_SUPPORT, NO_RECORDS, UNSUPPORTED_COMBINATION, CostKeyTuple, key_text

Bounds = Mapping[CostKeyTuple, tuple[Decimal, Decimal, int]]


def median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def percentile(sorted_values: Sequence[Decimal], q: Decimal) -> Decimal:
    """Linear-interpolation percentile (Hyndman-Fan type 7) in exact Decimal arithmetic."""
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    h = (len(sorted_values) - 1) * q
    low = int(h)
    if low + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[low] + (h - low) * (sorted_values[low + 1] - sorted_values[low])


def quantize(value: Decimal, places: int = 2) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)


@dataclass(frozen=True)
class KeyFit:
    """Unrounded percentile bounds from train; rounding happens once, at publication."""

    key: CostKeyTuple
    lower_raw: Decimal
    upper_raw: Decimal
    independent_base_case_count: int
    record_count: int

    def bounds(self, offset: Decimal | None = None, places: int = 2) -> tuple[Decimal, Decimal]:
        """Published bounds, optionally widened by a log-scale conformal offset, rounded once."""
        lower, upper = self.lower_raw, self.upper_raw
        if offset is not None:
            lower, upper = lower * (-offset).exp(), upper * offset.exp()
        return quantize(lower, places), quantize(upper, places)


def base_case_values(records: Iterable[PriceRecord]) -> dict[CostKeyTuple, dict[str, Decimal]]:
    """One median value per base case per key: the unit of independent support."""
    quotes: dict[CostKeyTuple, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        quotes[record.key][record.base_case_id].append(record.amount)
    return {key: {case: median(v) for case, v in cases.items()} for key, cases in quotes.items()}


def fit_empirical(train: Sequence[PriceRecord], *, quantiles: tuple[Decimal, Decimal]) -> dict[CostKeyTuple, KeyFit]:
    """Fit on the train partition only."""
    record_counts: dict[CostKeyTuple, int] = defaultdict(int)
    for record in train:
        record_counts[record.key] += 1
    fits = {}
    for key, cases in base_case_values(train).items():
        values = sorted(cases.values())
        fits[key] = KeyFit(key, percentile(values, quantiles[0]), percentile(values, quantiles[1]), len(cases),
                           record_counts[key])
    return fits


def served(fits: Mapping[CostKeyTuple, KeyFit], min_support: int, offset: Decimal | None = None,
           places: int = 2) -> dict[CostKeyTuple, tuple[Decimal, Decimal, int]]:
    """The rounded bounds that would be published for keys meeting the support minimum."""
    return {k: (*f.bounds(offset, places), f.independent_base_case_count) for k, f in fits.items()
            if f.independent_base_case_count >= min_support}


def support_group(count: int, edges: Sequence[int]) -> str:
    if count < edges[0]:
        return f"<{edges[0]}"
    for low, high in zip(edges, edges[1:]):
        if low <= count < high:
            return f"{low}-{high - 1}"
    return f"{edges[-1]}+"


def coverage_by_key(bounds: Bounds, records: Sequence[PriceRecord]) -> dict[str, dict]:
    """Per-key record coverage with counts, for keys that have a served range."""
    tallies: dict[CostKeyTuple, _Tally] = defaultdict(_Tally)
    for record in records:
        if record.key in bounds:
            tallies[record.key].add(record.amount, *bounds[record.key][:2])
    return {key_text(k): tallies[k].report() for k in sorted(bounds)}


def _order(group: str) -> int:
    return -1 if group.startswith("<") else int(group.split("-")[0].rstrip("+"))


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


@dataclass
class _Tally:
    evaluated: int = 0
    covered: int = 0
    below: int = 0
    above: int = 0

    def add(self, amount: Decimal, lower: Decimal, upper: Decimal) -> None:
        self.evaluated += 1
        if amount < lower:
            self.below += 1
        elif amount > upper:
            self.above += 1
        else:
            self.covered += 1

    def report(self) -> dict:
        return {"evaluated_records": self.evaluated, "covered": self.covered, "below": self.below,
                "above": self.above, "coverage": _rate(self.covered, self.evaluated),
                "exceedance_rate": _rate(self.below + self.above, self.evaluated)}


def evaluate_bounds(bounds: Bounds, records: Sequence[PriceRecord], edges: Sequence[int]) -> dict:
    """Record-level coverage of served bounds; equality at a bound is inside the range."""
    total, groups, withheld = _Tally(), defaultdict(_Tally), 0
    cases, keys = set(), set()
    for record in records:
        entry = bounds.get(record.key)
        if entry is None:
            withheld += 1
            continue
        lower, upper, support = entry
        total.add(record.amount, lower, upper)
        groups[support_group(support, edges)].add(record.amount, lower, upper)
        cases.add(record.base_case_id)
        keys.add(record.key)
    return total.report() | {
        "records_total": len(records), "records_without_range": withheld,
        "available_range_rate_records": _rate(len(records) - withheld, len(records)),
        "evaluated_base_cases": len(cases), "evaluated_keys": len(keys),
        "by_support_group": [{"group": g, **t.report()} for g, t in sorted(groups.items(), key=lambda i: _order(i[0]))],
    }


def _widths(bounds: Bounds) -> dict:
    widths = [upper - lower for lower, upper, _ in bounds.values()]
    if not widths:
        return {"mean_width": None, "median_width": None, "zero_width_keys": 0}
    return {"mean_width": format(quantize(sum(widths) / len(widths)), "f"),
            "median_width": format(quantize(median(widths)), "f"),
            "zero_width_keys": sum(1 for w in widths if w == 0)}


def _closeness(result: Mapping, nominal: Decimal) -> Decimal:
    return abs(Decimal(result["covered"]) / Decimal(result["evaluated_records"]) - nominal)


def support_sweep(fits: Mapping[CostKeyTuple, KeyFit], validation: Sequence[PriceRecord], *,
                  config: CostTableConfig, eligible_key_count: int) -> dict:
    """Search min_independent_base_cases on the validation partition only (uncalibrated bounds)."""
    candidates = []
    for threshold in config.sweep_candidates:
        bounds = served(fits, threshold, None, config.places)
        result = evaluate_bounds(bounds, validation, config.support_group_edges)
        candidates.append({
            "min_independent_base_cases": threshold, "supported_keys": len(bounds),
            "eligible_keys": eligible_key_count,
            "withheld_key_fraction": _rate(eligible_key_count - len(bounds), eligible_key_count),
            "selectable": result["evaluated_records"] >= config.sweep_min_evaluated, **_widths(bounds),
            **{k: result[k] for k in ("evaluated_records", "covered", "coverage", "records_total",
                                      "available_range_rate_records")},
        })
    nominal = config.nominal_coverage
    selectable = [c for c in candidates if c["selectable"]]
    selected = min(selectable, key=lambda c: (_closeness(c, nominal), -c["min_independent_base_cases"]), default=None)
    return {"synthetic": True, "partition": "validation", "selected_on": "validation",
            "criterion": "validation coverage closest to nominal; ties go to the larger threshold",
            "nominal_coverage": str(nominal), "min_evaluated_records": config.sweep_min_evaluated,
            "candidates": candidates,
            "selected": None if selected is None else selected["min_independent_base_cases"]}


def fit_conformal(fits: Mapping[CostKeyTuple, KeyFit], calibration: Sequence[PriceRecord], *, min_support: int,
                  nominal: Decimal) -> dict:
    """Split-conformal (CQR-style) offset fitted on the calibration partition only.

    One group: every supported key. The score per calibration record is
    ``max(ln(L/y), ln(y/U))`` on unrounded train bounds; the offset Q is the
    ceil((n+1)(1-alpha))-th smallest score, and bounds become ``[L*exp(-Q), U*exp(Q)]``,
    which stays positive and scale-free across cheap and expensive keys.
    """
    scores = []
    for record in calibration:
        fit = fits.get(record.key)
        if fit is None or fit.independent_base_case_count < min_support:
            continue
        scores.append(max((fit.lower_raw / record.amount).ln(), (record.amount / fit.upper_raw).ln()))
    scores.sort()
    n = len(scores)
    rank = int(((n + 1) * nominal).to_integral_value(ROUND_CEILING))  # ceil((n + 1)(1 - alpha))
    base = {"method": "cqr_log_ratio", "partition": "calibration", "scores": n, "alpha": format(1 - nominal, "f"),
            "group_definition": "one group: all keys supported at the frozen minimum support",
            "score": "max(ln(L/y), ln(y/U)) per calibration record on unrounded train bounds",
            "adjustment": "[L*exp(-Q), U*exp(Q)], rounded once at publication"}
    if n == 0 or rank > n:
        return base | {"offset": None, "reason": "too few calibration records for a finite conformal offset"}
    return base | {"offset": format(scores[rank - 1].quantize(Decimal("0.000001"), ROUND_HALF_UP), "f"),
                   "rank": rank}


def conformal_selection(fits: Mapping[CostKeyTuple, KeyFit], validation: Sequence[PriceRecord], conformal: dict, *,
                        min_support: int, config: CostTableConfig) -> dict:
    """Compare no adjustment with the calibration-fitted offset on validation; pick closest to nominal."""
    options: dict[str, Decimal | None] = {"none": None}
    if conformal.get("offset") is not None:
        options["cqr"] = Decimal(conformal["offset"])
    candidates = []
    for name, offset in options.items():
        bounds = served(fits, min_support, offset, config.places)
        result = evaluate_bounds(bounds, validation, config.support_group_edges)
        candidates.append({"conformal": name, "offset": None if offset is None else format(offset, "f"),
                           **_widths(bounds), **{k: result[k] for k in ("evaluated_records", "covered", "coverage")}})
    usable = [c for c in candidates if c["evaluated_records"]]
    best = min(usable, key=lambda c: (_closeness(c, config.nominal_coverage), c["conformal"] != "none"), default=None)
    return {"synthetic": True, "partition": "validation", "selected_on": "validation",
            "criterion": "validation coverage closest to nominal; ties keep none", "candidates": candidates,
            "selected": "none" if best is None else best["conformal"]}


def range_id_for(key: CostKeyTuple, cost_basis: str) -> str:
    return "rng-" + hashlib.sha256(f"{key_text(key)}|{cost_basis}".encode()).hexdigest()[:16]


def assemble_rows(fits: Mapping[CostKeyTuple, KeyFit], grid: Grid, *, keys_with_records: set[CostKeyTuple],
                  min_support: int, offset: Decimal | None, table_version: str, config: CostTableConfig,
                  as_of_date: date, provenance: Mapping[str, object], validation: Sequence[PriceRecord]) -> list[dict]:
    """One row per key in the frozen grid, supported and withheld alike. Withheld bounds are null."""
    bounds = served(fits, min_support, offset, config.places)
    tallies: dict[CostKeyTuple, _Tally] = defaultdict(_Tally)
    for record in validation:
        if record.key in bounds:
            tallies[record.key].add(record.amount, *bounds[record.key][:2])
    rows = []
    for key in grid.keys():
        fit = fits.get(key)
        if not grid.is_eligible(key):
            reason = UNSUPPORTED_COMBINATION
        elif key not in keys_with_records:
            reason = NO_RECORDS
        elif key not in bounds:
            reason = INSUFFICIENT_SUPPORT
        else:
            reason = None
        tally = tallies[key]
        rows.append({
            "schema_version": SCHEMA_VERSION, "range_id": range_id_for(key, grid.cost_basis),
            "part_code": key[0], "operation": key[1], "vehicle_class": key[2], "currency": key[3],
            "cost_basis": grid.cost_basis, "support_status": "withheld" if reason else "supported",
            "lower_amount": None if reason else format(bounds[key][0], "f"),
            "upper_amount": None if reason else format(bounds[key][1], "f"),
            "independent_base_case_count": fit.independent_base_case_count if fit else 0,
            "record_count": fit.record_count if fit else 0,
            "withheld_reason": reason, "method": config.method,
            "nominal_coverage": format(config.nominal_coverage, "f"),
            "observed_calibration": None if reason else {
                "coverage": _rate(tally.covered, tally.evaluated),
                "mean_width": format(bounds[key][1] - bounds[key][0], "f"),
                "report_ref": f"metrics.json#validation.by_key.{key_text(key)}"},
            "as_of_date": as_of_date.isoformat(), "cutoff_date": config.split.cutoff_date.isoformat(),
            "table_version": table_version, "synthetic": True, "provenance": dict(provenance),
        })
    return rows


def served_bounds(rows: Iterable[Mapping]) -> dict[CostKeyTuple, tuple[Decimal, Decimal, int]]:
    """Exactly the bounds a published table serves, read back from its rows."""
    return {(r["part_code"], r["operation"], r["vehicle_class"], r["currency"]):
            (Decimal(r["lower_amount"]), Decimal(r["upper_amount"]), r["independent_base_case_count"])
            for r in rows if r["support_status"] == "supported"}


def final_test_metrics(rows: Sequence[Mapping], test: Sequence[PriceRecord], *, config: CostTableConfig,
                       reservation: Mapping) -> dict:
    """Coverage, width, availability and withholding on the untouched test partition, run once."""
    bounds = served_bounds(rows)
    result = evaluate_bounds(bounds, test, config.support_group_edges)
    coverage = None if not result["evaluated_records"] else \
        Decimal(result["covered"]) / Decimal(result["evaluated_records"])
    low, high = config.target_band
    eligible = [r for r in rows if r["withheld_reason"] != UNSUPPORTED_COMBINATION]
    withheld_by_reason: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["withheld_reason"]:
            withheld_by_reason[row["withheld_reason"]] += 1
    widths = {key_text(k): {"lower": format(lo, "f"), "upper": format(up, "f"), "width": format(up - lo, "f"),
                            "independent_base_case_count": n} for k, (lo, up, n) in sorted(bounds.items())}
    return {
        "synthetic": True, "partition": "test", "evaluated_once": True,
        "test_membership_sha256": reservation["membership_sha256"],
        "nominal_coverage": format(config.nominal_coverage, "f"),
        "target_coverage_band": [format(low, "f"), format(high, "f")],
        "target_status": "not_evaluated" if coverage is None else "met" if low <= coverage <= high else "unmet",
        "coverage": {k: result[k] for k in ("evaluated_records", "covered", "below", "above", "coverage",
                                            "exceedance_rate", "evaluated_base_cases", "evaluated_keys")},
        "by_support_group": result["by_support_group"],
        "available_range_rate": {
            "keys": {"supported": len(bounds), "eligible": len(eligible), "grid": len(rows),
                     "rate_of_eligible": _rate(len(bounds), len(eligible))},
            "test_records": {"with_range": result["records_total"] - result["records_without_range"],
                             "total": result["records_total"], "rate": result["available_range_rate_records"]}},
        "withheld_keys_by_reason": dict(sorted(withheld_by_reason.items())),
        "width": _widths(bounds), "width_by_key": widths,
        "interpretation": "An exceedance is an unusual synthetic price, not proof of an incorrect price or fraud. "
                          "Ordinary-price exceedance here is not the injected-anomaly experiment.",
    }
