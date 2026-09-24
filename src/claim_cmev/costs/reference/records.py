"""Synthetic price records: the file format and the screened, typed record.

Raw records travel as strings (amounts are exact decimal strings). Only screening turns
them into ``PriceRecord`` values with a ``Decimal`` amount; nothing here uses floats for money.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Iterable, Mapping

from .config import canonical_hash
from .vocabulary import CostKeyTuple

RECORD_FIELDS = (
    "record_id", "base_case_id", "workshop_id", "part_code", "operation", "vehicle_class", "currency",
    "cost_basis", "quantity", "amount", "synthetic_date", "generator_version", "seed", "run_kind",
    "source_kind", "anomaly_label", "anomaly_magnitude",
)
RUN_KINDS = ("ordinary", "injected_anomaly")


@dataclass(frozen=True)
class PriceRecord:
    """One eligible quote after screening. ``base_case_id`` is the independent unit."""

    record_id: str
    base_case_id: str
    workshop_id: str
    part_code: str
    operation: str
    vehicle_class: str
    currency: str
    cost_basis: str
    amount: Decimal
    synthetic_date: date
    source_hash: str

    @property
    def key(self) -> CostKeyTuple:
        return (self.part_code, self.operation, self.vehicle_class, self.currency)


def record_hash(raw: Mapping[str, str]) -> str:
    return canonical_hash({k: raw.get(k, "") for k in sorted(raw)})


def write_records(path: Path, rows: Iterable[Mapping[str, str]]) -> str:
    """Write records as CSV with a fixed column order; returns the file SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECORD_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in RECORD_FIELDS})
    return file_sha256(path)


def read_records(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()
