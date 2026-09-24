"""group_observations: three identity statuses, full member retention, no summed areas."""
import random

import pytest

from claim_cmev.contracts.common import ContractError
from claim_cmev.contracts.imaging import PartSummary
from claim_cmev.vision.multiview import group_observations
from m3_support import CFG, context, identity, obs, worked_example


def _groups(observations, identities=()):
    ids = [c.confirmation_id for c in identities]
    return group_observations(observations, list(identities), context=context(confirmation_ids=ids), config=CFG)


def _shape(groups):
    return [(g.identity_status, g.part_code, g.side, tuple(g.member_observation_ids)) for g in groups]


def test_worked_example_without_confirmations():
    observations, _ = worked_example()
    groups = _groups(observations)
    assert _shape(groups) == [
        ("part_only", "front-door", "unknown", ("obs_1", "obs_2")),
        ("part_only", "fender", "unknown", ("obs_4",)),
        ("unresolved", None, "unknown", ("obs_3",)),
        ("unresolved", None, "unknown", ("obs_5",)),
    ]
    door = groups[0]
    assert door.observation_count == 2 and door.reasons == ["identity_not_resolved"]
    assert door.representative_observation_id == "obs_2" and door.representative_area_fraction == pytest.approx(0.0119, abs=1e-5)
    assert [g.reasons for g in groups[2:]] == [["ambiguous_between_parts"], ["mostly_background"]]


def test_group_area_is_one_member_area_never_a_sum():
    observations, _ = worked_example()
    for group in _groups(observations):
        areas = {o.observation_id: o.area_fraction for o in observations if o.observation_id in group.member_observation_ids}
        assert group.representative_area_fraction == areas[group.representative_observation_id]
        assert group.representative_area_fraction == max(areas.values())
        if len(areas) > 1:
            assert group.representative_area_fraction < sum(areas.values())


def test_every_observation_is_in_exactly_one_group():
    observations, _ = worked_example()
    groups = _groups(observations, [identity("ic_1", "ph_01", "front-door", "left")])
    members = [m for g in groups for m in g.member_observation_ids]
    assert sorted(members) == sorted(o.observation_id for o in observations)


def test_identity_confirmation_attaches_the_side_only_for_its_photo():
    observations, _ = worked_example()
    groups = _groups(observations, [identity("ic_1", "ph_01", "front-door", "left")])
    assert _shape(groups)[:3] == [
        ("resolved", "front-door", "left", ("obs_1",)),
        ("part_only", "front-door", "unknown", ("obs_2",)),  # the unconfirmed photo keeps side unknown
        ("part_only", "fender", "unknown", ("obs_4",)),
    ]
    assert groups[0].identity_confirmation_ids == ["ic_1"] and groups[0].reasons == []


def test_opposite_side_evidence_is_never_merged():
    observations = [obs("o_l", "ph_left", "dent", "front-door"), obs("o_r", "ph_right", "scratch", "front-door")]
    groups = _groups(observations, [identity("ic_l", "ph_left", "front-door", "left"),
                                    identity("ic_r", "ph_right", "front-door", "right")])
    assert _shape(groups) == [("resolved", "front-door", "left", ("o_l",)),
                              ("resolved", "front-door", "right", ("o_r",))]


def test_same_physical_part_confirmed_on_two_photos_forms_one_group():
    observations = [obs("o_1", "ph_01", "dent", "hood", area=0.02), obs("o_2", "ph_02", "dent", "hood", area=0.03)]
    [group] = _groups(observations, [identity("ic_1", "ph_01", "hood", "not_applicable"),
                                     identity("ic_2", "ph_02", "hood", "not_applicable")])
    assert (group.identity_status, group.side, group.observation_count) == ("resolved", "not_applicable", 2)
    assert group.representative_observation_id == "o_2" and group.identity_confirmation_ids == ["ic_1", "ic_2"]
    assert group.supporting_photo_ids == ["ph_01", "ph_02"]


def test_latest_identity_confirmation_wins():
    observations = [obs("o_1", "ph_01", "dent", "front-door")]
    groups = _groups(observations, [identity("ic_old", "ph_01", "front-door", "left", review=1),
                                    identity("ic_new", "ph_01", "front-door", "right", review=2)])
    assert _shape(groups) == [("resolved", "front-door", "right", ("o_1",))]
    assert groups[0].identity_confirmation_ids == ["ic_new"]


def test_identity_for_another_part_does_not_resolve():
    groups = _groups([obs("o_1", "ph_01", "dent", "fender")], [identity("ic_1", "ph_01", "front-door", "left")])
    assert _shape(groups) == [("part_only", "fender", "unknown", ("o_1",))]


def test_redelivered_identical_rows_collapse_and_conflicts_fail():
    row = obs("o_1", "ph_01", "dent", "hood")
    assert _shape(_groups([row, row])) == [("part_only", "hood", "unknown", ("o_1",))]
    with pytest.raises(ContractError) as err:
        _groups([row, obs("o_1", "ph_01", "scratch", "hood")])
    assert err.value.reason_code == "observation_id_conflict"


def test_no_instance_count_field_exists():
    names = set(PartSummary.model_fields)
    assert "observation_count" in names
    assert not {n for n in names if "instance" in n or (n.endswith("_count") and n != "observation_count")}


def test_grouping_is_deterministic_under_input_order():
    observations, _ = worked_example()
    confirmations = [identity("ic_1", "ph_01", "front-door", "left"), identity("ic_2", "ph_03", "fender", "left")]
    expected = _groups(observations, confirmations)
    shuffled = observations[:]
    random.Random(7).shuffle(shuffled)
    assert _groups(shuffled, confirmations[::-1]) == expected
    assert len({g.summary_id for g in expected}) == len(expected)
