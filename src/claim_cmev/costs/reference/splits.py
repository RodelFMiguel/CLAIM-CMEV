"""Record screening and grouped splits (M7 "Split discipline").

Screening excludes, with a reason, every record that is not an ordinary synthetic quote
on an eligible key under the fixed basis and before the cutoff; nothing is mapped to a
near neighbour. Splitting keeps every quote of one base case in one partition and
allocates base cases per cost key. The test membership is hashed and reserved before
any fitting, and the fitting code only ever receives train and validation records.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Iterable, Mapping

from .config import Grid, SplitConfig, canonical_hash
from .generator import _Rng
from .records import PriceRecord, record_hash
from .vocabulary import (
    BASIS_MISMATCH, CURRENCY_UNSUPPORTED, NON_COST_PARTS, OPERATIONS, PART_CODES, PARTITIONS,
    UNKNOWN_VEHICLE_CLASS_REASON, UNSUPPORTED_COMBINATION, CostKeyTuple, key_text,
)

_AMOUNT = re.compile(r"^\d{1,12}(\.\d{1,6})?$")


@dataclass(frozen=True)
class Exclusion:
    record_id: str
    base_case_id: str
    reason_code: str
    detail: str
    source_hash: str


def _reason(raw: Mapping[str, str], grid: Grid, cutoff: date, seen: set[str]) -> tuple[str, str] | None:
    record_id = raw.get("record_id", "")
    if not record_id or not raw.get("base_case_id"):
        return "identifier_missing", "record_id and base_case_id are both required"
    if record_id in seen:
        return "duplicate_record", "record_id already included"
    if raw.get("run_kind", "ordinary") != "ordinary" or raw.get("anomaly_label") == "true":
        return "injected_anomaly", "injected-anomaly records never build a reference"
    if raw.get("source_kind") != "synthetic":
        return "source_not_eligible", f"source_kind {raw.get('source_kind')!r} is not a documented synthetic seed"
    part, operation = raw.get("part_code", ""), raw.get("operation", "")
    if part not in PART_CODES:
        return "unknown_part", f"{part!r} is not in the part vocabulary"
    if part in NON_COST_PARTS:
        return "non_cost_part", f"{part!r} is outside the cost catalogue"
    if operation not in OPERATIONS:
        return "unknown_operation", f"{operation!r} is not in the operation vocabulary"
    if operation not in grid.operations:
        return "ineligible_operation", f"{operation!r} never inherits a repair, replace or paint range"
    if raw.get("vehicle_class") not in grid.vehicle_classes:
        return UNKNOWN_VEHICLE_CLASS_REASON, f"{raw.get('vehicle_class')!r} is not a documented vehicle class"
    if raw.get("currency") != grid.currency:
        return CURRENCY_UNSUPPORTED, f"{raw.get('currency')!r} is not {grid.currency}; no conversion"
    if raw.get("cost_basis") != grid.cost_basis:
        return BASIS_MISMATCH, f"{raw.get('cost_basis')!r} is not {grid.cost_basis}; bases never pool"
    try:
        quantity_is_one = Decimal(raw.get("quantity", "")) == 1
    except InvalidOperation:
        quantity_is_one = False
    if not quantity_is_one:
        return "quantity_not_one", "the fixed basis prices exactly one part; a missing quantity is not one"
    amount = raw.get("amount", "")
    if not _AMOUNT.fullmatch(amount) or Decimal(amount) <= 0:
        return "amount_invalid", f"{amount!r} is not a positive exact decimal string"
    try:
        day = date.fromisoformat(raw.get("synthetic_date", ""))
    except ValueError:
        return "date_invalid", "synthetic_date is not an ISO date"
    if day > cutoff:
        return "after_cutoff", f"{day.isoformat()} is after the cutoff {cutoff.isoformat()}"
    if (part, operation) not in grid.eligible_pairs:
        return UNSUPPORTED_COMBINATION, f"{part}/{operation} is not in the frozen eligible list"
    return None


def screen_records(raw_records: Iterable[Mapping[str, str]], *, grid: Grid,
                   cutoff_date: date) -> tuple[list[PriceRecord], list[Exclusion]]:
    """Split raw records into eligible typed records and reasoned exclusions."""
    included, excluded, seen = [], [], set()
    for raw in raw_records:
        source_hash = record_hash(raw)
        problem = _reason(raw, grid, cutoff_date, seen)
        if problem:
            excluded.append(Exclusion(raw.get("record_id", ""), raw.get("base_case_id", ""), *problem, source_hash))
            continue
        seen.add(raw["record_id"])
        included.append(PriceRecord(
            raw["record_id"], raw["base_case_id"], raw.get("workshop_id", ""), raw["part_code"], raw["operation"],
            raw["vehicle_class"], raw["currency"], raw["cost_basis"], Decimal(raw["amount"]),
            date.fromisoformat(raw["synthetic_date"]), source_hash))
    keys_by_case: dict[str, set] = defaultdict(set)
    for record in included:
        keys_by_case[record.base_case_id].add(record.key)
    spanning = {case for case, keys in keys_by_case.items() if len(keys) > 1}
    if spanning:
        excluded += [Exclusion(r.record_id, r.base_case_id, "base_case_spans_keys",
                               "one base case is one repair situation on one key", r.source_hash)
                     for r in included if r.base_case_id in spanning]
        included = [r for r in included if r.base_case_id not in spanning]
    return included, excluded


def membership_hash(records: Iterable[PriceRecord]) -> str:
    lines = sorted(f"{r.base_case_id}\t{r.record_id}" for r in records)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _allocate(n: int, ratios: Mapping[str, Decimal]) -> list[str]:
    """Largest-remainder allocation of n base cases to partitions, in PARTITIONS order."""
    exact = {p: ratios[p] * n for p in PARTITIONS}
    counts = {p: int(exact[p]) for p in PARTITIONS}
    leftovers = sorted(PARTITIONS, key=lambda p: (-(exact[p] - counts[p]), PARTITIONS.index(p)))
    for p in leftovers[: n - sum(counts.values())]:
        counts[p] += 1
    return [p for p in PARTITIONS for _ in range(counts[p])]


@dataclass(frozen=True)
class SplitResult:
    partitions: Mapping[str, tuple[PriceRecord, ...]]
    base_case_partition: Mapping[str, str]
    hashes: Mapping[str, str]
    config: SplitConfig
    config_hash: str

    def support_after_split(self) -> dict[str, dict[str, int]]:
        """Independent base cases per key and partition, recorded after splitting."""
        cases: dict[str, dict[str, set]] = defaultdict(lambda: {p: set() for p in PARTITIONS})
        for partition, records in self.partitions.items():
            for record in records:
                cases[key_text(record.key)][partition].add(record.base_case_id)
        return {k: {p: len(v[p]) for p in PARTITIONS} for k, v in sorted(cases.items())}

    def summary(self) -> dict[str, dict]:
        return {p: {"records": len(rs), "base_cases": len({r.base_case_id for r in rs}),
                    "membership_sha256": self.hashes[p]} for p, rs in self.partitions.items()}


def split_records(records: Iterable[PriceRecord], config: SplitConfig) -> SplitResult:
    """Grouped, key-stratified, seeded split by base case. Splitting by record_id is a defect."""
    by_case: dict[str, list[PriceRecord]] = defaultdict(list)
    for record in records:
        by_case[record.base_case_id].append(record)
    cases_by_key: dict[CostKeyTuple, list[str]] = defaultdict(list)
    for case, members in by_case.items():
        keys = {r.key for r in members}
        if len(keys) != 1:
            raise ValueError(f"base case {case} spans keys {sorted(keys)}; screen records first")
        cases_by_key[keys.pop()].append(case)
    assignment: dict[str, str] = {}
    for key in sorted(cases_by_key):
        cases = sorted(cases_by_key[key])
        seed = int(hashlib.sha256(f"{config.seed}|{key_text(key)}".encode()).hexdigest()[:16], 16)
        _Rng(seed).shuffle(cases)
        assignment.update(zip(cases, _allocate(len(cases), config.ratios)))
    partitions = {p: tuple(sorted((r for c, p2 in assignment.items() if p2 == p for r in by_case[c]),
                                  key=lambda r: r.record_id)) for p in PARTITIONS}
    return SplitResult(partitions, assignment, {p: membership_hash(rs) for p, rs in partitions.items()},
                       config, canonical_hash(config.describe()))


def reserve_test_membership(split: SplitResult) -> dict:
    """The final-test membership, reserved and hashed before any fitting."""
    test = split.partitions["test"]
    return {"partition": "test", "split_version": split.config.split_version, "split_config_hash": split.config_hash,
            "group_key": "base_case_id", "reserved_before_fitting": True,
            "base_case_count": len({r.base_case_id for r in test}), "record_count": len(test),
            "membership_sha256": split.hashes["test"],
            "base_case_ids": sorted({r.base_case_id for r in test})}
