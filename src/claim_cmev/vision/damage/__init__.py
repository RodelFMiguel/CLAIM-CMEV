"""M2 damage regions and deterministic damage-to-part assignment (docs/specs/module-02-*.md).

The SegFormer damage model, its adapter (``run_damage_segmentation``) and the Kafka
consumer shell are not implemented here. These pure functions take the model's class
mask and confidence map plus the M1 part mask on the same grid and return contract
``ImageDamageObservation`` records. Side is always ``unknown``.
"""
from .assignment import (
    REASON_PRECEDENCE,
    SIDE,
    SIDE_REASON,
    DamageAssignmentResult,
    ObservationContext,
    PartAssignment,
    assign_component,
    assign_damage_to_part,
    bbox_to_original_norm,
    decide_assignment,
    measure_containment,
    missing_part_mask_assignment,
)
from .config import AssignmentConfig, AssignmentRule, RegionConfig, load_assignment_config
from .regions import BACKGROUND_ID, DamageRegion, RegionSet, extract_regions

__all__ = [
    "BACKGROUND_ID", "REASON_PRECEDENCE", "SIDE", "SIDE_REASON", "AssignmentConfig", "AssignmentRule",
    "DamageAssignmentResult", "DamageRegion", "ObservationContext", "PartAssignment", "RegionConfig", "RegionSet",
    "assign_component", "assign_damage_to_part", "bbox_to_original_norm", "decide_assignment", "extract_regions",
    "load_assignment_config", "measure_containment", "missing_part_mask_assignment",
]
