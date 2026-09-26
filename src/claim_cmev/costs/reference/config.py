"""Load, check and hash the versioned M7 configuration under ``configs/costs/``.

Decimal settings are read from quoted strings; a float in a money or ratio setting is
refused. Every file is snapshotted into the build so a table records exactly what it used.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from claim_cmev import taxonomy

from .vocabulary import (
    COST_BASIS, COST_KEY_FIELDS, COST_OPERATIONS, CURRENCY, FORBIDDEN_KEY_FIELDS, METHODS, NON_COST_PARTS,
    PART_CODES, PARTITIONS, VEHICLE_CLASSES, CostKeyTuple,
)

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[4] / "configs" / "costs"
CONFIG_FILES = ("eligible_keys.yaml", "base_prices.yaml", "generator.yaml", "splits.yaml", "cost_table.yaml")
TAXONOMY_FILES = ("parts", "operations", "vehicle_classes", "cost_basis")
CENT = Decimal("0.01")


class ConfigError(ValueError):
    """The cost configuration is inconsistent or not in the frozen vocabulary."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} is not a mapping")
    return data


def dec(value: Any, name: str) -> Decimal:
    """Exact decimal from a string or int setting; floats are refused."""
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int)):
        raise ConfigError(f"{name} must be a quoted decimal string, got {value!r}")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ConfigError(f"{name} must be finite")
    return result


def as_date(value: Any, name: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"{name} is not an ISO date: {value!r}") from exc


def snapshot(config_dir: Path = DEFAULT_CONFIG_DIR) -> dict[str, dict]:
    """Every cost configuration file plus the taxonomy files the grid was checked against."""
    files = {name: load_yaml(Path(config_dir) / name) for name in CONFIG_FILES}
    root = taxonomy.taxonomy_root()
    return files | {f"taxonomy/{name}.yaml": load_yaml(root / f"{name}.yaml") for name in TAXONOMY_FILES}


def taxonomy_versions() -> dict[str, str]:
    return {"parts": taxonomy.load_parts().version, "operations": taxonomy.load_operations().version,
            "vehicle_classes": taxonomy.load_vehicle_classes().version,
            "cost_basis": taxonomy.load_cost_basis()["taxonomy_version"]}


@dataclass(frozen=True)
class Grid:
    """The frozen key grid: catalogue parts x cost operations x vehicle classes, one currency."""

    version: str
    currency: str
    cost_basis: str
    catalogue_parts: tuple[str, ...]
    operations: tuple[str, ...]
    vehicle_classes: tuple[str, ...]
    eligible_pairs: frozenset[tuple[str, str]]
    taxonomy_versions: Mapping[str, str] = field(default_factory=dict, compare=False)

    @property
    def taxonomy_version(self) -> str:
        """One string naming every taxonomy version the grid was checked against."""
        return "+".join(self.taxonomy_versions[k] for k in sorted(self.taxonomy_versions)) or self.version

    def keys(self) -> list[CostKeyTuple]:
        return [(p, o, v, self.currency) for p in self.catalogue_parts for o in self.operations
                for v in self.vehicle_classes]

    def eligible_keys(self) -> list[CostKeyTuple]:
        return [k for k in self.keys() if (k[0], k[1]) in self.eligible_pairs]

    def in_grid(self, key: CostKeyTuple) -> bool:
        return (key[0] in self.catalogue_parts and key[1] in self.operations
                and key[2] in self.vehicle_classes and key[3] == self.currency)

    def is_eligible(self, key: CostKeyTuple) -> bool:
        return self.in_grid(key) and (key[0], key[1]) in self.eligible_pairs


def check_taxonomy() -> dict[str, Any]:
    """The shared taxonomy must carry the fixed basis and the exact v2 key; returns the basis."""
    basis = taxonomy.load_cost_basis()
    if basis.get("cost_basis") != COST_BASIS or basis.get("currency") != CURRENCY:
        raise ConfigError(f"taxonomy cost basis must be {COST_BASIS} in {CURRENCY}")
    if str(basis.get("quantity")) != "1" or basis.get("tax") != "excluded" or basis.get("discounts") != "excluded":
        raise ConfigError("the fixed basis prices exactly one part, before tax, without discounts")
    if tuple(basis.get("cost_key_fields", ())) != COST_KEY_FIELDS:
        raise ConfigError("the taxonomy cost key must be exactly part, operation, vehicle class and currency")
    if not {"side", "damage_type", "model_year"} <= set(basis.get("excluded_from_key", ())) \
            or FORBIDDEN_KEY_FIELDS & set(basis.get("cost_key_fields", ())):
        raise ConfigError("side, damage type and model year must stay out of the cost key")
    if tuple(taxonomy.cost_eligible_operations()) != COST_OPERATIONS:
        raise ConfigError("taxonomy cost-eligible operations differ from repair, replace and paint")
    if taxonomy.load_vehicle_classes().codes != VEHICLE_CLASSES:
        raise ConfigError("taxonomy vehicle classes differ from the contract's four cost classes")
    if taxonomy.load_parts().codes != PART_CODES:
        raise ConfigError("taxonomy part codes differ from the contract part vocabulary")
    return basis


