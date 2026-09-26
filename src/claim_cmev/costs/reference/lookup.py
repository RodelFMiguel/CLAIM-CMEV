"""Read-only loader and lookup for published M7 tables (application platform section 10).

Imported by ``cmev-consolidator`` and ``cmev-api``. No model runs here. A table is loaded
by its pinned version only after its manifest and every file hash verify; a v1-shaped
table (side, damage type or year in ``cost_key_fields``) is refused with
``schema_unsupported``. ``lookup_range`` never extrapolates, never falls back to another
vehicle class, never pools operations and never converts currency: it returns either a
supported range or ``None`` bounds with a reason code.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Sequence

from claim_cmev.contracts.common import SCHEMA_VERSION, ContractError, check_schema_version
from claim_cmev.contracts.costs import ReferenceCostRange, check_cost_key_fields

from .validation import row_problems
from .vocabulary import (
    BASIS_MISMATCH, COST_BASIS, COST_KEY_FIELDS, COST_OPERATIONS, CURRENCY, CURRENCY_UNSUPPORTED,
    FORBIDDEN_KEY_FIELDS, INSUFFICIENT_SUPPORT, NO_KEY, NO_RECORDS, NON_COST_PARTS, RANGE_INVALID,
    UNKNOWN_VEHICLE_CLASS_REASON,
    UNSUPPORTED_COMBINATION, VEHICLE_CLASSES, CostKeyTuple,
)

MANIFEST_FILE = "build_manifest.json"
RANGES_FILE = "ranges.json"
REGISTRY_FILE = "registry.json"
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# A withheld row's own reason, mapped onto the lookup/M8 reason vocabulary.
_WITHHELD_TO_REASON = {INSUFFICIENT_SUPPORT: INSUFFICIENT_SUPPORT, NO_RECORDS: INSUFFICIENT_SUPPORT,
                       UNSUPPORTED_COMBINATION: UNSUPPORTED_COMBINATION}


class CostTableError(ContractError):
    """The pinned table cannot be served: ``table_missing``, ``manifest_invalid``,
    ``hash_mismatch``, ``schema_unsupported`` or ``range_invalid``. This is an operational
    error, distinct from a valid table that holds no range for a key."""


@dataclass(frozen=True)
class LookupKey:
    part_code: str
    operation: str
    vehicle_class: str
    currency: str
    cost_basis: str | None


def coerce_key(key: Any, cost_basis: str | None = None) -> LookupKey:
    """Accept a contract ``CostKey``, a mapping or any object with the key attributes.

    The contract ``CostKey`` has exactly four fields and is defined under the one fixed
    basis, so a key without ``cost_basis`` means that basis. A basis given on the key or
    as ``cost_basis`` is compared with the table's and never pooled.
    """
    get = key.get if isinstance(key, Mapping) else lambda name, default=None: getattr(key, name, default)
    present = {name for name in FORBIDDEN_KEY_FIELDS if get(name) is not None}
    if present:
        raise ContractError("schema_unsupported", f"cost keys never carry {sorted(present)}")
    missing = [f for f in COST_KEY_FIELDS if not isinstance(get(f), str)]
    if missing:
        raise ValueError(f"cost key is missing {missing}")
    basis = cost_basis if cost_basis is not None else get("cost_basis")
    return LookupKey(*(get(f) for f in COST_KEY_FIELDS), COST_BASIS if basis is None else basis)


@dataclass(frozen=True)
class RangeResult:
    """Outcome of one lookup against one pinned table.

    ``support_status`` is ``supported`` (bounds present, ``reason_code`` None), ``withheld``
    (the key has a row but no range) or ``absent`` (no row applies). Bounds are exact
    ``Decimal`` values, never zero stand-ins; a non-supported result always has a reason.
    """

    table_version: str
    support_status: Literal["supported", "withheld", "absent"]
    reason_code: str | None
    lower_amount: Decimal | None
    upper_amount: Decimal | None
    currency: str
    cost_basis: str
    range_id: str | None = None
    independent_base_case_count: int | None = None
    record_count: int | None = None
    withheld_reason: str | None = None
    method: str | None = None
    nominal_coverage: Decimal | None = None
    as_of_date: str | None = None
    cutoff_date: str | None = None
    synthetic: bool = True
    range: ReferenceCostRange | None = None  # the contract row applied, when the key has one

    @property
    def supported(self) -> bool:
        return self.support_status == "supported"

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form for the API; money stays a decimal string with currency and basis."""
        text = lambda v: None if v is None else format(v, "f")
        return {"table_version": self.table_version, "support_status": self.support_status,
                "reason_code": self.reason_code, "range_id": self.range_id,
                "lower_amount": text(self.lower_amount), "upper_amount": text(self.upper_amount),
                "currency": self.currency, "cost_basis": self.cost_basis,
                "independent_base_case_count": self.independent_base_case_count,
                "record_count": self.record_count, "withheld_reason": self.withheld_reason, "method": self.method,
                "nominal_coverage": text(self.nominal_coverage), "as_of_date": self.as_of_date,
                "cutoff_date": self.cutoff_date, "synthetic": self.synthetic}


