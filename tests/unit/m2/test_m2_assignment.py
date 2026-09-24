"""assign_damage_to_part on synthetic masks: every assignment outcome and reason code."""
from fractions import Fraction

import numpy as np
import pytest

from claim_cmev.contracts.common import ContractError
from claim_cmev.vision.damage import REASON_PRECEDENCE, SIDE_REASON, decide_assignment
from m2_support import (
    DAMAGE_ID,
    GRID,
    PART_ID,
    blank,
    config,
    context,
    door_and_fender,
    paint,
    run,
    validate_event,
)

RULE = config().assignment


def _one(result):
    [obs] = result.observations
    return obs


def test_clear_assignment_inside_one_part():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    obs = _one(run(damage, door_and_fender()))
    assert (obs.assignment_status, obs.part_code, obs.part_reason) == ("assigned", "front-door", None)
    assert [(c.part_code, c.containment, c.rank) for c in obs.candidates] == [("front-door", 1.0, 1)]
    assert (obs.primary_containment, obs.runner_up_containment, obs.background_containment) == (1.0, None, 0.0)
    assert obs.side == "unknown" and obs.side_reason == SIDE_REASON
    assert obs.area_pixels == 400 and obs.area_denominator_pixels == GRID * GRID
    assert obs.area_fraction == round(400 / (GRID * GRID), 6)
    assert obs.damage_code == "dent" and obs.damage_confidence == pytest.approx(0.8)
    assert obs.part_mask_ref is not None and obs.damage_mask_ref.component_index == 1
    assert obs.assignment_config_version == config().config_version
    assert obs.versions["parts_model"] and obs.versions["damage_model"]


def test_boundary_crossing_region_is_ambiguous_and_keeps_both_candidates():
    damage = paint(blank(), DAMAGE_ID["scratch"], 54, 40, 74, 60)  # 10 columns on each part
    obs = _one(run(damage, door_and_fender()))
    assert (obs.assignment_status, obs.part_code, obs.part_reason) == ("unresolved", None, "ambiguous_between_parts")
    assert [(c.part_code, c.containment, c.rank) for c in obs.candidates] == [("front-door", 0.5, 1), ("fender", 0.5, 2)]
    assert obs.runner_up_containment == 0.5
    assert len(obs.candidates) == 2  # never split into two observations


def test_mostly_background_region_is_unresolved_with_candidates():
    damage = paint(blank(), DAMAGE_ID["crack"], 10, 0, 30, 20)  # 16 rows background, 4 rows door
    obs = _one(run(damage, door_and_fender()))
    assert (obs.part_code, obs.part_reason) == (None, "mostly_background")
    assert obs.background_containment == 0.8
    assert [(c.part_code, c.containment) for c in obs.candidates] == [("front-door", 0.2)]


def test_no_part_overlap():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 112, 30, 128)  # 320 pixels, all background
    obs = _one(run(damage, door_and_fender()))
    assert (obs.part_code, obs.part_reason, obs.candidates) == (None, "no_part_overlap", [])
    assert obs.background_containment == 1.0 and obs.primary_containment == 0.0


def test_missing_part_mask_keeps_the_damage_with_unknown_part():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    result = run(damage, None)
    obs = _one(result)
    assert (obs.part_code, obs.part_reason, obs.part_mask_ref, obs.candidates) == (None, "part_masks_missing", None, [])
    assert obs.area_pixels == 400 and obs.side == "unknown"
    assert "part_masks_missing" in result.reasons and result.unknown_part_count == 1 and not result.empty_result


def test_primary_part_not_accepted_by_m1():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    obs = _one(run(damage, door_and_fender(), accepted={"fender"}))
    assert (obs.part_code, obs.part_reason) == (None, "part_not_accepted")
    assert [c.part_code for c in obs.candidates] == ["front-door"]


