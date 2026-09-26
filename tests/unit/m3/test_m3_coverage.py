"""decide_coverage: the ordered table, confirmation gating and failure handling."""
import itertools

import pytest

from claim_cmev.contracts.common import PART_CODES, ContractError
from claim_cmev.contracts.imaging import ViewScreen
from claim_cmev.vision.multiview import COVERAGE_RULES, CoverageSlot, SlotFacts, decide_coverage, decide_slot
from m3_support import CFG, CROPPED, PASS, context, covers, identity, obs, pred, run, signals_for, slot, worked_example


def test_every_supported_slot_gets_coverage_including_undamaged_parts():
    outcome = run(*worked_example())
    assert {c.part_code for c in outcome.coverage} == set(PART_CODES)
    assert len({(c.part_code, c.side) for c in outcome.coverage}) == len(outcome.coverage)
    roof = slot(outcome, "roof")  # never photographed, never damaged
    assert (roof.state, roof.reasons) == ("unresolved", ["identity_not_resolved"])


def test_passing_views_without_identity_stay_unresolved():
    outcome = run(*worked_example())
    door = slot(outcome, "front-door")
    assert (door.state, door.reasons) == ("unresolved", ["identity_not_resolved"])
    assert [v.screen_result for v in door.views] == ["pass", "pass"] and door.covering_photo_ids == []


def test_adequate_needs_identity_screen_and_a_recorded_coverage_confirmation():
    observations, predictions = worked_example()
    ids = [identity("ic_1", "ph_01", "front-door", "left"), identity("ic_2", "ph_02", "front-door", "left")]
    awaiting = slot(run(observations, predictions, ids), "front-door", "left")
    assert (awaiting.state, awaiting.reasons) == ("inadequate", ["awaiting_coverage_confirmation"])
    confirmed = run(observations, predictions, ids, [covers("cc_1", "front-door", "left", ["ph_01", "ph_02"])])
    door = slot(confirmed, "front-door", "left")
    assert (door.state, door.reasons, door.coverage_confirmation_id) == ("adequate", [], "cc_1")
    assert door.covering_photo_ids == ["ph_01", "ph_02"] and door.identity_confirmation_ids == ["ic_1", "ic_2"]


def test_passing_screens_alone_never_produce_adequate():
    """Rule test: every screen/identity combination without a coverage confirmation."""
    for screen, side, has_view in itertools.product(("pass", "fail", "not_run"), ("left", "unknown"), (True, False)):
        views = (ViewScreen(photo_id="ph_01", screen_result=screen, reasons=[] if screen == "pass" else ["x"]),)
        facts = SlotFacts(CoverageSlot("hood", side, ("ph_01",)), views if has_view else (), None, False)
        assert decide_slot(facts).state != "adequate"


def test_sharp_but_cropped_view_is_inadequate_even_with_confirmation():
    predictions = [pred("ph_01", "fender", 11_020)]
    ids = [identity("ic_1", "ph_01", "fender", "left")]
    outcome = run([], predictions, ids, [covers("cc_1", "fender", "left", ["ph_01"])],
                  signals=signals_for(predictions, **{"ph_01:fender": CROPPED}))
    fender = slot(outcome, "fender", "left")
    assert (fender.state, fender.reasons) == ("inadequate", ["cropped_at_border"])
    [view] = fender.views
    assert view.signals["blur_score"] == 950.0 and view.signals["border_touch_fraction"] == 0.46


def test_missing_photograph_of_a_resolved_part_is_not_visible():
    # The surveyor identified the left tail-light on this photo, but M1 accepted no tail-light mask there.
    predictions = [pred("ph_01", "front-door"), pred("ph_01", "tail-light", 900, accepted=False)]
    outcome = run([], predictions, [identity("ic_1", "ph_01", "tail-light", "left")])
    tail = slot(outcome, "tail-light", "left")
    assert (tail.state, tail.reasons, tail.views) == ("not_visible", ["no_accepted_part_mask"], [])


def test_no_photographs_at_all_withholds_every_slot():
    outcome = run([], [], branch_status="skipped_no_photos", photo_ids=[])
    assert {c.state for c in outcome.coverage} == {"unresolved"}
    assert all(c.reasons == ["identity_not_resolved", "no_photographs"] for c in outcome.coverage)
    assert outcome.summaries == () and outcome.reasons == ("no_photographs",)


def test_failed_image_branch_is_a_processing_failure_never_not_visible():
    ids = [identity("ic_1", "ph_01", "tail-light", "left")]
    outcome = run([], [], ids, branch_status="failed")
    assert outcome.processing_status == "failed" and outcome.reasons == ("processing_failed",)
    assert {c.state for c in outcome.coverage} == {"unresolved"}
    assert {tuple(c.reasons) for c in outcome.coverage} == {("processing_failed",)}
    assert slot(outcome, "tail-light", "left").state == "unresolved"


def test_partial_failure_marks_affected_slots_only():
    predictions = [pred("ph_01", "hood"), pred("ph_02", "front-door")]
    ids = [identity("ic_1", "ph_01", "hood", "not_applicable"), identity("ic_2", "ph_03", "back-door", "left"),
           identity("ic_3", "ph_02", "front-door", "left")]
    outcome = run([], predictions, ids, [covers("cc_1", "hood", "not_applicable", ["ph_01"])],
                  branch_status="partial", failed_photo_ids=["ph_03"])
    assert outcome.processing_status == "partial"
    assert slot(outcome, "hood", "not_applicable").state == "adequate"                  # unaffected photo
    back = slot(outcome, "back-door", "left")                                            # confirmed on the failed photo
    assert (back.state, back.reasons) == ("unresolved", ["processing_failed"])
    door = slot(outcome, "front-door", "left")
    assert (door.state, door.reasons) == ("inadequate", ["awaiting_coverage_confirmation"])
    with pytest.raises(ContractError) as err:
        run([], predictions, branch_status="partial")
    assert err.value.reason_code == "branch_status_unknown"


