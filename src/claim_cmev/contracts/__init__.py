"""Shared contract records, schema 0.2.0. See docs/specs/data_contracts.md.

This package has no application imports, so every service and container can use it.
Kafka message schemas live in ``contracts.events``; fixture bundles in
``contracts.fixtures``.
"""
from .assessment import (
    Assessment,
    AssessmentFinding,
    CheckResult,
    EvidenceRef,
    ExplanationSentence,
    ProposedRepairAddition,
)
from .claims import ClaimFile, ClaimInput, ReusedArtifact
from .common import (
    COST_BASIS,
    COST_CURRENCY,
    SCHEMA_VERSION,
    ArtifactRef,
    ContractError,
    ContractModel,
    Provenance,
    Reason,
    to_decimal,
)
from .costs import (
    COST_KEY_FIELDS,
    ApprovalRecord,
    CostCheck,
    CostKey,
    GeneratorProvenance,
    ReferenceBuildMember,
    ReferenceCostRange,
    check_cost_key_fields,
    count_independent_base_cases,
)
from .documents import (
    DeclarationCompleteness,
    DocumentPage,
    EffectivePrice,
    FieldUncertainty,
    LineItem,
    PageQuality,
    PageTransform,
    PenMark,
    TextBox,
    TrocrSuggestion,
    effective_price_for,
    with_effective_price,
)
from .imaging import (
    AssignmentCandidate,
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    ImageQuality,
    ImageTransform,
    MaskRef,
    PartCoverage,
    PartPrediction,
    PartSummary,
    ViewScreen,
)
from .review import DECISION_CHANGING_ACTIONS, Finalization, FinalizationPrecondition, ReviewEvent

__all__ = [
    "COST_BASIS", "COST_CURRENCY", "COST_KEY_FIELDS", "DECISION_CHANGING_ACTIONS", "SCHEMA_VERSION",
    "ApprovalRecord", "ArtifactRef", "Assessment", "AssessmentFinding", "AssignmentCandidate", "CheckResult",
    "ClaimFile", "ClaimInput", "ContractError", "ContractModel", "CostCheck", "CostKey", "CoverageConfirmation",
    "DeclarationCompleteness", "DocumentPage", "EffectivePrice", "EvidenceRef", "ExplanationSentence",
    "FieldUncertainty", "Finalization", "FinalizationPrecondition", "GeneratorProvenance", "IdentityConfirmation",
    "ImageDamageObservation", "ImageQuality", "ImageTransform", "LineItem", "MaskRef", "PageQuality", "PageTransform",
    "PartCoverage", "PartPrediction", "PartSummary", "PenMark", "ProposedRepairAddition", "Provenance", "Reason",
    "ReferenceBuildMember", "ReferenceCostRange", "ReusedArtifact", "ReviewEvent", "TextBox", "TrocrSuggestion",
    "ViewScreen", "check_cost_key_fields", "count_independent_base_cases", "effective_price_for", "to_decimal",
    "with_effective_price",
]