def test_below_containment_threshold():
    parts = blank()
    paint(parts, PART_ID["front-door"], 0, 0, 10, 20)   # 200 px of the region: 0.5
    paint(parts, PART_ID["fender"], 10, 0, 12, 20)      # 40 px: 0.1, background 0.4
    damage = paint(blank(), DAMAGE_ID["dent"], 0, 0, 20, 20)
    obs = _one(run(damage, parts))
    assert (obs.part_code, obs.part_reason) == (None, "below_containment_threshold")
    assert (obs.primary_containment, obs.runner_up_containment, obs.background_containment) == (0.5, 0.1, 0.4)


def test_multiple_regions_each_get_one_observation_and_a_stable_index():
    damage = blank()
    paint(damage, DAMAGE_ID["dent"], 4, 20, 24, 40)       # door
    paint(damage, DAMAGE_ID["dent"], 90, 60, 110, 80)     # fender, same class, separate component
    paint(damage, DAMAGE_ID["scratch"], 30, 70, 50, 90)   # door, other class
    paint(damage, DAMAGE_ID["scratch"], 70, 20, 75, 25)   # 25 px: dropped below_min_pixels
    result = run(damage, door_and_fender())
    got = [(o.damage_code, o.part_code, o.damage_mask_ref.component_index) for o in result.observations]
    assert got == [("dent", "front-door", 1), ("dent", "fender", 2), ("scratch", "front-door", 3)]
    assert len(set(result.observation_ids)) == 3
    assert result.below_min_pixels_count == 1 and result.dropped_region_count == 1
    assert "below_min_pixels" in result.reasons
    assert sorted(np.unique(result.component_labels).tolist()) == [0, 1, 2, 3]
    assert result.component_labels.dtype == np.uint16
    assert int(np.count_nonzero(result.component_labels == 2)) == 400
    assert all(o.side == "unknown" for o in result.observations)


def test_opposite_side_views_never_resolve_a_side():
    """A left-side view and its mirror image (as from the right) assign the same part; side stays unknown."""
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    parts = door_and_fender()
    left = _one(run(damage, parts, photo_id="ph_left_side"))
    right = _one(run(np.fliplr(damage).copy(), np.fliplr(parts).copy(), photo_id="ph_right_side"))
    for obs in (left, right):
        assert (obs.part_code, obs.side, obs.side_reason) == ("front-door", "unknown", SIDE_REASON)
    assert left.observation_id != right.observation_id
    assert left.bbox_norm[0] == pytest.approx(1 - right.bbox_norm[2])  # geometry mirrored, identity not


def test_mismatched_grids_fail_rather_than_resample():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    small_parts = door_and_fender(64)
    with pytest.raises(ContractError) as err:
        run(damage, small_parts)
    assert err.value.reason_code == "mask_geometry_mismatch"
    with pytest.raises(ContractError) as err:
        run(damage, door_and_fender(), confidence=np.full((64, 64), 0.8))
    assert err.value.reason_code == "mask_geometry_mismatch"
    cfg = config()
    with pytest.raises(ContractError) as err:  # stored mask reference records another grid
        run(damage, door_and_fender(), cfg=cfg, ctx=context(cfg, size=64))
    assert err.value.reason_code == "mask_geometry_mismatch"


def test_parts_version_mismatch_fails_the_job():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    with pytest.raises(ContractError) as err:
        run(damage, door_and_fender(), part_mask_model_version="another-parts/9.9.9")
    assert err.value.reason_code == "parts_version_mismatch"


def test_assignment_config_version_is_pinned():
    cfg = config()
    other = cfg.with_overrides({"config_version": "m2-assignment/9.9.9"})
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    with pytest.raises(ContractError) as err:
        run(damage, door_and_fender(), cfg=other, ctx=context(cfg))
    assert err.value.reason_code == "assignment_config_version_mismatch"


def test_unknown_class_ids_are_an_encoding_error():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    parts = door_and_fender()
    parts[0, 0] = 99
    with pytest.raises(ContractError) as err:
        run(damage, parts)
    assert err.value.reason_code == "mask_encoding_mismatch"
    damage[0, 0] = 9
    with pytest.raises(ContractError) as err:
        run(damage, door_and_fender())
    assert err.value.reason_code == "mask_encoding_mismatch"


