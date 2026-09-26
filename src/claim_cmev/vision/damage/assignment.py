"""Deterministic damage-to-part assignment by mask containment (M2 steps 8-14).

Integration contracts section 3 is the boundary contract. For each damage region,
``containment(part) = |region and part| / |region|`` against the M1 part mask on the
same grid. A part is assigned only when every condition holds; otherwise the region
keeps unknown part identity with a reason code and its ranked candidates. When several
conditions fail, the first reason in ``REASON_PRECEDENCE`` is recorded; that order
reproduces the M2 worked example (a region 84 percent on background is
``mostly_background``; a 0.47/0.44 split is ``ambiguous_between_parts``).

Side is never resolved here: every observation carries ``side = "unknown"`` with reason
``hitl_labels_unsided``. Nothing is inferred from image position, orientation, other
parts or file names. Mismatched grids fail with ``mask_geometry_mismatch``; a class map
is never resampled. Decisions use exact fractions; stored values are rounded to six
decimal places.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from ...contracts.common import PART_CODES, ContractError, Provenance, deterministic_id
from ...contracts.imaging import AssignmentCandidate, ImageDamageObservation, MaskRef
from ...contracts.imaging import ImageTransform as ContractImageTransform
from ..transforms import ImageTransform as FrameTransform
from ..transforms import box_to_quad, map_points, normalise_box, quad_to_box
from .config import AssignmentConfig, AssignmentRule
from .regions import BACKGROUND_ID, RegionSet, extract_regions

SIDE = "unknown"
SIDE_REASON = "hitl_labels_unsided"
REASON_PRECEDENCE = ("part_masks_missing", "no_part_overlap", "mostly_background", "ambiguous_between_parts",
                     "part_not_accepted", "below_containment_threshold")
"""First failing condition wins. All six are the contract ``PartReason`` values."""
REQUIRED_VERSIONS = ("damage_model", "parts_model", "taxonomy", "assignment_config")
_PART_ORDER = {code: i for i, code in enumerate(PART_CODES)}
_STORE_DIGITS = 6


def _exact(value: Fraction | float | int) -> Fraction:
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _store(value: Fraction) -> float:
    return round(float(value), _STORE_DIGITS)


@dataclass(frozen=True)
class PartAssignment:
    """The decision for one region. ``candidates`` is ranked and always retained."""

    assignment_status: Literal["assigned", "unresolved"]
    part_code: str | None
    part_reason: str | None
    candidates: tuple[AssignmentCandidate, ...]
    primary_containment: float
    runner_up_containment: float | None
    background_containment: float


def decide_assignment(containments: Mapping[str, Fraction | float], background: Fraction | float,
                      accepted_parts: Collection[str], rule: AssignmentRule) -> PartAssignment:
    """Apply the assignment conditions to measured containments (pure, no pixels)."""
    ranked = sorted(((code, _exact(value)) for code, value in containments.items() if _exact(value) > 0),
                    key=lambda item: (-item[1], _PART_ORDER.get(item[0], len(_PART_ORDER)), item[0]))
    candidates = tuple(AssignmentCandidate(part_code=code, containment=_store(value), rank=rank)
                       for rank, (code, value) in enumerate(ranked, start=1))
    background = _exact(background)
    primary = ranked[0][1] if ranked else Fraction(0)
    runner_up = ranked[1][1] if len(ranked) > 1 else None

    if not ranked:
        reason = "no_part_overlap"
    elif background > rule.exact("assign_background_max"):
        reason = "mostly_background"
    elif runner_up is not None and primary - runner_up < rule.exact("assign_ambiguity_margin"):
        reason = "ambiguous_between_parts"
    elif ranked[0][0] not in accepted_parts:
        reason = "part_not_accepted"
    elif primary < rule.exact("assign_min_containment"):
        reason = "below_containment_threshold"
    else:
        reason = None
    return PartAssignment(
        assignment_status="assigned" if reason is None else "unresolved",
        part_code=ranked[0][0] if reason is None else None, part_reason=reason, candidates=candidates,
        primary_containment=_store(primary), runner_up_containment=None if runner_up is None else _store(runner_up),
        background_containment=_store(background))


def missing_part_mask_assignment() -> PartAssignment:
    """No part mask for the photo: the damage stands, part identity is unknown.

    The contract fields are not nullable, so the containments are recorded as 0.0; the
    reason ``part_masks_missing`` says they were not measured.
    """
    return PartAssignment(assignment_status="unresolved", part_code=None, part_reason="part_masks_missing",
                          candidates=(), primary_containment=0.0, runner_up_containment=None,
                          background_containment=0.0)


def measure_containment(component_mask: NDArray, part_mask: NDArray,
                        part_classes: Mapping[int, str]) -> tuple[dict[str, Fraction], Fraction]:
    """Exact containment of one boolean region in every part class, and in background."""
    if component_mask.shape != part_mask.shape:
        raise ContractError("mask_geometry_mismatch", f"region grid {component_mask.shape} != part grid {part_mask.shape}")
    labels = part_mask[component_mask.astype(bool)]
    total = int(labels.size)
    if total == 0:
        raise ContractError("empty_region", "a damage region has at least one pixel")
    ids, counts = np.unique(labels, return_counts=True)
    containments: dict[str, Fraction] = {}
    background = Fraction(0)
    for class_id, count in zip(ids.tolist(), counts.tolist()):
        if class_id == BACKGROUND_ID:
            background = Fraction(count, total)
        elif class_id in part_classes:
            containments[part_classes[class_id]] = Fraction(count, total)
        else:
            raise ContractError("mask_encoding_mismatch", f"part class id {class_id} is not in the class map")
    return containments, background


def assign_component(component_mask: NDArray, part_mask: NDArray | None, part_classes: Mapping[int, str],
                     accepted_parts: Collection[str], rule: AssignmentRule) -> PartAssignment:
    """Assign one boolean region against a part class-index mask on the same grid."""
    if part_mask is None:
        return missing_part_mask_assignment()
    containments, background = measure_containment(component_mask, part_mask, part_classes)
    return decide_assignment(containments, background, accepted_parts, rule)


# ---------------------------------------------------------------- photo level
@dataclass(frozen=True)
class ObservationContext:
    """Identity and references copied onto every observation of one photo."""

    claim_id: str
    input_revision: int
    photo_id: str
    job_key: str
    versions: Mapping[str, str]
    provenance: Provenance
    damage_mask_ref: MaskRef
    part_mask_ref: MaskRef | None


@dataclass(frozen=True)
class DamageAssignmentResult:
    photo_id: str
    observations: tuple[ImageDamageObservation, ...]
    component_labels: NDArray[np.uint16]
    """Component-index raster for ``components.png``: value ``i`` is ``component_index`` ``i``."""
    damage_mask_ref: MaskRef
    dropped_low_confidence_pixels: int
    below_min_pixels_count: int
    max_components_exceeded_count: int
    reasons: tuple[str, ...]

    @property
    def observation_ids(self) -> tuple[str, ...]:
        return tuple(o.observation_id for o in self.observations)

    @property
    def dropped_region_count(self) -> int:
        return self.below_min_pixels_count + self.max_components_exceeded_count

    @property
    def unknown_part_count(self) -> int:
        return sum(o.part_code is None for o in self.observations)

    @property
    def empty_result(self) -> bool:
        """A successful run with no surviving region; never proof the vehicle is undamaged."""
        return not self.observations

    def event_payload(self) -> dict[str, Any]:
        """The ``cmev.evt.damage-segmented.v1`` payload (``damage_code`` travels as ``damage_type``)."""
        ref = self.damage_mask_ref
        return {
            "photo_id": self.photo_id,
            "damage_mask_ref": {"artifact_id": ref.artifact_id, "object_uri": ref.object_uri, "sha256": ref.sha256,
                                "width": ref.width, "height": ref.height, "encoding": ref.encoding},
            "observations": [_event_observation(o) for o in self.observations],
            "observation_ids": list(self.observation_ids),
            "dropped_region_count": self.dropped_region_count,
            "unknown_part_count": self.unknown_part_count,
            "empty_result": self.empty_result,
        }


def _event_observation(o: ImageDamageObservation) -> dict[str, Any]:
    return {
        "observation_id": o.observation_id, "photo_id": o.photo_id, "damage_type": o.damage_code,
        "damage_confidence": o.damage_confidence, "assignment_status": o.assignment_status,
        "part_code": o.part_code, "part_reason": o.part_reason, "side": o.side, "side_reason": o.side_reason,
        "candidates": [c.model_dump(mode="json") for c in o.candidates],
        "primary_containment": o.primary_containment, "runner_up_containment": o.runner_up_containment,
        "background_containment": o.background_containment, "area_pixels": o.area_pixels,
        "area_fraction": o.area_fraction, "bbox_norm": list(o.bbox_norm),
        "damage_mask_ref": {"artifact_id": o.damage_mask_ref.artifact_id,
                            "component_index": o.damage_mask_ref.component_index},
        "part_mask_ref": None if o.part_mask_ref is None else {"artifact_id": o.part_mask_ref.artifact_id},
        "versions": dict(o.versions), "assignment_config_version": o.assignment_config_version,
    }


def _check_part_classes(part_classes: Mapping[int, str], accepted_parts: Collection[str]) -> None:
    if BACKGROUND_ID in part_classes:
        raise ContractError("mask_encoding_mismatch", "class id 0 is background, not a part class")
    codes = list(part_classes.values())
    if set(codes) - set(PART_CODES) or set(accepted_parts) - set(PART_CODES):
        raise ContractError("taxonomy_version_mismatch", "part classes must be canonical part codes")
    if len(set(codes)) != len(codes):
        raise ContractError("mask_encoding_mismatch", "two class ids map to one part code")


def _check_geometry(shape: tuple[int, ...], confidence: NDArray, part_mask: NDArray | None,
                    context: ObservationContext, transform: Any) -> None:
    height, width = shape
    grids = {"confidence": confidence.shape}
    if part_mask is not None:
        grids["part_mask"] = part_mask.shape
    for name, ref in (("damage_mask_ref", context.damage_mask_ref), ("part_mask_ref", context.part_mask_ref)):
        if ref is not None:
            grids[name] = (ref.height, ref.width)
    if isinstance(transform, ContractImageTransform):
        grids["transform"] = (transform.model_height, transform.model_width)
    elif isinstance(transform, FrameTransform):
        grids["transform"] = (transform.frame_height, transform.frame_width)
    elif transform is not None:
        raise TypeError(f"unsupported transform type {type(transform).__name__}")
    wrong = {name: grid for name, grid in grids.items() if tuple(grid) != (height, width)}
    if wrong:
        raise ContractError("mask_geometry_mismatch", f"damage grid {(height, width)} differs from {wrong}; "
                                                      "class maps are never resampled")


def _check_context(context: ObservationContext, config: AssignmentConfig, part_mask: NDArray | None,
                   part_mask_model_version: str | None) -> None:
    missing = [key for key in REQUIRED_VERSIONS if not context.versions.get(key)]
    if missing:
        raise ContractError("versions_incomplete", f"observation versions lack {missing}")
    if context.versions["assignment_config"] != config.config_version:
        raise ContractError("assignment_config_version_mismatch",
                            f"{context.versions['assignment_config']!r} != {config.config_version!r}")
    if part_mask_model_version is not None and part_mask_model_version != context.versions["parts_model"]:
        raise ContractError("parts_version_mismatch",
                            f"part mask from {part_mask_model_version!r}, job pins {context.versions['parts_model']!r}")
    if part_mask is not None and context.part_mask_ref is None:
        raise ContractError("part_mask_ref_missing", "a supplied part mask needs its artifact reference")
    for ref in (context.damage_mask_ref, context.part_mask_ref):
        if ref is not None and ref.source_photo_id != context.photo_id:
            raise ContractError("photo_mismatch", f"mask {ref.artifact_id} belongs to photo {ref.source_photo_id}")


def bbox_to_original_norm(bbox_model: tuple[int, int, int, int], grid: tuple[int, int],
                          transform: ContractImageTransform | FrameTransform | None) -> tuple[float, ...]:
    """Model-frame pixel box to ``bbox_norm`` on the original (EXIF-oriented) photo.

    Without a transform the mask grid is taken as the original frame; the adapter always
    passes the M1 transform for letterboxed frames.
    """
    height, width = grid
    if transform is None:
        box = normalise_box(bbox_model, width, height)
    elif isinstance(transform, ContractImageTransform):
        box = transform.model_box_to_original_norm(tuple(float(v) for v in bbox_model))
    else:
        quad = map_points(transform.model_to_oriented(), box_to_quad(bbox_model))
        box = normalise_box(quad_to_box(quad), transform.oriented_width, transform.oriented_height)
    return tuple(round(v, _STORE_DIGITS) for v in box)


def assign_damage_to_part(
    damage_mask: NDArray,
    part_mask: NDArray | None,
    *,
    confidence: NDArray,
    config: AssignmentConfig,
    damage_classes: Mapping[int, str],
    part_classes: Mapping[int, str],
    accepted_parts: Collection[str],
    context: ObservationContext,
    transform: ContractImageTransform | FrameTransform | None = None,
    part_mask_model_version: str | None = None,
) -> DamageAssignmentResult:
    """Extract damage regions on one photo and assign each to a part (pure, deterministic).

    ``damage_mask`` and ``part_mask`` are class-index arrays on one model-frame grid
    (0 is background); ``confidence`` is the per-pixel damage softmax maximum.
    ``part_mask = None`` records every region as ``part_masks_missing``; the damage still
    stands. Observation IDs derive from the job key, photo ID and region index, so a
    replay reproduces them.
    """
    if damage_mask.ndim != 2:
        raise ContractError("mask_geometry_mismatch", "a damage mask is a 2-D class-index array")
    grid = damage_mask.shape
    _check_context(context, config, part_mask, part_mask_model_version)
    _check_geometry(grid, confidence, part_mask, context, transform)
    _check_part_classes(part_classes, accepted_parts)
    if part_mask is not None:
        present = {int(v) for v in np.unique(part_mask)} - {BACKGROUND_ID}
        if present - set(part_classes):
            raise ContractError("mask_encoding_mismatch",
                                f"part class ids {sorted(present - set(part_classes))} are not in the class map")

    region_set: RegionSet = extract_regions(damage_mask, confidence, damage_classes, config.regions)
    denominator = int(grid[0] * grid[1])
    part_ref = context.part_mask_ref if part_mask is not None else None
    observations = []
    for region in region_set.regions:
        decision = assign_component(region_set.labels == region.index, part_mask, part_classes, accepted_parts,
                                    config.assignment)
        observations.append(ImageDamageObservation(
            claim_id=context.claim_id, input_revision=context.input_revision, provenance=context.provenance,
            versions=dict(context.versions),
            observation_id=deterministic_id("ob", context.job_key, context.photo_id, region.index),
            photo_id=context.photo_id, damage_code=region.damage_code,
            damage_confidence=min(1.0, max(0.0, round(region.mean_confidence, _STORE_DIGITS))),
            assignment_status=decision.assignment_status, part_code=decision.part_code,
            part_reason=decision.part_reason, side=SIDE, side_reason=SIDE_REASON,
            candidates=list(decision.candidates), primary_containment=decision.primary_containment,
            runner_up_containment=decision.runner_up_containment,
            background_containment=decision.background_containment, area_pixels=region.pixel_count,
            area_fraction=round(region.pixel_count / denominator, _STORE_DIGITS),
            area_denominator_pixels=denominator,
            damage_mask_ref=context.damage_mask_ref.model_copy(update={"component_index": region.index}),
            part_mask_ref=part_ref, bbox_norm=bbox_to_original_norm(region.bbox_model, grid, transform),
            assignment_config_version=config.config_version))

    reasons = list(region_set.reasons)
    if part_mask is None:
        reasons.append("part_masks_missing")
    if not observations:
        reasons.append("no_damage_regions")
    return DamageAssignmentResult(
        photo_id=context.photo_id, observations=tuple(observations), component_labels=region_set.labels,
        damage_mask_ref=context.damage_mask_ref, dropped_low_confidence_pixels=region_set.dropped_low_confidence_pixels,
        below_min_pixels_count=region_set.below_min_pixels_count,
        max_components_exceeded_count=region_set.max_components_exceeded_count, reasons=tuple(reasons))
