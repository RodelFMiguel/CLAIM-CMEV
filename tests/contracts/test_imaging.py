"""Image records: side is never invented, adequate needs a confirmation, transforms round-trip."""
from __future__ import annotations

import random

import pytest
from PIL import Image, ImageOps
from pydantic import ValidationError

from claim_cmev.contracts import (
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    ImageTransform,
    PartCoverage,
    PartPrediction,
    PartSummary,
)
from claim_cmev.contracts.common import DAMAGE_CODES, PART_CODES
from contract_factories import NOW, SCOPE, coverage, mask, observation, transform


def test_observation_valid_and_round_trip():
    obs = ImageDamageObservation(**observation())
    assert obs.side == "unknown" and obs.side_reason == "hitl_labels_unsided"
    assert ImageDamageObservation.model_validate_json(obs.model_dump_json()) == obs


@pytest.mark.parametrize("overrides", [
    {"side": "left"},  # a model never produces a side
    {"damage_code": "corrosion"},  # a v1/HITL label under the CarDD taxonomy
    {"damage_code": "unknown"},
    {"versions": {"taxonomy": "damage-hitl-1.0.0"}},  # contingency taxonomy on a v2 record
    {"part_code": None},  # assigned without a part
    {"assignment_status": "unresolved"},  # unresolved with a part and no reason
    {"part_code": None, "assignment_status": "unresolved"},  # null part without part_reason
    {"part_mask_ref": None},  # null mask without part_masks_missing
    {"area_fraction": 0.5},  # denominator mismatch
    {"candidates": []},  # an assigned observation keeps its candidates
    {"candidates": [{"part_code": "grille", "containment": 0.91, "rank": 1}]},  # assigned part is not rank 1
    {"primary_containment": 0.5},
    {"bbox_norm": (0.5, 0.5, 0.4, 0.6)},  # unordered box
    {"bbox_norm": (0.1, 0.1, 1.2, 0.6)},  # outside [0, 1]
])
def test_observation_invalid(overrides):
    with pytest.raises(ValidationError):
        ImageDamageObservation(**observation(**overrides))


def test_unresolved_observation_keeps_candidates_and_reason():
    obs = ImageDamageObservation(**observation(assignment_status="unresolved", part_code=None,
                                               part_reason="ambiguous_between_parts"))
    assert obs.part_code is None and len(obs.candidates) == 2
    missing = ImageDamageObservation(**observation(assignment_status="unresolved", part_code=None, candidates=[],
                                                   primary_containment=0.0, runner_up_containment=None,
                                                   part_reason="part_masks_missing", part_mask_ref=None))
    assert missing.part_mask_ref is None


def test_side_is_never_invented_by_a_model_fuzz():
    rng = random.Random(20260924)
    for i in range(300):
        parts = rng.sample(PART_CODES, k=rng.randint(1, 3))
        scores = sorted((round(rng.random(), 3) for _ in parts), reverse=True)
        candidates = [{"part_code": p, "containment": s, "rank": r + 1} for r, (p, s) in enumerate(zip(parts, scores))]
        assigned = rng.random() < 0.6
        data = observation(observation_id=f"ob{i}", damage_code=rng.choice(DAMAGE_CODES), candidates=candidates,
                           primary_containment=scores[0], runner_up_containment=scores[1] if len(scores) > 1 else None,
                           assignment_status="assigned" if assigned else "unresolved",
                           part_code=parts[0] if assigned else None,
                           part_reason=None if assigned else "below_containment_threshold")
        assert ImageDamageObservation(**data).side == "unknown"
        with pytest.raises(ValidationError):
            ImageDamageObservation(**{**data, "side": rng.choice(["left", "right", "centre", "not_applicable"])})


def test_part_prediction_side_is_unknown():
    data = {**SCOPE, "versions": {"parts_model": "p-1"}, "prediction_id": "pp1", "photo_id": "ph1",
            "part_code": "front-door", "mask_ref": mask(), "mean_confidence": 0.9, "pixel_count": 4000,
            "transform": transform()}
    assert PartPrediction(**data).side == "unknown"
    with pytest.raises(ValidationError):
        PartPrediction(**data, side="left")


def test_adequate_coverage_requires_a_recorded_confirmation():
    assert PartCoverage(**coverage()).state == "adequate"
    with pytest.raises(ValidationError, match="coverage confirmation"):
        PartCoverage(**coverage(coverage_confirmation_id=None))
    with pytest.raises(ValidationError, match="unknown side"):
        PartCoverage(**coverage(side="unknown"))  # left/right coverage is never fabricated
    with pytest.raises(ValidationError):
        PartCoverage(**coverage(state="not_visible", coverage_confirmation_id=None))  # no reason
    unresolved = PartCoverage(**coverage(side="unknown", state="unresolved", coverage_confirmation_id=None,
                                         covering_photo_ids=[], reasons=["identity_not_resolved"]))
    assert unresolved.state == "unresolved"
    failed = PartCoverage(**coverage(side="unknown", state="unresolved", coverage_confirmation_id=None,
                                     covering_photo_ids=[], reasons=["processing_failed"]))
    assert failed.reasons == ["processing_failed"]


