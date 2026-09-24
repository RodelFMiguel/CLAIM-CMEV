"""Image-branch records: M1 predictions, M2 observations, M3 summaries and coverage.

Data contracts section 6 and integration contracts section 3.3. Side is never produced
by a model: M1 and M2 rows carry ``side = "unknown"``. Only a recorded
``IdentityConfirmation`` attaches a side, and ``adequate`` coverage requires a recorded
``CoverageConfirmation``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import (
    RESOLVED_SIDES,
    BoxNorm,
    ClaimRecord,
    ClaimScoped,
    Confidence,
    ContractModel,
    DamageCode,
    PartCode,
    ReasonCode,
    RecordId,
    Revision,
    Sha256,
    Side,
    UtcDatetime,
)

PartReason = Literal["no_part_overlap", "below_containment_threshold", "ambiguous_between_parts",
                     "part_not_accepted", "mostly_background", "part_masks_missing"]
CoverageState = Literal["adequate", "inadequate", "not_visible", "unresolved"]
IdentityStatus = Literal["resolved", "part_only", "unresolved"]
AREA_TOLERANCE = 5e-4
"""Allowed rounding between ``area_fraction`` and ``area_pixels / denominator``."""


class MaskRef(ContractModel):
    """A raster mask artifact: dimensions, class encoding and the source photo."""

    artifact_id: RecordId
    object_uri: str = Field(min_length=1)
    sha256: Sha256
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    encoding: str = Field(min_length=1)
    source_photo_id: RecordId
    component_index: int | None = Field(default=None, ge=1)


# EXIF orientation -> (clockwise display rotation, horizontal mirror applied before rotating)
_EXIF = {1: (0, False), 2: (0, True), 3: (180, False), 4: (180, True),
         5: (270, True), 6: (90, False), 7: (90, True), 8: (270, False)}


class ImageTransform(ContractModel):
    """Preprocessing between the stored photo and the model frame, and its inverse.

    The *original* frame is the photo as displayed, after EXIF orientation. The model
    frame is that image resized by ``scale`` and padded by ``pad_left``/``pad_top``
    (letterbox). ``bbox_norm`` values are normalised to the original frame.
    """

    stored_width: int = Field(gt=0)
    stored_height: int = Field(gt=0)
    exif_orientation: int = Field(default=1, ge=1, le=8)
    model_width: int = Field(gt=0)
    model_height: int = Field(gt=0)
    scale: float = Field(gt=0)
    pad_left: float = Field(default=0.0, ge=0)
    pad_top: float = Field(default=0.0, ge=0)
    mask_frame: Literal["model"] = "model"

    @property
    def original_size(self) -> tuple[int, int]:
        rotation, _ = _EXIF[self.exif_orientation]
        if rotation in (90, 270):
            return self.stored_height, self.stored_width
        return self.stored_width, self.stored_height

    def model_to_original(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.pad_left) / self.scale, (y - self.pad_top) / self.scale)

    def original_to_model(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.scale + self.pad_left, y * self.scale + self.pad_top)

    def model_box_to_original_norm(self, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """Invert letterboxing for a model-frame pixel box and normalise it, clipped to [0, 1]."""
        w, h = self.original_size
        (x0, y0), (x1, y1) = self.model_to_original(box[0], box[1]), self.model_to_original(box[2], box[3])
        clip = lambda v: min(1.0, max(0.0, v))  # noqa: E731
        return (clip(x0 / w), clip(y0 / h), clip(x1 / w), clip(y1 / h))

    def original_to_stored(self, x: float, y: float) -> tuple[float, float]:
        """Map a displayed-frame point back to the stored (pre-EXIF) pixel grid."""
        rotation, mirrored = _EXIF[self.exif_orientation]
        w, h = self.original_size
        # undo the display rotation (clockwise) first, then the mirror
        if rotation == 90:
            x, y = y, w - x
        elif rotation == 180:
            x, y = w - x, h - y
        elif rotation == 270:
            x, y = h - y, x
        if mirrored:
            x = self.stored_width - x
        return (x, y)

    def stored_to_original(self, x: float, y: float) -> tuple[float, float]:
        rotation, mirrored = _EXIF[self.exif_orientation]
        w, h = self.original_size
        if mirrored:
            x = self.stored_width - x
        if rotation == 90:
            x, y = w - y, x
        elif rotation == 180:
            x, y = w - x, h - y
        elif rotation == 270:
            x, y = y, h - x
        return (x, y)


class PartPrediction(ClaimRecord):
    """One predicted part class present on one photo (M1). Side is always unknown."""

    prediction_id: RecordId
    photo_id: RecordId
    part_code: PartCode
    side: Literal["unknown"] = "unknown"
    mask_ref: MaskRef
    mean_confidence: Confidence
    pixel_count: int = Field(ge=0)
    accepted: bool = True
    transform: ImageTransform


class ImageQuality(ClaimRecord):
    """Photo-level screening signals. They never establish whole-panel visibility."""

    photo_id: RecordId
    state: Literal["acceptable", "limited", "unusable", "not_assessed"]
    blur_score: float | None = None
    exposure_state: Literal["normal", "under", "over", "not_assessed"] = "not_assessed"
    obstruction_flag: bool | None = None
    size_limited: bool | None = None
    reasons: list[ReasonCode] = Field(default_factory=list)
    config_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _reasons(self) -> ImageQuality:
        if self.state != "acceptable" and not self.reasons:
            raise ValueError(f"image quality {self.state!r} needs a reason")
        return self


class AssignmentCandidate(ContractModel):
    part_code: PartCode
    containment: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)


class ImageDamageObservation(ClaimRecord):
    """One surviving damage region on one photo (M2), under the CarDD taxonomy only."""

    observation_id: RecordId
    photo_id: RecordId
    damage_code: DamageCode
    damage_confidence: Confidence
    assignment_status: Literal["assigned", "unresolved"]
    part_code: PartCode | None
    part_reason: PartReason | None = None
    side: Literal["unknown"] = "unknown"
    side_reason: ReasonCode = "hitl_labels_unsided"
    candidates: list[AssignmentCandidate]
    primary_containment: float = Field(ge=0.0, le=1.0)
    runner_up_containment: float | None = Field(ge=0.0, le=1.0)
    background_containment: float = Field(ge=0.0, le=1.0)
    area_pixels: int = Field(ge=0)
    area_fraction: float = Field(ge=0.0, le=1.0)
    area_denominator: Literal["model_frame_pixels"] = "model_frame_pixels"
    area_denominator_pixels: int = Field(gt=0)
    damage_mask_ref: MaskRef
    part_mask_ref: MaskRef | None
    bbox_norm: BoxNorm
    assignment_config_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> ImageDamageObservation:
        taxonomy = self.versions.get("taxonomy", "")
        if not taxonomy.startswith("damage-cardd-"):
            raise ValueError("versions['taxonomy'] must name the CarDD damage taxonomy (damage-cardd-x.y.z)")
        if self.assignment_status == "assigned":
            if self.part_code is None or self.part_reason is not None:
                raise ValueError("an assigned observation has a part_code and no part_reason")
        elif self.part_code is not None or self.part_reason is None:
            raise ValueError("an unresolved observation has a null part_code with part_reason")
        if self.part_mask_ref is None and self.part_reason != "part_masks_missing":
            raise ValueError("part_mask_ref is null only with part_reason 'part_masks_missing'")
        ranks = [c.rank for c in self.candidates]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("candidates are ranked 1..n in order")
        if any(a.containment < b.containment for a, b in zip(self.candidates, self.candidates[1:])):
            raise ValueError("candidates are ordered by descending containment")
        if self.candidates:
            if abs(self.candidates[0].containment - self.primary_containment) > 1e-9:
                raise ValueError("primary_containment is the first candidate's containment")
            if self.part_code is not None and self.candidates[0].part_code != self.part_code:
                raise ValueError("an assigned part is the first-ranked candidate")
        elif self.part_code is not None:
            raise ValueError("an assigned observation retains its candidate list")
        if abs(self.area_pixels / self.area_denominator_pixels - self.area_fraction) > AREA_TOLERANCE:
            raise ValueError("area_fraction must equal area_pixels over the recorded denominator")
        return self


class PartSummary(ClaimRecord):
    """One M3 group. It carries no count of physical damage instances."""

    summary_id: RecordId
    identity_status: IdentityStatus
    part_code: PartCode | None
    side: Side
    member_observation_ids: list[RecordId] = Field(min_length=1)
    observation_count: int = Field(ge=1)
    damage_codes: list[DamageCode]
    supporting_photo_ids: list[RecordId] = Field(min_length=1)
    mask_refs: list[MaskRef] = Field(default_factory=list)
    aggregation_method: Literal["max_member"] = "max_member"
    representative_area_fraction: float = Field(ge=0.0, le=1.0)
    representative_observation_id: RecordId
    max_confidence: Confidence
    identity_confirmation_ids: list[RecordId] = Field(default_factory=list)
    reasons: list[ReasonCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> PartSummary:
        members = self.member_observation_ids
        if len(set(members)) != len(members) or self.observation_count != len(members):
            raise ValueError("observation_count equals the number of distinct member observations")
        if self.representative_observation_id not in members:
            raise ValueError("the representative area comes from one member, never a sum")
        if len(set(self.damage_codes)) != len(self.damage_codes):
            raise ValueError("damage_codes is a set")
        if self.identity_status == "resolved":
            if self.part_code is None or self.side not in RESOLVED_SIDES or not self.identity_confirmation_ids:
                raise ValueError("a resolved group has a part, a resolved side and its identity confirmation")
        elif self.identity_status == "part_only":
            if self.part_code is None or self.side != "unknown":
                raise ValueError("a part_only group has a part code and side 'unknown'")
        elif self.part_code is not None or self.side != "unknown" or len(members) != 1:
            raise ValueError("an unresolved group holds one observation with no part and side 'unknown'")
        return self


class ViewScreen(ContractModel):
    """Screening signals for one photo of one slot. Obstruction is never inferred."""

    photo_id: RecordId
    screen_result: Literal["pass", "fail", "not_run"]
    signals: dict[str, float] = Field(default_factory=dict)
    reasons: list[ReasonCode] = Field(default_factory=list)


class PartCoverage(ClaimRecord):
    """Coverage for one (part, side) slot. Recorded for every slot, damaged or not."""

    coverage_id: RecordId
    part_code: PartCode
    side: Side
    state: CoverageState
    covering_photo_ids: list[RecordId] = Field(default_factory=list)
    views: list[ViewScreen] = Field(default_factory=list)
    reasons: list[ReasonCode] = Field(default_factory=list)
    coverage_confirmation_id: RecordId | None = None
    identity_confirmation_ids: list[RecordId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> PartCoverage:
        if self.state == "adequate":
            if self.coverage_confirmation_id is None:
                raise ValueError("'adequate' coverage requires a recorded coverage confirmation")
            if not self.covering_photo_ids:
                raise ValueError("'adequate' coverage names its covering photos")
        elif not self.reasons:
            raise ValueError(f"coverage {self.state!r} needs a reason code")
        if self.side == "unknown" and self.state != "unresolved":
            raise ValueError("a slot with unknown side is 'unresolved'; left or right coverage is never fabricated")
        return self


class _HumanConfirmation(ClaimScoped):
    confirmation_id: RecordId
    actor: str = Field(min_length=1)
    recorded_at: UtcDatetime
    review_revision: Revision
    note: str | None = None
    source: Literal["human"] = "human"

    @model_validator(mode="after")
    def _human(self) -> _HumanConfirmation:
        if self.provenance.source_kind not in ("real", "fixture"):
            raise ValueError("a confirmation is a real human action (or a labelled fixture)")
        return self


class IdentityConfirmation(_HumanConfirmation):
    """A surveyor's statement that a photo shows one physical part and side."""

    photo_id: RecordId
    part_code: PartCode
    side: Literal["left", "right", "centre", "not_applicable"]


class CoverageConfirmation(_HumanConfirmation):
    """A surveyor's statement that the named views show enough of one physical part."""

    part_code: PartCode
    side: Literal["left", "right", "centre", "not_applicable"]
    covering_photo_ids: list[RecordId] = Field(min_length=1)
    covers_enough: bool
    reason: ReasonCode | None = None

    @model_validator(mode="after")
    def _reason(self) -> CoverageConfirmation:
        if not self.covers_enough and not self.reason:
            raise ValueError("a 'does not cover enough' confirmation carries a reason")
        return self
