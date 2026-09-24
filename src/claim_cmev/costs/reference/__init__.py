"""M7 synthetic reference cost ranges: generator, grouped splits, empirical fit, publication and lookup.

The runtime surface is `load_table`, `lookup_range` and `PinnedCostTable.lookup`; nothing
here runs a model inside a claim request. All prices are synthetic.
"""
from .lookup import (
    CostTableError, LookupKey, PinnedCostTable, RangeResult, active_table_version, list_tables, load_table,
    lookup_range,
)
from .vocabulary import COST_BASIS, COST_KEY_FIELDS, CURRENCY, LOOKUP_REASON_CODES

__all__ = [
    "COST_BASIS", "COST_KEY_FIELDS", "CURRENCY", "LOOKUP_REASON_CODES", "CostTableError", "LookupKey",
    "PinnedCostTable", "RangeResult", "active_table_version", "list_tables", "load_table", "lookup_range",
]