def load_grid(config_dir: Path = DEFAULT_CONFIG_DIR) -> Grid:
    config_dir = Path(config_dir)
    check_taxonomy()
    data = load_yaml(config_dir / "eligible_keys.yaml")
    if data["cost_basis"] != COST_BASIS:
        raise ConfigError("cost_basis must be the single fixed basis " + COST_BASIS)
    if data["currency"] != CURRENCY:
        raise ConfigError("currency must be SGD; no other currency is priced")
    parts = tuple(data["catalogue_parts"])
    catalogue = tuple(p["code"] for p in taxonomy.load_parts().meta["parts"] if p.get("cost_eligible"))
    unknown = [p for p in parts if p not in PART_CODES or p in NON_COST_PARTS]
    if unknown or len(set(parts)) != len(parts) or set(parts) != set(catalogue):
        raise ConfigError(f"catalogue parts must be the taxonomy's cost-eligible parts; outside: {unknown}")
    operations = tuple(data["operations"])
    if operations != COST_OPERATIONS:
        raise ConfigError("the grid operations are exactly repair, replace and paint")
    classes = tuple(data["vehicle_classes"])
    if not classes or any(c not in VEHICLE_CLASSES for c in classes):
        raise ConfigError(f"vehicle classes outside the proposed four: {classes}")
    pairs = set()
    for part, ops in data["eligible"].items():
        if part not in parts:
            raise ConfigError(f"eligible part {part!r} is not in the catalogue")
        for op in ops:
            if op not in operations:
                raise ConfigError(f"eligible operation {op!r} for {part!r} is not a cost operation")
            pairs.add((part, op))
    return Grid(data["version"], CURRENCY, COST_BASIS, parts, operations, classes, frozenset(pairs),
                taxonomy_versions())


def load_base_prices(config_dir: Path, grid: Grid) -> tuple[dict[CostKeyTuple, Decimal], dict]:
    """Expand pair prices by class multiplier into one documented base price per eligible key."""
    data = load_yaml(Path(config_dir) / "base_prices.yaml")
    if data["currency"] != grid.currency or data["cost_basis"] != grid.cost_basis:
        raise ConfigError("base prices must use the grid currency and basis")
    multipliers = {c: dec(v["multiplier"], f"class_multipliers.{c}") for c, v in data["class_multipliers"].items()}
    if set(multipliers) != set(grid.vehicle_classes):
        raise ConfigError("class multipliers must cover exactly the grid vehicle classes")
    declared = {(p, o) for p, ops in data["pairs"].items() for o in ops}
    if declared != set(grid.eligible_pairs):
        difference = declared ^ grid.eligible_pairs
        raise ConfigError(f"base prices must cover exactly the eligible pairs; differs by {difference}")
    prices = {}
    for part, ops in data["pairs"].items():
        for op, entry in ops.items():
            if not entry.get("note"):
                raise ConfigError(f"base price {part}/{op} needs a justification note")
            price = dec(entry["price"], f"pairs.{part}.{op}.price")
            if price <= 0:
                raise ConfigError(f"base price {part}/{op} must be positive")
            for vehicle_class, multiplier in multipliers.items():
                prices[(part, op, vehicle_class, grid.currency)] = (price * multiplier).quantize(CENT, ROUND_HALF_UP)
    return prices, data


@dataclass(frozen=True)
class InjectedConfig:
    seed_offset: int
    base_cases_per_key: int
    quotes_per_base_case: int
    prevalence: Decimal
    magnitudes: tuple[Decimal, ...]