def summary(**kw) -> dict:
    data = {**SCOPE, "versions": {"summary_config": "s-1"}, "summary_id": "ps1", "identity_status": "resolved",
            "part_code": "front-door", "side": "left", "member_observation_ids": ["ob1", "ob2"],
            "observation_count": 2, "damage_codes": ["scratch"], "supporting_photo_ids": ["ph1", "ph2"],
            "representative_area_fraction": 0.0119, "representative_observation_id": "ob2", "max_confidence": 0.8,
            "identity_confirmation_ids": ["ic1"]}
    data.update(kw)
    return data


@pytest.mark.parametrize("overrides", [
    {"observation_count": 3},  # an instance count is never inferred
    {"representative_observation_id": "ob9"},  # area comes from one member
    {"identity_confirmation_ids": []},  # resolved needs its confirmation
    {"side": "unknown"},
    {"identity_status": "part_only"},  # part_only has side unknown
    {"identity_status": "unresolved", "part_code": None, "side": "unknown"},  # one member only
    {"damage_codes": ["scratch", "scratch"]},
    {"dent_count": 2},  # no physical instance count field exists
])
def test_part_summary_invalid(overrides):
    with pytest.raises(ValidationError):
        PartSummary(**summary(**overrides))


def test_part_summary_valid_statuses():
    assert PartSummary(**summary()).identity_status == "resolved"
    assert PartSummary(**summary(identity_status="part_only", side="unknown", identity_confirmation_ids=[]))
    assert PartSummary(**summary(identity_status="unresolved", part_code=None, side="unknown",
                                 member_observation_ids=["ob2"], observation_count=1, identity_confirmation_ids=[],
                                 reasons=["ambiguous_between_parts"]))


def test_confirmations_are_human_and_resolve_side():
    base = {**SCOPE, "confirmation_id": "ic1", "actor": "surveyor:t", "recorded_at": NOW, "review_revision": 1}
    identity = IdentityConfirmation(**base, photo_id="ph1", part_code="front-door", side="left")
    assert identity.source == "human"
    with pytest.raises(ValidationError):
        IdentityConfirmation(**base, photo_id="ph1", part_code="front-door", side="unknown")
    with pytest.raises(ValidationError):
        IdentityConfirmation(**{**base, "provenance": {**SCOPE["provenance"], "source_kind": "synthetic"}},
                             photo_id="ph1", part_code="front-door", side="left")
    cov = dict(base, confirmation_id="cc1", part_code="front-door", side="left", covering_photo_ids=["ph1"])
    assert CoverageConfirmation(**cov, covers_enough=True).covers_enough
    with pytest.raises(ValidationError):
        CoverageConfirmation(**cov, covers_enough=False)  # a negative confirmation carries a reason
    with pytest.raises(ValidationError):
        CoverageConfirmation(**{**cov, "covering_photo_ids": []}, covers_enough=True)


# --- coordinate round trips (data contracts section 4) ----------------------------------------
@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_orientation_round_trip_matches_pillow(orientation):
    stored = Image.new("L", (40, 30), 0)
    stored.putpixel((7, 5), 255)
    stored.getexif()[0x0112] = orientation
    shown = ImageOps.exif_transpose(stored)
    t = ImageTransform(stored_width=40, stored_height=30, exif_orientation=orientation, model_width=64,
                       model_height=64, scale=1.0)
    assert t.original_size == shown.size
    # pixel centres: map the marked stored pixel into the displayed frame and back
    x, y = t.stored_to_original(7.5, 5.5)
    assert shown.getpixel((int(x), int(y))) == 255
    assert t.original_to_stored(x, y) == pytest.approx((7.5, 5.5))


def test_letterbox_round_trip_to_original_photo():
    t = ImageTransform(**transform())
    # the full model-frame content region maps back to the whole original photo
    top = t.pad_top
    box = t.model_box_to_original_norm((0.0, top, 512.0, 512.0 - top))
    assert box == pytest.approx((0.0, 0.0, 1.0, 1.0))
    for point in [(0.0, 0.0), (1234.5, 987.0), (4032.0, 3024.0)]:
        assert t.model_to_original(*t.original_to_model(*point)) == pytest.approx(point)
    rotated = ImageTransform(**{**transform(), "exif_orientation": 6, "scale": 512 / 4032, "pad_left": 64.0,
                                "pad_top": 0.0})
    assert rotated.original_size == (3024, 4032)
    assert rotated.model_box_to_original_norm((64.0, 0.0, 448.0, 512.0)) == pytest.approx((0.0, 0.0, 1.0, 1.0))
