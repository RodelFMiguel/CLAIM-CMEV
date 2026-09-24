"""M8 consolidation and checks: deterministic rules only, no model and no language model.

Entry points: ``consolidate`` (pure, rules R1 to R12 and additions A1 to A8),
``compare_amount`` (pure Decimal cost arithmetic), ``propose_additions``, the frozen
reason-code catalogue and ``load_rule_config``. ``cmev-consolidator`` wires ``consolidate``
to ``cmev.cmd.consolidate.v1``; this package performs no I/O except ``load_rule_config``.
"""
from .adapter import (
    ConsolidationRequest,
    ConsolidationResult,
    bundle_versions,
    consolidate,
    job_key_for,
    request_from_bundle,
)
from .additions import declaration_gate, missing_repairs_check, propose_additions
from .config import RuleConfig, load_rule_config
from .cost_check import RangeLookup, RangeResultLike, compare_amount, cost_check_not_evaluated, cost_key, cost_rule_id
from .lineage import CarriedDismissal, carry_forward_dismissals, content_hash, dismissal_carries_forward
from .reason_codes import (
    REASON_CATALOGUE_VERSION,
    REASON_CODES,
    SYNTHETIC_COST_NOTICE,
    ReasonCodeEntry,
    catalogue,
    display_text,
    is_known,
    reason,
    require_code,
)

__all__ = [
    "REASON_CATALOGUE_VERSION", "REASON_CODES", "SYNTHETIC_COST_NOTICE", "CarriedDismissal", "ConsolidationRequest",
    "ConsolidationResult", "RangeLookup", "RangeResultLike", "ReasonCodeEntry", "RuleConfig", "bundle_versions",
    "carry_forward_dismissals", "catalogue", "compare_amount", "consolidate", "content_hash", "cost_check_not_evaluated",
    "cost_key", "cost_rule_id", "declaration_gate", "dismissal_carries_forward", "display_text", "is_known",
    "job_key_for", "load_rule_config", "missing_repairs_check", "propose_additions", "reason", "request_from_bundle",
    "require_code",
]