def check_manifest(manifest: Mapping[str, Any], table_version: str | None = None) -> None:
    """Refuse a v1-shaped or malformed manifest before anything else is read."""
    fields = manifest.get("cost_key_fields")
    try:
        if not isinstance(fields, list):
            raise ContractError("schema_unsupported", f"cost_key_fields {fields!r} is not a list")
        check_cost_key_fields(fields)
    except ContractError as exc:
        v1 = sorted(FORBIDDEN_KEY_FIELDS & set(fields or ()))
        raise CostTableError("schema_unsupported", exc.message + (f"; v1 fields {v1}" if v1 else "")) from exc
    try:
        check_schema_version(manifest.get("schema_version"))
    except ContractError as exc:
        raise CostTableError("schema_unsupported", exc.message) from exc
    if manifest.get("synthetic") is not True:
        raise CostTableError("manifest_invalid", "every table in this project is synthetic")
    if table_version is not None and manifest.get("table_version") != table_version:
        raise CostTableError("manifest_invalid", "manifest table_version does not match the pinned version")
    for name in ("cost_basis", "currency", "method", "files", "min_independent_support"):
        if name not in manifest:
            raise CostTableError("manifest_invalid", f"manifest lacks {name}")


class PinnedCostTable:
    """One immutable, validated table version. ``lookup(key)`` is the M8 ``RangeLookup`` protocol."""

    def __init__(self, manifest: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], *,
                 manifest_sha256: str | None = None, path: Path | None = None):
        check_manifest(manifest)
        self._manifest = copy.deepcopy(dict(manifest))
        self.table_version: str = manifest["table_version"]
        self.cost_basis: str = manifest["cost_basis"]
        self.currency: str = manifest["currency"]
        self.method: str = manifest["method"]
        self.min_independent_support: int = manifest["min_independent_support"]
        self.manifest_sha256, self.path = manifest_sha256, path
        index: dict[CostKeyTuple, dict] = {}
        problems = []
        for row in rows:
            problems += row_problems(row, cost_basis=self.cost_basis, currency=self.currency,
                                     min_support=self.min_independent_support,
                                     zero_width_allowed=manifest.get("interval_integrity", {}).get(
                                         "zero_width_allowed", True))
            if row.get("table_version") != self.table_version:
                problems.append(f"row {row.get('range_id')} belongs to {row.get('table_version')!r}")
            key = tuple(row.get(f) for f in COST_KEY_FIELDS)
            if key in index:
                problems.append(f"duplicate key {key}")
            index[key] = copy.deepcopy(dict(row))
        if problems:
            raise CostTableError(RANGE_INVALID, "; ".join(problems[:5]))
        self._index = index
        self._records = {key: ReferenceCostRange.model_validate(row) for key, row in index.items()}
        self._by_range_id = {row["range_id"]: row for row in index.values()}

    @classmethod
    def from_rows(cls, table_version: str, rows: Sequence[Mapping[str, Any]], *, min_independent_support: int,
                  method: str = "empirical_percentile", cost_basis: str = COST_BASIS,
                  currency: str = CURRENCY) -> "PinnedCostTable":
        """In-memory table for rule tests; the rows get exactly the same validation as a loaded table."""
        manifest = {"schema_version": SCHEMA_VERSION, "table_version": table_version, "synthetic": True,
                    "cost_key_fields": list(COST_KEY_FIELDS), "cost_basis": cost_basis, "currency": currency,
                    "method": method, "files": {}, "min_independent_support": min_independent_support}
        return cls(manifest, rows)

    @property
    def manifest(self) -> dict[str, Any]:
        return copy.deepcopy(self._manifest)

    def rows(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(row) for row in self._index.values()]

    def row(self, range_id: str) -> dict[str, Any] | None:
        row = self._by_range_id.get(range_id)
        return None if row is None else copy.deepcopy(row)

    def ranges(self) -> list[ReferenceCostRange]:
        """Every row as the shared, immutable ``ReferenceCostRange`` contract record."""
        return list(self._records.values())

    def lookup(self, key: Any, *, cost_basis: str | None = None) -> RangeResult:
        return lookup_range(self, key, cost_basis=cost_basis)

    def _row_for(self, key: CostKeyTuple) -> tuple[dict[str, Any], ReferenceCostRange] | None:
        return (self._index[key], self._records[key]) if key in self._index else None


