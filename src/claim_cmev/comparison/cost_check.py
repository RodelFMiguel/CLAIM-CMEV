"""Pure cost comparison (module 08 "Cost comparison arithmetic", rules R9 to R12).

Money is ``Decimal`` from end to end; no binary float appears. Every precondition must
hold before any comparison, and when one fails there is no deviation, no direction and no
bounds at all, only a reason. Equality at a bound is inside the range. The relative score
is display-only and never divides by zero, the amount, a bound or a midpoint.

``RangeLookup`` is satisfied directly by M7's ``PinnedCostTable`` (``claim_cmev.costs.
reference``), whose ``lookup`` returns a ``RangeResult``; rule tests use in-memory tables.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Protocol

from claim_cmev.contracts.common import COST_BASIS, COST_CURRENCY, COST_OPERATIONS, COST_VEHICLE_CLASSES, ContractError
from claim_cmev.contracts.costs import CostCheck, CostKey

from .reason_codes import require_code

AMOUNT_REASONS = frozenset({"amount_unresolved", "amount_unreadable"})
LOOKUP_REASON_RULES = {
    "basis_mismatch": "R10", "currency_unsupported": "R10", "unknown_vehicle_class": "R10",
    "unsupported_combination": "R10", "no_key": "R11", "insufficient_support": "R11", "range_invalid": "R11",
}
"""M7 ``LOOKUP_REASON_CODES`` mapped onto M8 rules; each code is also an M8 catalogue code."""
COST_RULE_BY_REASON = {
    "amount_unresolved": "R9", "amount_unreadable": "R9", "quantity_missing": "R10", "quantity_not_one": "R10",
    **LOOKUP_REASON_RULES, "amount_in_range": "R12", "amount_above_range": "R12", "amount_below_range": "R12",
}


class RangeResultLike(Protocol):
    """The fields of M7 ``RangeResult`` that M8 reads."""

    @property
    def table_version(self) -> str: ...
    @property
    def support_status(self) -> str: ...  # supported | withheld | absent
    @property
    def reason_code(self) -> str | None: ...
    @property
    def lower_amount(self) -> Decimal | None: ...
    @property
    def upper_amount(self) -> Decimal | None: ...
    @property
    def currency(self) -> str: ...
    @property
    def cost_basis(self) -> str: ...
    @property
    def range_id(self) -> str | None: ...
    @property
    def independent_base_case_count(self) -> int | None: ...


class RangeLookup(Protocol):
    """A loaded, frozen reference table pinned by version (M7 ``PinnedCostTable``)."""

    @property
    def table_version(self) -> str: ...
    def lookup(self, key: CostKey) -> RangeResultLike: ...


def _decimal(value: Decimal | str | int | None) -> Decimal | None:
    if value is None or isinstance(value, bool) or isinstance(value, float):
        return None  # floats are refused outright: money is never a binary float
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _text(value: Decimal) -> str:
    return format(value, "f")


def cost_check_not_evaluated(entry_id: str, reason_code: str, *, policy_version: str, cost_table_version: str,
                             amount: Decimal | str | None = None, result: str = "not_evaluated",
                             independent_base_case_count: int | None = None) -> CostCheck:
    """A cost check that compared nothing: no bounds, no range, no direction, no deviation."""
    number = _decimal(amount)
    return CostCheck(entry_id=entry_id, result=result, amount=None if number is None else _text(number),
                     reason_code=require_code(reason_code), independent_base_case_count=independent_base_case_count,
                     policy_version=policy_version, cost_table_version=cost_table_version)


def compare_amount(amount: Decimal | str | None, lookup: RangeResultLike | None, *,
                   quantity: Decimal | str | int | None, currency: str | None, cost_basis: str | None,
                   entry_id: str, policy_version: str, cost_table_version: str | None = None,
                   operation: str | None = None, vehicle_class: str | None = None,
                   amount_reason: str = "amount_unreadable", quantity_must_equal: int = 1,
                   money_places: int = 2, score_places: int = 4) -> CostCheck:
    """Compare one effective price with one pinned range; return the contract ``CostCheck``.

    Preconditions, in order, each failing to ``not_evaluated`` with its own reason: amount
    present and nonnegative (``amount_reason``); quantity present (``quantity_missing``)
    and exactly ``quantity_must_equal`` (``quantity_not_one``); currency and basis equal
    to the table's (``currency_unsupported``, ``basis_mismatch``; ``None`` means unknown
    and fails); operation eligible (``unsupported_combination``); vehicle class known
    (``unknown_vehicle_class``); range supported (the lookup's reason; a support
    shortfall is stored as result ``insufficient_support``); bounds valid
    (``range_invalid``). Then ``a``, ``L`` and ``U`` are quantised once, ROUND_HALF_UP.
    """
    table_version = cost_table_version or (lookup.table_version if lookup is not None else None)
    if not table_version:
        raise ValueError("cost_table_version is required when no lookup result is given")
    if lookup is not None and lookup.table_version != table_version:
        raise ContractError("cost_table_version_mismatch",
                            f"lookup from {lookup.table_version!r}, assessment pins {table_version!r}")
    common = {"policy_version": policy_version, "cost_table_version": table_version}

    a = _decimal(amount)
    if a is None or a < 0:
        reason = amount_reason if amount_reason in AMOUNT_REASONS else "amount_unreadable"
        return cost_check_not_evaluated(entry_id, reason, **common)

    def withheld(reason: str, **extra) -> CostCheck:
        return cost_check_not_evaluated(entry_id, reason, amount=a, **common, **extra)

    q = _decimal(quantity)
    if q is None:
        return withheld("quantity_missing")
    if q != quantity_must_equal:
        return withheld("quantity_not_one")
    table_currency = lookup.currency if lookup is not None else COST_CURRENCY
    table_basis = lookup.cost_basis if lookup is not None else COST_BASIS
    if currency is None or currency != table_currency:
        return withheld("currency_unsupported")
    if cost_basis is None or cost_basis != table_basis:
        return withheld("basis_mismatch")
    if operation is not None and operation not in COST_OPERATIONS:
        return withheld("unsupported_combination")
    if vehicle_class is not None and vehicle_class not in COST_VEHICLE_CLASSES:
        return withheld("unknown_vehicle_class")
    if lookup is None:
        return withheld("no_key")
    if lookup.support_status != "supported":
        reason = lookup.reason_code
        if reason not in LOOKUP_REASON_RULES:
            raise ContractError("reason_code_unknown", f"lookup reason {reason!r} is not an M7 lookup code")
        return withheld(reason, result="insufficient_support" if reason == "insufficient_support" else "not_evaluated",
                        independent_base_case_count=lookup.independent_base_case_count)
    lower, upper = _decimal(lookup.lower_amount), _decimal(lookup.upper_amount)
    if lower is None or upper is None or lower < 0 or lower > upper or not lookup.range_id:
        return withheld("range_invalid", independent_base_case_count=lookup.independent_base_case_count)

    money = Decimal(1).scaleb(-money_places)
    a, lower, upper = (v.quantize(money, rounding=ROUND_HALF_UP) for v in (a, lower, upper))
    if a < lower:
        result, direction, deviation, code = "outside_range", "below", lower - a, "amount_below_range"
    elif a > upper:
        result, direction, deviation, code = "outside_range", "above", a - upper, "amount_above_range"
    else:  # L <= a <= U, equality at either bound included
        result, direction, deviation, code = "within_range", None, Decimal(0).quantize(money), "amount_in_range"
    width = upper - lower
    if width > 0:
        score = (deviation / width).quantize(Decimal(1).scaleb(-score_places), rounding=ROUND_HALF_UP)
        score_text, score_reason = _text(score), None
    else:  # a valid zero-width interval: never divide
        score_text, score_reason = None, require_code("zero_width_interval")
    return CostCheck(entry_id=entry_id, result=result, amount=_text(a), lower_amount=_text(lower),
                     upper_amount=_text(upper), range_id=lookup.range_id, direction=direction,
                     absolute_deviation=_text(deviation), normalised_score=score_text,
                     normalised_score_reason=score_reason,
                     independent_base_case_count=lookup.independent_base_case_count,
                     reason_code=require_code(code), **common)


def cost_rule_id(check: CostCheck) -> str | None:
    """The M8 rule that produced a reached cost check (R9 to R12); ``None`` when not reached."""
    return COST_RULE_BY_REASON.get(check.reason_code or "")


def cost_key(part_code: str | None, operation: str | None, vehicle_class: str | None,
             currency: str | None) -> CostKey | None:
    """The four-field v2 key, or ``None`` when a field cannot form one (never side, damage or year)."""
    if not part_code or operation not in COST_OPERATIONS or vehicle_class not in COST_VEHICLE_CLASSES:
        return None
    if not currency or len(currency) != 3 or not currency.isupper():
        return None
    return CostKey(part_code=part_code, operation=operation, vehicle_class=vehicle_class, currency=currency)


__all__ = ["COST_RULE_BY_REASON", "LOOKUP_REASON_RULES", "RangeLookup", "RangeResultLike", "compare_amount",
           "cost_check_not_evaluated", "cost_key", "cost_rule_id"]