def test_resolved_slot_without_views_is_not_not_visible_when_photos_failed():
    outcome = run([], [pred("ph_01", "hood")], [identity("ic_1", "ph_01", "tail-light", "left")],
                  branch_status="partial", failed_photo_ids=["ph_02"])
    tail = slot(outcome, "tail-light", "left")
    assert (tail.state, tail.reasons) == ("unresolved", ["processing_failed"])


def test_opposite_side_coverage_never_covers_the_declared_side():
    predictions = [pred("ph_left", "front-door"), pred("ph_right", "front-door")]
    ids = [identity("ic_l", "ph_left", "front-door", "left"), identity("ic_r", "ph_right", "front-door", "right")]
    outcome = run([], predictions, ids, [covers("cc_r", "front-door", "right", ["ph_right"])])
    left, right = slot(outcome, "front-door", "left"), slot(outcome, "front-door", "right")
    assert (left.state, left.reasons, left.coverage_confirmation_id) == (
        "inadequate", ["awaiting_coverage_confirmation"], None)
    assert [v.photo_id for v in left.views] == ["ph_left"]
    assert (right.state, right.covering_photo_ids) == ("adequate", ["ph_right"])
    assert not [c for c in outcome.coverage if c.part_code == "front-door" and c.side == "unknown"]


def test_unconfirmed_photo_of_a_resolved_part_keeps_an_unknown_slot():
    predictions = [pred("ph_01", "front-door"), pred("ph_02", "front-door")]
    outcome = run([], predictions, [identity("ic_1", "ph_01", "front-door", "left")])
    unknown = slot(outcome, "front-door", "unknown")
    assert (unknown.state, [v.photo_id for v in unknown.views]) == ("unresolved", ["ph_02"])
    assert [v.photo_id for v in slot(outcome, "front-door", "left").views] == ["ph_01"]


def test_view_without_pixel_signals_is_not_run_and_never_passes():
    predictions = [pred("ph_01", "hood")]
    outcome = run([], predictions, [identity("ic_1", "ph_01", "hood", "not_applicable")],
                  [covers("cc_1", "hood", "not_applicable", ["ph_01"])], signals={})
    hood = slot(outcome, "hood", "not_applicable")
    assert hood.state == "inadequate" and hood.reasons[0] == "screen_not_run"
    assert hood.views[0].screen_result == "not_run" and hood.views[0].signals == {"part_area_fraction": 0.152588}


def test_surveyor_says_views_do_not_cover_enough():
    predictions = [pred("ph_01", "hood")]
    outcome = run([], predictions, [identity("ic_1", "ph_01", "hood", "not_applicable")],
                  [covers("cc_1", "hood", "not_applicable", ["ph_01"], enough=False, reason="reflection_hides_panel")])
    hood = slot(outcome, "hood", "not_applicable")
    assert (hood.state, hood.reasons, hood.coverage_confirmation_id) == ("inadequate", ["reflection_hides_panel"], "cc_1")


def test_confirmation_naming_no_passing_view_is_not_adequate():
    predictions = [pred("ph_01", "hood"), pred("ph_02", "hood")]
    ids = [identity("ic_1", "ph_01", "hood", "not_applicable"), identity("ic_2", "ph_02", "hood", "not_applicable")]
    outcome = run([], predictions, ids, [covers("cc_1", "hood", "not_applicable", ["ph_02"])],
                  signals=signals_for(predictions, **{"ph_02:hood": {**PASS, "blur_score": 5.0}}))
    hood = slot(outcome, "hood", "not_applicable")
    assert (hood.state, hood.reasons) == ("inadequate", ["coverage_confirmation_views_mismatch"])


def test_rule_table_order_matches_the_specification():
    ids = [rule.rule_id for rule in COVERAGE_RULES]
    spec_rows = ["processing_failed", "identity_not_resolved", "no_accepted_part_mask", "every_view_fails_screen",
                 "awaiting_coverage_confirmation", "adequate"]
    assert [i for i in ids if i in spec_rows] == spec_rows and ids[-1] == "adequate"
    assert ids.index("no_view_passes_screen") < ids.index("awaiting_coverage_confirmation")


def test_decide_coverage_standalone_matches_the_summary():
    observations, predictions = worked_example()
    ids = [identity("ic_1", "ph_03", "fender", "left")]
    coverage = decide_coverage(predictions, observations, ids, (), context=context(confirmation_ids=["ic_1"]),
                               config=CFG, view_signals=signals_for(predictions))
    assert tuple(coverage) == run(observations, predictions, ids).coverage


def test_subset_supported_list_still_covers_observed_parts():
    observations, predictions = worked_example()
    outcome = run(observations, predictions, supported_parts=("hood",))
    assert {c.part_code for c in outcome.coverage} == {"hood", "front-door", "fender", "back-door"}


def test_unsupported_observation_part_is_reported():
    outcome = run([obs("o_1", "ph_01", "dent", "roof")], [], supported_parts=())
    assert [(c.part_code, c.state) for c in outcome.coverage] == [("roof", "unresolved")]
