"""Build validation: a malformed interval is never published and never served.

Refuses crossed, non-finite, negative, float or zero-substituted bounds; any basis or
currency mixing; v1 key fields; withheld rows that carry bounds; and support counted
by quote rather than by distinct base case. The loader reuses ``row_problems``.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

from pydantic import ValidationError

from claim_cmev.contracts.common import ContractError
from claim_cmev.contracts.costs import ReferenceCostRange

from .records import PriceRecord
from .vocabulary import (
    COST_BASIS, COST_KEY_FIELDS, COST_OPERATIONS, CURRENCY, FORBIDDEN_KEY_FIELDS, METHODS, VEHICLE_CLASSES,
    WITHHELD_REASONS,
)


class BuildValidationError(ValueError):
    """The candidate table failed validation; nothing is published."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        more = f" (+{len(self.problems) - 10} more)" if len(self.problems) > 10 else ""
        super().__init__("; ".join(self.problems[:10]) + more)


def _bound(value: object, name: str, problems: list[str]) -> Decimal | None:
    if not isinstance(value, str):
        problems.append(f"{name} must be a decimal string, got {type(value).__name__}")
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        problems.append(f"{name} {value!r} is not a decimal")
        return None
    if not number.is_finite():
        problems.append(f"{name} is non-finite")
        return None
    if number < 0:
        problems.append(f"{name} is negative")
        return None
    return number


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def row_problems(row: Mapping, *, cost_basis: str = COST_BASIS, currency: str = CURRENCY,
                 min_support: int | None = None, zero_width_allowed: bool = True) -> list[str]:
    """Every reason one published row may not be served; empty when the row is valid."""
    label = "/".join(str(row.get(f)) for f in COST_KEY_FIELDS)
    problems: list[str] = []
    forbidden = FORBIDDEN_KEY_FIELDS & set(row)
    if forbidden:
        problems.append(f"v1 key fields {sorted(forbidden)} are not part of the v2 cost key")
    if row.get("operation") not in COST_OPERATIONS:
        problems.append(f"operation {row.get('operation')!r} never has a range")
    if row.get("vehicle_class") not in VEHICLE_CLASSES:
        problems.append(f"vehicle class {row.get('vehicle_class')!r} never has a range")
    if not row.get("part_code") or not row.get("range_id"):
        problems.append("part_code and range_id are required")
    if row.get("currency") != currency or row.get("cost_basis") != cost_basis:
        problems.append(f"basis or currency mixing: {row.get('currency')}/{row.get('cost_basis')} "
                        f"in a {currency}/{cost_basis} table")
    if row.get("method") not in METHODS:
        problems.append(f"unknown method {row.get('method')!r}")
    if row.get("synthetic") is not True:
        problems.append("every row must carry synthetic = true")
    support, records = row.get("independent_base_case_count"), row.get("record_count")
    if not (_count(support) and _count(records)):
        problems.append("support and record counts must be nonnegative integers")
    elif support > records:
        problems.append("independent support exceeds the record count")
    status = row.get("support_status")
    if status == "supported":
        lower = _bound(row.get("lower_amount"), "lower_amount", problems)
        upper = _bound(row.get("upper_amount"), "upper_amount", problems)
        if lower is not None and upper is not None:
            if lower > upper:
                problems.append(f"crossed bounds {lower} > {upper}")
            elif upper == 0:
                problems.append("a zero range is never published")
            elif lower == upper and not zero_width_allowed:
                problems.append("zero-width interval is not permitted by this build")
        if row.get("withheld_reason") is not None:
            problems.append("a supported row carries no withheld_reason")
        if min_support is not None and _count(support) and support < min_support:
            problems.append(f"supported with {support} independent base cases, below the minimum {min_support}")
    elif status == "withheld":
        if row.get("lower_amount") is not None or row.get("upper_amount") is not None:
            problems.append("a withheld row carries null bounds, never a zero or fallback range")
        if row.get("withheld_reason") not in WITHHELD_REASONS:
            problems.append(f"withheld row needs a reason from {WITHHELD_REASONS}")
    else:
        problems.append(f"support_status {status!r} is not supported or withheld")
    if not problems:  # the shared contract record must accept every servable row
        try:
            ReferenceCostRange.model_validate({**row, "table_version": row.get("table_version") or "unstamped"})
        except (ValidationError, ContractError) as exc:
            problems.append("not a valid ReferenceCostRange: " + " ".join(str(exc).split())[:300])
    return [f"{label}: {p}" for p in problems]


def validate_ranges(rows: Sequence[Mapping], *, min_support: int, cost_basis: str = COST_BASIS,
                    currency: str = CURRENCY, zero_width_allowed: bool = True,
                    train_records: Iterable[PriceRecord] | None = None) -> None:
    """Raise ``BuildValidationError`` unless every row is servable and support is by base case."""
    problems: list[str] = []
    seen, range_ids = set(), set()
    for row in rows:
        key = tuple(row.get(f) for f in COST_KEY_FIELDS)
        if key in seen or row.get("range_id") in range_ids:
            problems.append(f"{'/'.join(map(str, key))}: duplicate key or range_id")
        seen.add(key)
        range_ids.add(row.get("range_id"))
        problems += row_problems(row, cost_basis=cost_basis, currency=currency, min_support=min_support,
                                 zero_width_allowed=zero_width_allowed)
    if train_records is not None:
        cases: dict[tuple, set] = defaultdict(set)
        bases: dict[tuple, set] = defaultdict(set)
        for record in train_records:
            cases[record.key].add(record.base_case_id)
            bases[record.key].add((record.currency, record.cost_basis))
        for row in rows:
            key = tuple(row.get(f) for f in COST_KEY_FIELDS)
            if bases.get(key, set()) - {(currency, cost_basis)}:
                problems.append(f"{'/'.join(key)}: basis or currency mixing among training records")
            expected = len(cases.get(key, ()))
            if row.get("independent_base_case_count") != expected:
                problems.append(f"{'/'.join(key)}: support {row.get('independent_base_case_count')} is not the "
                                f"{expected} distinct base cases; support counts base cases, never quotes")
    if problems:
        raise BuildValidationError(problems)
