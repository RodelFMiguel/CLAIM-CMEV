"""M3 part summary and coverage (docs/specs/module-03-part-summary-coverage.md).

Deterministic and weight-free: grouping of M2 observations under a part identity,
the four view-screening signals, the ordered coverage table, the confirmation index and
the reuse lineage. The Kafka consumer shell and ``run_part_summary`` adapter are not
implemented here. The specification proposes renaming this package ``vision/summary``;
the existing ``vision/multiview`` name is kept.
"""
from .config import SummaryConfig, SummarySettings, load_summary_config
from .confirmations import (
    CONFIRMATION_SET_KEY,
    ConfirmationIndex,
    ReuseLineage,
    SummaryContext,
    build_confirmation_index,
    check_inputs,
    confirmation_set_signature,
    summary_job_key,
    summary_job_versions,
)
from .coverage import (
    BRANCH_STATUSES,
    COVERAGE_RULES,
    COVERAGE_STATES,
    CoverageDecision,
    CoverageRule,
    CoverageSlot,
    SlotFacts,
    build_slots,
    decide_coverage,
    decide_slot,
)
from .grouping import group_key, group_observations
from .screening import SIGNAL_NAMES, compute_view_signals, screen_view
from .summary import PartSummaryOutcome, summarise_parts

__all__ = [
    "BRANCH_STATUSES", "CONFIRMATION_SET_KEY", "COVERAGE_RULES", "COVERAGE_STATES", "SIGNAL_NAMES",
    "ConfirmationIndex", "CoverageDecision", "CoverageRule", "CoverageSlot", "PartSummaryOutcome", "ReuseLineage",
    "SlotFacts", "SummaryConfig", "SummaryContext", "SummarySettings", "build_confirmation_index", "build_slots",
    "check_inputs", "compute_view_signals", "confirmation_set_signature", "decide_coverage", "decide_slot",
    "group_key", "group_observations", "load_summary_config", "screen_view", "summarise_parts", "summary_job_key",
    "summary_job_versions",
]