@dataclass(frozen=True)
class GeneratorConfig:
    """Eligible keys, base prices, sparsity plan, noise model and seed for one generator run."""

    generator_version: str
    seed: int
    total_records: int
    quotes_min: int
    quotes_max: int
    workshop_count: int
    base_case_sigma: Decimal
    workshop_sigma: Decimal
    quote_noise_sigma: Decimal
    factor_places: int
    spread: Decimal
    sparse_key_fraction: Decimal
    sparse_min: int
    sparse_max: int
    empty_key_fraction: Decimal
    date_start: date
    date_end: date
    as_of_date: date
    injected: InjectedConfig
    grid: Grid
    base_prices: Mapping[CostKeyTuple, Decimal] = field(repr=False)
    source: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def with_seed(self, seed: int | None) -> "GeneratorConfig":
        return self if seed is None else replace(self, seed=int(seed))

    def describe(self) -> dict:
        """JSON-safe settings recorded in the generator manifest."""
        return {
            "generator_version": self.generator_version, "seed": self.seed, "total_records_target": self.total_records,
            "quotes_per_base_case": {"min": self.quotes_min, "max": self.quotes_max},
            "workshop_count": self.workshop_count,
            "noise_model": {"distribution": "lognormal", "base_case_sigma": str(self.base_case_sigma),
                            "workshop_sigma": str(self.workshop_sigma),
                            "quote_noise_sigma": str(self.quote_noise_sigma),
                            "factor_places": self.factor_places},
            "base_cases_per_key_spread": str(self.spread),
            "sparsity": {"sparse_key_fraction": str(self.sparse_key_fraction),
                         "sparse_base_cases": {"min": self.sparse_min, "max": self.sparse_max},
                         "empty_key_fraction": str(self.empty_key_fraction)},
            "dates": {"start": self.date_start.isoformat(), "end": self.date_end.isoformat(),
                      "as_of_date": self.as_of_date.isoformat()},
        }


def load_generator_config(config_dir: Path = DEFAULT_CONFIG_DIR, *, seed: int | None = None) -> GeneratorConfig:
    config_dir = Path(config_dir)
    grid = load_grid(config_dir)
    prices, price_source = load_base_prices(config_dir, grid)
    data = load_yaml(config_dir / "generator.yaml")
    noise, sparsity, dates, inj = data["noise_model"], data["sparsity"], data["dates"], data["injected_anomaly"]
    quotes = data["quotes_per_base_case"]
    config = GeneratorConfig(
        generator_version=data["generator_version"], seed=int(data["seed"]), total_records=int(data["total_records"]),
        quotes_min=int(quotes["min"]), quotes_max=int(quotes["max"]), workshop_count=int(data["workshop_count"]),
        base_case_sigma=dec(noise["base_case_sigma"], "base_case_sigma"),
        workshop_sigma=dec(noise["workshop_sigma"], "workshop_sigma"),
        quote_noise_sigma=dec(noise["quote_noise_sigma"], "quote_noise_sigma"),
        factor_places=int(noise["factor_places"]), spread=dec(data["base_cases_per_key_spread"], "spread"),
        sparse_key_fraction=dec(sparsity["sparse_key_fraction"], "sparse_key_fraction"),
        sparse_min=int(sparsity["sparse_base_cases"]["min"]), sparse_max=int(sparsity["sparse_base_cases"]["max"]),
        empty_key_fraction=dec(sparsity["empty_key_fraction"], "empty_key_fraction"),
        date_start=as_date(dates["start"], "dates.start"), date_end=as_date(dates["end"], "dates.end"),
        as_of_date=as_date(dates["as_of_date"], "dates.as_of_date"),
        injected=InjectedConfig(int(inj["seed_offset"]), int(inj["base_cases_per_key"]),
                                int(inj["quotes_per_base_case"]), dec(inj["prevalence"], "prevalence"),
                                tuple(dec(m, "magnitudes") for m in inj["magnitudes"])),
        grid=grid, base_prices=prices, source={"generator": data, "base_prices": price_source},
    )
    if not 1 <= config.quotes_min <= config.quotes_max or config.quotes_max > config.workshop_count:
        raise ConfigError("quotes per base case must be 1..workshop_count so each quote has its own workshop")
    if config.date_start > config.date_end:
        raise ConfigError("generator date range is reversed")
    if config.sparse_key_fraction + config.empty_key_fraction >= 1:
        raise ConfigError("sparse and empty key fractions leave no ordinary keys")
    if any(m <= -1 for m in config.injected.magnitudes):
        raise ConfigError("an injected magnitude of -100% or less would create a non-positive price")
    return config.with_seed(seed)


