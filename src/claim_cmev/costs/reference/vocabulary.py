"""M7 cost vocabulary, the fixed cost basis and the reason codes shared with M8.

The cost key is exactly part_code + operation + vehicle_class + currency under one
fixed basis. Side, damage type and model year are never key fields or features. The
constants come from the shared contracts; ``config.load_grid`` also checks the frozen
grid against the versioned taxonomy in ``configs/taxonomy/``.
"""
from __future__ import annotations

from claim_cmev.contracts.common import (
    COST_BASIS, COST_CURRENCY, COST_OPERATIONS, COST_VEHICLE_CLASSES, OPERATIONS, PART_CODES,
)
from claim_cmev.contracts.costs import COST_KEY_FIELDS

CURRENCY = COST_CURRENCY
VEHICLE_CLASSES = COST_VEHICLE_CLASSES  # the four proposed classes; `unknown` never has a range
UNKNOWN_VEHICLE_CLASS = "unknown"
# Any of these in a key, a manifest's cost_key_fields or a feature list marks a v1-shaped table.
FORBIDDEN_KEY_FIELDS = frozenset({"side", "damage_type", "damage_code", "model_year", "year", "make", "model"})
NON_COST_PARTS = frozenset({"front-wheel", "back-wheel", "licence-plate"})

METHODS = ("empirical_percentile", "lightgbm_quantile")
PARTITIONS = ("train", "validation", "calibration", "test")

# Row-level withheld reasons (M7 "Support rule").
INSUFFICIENT_SUPPORT = "insufficient_support"
NO_RECORDS = "no_records"
UNSUPPORTED_COMBINATION = "unsupported_combination"
WITHHELD_REASONS = (INSUFFICIENT_SUPPORT, NO_RECORDS, UNSUPPORTED_COMBINATION)

# Lookup reason codes (application platform section 10, M8 rules R10 and R11).
NO_KEY = "no_key"
UNKNOWN_VEHICLE_CLASS_REASON = "unknown_vehicle_class"
CURRENCY_UNSUPPORTED = "currency_unsupported"
BASIS_MISMATCH = "basis_mismatch"
RANGE_INVALID = "range_invalid"
LOOKUP_REASON_CODES = frozenset({
    NO_KEY, INSUFFICIENT_SUPPORT, UNSUPPORTED_COMBINATION, UNKNOWN_VEHICLE_CLASS_REASON,
    CURRENCY_UNSUPPORTED, BASIS_MISMATCH, RANGE_INVALID,
})

# Record exclusion reasons written to exclusions.csv (M7 "Failure and uncertainty handling").
EXCLUSION_REASONS = frozenset({
    "unknown_part", "non_cost_part", "unknown_operation", "ineligible_operation", UNKNOWN_VEHICLE_CLASS_REASON,
    CURRENCY_UNSUPPORTED, BASIS_MISMATCH, "quantity_not_one", "amount_invalid", UNSUPPORTED_COMBINATION,
    "after_cutoff", "date_invalid", "duplicate_record", "injected_anomaly", "source_not_eligible",
    "base_case_spans_keys", "identifier_missing",
})

SYNTHETIC_NOTICE = (
    "Synthetic reference prices from a documented generator. They test range logic, calibration and "
    "withholding only; they do not establish real repair-price accuracy, market drift or fraud. An interval "
    "exceedance is an unusual synthetic price, not proof of an incorrect price."
)

CostKeyTuple = tuple[str, str, str, str]

__all__ = [
    "BASIS_MISMATCH", "COST_BASIS", "COST_KEY_FIELDS", "COST_OPERATIONS", "CURRENCY", "CURRENCY_UNSUPPORTED",
    "EXCLUSION_REASONS", "FORBIDDEN_KEY_FIELDS", "INSUFFICIENT_SUPPORT", "LOOKUP_REASON_CODES", "METHODS", "NO_KEY",
    "NO_RECORDS", "NON_COST_PARTS", "OPERATIONS", "PARTITIONS", "PART_CODES", "RANGE_INVALID", "SYNTHETIC_NOTICE",
    "UNKNOWN_VEHICLE_CLASS", "UNKNOWN_VEHICLE_CLASS_REASON", "UNSUPPORTED_COMBINATION", "VEHICLE_CLASSES",
    "WITHHELD_REASONS", "CostKeyTuple", "key_text",
]


def key_text(key: CostKeyTuple) -> str:
    return "/".join(key)
