"""Seeded synthetic price generator (M7 build task 1, training specification section 9.3).

A base case is one independent repair situation; it yields 1-5 correlated quotes from
distinct workshops. Money is exact: random multipliers are rounded to ``Decimal`` before
they touch an amount. Ordinary prices and injected anomalies are separate runs with
separate seeds, base cases and manifests. Side, damage type and model year never enter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any, Sequence

from .config import CENT, GeneratorConfig, canonical_hash
from .records import write_records
from .vocabulary import COST_BASIS, SYNTHETIC_NOTICE, CostKeyTuple, key_text

PRICES_FILE = "prices.csv"
GENERATOR_MANIFEST = "generator_manifest.json"


class _Rng:
    """Draws built only on ``random.Random.random()``, whose sequence Python keeps stable."""

    def __init__(self, seed: int):
        self._random = random.Random(seed)

    def uniform(self) -> float:
        return self._random.random()

    def integer(self, low: int, high: int) -> int:
        return low + min(int(self.uniform() * (high - low + 1)), high - low)

    def normal(self) -> float:
        u1, u2 = 1.0 - self.uniform(), self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def shuffle(self, items: list) -> None:
        for i in range(len(items) - 1, 0, -1):
            j = self.integer(0, i)
            items[i], items[j] = items[j], items[i]

    def sample(self, items: Sequence, k: int) -> list:
        pool = list(items)
        self.shuffle(pool)
        return pool[:k]


def _factor(rng: _Rng, sigma: Decimal, places: int) -> Decimal:
    """Lognormal multiplier, rounded to a fixed-place Decimal before any money arithmetic."""
    draw = rng.normal()
    return Decimal(1) if sigma == 0 else Decimal(f"{math.exp(float(sigma) * draw):.{places}f}")


@dataclass(frozen=True)
class GeneratorManifest:
    records_path: Path
    manifest_path: Path
    data: dict[str, Any]

    @property
    def run_kind(self) -> str:
        return self.data["run_kind"]


def _plan(config: GeneratorConfig, rng: _Rng) -> tuple[dict[CostKeyTuple, int], list, list]:
    keys = config.grid.eligible_keys()
    order = list(keys)
    rng.shuffle(order)
    n_empty = int((config.empty_key_fraction * len(keys)).to_integral_value(ROUND_HALF_UP))
    n_sparse = int((config.sparse_key_fraction * len(keys)).to_integral_value(ROUND_HALF_UP))
    empty, sparse = set(order[:n_empty]), set(order[n_empty:n_empty + n_sparse])
    counts = {k: 0 if k in empty else rng.integer(config.sparse_min, config.sparse_max) if k in sparse else None
              for k in keys}
    ordinary = [k for k in keys if counts[k] is None]
    mean_quotes = Decimal(config.quotes_min + config.quotes_max) / 2
    budget = Decimal(config.total_records) / mean_quotes - sum(c for c in counts.values() if c)
    mean_cases = budget / len(ordinary)
    for key in ordinary:
        scale = 1 - config.spread + 2 * config.spread * Decimal(f"{rng.uniform():.6f}")
        counts[key] = max(1, int((mean_cases * scale).to_integral_value(ROUND_HALF_UP)))
    ordered = lambda ks: sorted(ks, key=keys.index)
    return counts, ordered(sparse), ordered(empty)


def _row(config: GeneratorConfig, run_kind: str, seed: int, record_id: str, base_case_id: str, workshop: str,
         key: CostKeyTuple, amount: Decimal, day, label: str = "", magnitude: str = "") -> dict[str, str]:
    part, operation, vehicle_class, currency = key
    return {"record_id": record_id, "base_case_id": base_case_id, "workshop_id": workshop, "part_code": part,
            "operation": operation, "vehicle_class": vehicle_class, "currency": currency,
            "cost_basis": config.grid.cost_basis, "quantity": "1", "amount": format(amount, "f"),
            "synthetic_date": day.isoformat(), "generator_version": config.generator_version, "seed": str(seed),
            "run_kind": run_kind, "source_kind": "synthetic", "anomaly_label": label, "anomaly_magnitude": magnitude}


def _cases(config: GeneratorConfig, rng: _Rng, run_kind: str, seed: int, counts: dict[CostKeyTuple, int],
           quotes: tuple[int, int]) -> tuple[list[list[dict[str, str]]], dict[str, str]]:
    """Base cases (each a list of quote rows) for every key, in grid order."""
    tag = hashlib.sha256(f"{config.generator_version}|{seed}|{run_kind}".encode()).hexdigest()[:6]
    workshops = [f"ws-{i:02d}" for i in range(1, config.workshop_count + 1)]
    factors = {w: _factor(rng, config.workshop_sigma, config.factor_places) for w in workshops}
    span = (config.date_end - config.date_start).days
    cases, record_index, case_index = [], 0, 0
    for key in config.grid.eligible_keys():
        for _ in range(counts.get(key, 0)):
            case_index += 1
            base_case_id = f"bc-{tag}-{case_index:05d}"
            day = config.date_start + timedelta(days=rng.integer(0, span))
            case_factor = _factor(rng, config.base_case_sigma, config.factor_places)
            rows = []
            for workshop in rng.sample(workshops, rng.integer(*quotes)):
                record_index += 1
                noise = _factor(rng, config.quote_noise_sigma, config.factor_places)
                amount = config.base_prices[key] * case_factor * factors[workshop] * noise
                amount = amount.quantize(CENT, ROUND_HALF_UP)
                rows.append(_row(config, run_kind, seed, f"pr-{tag}-{record_index:06d}", base_case_id, workshop,
                                 key, amount, day))
            cases.append(rows)
    return cases, {w: str(f) for w, f in factors.items()}


def _manifest(config: GeneratorConfig, run_kind: str, seed: int, rows: list[dict[str, str]],
              factors: dict[str, str], sha256: str, extra: dict[str, Any]) -> dict[str, Any]:
    per_key_cases: dict[str, set] = {key_text(k): set() for k in config.grid.eligible_keys()}
    per_key_records = dict.fromkeys(per_key_cases, 0)
    for row in rows:
        text = key_text((row["part_code"], row["operation"], row["vehicle_class"], row["currency"]))
        per_key_cases[text].add(row["base_case_id"])
        per_key_records[text] += 1
    return {
        "generator_version": config.generator_version, "run_kind": run_kind, "seed": seed, "synthetic": True,
        "provenance": {"source_kind": "synthetic"}, "notice": SYNTHETIC_NOTICE,
        "settings": config.describe() | {"seed": seed},
        "config_hash": canonical_hash(config.source), "eligible_keys_version": config.grid.version,
        "base_prices_version": config.source["base_prices"]["version"],
        "currency": config.grid.currency, "cost_basis": COST_BASIS, "cost_basis_version": COST_BASIS,
        "cost_key_fields": ["part_code", "operation", "vehicle_class", "currency"],
        "excluded_from_generation": ["model_year", "side", "damage_type"],
        "eligible_key_list": list(per_key_cases),
        "base_price_table": {key_text(k): format(v, "f") for k, v in sorted(config.base_prices.items())},
        "record_count": len(rows), "base_case_count": len({r["base_case_id"] for r in rows}),
        "base_case_count_per_key": {k: len(v) for k, v in per_key_cases.items()},
        "record_count_per_key": per_key_records,
        "workshop_factors": factors,
        "workshop_factor_range": [min(factors.values(), key=Decimal), max(factors.values(), key=Decimal)],
        "noise_model": config.describe()["noise_model"],
        "cutoff_date": None, "cutoff_date_reason": "applied by the split configuration at build time",
        "output": {"file": PRICES_FILE, "sha256": sha256},
    } | extra


def _write(out_dir: Path, rows: list[dict[str, str]], build_manifest) -> GeneratorManifest:
    out_dir = Path(out_dir)
    records_path = out_dir / PRICES_FILE
    sha256 = write_records(records_path, rows)
    data = build_manifest(sha256)
    manifest_path = out_dir / GENERATOR_MANIFEST
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return GeneratorManifest(records_path, manifest_path, data)


def generate_prices(*, config: GeneratorConfig, out_dir: Path) -> GeneratorManifest:
    """Ordinary synthetic prices with the recorded sparsity plan; writes prices.csv and its manifest."""
    rng = _Rng(config.seed)
    counts, sparse, empty = _plan(config, rng)
    cases, factors = _cases(config, rng, "ordinary", config.seed, counts, (config.quotes_min, config.quotes_max))
    rows = [row for case in cases for row in case]
    extra = {"sparse_keys": [key_text(k) for k in sparse], "empty_keys": [key_text(k) for k in empty],
             "sparsity_plan": {"sparse_key_count": len(sparse), "empty_key_count": len(empty),
                               "note": "sparse keys receive fewer base cases than the support threshold"}}
    return _write(out_dir, rows, lambda sha: _manifest(config, "ordinary", config.seed, rows, factors, sha, extra))


def generate_injected_anomalies(*, config: GeneratorConfig, out_dir: Path) -> GeneratorManifest:
    """Separate labelled run for experiment C: new base cases, a stated prevalence and magnitudes.

    Labels come from this run, never from an interval. A reference build refuses this file.
    """
    injected = config.injected
    seed = config.seed + injected.seed_offset
    rng = _Rng(seed)
    counts = dict.fromkeys(config.grid.eligible_keys(), injected.base_cases_per_key)
    quotes = (injected.quotes_per_base_case, injected.quotes_per_base_case)
    cases, factors = _cases(config, rng, "injected_anomaly", seed, counts, quotes)
    n_labelled = int((injected.prevalence * len(cases)).to_integral_value(ROUND_HALF_UP))
    labelled = sorted(rng.sample(range(len(cases)), n_labelled))
    by_magnitude: dict[str, int] = {}
    for position, index in enumerate(labelled):
        magnitude = injected.magnitudes[position % len(injected.magnitudes)]
        by_magnitude[str(magnitude)] = by_magnitude.get(str(magnitude), 0) + 1
        for row in cases[index]:
            changed = (Decimal(row["amount"]) * (1 + magnitude)).quantize(CENT, ROUND_HALF_UP)
            row.update(amount=format(changed, "f"), anomaly_label="true", anomaly_magnitude=str(magnitude))
    rows = [row for case in cases for row in case]
    for row in rows:
        row["anomaly_label"] = row["anomaly_label"] or "false"
    extra = {"ordinary_run_seed": config.seed, "prevalence": str(injected.prevalence),
             "magnitudes": [str(m) for m in injected.magnitudes], "labelled_base_case_count": n_labelled,
             "labelled_by_magnitude": by_magnitude,
             "label_rule": "labels are set by this run at the stated prevalence, never by a predicted interval",
             "use": "experiment C only; never a reference-build input"}
    return _write(out_dir, rows, lambda sha: _manifest(config, "injected_anomaly", seed, rows, factors, sha, extra))