def lookup_range(table: PinnedCostTable, key: Any, *, cost_basis: str | None = None) -> RangeResult:
    """Supported range or null bounds with a reason. No extrapolation and no fallback.

    Checks, in order: basis (``basis_mismatch``), currency (``currency_unsupported``),
    vehicle class (``unknown_vehicle_class``), operation (``unsupported_combination`` for
    ``other``/``unknown``), then the frozen grid: a vocabulary part outside the cost
    is ``unsupported_combination``, any other key without a row is ``no_key``,
    and a withheld row maps to ``insufficient_support`` or ``unsupported_combination``.
    """
    k = coerce_key(key, cost_basis)
    common = {"table_version": table.table_version, "currency": table.currency, "cost_basis": table.cost_basis}

    def absent(reason: str) -> RangeResult:
        return RangeResult(support_status="absent", reason_code=reason, lower_amount=None, upper_amount=None, **common)

    if k.cost_basis != table.cost_basis:
        return absent(BASIS_MISMATCH)
    if k.currency != table.currency:
        return absent(CURRENCY_UNSUPPORTED)
    if k.vehicle_class not in VEHICLE_CLASSES:
        return absent(UNKNOWN_VEHICLE_CLASS_REASON)
    if k.operation not in COST_OPERATIONS:
        return absent(UNSUPPORTED_COMBINATION)
    found = table._row_for((k.part_code, k.operation, k.vehicle_class, k.currency))
    if found is None:
        return absent(UNSUPPORTED_COMBINATION if k.part_code in NON_COST_PARTS else NO_KEY)
    row, record = found
    detail = {"range": record, "range_id": row["range_id"],
              "independent_base_case_count": row["independent_base_case_count"],
              "record_count": row["record_count"], "method": row["method"],
              "nominal_coverage": Decimal(row["nominal_coverage"]), "as_of_date": row.get("as_of_date"),
              "cutoff_date": row.get("cutoff_date"), **common}
    if row["support_status"] != "supported":
        return RangeResult(support_status="withheld", reason_code=_WITHHELD_TO_REASON[row["withheld_reason"]],
                           lower_amount=None, upper_amount=None, withheld_reason=row["withheld_reason"], **detail)
    lower, upper = Decimal(row["lower_amount"]), Decimal(row["upper_amount"])
    if not (lower.is_finite() and upper.is_finite() and 0 <= lower <= upper):  # defensive; load validated it
        return RangeResult(support_status="withheld", reason_code=RANGE_INVALID, lower_amount=None,
                           upper_amount=None, **detail)
    return RangeResult(support_status="supported", reason_code=None, lower_amount=lower, upper_amount=upper, **detail)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_dir(registry_root: Path | str, table_version: str) -> Path:
    if not isinstance(table_version, str) or not _VERSION.fullmatch(table_version):
        raise CostTableError("table_missing", f"{table_version!r} is not a valid table version")
    return Path(registry_root) / table_version


def load_table(registry_root: Path | str, table_version: str) -> PinnedCostTable:
    """Load one pinned table read-only after verifying its manifest and every file hash."""
    return load_table_dir(table_dir(registry_root, table_version), table_version)


def load_table_dir(directory: Path, table_version: str) -> PinnedCostTable:
    """Verify and load the table files in ``directory``; never writes."""
    directory = Path(directory)
    manifest_path = directory / MANIFEST_FILE
    if not manifest_path.is_file():
        raise CostTableError("table_missing", f"no published table {table_version!r} at {directory}")
    raw = manifest_path.read_bytes()
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CostTableError("manifest_invalid", str(exc)) from exc
    check_manifest(manifest, table_version)
    files = manifest["files"]
    if RANGES_FILE not in files:
        raise CostTableError("manifest_invalid", f"manifest does not list {RANGES_FILE}")
    for name, expected in files.items():
        path = directory / name
        if Path(name).name != name or not path.is_file() or _sha256(path) != expected:
            raise CostTableError("hash_mismatch", f"{name} is missing or does not match its manifest hash")
    rows = json.loads((directory / RANGES_FILE).read_text(encoding="utf-8"))
    table = PinnedCostTable(manifest, rows, manifest_sha256=hashlib.sha256(raw).hexdigest(), path=directory)
    supported = sum(1 for r in rows if r["support_status"] == "supported")
    if len(rows) != manifest.get("grid_key_count") or supported != manifest.get("key_count"):
        raise CostTableError("manifest_invalid", "row counts do not match the manifest")
    return table


def read_registry(registry_root: Path | str) -> dict[str, Any]:
    path = Path(registry_root) / REGISTRY_FILE
    if not path.is_file():
        return {"active_version": None, "tables": [], "history": []}
    return json.loads(path.read_text(encoding="utf-8"))


def active_table_version(registry_root: Path | str) -> str | None:
    """The default for new assessments; existing assessments keep their pinned version."""
    return read_registry(registry_root).get("active_version")


def list_tables(registry_root: Path | str) -> list[dict[str, Any]]:
    registry = read_registry(registry_root)
    active = registry.get("active_version")
    return [dict(entry, active=entry["table_version"] == active) for entry in registry.get("tables", [])]