@dataclass(frozen=True)
class SplitConfig:
    split_version: str
    group_key: str
    seed: int
    cutoff_date: date
    ratios: Mapping[str, Decimal]

    def describe(self) -> dict:
        return {"split_version": self.split_version, "group_key": self.group_key, "stratify_by": "cost_key",
                "seed": self.seed, "cutoff_date": self.cutoff_date.isoformat(),
                "ratios": {k: str(v) for k, v in self.ratios.items()}}


def load_split_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> SplitConfig:
    data = load_yaml(Path(config_dir) / "splits.yaml")
    ratios = {name: dec(data["ratios"][name], f"ratios.{name}") for name in PARTITIONS}
    if sum(ratios.values()) != 1 or any(r <= 0 for r in ratios.values()):
        raise ConfigError("split ratios must be positive and sum to exactly 1")
    if data["group_key"] != "base_case_id":
        raise ConfigError("price splits are grouped by base_case_id; splitting by record_id is a defect")
    return SplitConfig(data["split_version"], data["group_key"], int(data["seed"]),
                       as_date(data["cutoff_date"], "cutoff_date"), ratios)


@dataclass(frozen=True)
class CostTableConfig:
    """Method, support policy, splits and grid for one reference-table build."""

    config_version: str
    method: str
    nominal_coverage: Decimal
    quantiles: tuple[Decimal, Decimal]
    percentile_definition: str
    base_case_aggregation: str
    min_independent_base_cases: int | None
    sweep_candidates: tuple[int, ...]
    sweep_min_evaluated: int
    support_group_edges: tuple[int, ...]
    conformal: str | None
    conformal_decision: str
    places: int
    zero_width_allowed: bool
    target_band: tuple[Decimal, Decimal]
    lightgbm_status: str
    split: SplitConfig
    grid: Grid
    snapshot: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def with_policy(self, **changes: Any) -> "CostTableConfig":
        """A copy with changed build policy (tests and experiments; the snapshot records the change)."""
        snapshot = dict(self.snapshot)
        snapshot["overrides"] = {k: str(v) for k, v in changes.items()}
        return replace(self, snapshot=snapshot, **changes)


def load_cost_table_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> CostTableConfig:
    config_dir = Path(config_dir)
    data = load_yaml(config_dir / "cost_table.yaml")
    if data["method"] not in METHODS:
        raise ConfigError(f"unknown method {data['method']!r}")
    if data["method"] != "empirical_percentile":
        raise ConfigError("only empirical_percentile is implemented; the LightGBM comparison is not run in this pass")
    if data["percentile_definition"] != "linear" or data["base_case_aggregation"] != "median":
        raise ConfigError("implemented percentile definition is linear over per-base-case medians")
    if data["conformal"] not in (None, "none", "cqr"):
        raise ConfigError("conformal must be none or cqr (null selects on validation in this build)")
    rounding = data["rounding"]
    if rounding["mode"] != "ROUND_HALF_UP":
        raise ConfigError("publication rounding is ROUND_HALF_UP")
    quantiles = tuple(dec(q, "quantiles") for q in data["quantiles"])
    if len(quantiles) != 2 or not Decimal(0) < quantiles[0] < quantiles[1] < Decimal(1):
        raise ConfigError("quantiles must be two ordered values inside (0, 1)")
    minimum = data["min_independent_base_cases"]
    sweep = data["support_sweep"]
    return CostTableConfig(
        config_version=data["config_version"], method=data["method"],
        nominal_coverage=dec(data["nominal_coverage"], "nominal_coverage"), quantiles=quantiles,
        percentile_definition=data["percentile_definition"], base_case_aggregation=data["base_case_aggregation"],
        min_independent_base_cases=None if minimum is None else int(minimum),
        sweep_candidates=tuple(int(c) for c in sweep["candidates"]),
        sweep_min_evaluated=int(sweep["min_evaluated_records"]),
        support_group_edges=tuple(int(e) for e in data["support_group_edges"]),
        conformal=data["conformal"], conformal_decision=str(data["conformal_decision"]).strip(),
        places=int(rounding["places"]), zero_width_allowed=bool(data["zero_width_allowed"]),
        target_band=tuple(dec(b, "target_coverage_band") for b in data["target_coverage_band"]),
        lightgbm_status=data.get("lightgbm_status", "not_run"),
        split=load_split_config(config_dir), grid=load_grid(config_dir), snapshot=snapshot(config_dir),
    )
