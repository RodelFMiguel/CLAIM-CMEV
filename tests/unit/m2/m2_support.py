"""Shared helpers for the M2 assignment tests. Every mask here is SYNTHETIC test material."""
from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
import hashlib
from typing import Any

import numpy as np

from claim_cmev.contracts.common import PART_CODES, Provenance, make_job_key
from claim_cmev.contracts.events.envelope import Envelope
from claim_cmev.contracts.events.registry import validate_message
from claim_cmev.contracts.imaging import MaskRef
from claim_cmev.vision.damage import (
    AssignmentConfig,
    DamageAssignmentResult,
    ObservationContext,
    assign_damage_to_part,
    load_assignment_config,
)

CLAIM_ID = "01K6F1XTVRE000000000000100"
GRID = 128
PHOTO = "ph_test_front_left"
# Test-only class maps. The real maps come from the M1/M2 model manifests.
PART_CLASSES = {i + 1: code for i, code in enumerate(PART_CODES)}
PART_ID = {code: i for i, code in PART_CLASSES.items()}
DAMAGE_CLASSES = {1: "dent", 2: "scratch", 3: "crack", 4: "glass-shatter", 5: "lamp-broken", 6: "tire-flat"}
DAMAGE_ID = {code: i for i, code in DAMAGE_CLASSES.items()}
PROVENANCE = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="cmev-worker-damage",
                        source_dataset_id="synthetic:m2-unit")


def config(**overrides: Any) -> AssignmentConfig:
    base = load_assignment_config()
    return base.with_overrides(overrides) if overrides else base


def versions(cfg: AssignmentConfig) -> dict[str, str]:
    return {"damage_model": "test-damage/0.0.0", "parts_model": "test-parts/0.0.0",
            "assignment_config": cfg.config_version, "taxonomy": "damage-cardd-1.0.0", "code": "test"}


def mask_ref(kind: str, photo_id: str = PHOTO, size: int = GRID) -> MaskRef:
    uri = f"s3://cmev-derived/test/{photo_id}/{kind}.png"
    return MaskRef(artifact_id=f"am_{kind}_{photo_id}", object_uri=uri, sha256=hashlib.sha256(uri.encode()).hexdigest(),
                   width=size, height=size, encoding="class_index_png", source_photo_id=photo_id)


def context(cfg: AssignmentConfig, photo_id: str = PHOTO, *, part_ref: bool = True, size: int = GRID,
            input_revision: int = 1) -> ObservationContext:
    vers = versions(cfg)
    return ObservationContext(
        claim_id=CLAIM_ID, input_revision=input_revision, photo_id=photo_id,
        job_key=make_job_key(CLAIM_ID, input_revision, "damage_segment", vers, photo_id), versions=vers,
        provenance=PROVENANCE, damage_mask_ref=mask_ref("damage", photo_id, size),
        part_mask_ref=mask_ref("parts", photo_id, size) if part_ref else None)


def blank(size: int = GRID) -> np.ndarray:
    return np.zeros((size, size), dtype=np.uint8)


def paint(mask: np.ndarray, value: int, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """Fill the pixel-edge box [x0, x1) x [y0, y1) in place and return the mask."""
    mask[y0:y1, x0:x1] = value
    return mask


def door_and_fender(size: int = GRID) -> np.ndarray:
    """Front-door on columns [0, 64), fender on [64, 128) of rows [16, 112); background elsewhere."""
    parts = blank(size)
    paint(parts, PART_ID["front-door"], 0, 16, 64, 112)
    paint(parts, PART_ID["fender"], 64, 16, 128, 112)
    return parts


def run(damage: np.ndarray, parts: np.ndarray | None, *, cfg: AssignmentConfig | None = None,
        confidence: np.ndarray | None = None, accepted: Collection[str] | None = None,
        photo_id: str = PHOTO, **kwargs: Any) -> DamageAssignmentResult:
    cfg = cfg or config()
    conf = np.full(damage.shape, 0.8) if confidence is None else confidence
    ctx = kwargs.pop("ctx", None) or context(cfg, photo_id, part_ref=parts is not None, size=damage.shape[0])
    return assign_damage_to_part(
        damage, parts, confidence=conf, config=cfg, damage_classes=DAMAGE_CLASSES, part_classes=PART_CLASSES,
        accepted_parts=set(PART_CODES) if accepted is None else accepted, context=ctx, **kwargs)


def validate_event(result: DamageAssignmentResult, cfg: AssignmentConfig) -> dict[str, Any]:
    envelope = Envelope.build("cmev.evt.damage-segmented.v1", claim_id=CLAIM_ID, input_revision=1,
                              task="damage_segment", versions=versions(cfg), provenance=PROVENANCE, trace_id="t-m2",
                              occurred_at=datetime(2026, 9, 24, tzinfo=UTC), target=result.photo_id)
    return validate_message("cmev.evt.damage-segmented.v1", envelope.message(result.event_payload()))
