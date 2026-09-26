"""Small helpers shared by the M7 tests (imported by name; pytest prepends this directory)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from claim_cmev.costs.reference.records import PriceRecord

BASIS = "single_part_pre_tax_no_discount_v1"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def rewrite(directory: Path, name: str, value, *, rehash: bool = True) -> None:
    """Rewrite a table file; optionally update its manifest hash so only content checks can catch it."""
    (directory / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if rehash:
        manifest = read_json(directory / "build_manifest.json")
        manifest["files"][name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        (directory / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def record(record_id: str, base_case_id: str, amount: str, *, part="front-bumper", operation="replace",
           vehicle_class="sedan_standard", currency="SGD", basis=BASIS) -> PriceRecord:
    return PriceRecord(record_id, base_case_id, "ws-01", part, operation, vehicle_class, currency, basis,
                       Decimal(amount), date(2026, 1, 1), hashlib.sha256(record_id.encode()).hexdigest())


def row(**overrides) -> dict:
    """A valid supported ReferenceCostRange-shaped row."""
    base = {"schema_version": "0.2.0", "range_id": "rng-test-1", "part_code": "front-bumper", "operation": "replace",
            "vehicle_class": "sedan_standard", "currency": "SGD", "cost_basis": BASIS, "support_status": "supported",
            "lower_amount": "620.00", "upper_amount": "1020.00", "independent_base_case_count": 47,
            "record_count": 141, "withheld_reason": None, "method": "empirical_percentile",
            "nominal_coverage": "0.90", "observed_calibration": None, "as_of_date": "2026-09-22",
            "cutoff_date": "2026-06-30", "table_version": "t-1", "synthetic": True,
            "provenance": {"source_kind": "synthetic", "generator_version": "gen-test", "seed": 1,
                           "generator_ref": "generator.json"}}
    return base | overrides


def withheld(reason: str = "insufficient_support", count: int = 3, **overrides) -> dict:
    fields = {"support_status": "withheld", "lower_amount": None, "upper_amount": None, "withheld_reason": reason,
              "independent_base_case_count": count, "record_count": count * 3}
    return row(**(fields | overrides))