def test_empty_result_is_a_successful_run_with_a_reason():
    result = run(blank(), door_and_fender())
    assert result.observations == () and result.empty_result and "no_damage_regions" in result.reasons
    payload = validate_event(result, config())["payload"]
    assert payload["empty_result"] is True and payload["observations"] == []


def test_replay_reproduces_ids_and_records():
    damage = paint(blank(), DAMAGE_ID["dent"], 10, 30, 30, 50)
    first, second = run(damage, door_and_fender()), run(damage, door_and_fender())
    assert first.observations == second.observations
    other = run(damage, door_and_fender(), cfg=config(), ctx=context(config(), input_revision=2))
    assert other.observation_ids != first.observation_ids  # a new job key gives new rows


def test_event_payload_validates_and_renames_damage_code():
    damage = blank()
    paint(damage, DAMAGE_ID["dent"], 10, 30, 30, 50)
    paint(damage, DAMAGE_ID["scratch"], 54, 60, 74, 80)
    result = run(damage, door_and_fender())
    payload = validate_event(result, config())["payload"]
    assert [o["damage_type"] for o in payload["observations"]] == ["dent", "scratch"]
    assert payload["unknown_part_count"] == 1 and payload["observation_ids"] == list(result.observation_ids)
    assert all(o["side"] == "unknown" for o in payload["observations"])


# ---------------------------------------------------------------- the pure rule
@pytest.mark.parametrize(("containments", "background", "status", "reason"), [
    ({"front-door": 0.78, "fender": 0.14}, 0.08, "assigned", None),                  # worked example c1
    ({"front-door": 0.47, "fender": 0.44}, 0.09, "unresolved", "ambiguous_between_parts"),  # c2
    ({"front-door": 0.11, "fender": 0.05}, 0.84, "unresolved", "mostly_background"),        # c4
])
def test_worked_example_from_the_m2_specification(containments, background, status, reason):
    decision = decide_assignment(containments, background, {"front-door", "fender"}, RULE)
    assert (decision.assignment_status, decision.part_reason) == (status, reason)
    assert [c.part_code for c in decision.candidates] == list(containments)


def test_thresholds_compare_exactly_at_the_boundary():
    # 0.6 - 0.4 is 0.19999999999999998 in binary floating point; the rule uses exact fractions,
    # so a margin of exactly assign_ambiguity_margin passes and 0.6 meets the 0.60 minimum.
    assert 0.6 - 0.4 < 0.2
    at_both = decide_assignment({"hood": 0.6, "grille": 0.4}, 0.0, {"hood"}, RULE)
    assert (at_both.assignment_status, at_both.part_code) == ("assigned", "hood")
    pixels = decide_assignment({"hood": Fraction(60, 100), "grille": Fraction(40, 100)}, Fraction(0), {"hood"}, RULE)
    assert pixels.part_code == "hood"
    assert decide_assignment({"hood": 0.6, "grille": 0.41}, 0.0, {"hood"}, RULE).part_reason == "ambiguous_between_parts"
    assert decide_assignment({"hood": 0.5}, 0.5, {"hood"}, RULE).part_reason == "below_containment_threshold"
    assert decide_assignment({"hood": 0.45}, 0.55, {"hood"}, RULE).part_reason == "mostly_background"


def test_single_candidate_is_never_called_ambiguous():
    decision = decide_assignment({"hood": 0.3}, 0.2, {"hood"}, RULE)
    assert decision.part_reason == "below_containment_threshold" and decision.runner_up_containment is None


def test_reason_precedence_is_the_contract_vocabulary():
    assert set(REASON_PRECEDENCE) == {"no_part_overlap", "below_containment_threshold", "ambiguous_between_parts",
                                      "part_not_accepted", "mostly_background", "part_masks_missing"}
