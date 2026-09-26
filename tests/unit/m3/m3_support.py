"""Shared helpers for the M3 rule tests. Every record here is a SYNTHETIC fixture."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any

from claim_cmev.contracts.common import Provenance
from claim_cmev.contracts.imaging import (
    AssignmentCandidate,
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    ImageTransform,
    MaskRef,
    PartPrediction,
)
from claim_cmev.vision.multiview import SummaryContext, load_summary_config, summarise_parts, summary_job_key

CLAIM_ID = "01K6F1XTVRE000000000000200"
FRAME = 512
CFG = load_summary_config()
BASE_TIME = datetime(2026, 9, 24, 2, 0, tzinfo=UTC)
PARTS_VERSIONS = {"parts_model": "test-parts/0.0.0", "taxonomy": "parts-1.0.0", "code": "test"}
DAMAGE_VERSIONS = {"damage_model": "test-damage/0.0.0", "parts_model": "test-parts/0.0.0",
                   "assignment_config": "m2-assignment/0.1.0", "taxonomy": "damage-cardd-1.0.0", "code": "test"}
SUMMARY_VERSIONS = {"summary_config": CFG.config_version, "taxonomy": "parts-1.0.0", "code": "test"}
TRANSFORM = ImageTransform(stored_width=4032, stored_height=3024, model_width=FRAME, model_height=FRAME,
                           scale=FRAME / 4032, pad_top=64.0)
PASS = {"blur_score": 180.0, "mean_luma": 120.0, "clipped_fraction": 0.01, "border_touch_fraction": 0.05}
CROPPED = {**PASS, "blur_score": 950.0, "border_touch_fraction": 0.46}  # sharp but cut off at the border


def prov(service: str) -> Provenance:
    return Provenance(source_kind="fixture", runtime_profile="lean", producer_service=service,
                      source_dataset_id="synthetic:m3-unit")


def mask(kind: str, photo: str, component: int | None = None) -> MaskRef:
    uri = f"s3://cmev-derived/test/{photo}/{kind}.png"
    return MaskRef(artifact_id=f"am_{kind}_{photo}", object_uri=uri, sha256=hashlib.sha256(uri.encode()).hexdigest(),
                   width=FRAME, height=FRAME, encoding="class_index_png", source_photo_id=photo,
                   component_index=component)


def obs(obs_id: str, photo: str, damage: str, part: str | None, *, reason: str | None = None, area: float = 0.01,
        conf: float = 0.8, rev: int = 1, candidates: Sequence[tuple[str, float]] | None = None) -> ImageDamageObservation:
    pixels = round(area * FRAME * FRAME)
    if candidates is None:
        candidates = [(part, 0.9)] if part else ([("front-door", 0.47), ("fender", 0.44)]
                                                 if reason == "ambiguous_between_parts" else [])
    ranked = [AssignmentCandidate(part_code=p, containment=c, rank=i + 1) for i, (p, c) in enumerate(candidates)]
    return ImageDamageObservation(
        claim_id=CLAIM_ID, input_revision=rev, provenance=prov("cmev-worker-damage"), versions=DAMAGE_VERSIONS,
        observation_id=obs_id, photo_id=photo, damage_code=damage, damage_confidence=conf,
        assignment_status="assigned" if part else "unresolved", part_code=part, part_reason=reason,
        candidates=ranked, primary_containment=ranked[0].containment if ranked else 0.0,
        runner_up_containment=ranked[1].containment if len(ranked) > 1 else None,
        background_containment=0.84 if reason == "mostly_background" else 0.05, area_pixels=pixels,
        area_fraction=round(pixels / FRAME**2, 6), area_denominator_pixels=FRAME**2,
        damage_mask_ref=mask("damage", photo, 1), part_mask_ref=None if reason == "part_masks_missing" else mask("parts", photo),
        bbox_norm=(0.3, 0.4, 0.4, 0.5), assignment_config_version="m2-assignment/0.1.0")


def pred(photo: str, part: str, pixels: int = 40_000, *, accepted: bool = True, rev: int = 1) -> PartPrediction:
    return PartPrediction(claim_id=CLAIM_ID, input_revision=rev, provenance=prov("cmev-worker-parts"),
                          versions=PARTS_VERSIONS, prediction_id=f"pp_{photo}_{part}", photo_id=photo, part_code=part,
                          mask_ref=mask("parts", photo), mean_confidence=0.85, pixel_count=pixels, accepted=accepted,
                          transform=TRANSFORM)


def identity(cid: str, photo: str, part: str, side: str, *, rev: int = 1, review: int = 1) -> IdentityConfirmation:
    return IdentityConfirmation(claim_id=CLAIM_ID, input_revision=rev, provenance=prov("cmev-api"), confirmation_id=cid,
                                actor="surveyor:test", recorded_at=BASE_TIME + timedelta(minutes=review),
                                review_revision=review, photo_id=photo, part_code=part, side=side)


def covers(cid: str, part: str, side: str, photos: Iterable[str], *, enough: bool = True, reason: str | None = None,
           rev: int = 1, review: int = 1) -> CoverageConfirmation:
    return CoverageConfirmation(claim_id=CLAIM_ID, input_revision=rev, provenance=prov("cmev-api"),
                                confirmation_id=cid, actor="surveyor:test",
                                recorded_at=BASE_TIME + timedelta(minutes=review), review_revision=review,
                                part_code=part, side=side, covering_photo_ids=list(photos), covers_enough=enough,
                                reason=reason)


def context(rev: int = 1, *, reuse: int | None = None, confirmation_ids: Iterable[str] = (),
            source_kind: str = "fixture") -> SummaryContext:
    return SummaryContext(
        claim_id=CLAIM_ID, input_revision=rev, job_key=summary_job_key(CLAIM_ID, rev, SUMMARY_VERSIONS, confirmation_ids),
        versions=SUMMARY_VERSIONS, reuse_from_input_revision=reuse,
        provenance=Provenance(source_kind=source_kind, runtime_profile="lean", producer_service="cmev-worker-summary"))


def signals_for(predictions: Iterable[PartPrediction], **special: dict[str, float]) -> dict[tuple[str, str], dict]:
    """Passing pixel signals for every prediction; ``special['<photo>:<part>']`` overrides one view."""
    return {(p.photo_id, p.part_code): dict(special.get(f"{p.photo_id}:{p.part_code}", PASS)) for p in predictions}


def run(observations=(), predictions=(), identities=(), coverages=(), *, rev: int = 1, reuse: int | None = None,
        signals: dict | None = None, **kwargs: Any):
    ids = [c.confirmation_id for c in [*identities, *coverages]]
    return summarise_parts(list(observations), list(predictions), list(identities), list(coverages),
                           context=context(rev, reuse=reuse, confirmation_ids=ids), config=CFG,
                           view_signals=signals_for(predictions) if signals is None else signals, **kwargs)


def slot(outcome, part: str, side: str = "unknown"):
    [found] = [c for c in outcome.coverage if (c.part_code, c.side) == (part, side)]
    return found


def worked_example(rev: int = 1) -> tuple[list[ImageDamageObservation], list[PartPrediction]]:
    """The M3 specification worked example: four photographs, five observations."""
    observations = [
        obs("obs_1", "ph_01", "scratch", "front-door", area=0.0094, rev=rev),
        obs("obs_2", "ph_02", "scratch", "front-door", area=0.0119, rev=rev),
        obs("obs_3", "ph_02", "dent", None, reason="ambiguous_between_parts", rev=rev),
        obs("obs_4", "ph_03", "dent", "fender", rev=rev),
        obs("obs_5", "ph_04", "crack", None, reason="mostly_background", candidates=[("front-door", 0.11)], rev=rev),
    ]
    predictions = [pred("ph_01", "front-door", rev=rev), pred("ph_02", "front-door", rev=rev),
                   pred("ph_02", "fender", rev=rev), pred("ph_03", "fender", rev=rev),
                   pred("ph_04", "back-door", rev=rev)]
    return observations, predictions
