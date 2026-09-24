"""Experiment A (module 08 acceptance, evaluation plan 6.1): deterministic rule tests.

Built inputs, no models. Each ``test_aNN_*`` is required case NN of the module 08 list;
``CASES`` maps every case to its test and ``SAFETY_INVARIANTS`` maps SI-01 to SI-12 to at
least one test. These tests establish rule correctness only, never model accuracy.
"""
from __future__ import annotations

from decimal import Decimal
import re

import pytest
from pydantic import ValidationError

from claim_cmev.comparison import (
    SYNTHETIC_COST_NOTICE, REASON_CODES, carry_forward_dismissals, compare_amount, consolidate, content_hash,
    dismissal_carries_forward, job_key_for,
)
from claim_cmev.contracts.assessment import ProposedRepairAddition
from claim_cmev.contracts.claims import ReusedArtifact
from claim_cmev.contracts.common import COST_BASIS, ContractError, Provenance
from claim_cmev.contracts.events.envelope import JOB_KEY_PATTERN
from claim_cmev.contracts.review import Finalization

from m8_support import (
    CONFIG, DEFAULT_ROWS, NOW, RANGES, TABLE, StubRanges, World, checks, codes, emitted_codes, finding, supported,
    table,
)


def clean(amount: str | None = "500.00", **item_kw) -> World:
    """Resolved left front door, confirmed adequate coverage, a confident dent, one row."""
    world = World()
    world.seen("front-door", "left")
    world.item("e1", amount=amount, **item_kw)
    return world


def cmp(amount, lookup=None, **kw):
    base = dict(quantity="1", currency="SGD", cost_basis=COST_BASIS, entry_id="e1", policy_version="m8-rules/0.1.0",
                cost_table_version=TABLE)
    return compare_amount(amount, lookup if lookup is not None else supported("380.00", "620.00"), **(base | kw))


def not_compared(check) -> bool:
    return all(v is None for v in (check.direction, check.absolute_deviation, check.normalised_score,
                                   check.lower_amount, check.upper_amount, check.range_id))


# ---------------------------------------------------------------- evidence and coverage (1-8)
def test_a01_supported_in_range_is_ok_with_zero_discrepancy_flags():
    result = clean().run()
    f = finding(result, "e1")
    assert f.overall_result == "ok" and f.row_state == "active" and result.outcome_rules["e1"] == "R12"
    assert checks(f) == {"documentary": "passed", "mark_state": "passed", "photographic": "passed",
                         "cost": "within_range"}
    assert result.finding_counts["unsupported"] == result.finding_counts["cost_outlier"] == 0
    assert (f.applied_range_id, f.cost_check.absolute_deviation, f.cost_check.direction) == ("r-fd-repair", "0.00", None)
    assert f.cost_check.cost_table_version == TABLE and f.pinned_versions["cost_table"] == TABLE


def test_a02_confirmed_adequate_coverage_without_damage_is_unsupported():
    world = World()
    world.seen("front-door", "left", damage=())
    world.item("e1")
    result = world.run()
    f = finding(result, "e1")
    assert f.overall_result == "unsupported" and result.outcome_rules["e1"] == "R8"
    assert f.photographic_check.result == "failed"
    assert codes(f.photographic_check) == ["no_supported_damage_in_adequate_views"]
    assert f.photographic_check.detail["covering_photo_ids"] == ["ph-front-door-left"]
    assert f.cost_check.result == "not_evaluated" and f.cost_check.reason_code == "check_not_reached"


def test_a02b_negative_needs_the_recorded_coverage_confirmation_record():
    world = World()
    world.seen("front-door", "left", damage=(), record_coverage_confirmation=False)
    world.item("e1")
    f = finding(world.run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["coverage_unresolved"]
    relaxed = CONFIG.with_overrides({"coverage": {"require_human_confirmation_for_negative": False}})
    assert finding(world.run(config=relaxed), "e1").overall_result == "unsupported"


def test_a03_right_door_damage_never_supports_or_refutes_a_left_door_row():
    world = World()
    world.seen("front-door", "right")
    world.item("e1", side="left")
    f = finding(world.run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["part_identity_unresolved"]
    assert f.photographic_check.result == "not_evaluated"
    assert not any(ref.kind == "observation" for ref in f.evidence_refs)


def test_a04_unresolved_side_on_the_observation_gives_side_unresolved():
    world = World()
    world.unsided("front-door")
    world.item("e1", side="left")
    f = finding(world.run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["side_unresolved"]
    assert f.documentary_check.rule_id == "R5"


def test_a04b_side_is_never_invented_only_a_recorded_identity_confirmation_unblocks():
    unsided_row = World()
    unsided_row.seen("front-door", "left")
    unsided_row.item("e1", side="unknown")
    assert codes(finding(unsided_row.run(), "e1")) == ["side_unresolved"]
    confirmed = clean()
    assert finding(confirmed.run(), "e1").overall_result == "ok"
    # the same records without the identity confirmation record: withheld, never matched
    f = finding(consolidate(confirmed.request(identity_confirmations=[]), config=CONFIG, ranges=RANGES), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["part_identity_unresolved"]


@pytest.mark.parametrize("damage", [(), (("dent", 0.95),)])
def test_a05_sharp_large_mask_in_a_cropped_view_is_coverage_inadequate(damage):
    world = World()
    world.seen("front-door", "left", damage=damage, coverage="inadequate", reasons=("cropped_at_border",))
    world.item("e1")
    f = finding(world.run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["coverage_inadequate"]
    assert f.photographic_check.result == "insufficient"
    assert f.photographic_check.detail["coverage_reasons"] == ["cropped_at_border"]


def test_a06_part_in_no_photograph_is_coverage_not_visible():
    world = World()
    world.seen("tail-light", "left", damage=(), coverage="not_visible")
    world.item("e1", "tail-light", "left", "replace", "310.00")
    f = finding(world.run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["coverage_not_visible"]
    assert f.overall_result != "unsupported"


def test_a07_damage_below_threshold_is_uncertain():
    world = World()
    world.seen("front-door", "left", damage=(("dent", 0.3),))
    world.item("e1")
    f = finding(world.run(), "e1")
    assert codes(f) == ["damage_evidence_uncertain"] and f.photographic_check.rule_id == "R7"


def test_a07b_out_of_scope_or_identity_uncertain_evidence_blocks_a_negative():
    scoped = CONFIG.with_overrides({"damage": {"supported_types": ["scratch", "crack"]}})
    assert codes(finding(clean().run(config=scoped), "e1")) == ["damage_type_out_of_scope"]
    nearby = World()
    nearby.seen("front-door", "left", damage=())
    nearby.unsided("front-door")
    nearby.item("e1")
    assert codes(finding(nearby.run(), "e1")) == ["damage_evidence_uncertain"]
    straddle = World()
    straddle.seen("front-door", "left", damage=())
    straddle.unresolved("ob-straddle", (("front-door", 0.5), ("fender", 0.45)))
    straddle.item("e1")
    assert codes(finding(straddle.run(), "e1")) == ["damage_evidence_uncertain"]


@pytest.mark.parametrize(("state", "code", "incomplete"), [("failed", "branch_failed", "image_branch_failed"),
                                                           ("skipped_no_photos", "branch_missing",
                                                            "image_branch_missing")])
def test_a08_failed_image_job_is_branch_failed_and_incomplete(state, code, incomplete):
    world = clean()
    world.image_state = state
    result = world.run()
    f = finding(result, "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == [code]
    assert f.photographic_check.result == "not_evaluated" and codes(f.photographic_check) == [code]
    assert result.assessment.state == "incomplete" and result.assessment.incomplete_reasons == [incomplete]
    assert result.assessment.missing_repairs_check.result == "not_evaluated"
    assert result.ready_payload()["assessment_state"] == "incomplete"


# ---------------------------------------------------------------- marks and amounts (9-17)
def test_a09_pending_exclusion_withholds_and_does_not_exclude():
    world = clean()
    world.mark("m1", "e1", "exclusion", "pending")
    f = finding(world.run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["mark_pending"]
    assert f.row_state == "active" and f.mark_state_check.result == "insufficient"


def test_a10_confirmed_exclusion_is_excluded_never_ok():
    world = clean()
    world.mark("m1", "e1", "exclusion", "confirmed")
    result = world.run()
    f = finding(result, "e1")
    assert (f.row_state, f.overall_result) == ("excluded", "not_evaluated")
    assert set(checks(f).values()) == {"not_evaluated"} and codes(f)[0] == "exclusion_confirmed"
    assert all(codes(c) == ["exclusion_confirmed"] for c in (f.documentary_check, f.mark_state_check,
                                                             f.photographic_check))
    assert f.cost_check.reason_code == "exclusion_confirmed"
    assert result.finding_counts["excluded"] == 1 and result.finding_counts["ok"] == 0
    assert f.applied_range_reason == "exclusion_confirmed" and not_compared(f.cost_check)


def test_a11_rejected_exclusion_row_is_checked_normally():
    world = clean()
    world.mark("m1", "e1", "exclusion", "rejected")
    f = finding(world.run(), "e1")
    assert (f.row_state, f.overall_result) == ("active", "ok")


def test_a12_pending_price_change_never_compares_the_printed_amount():
    world = clean(amount="500.00")  # the printed amount is inside the range
    world.mark("m1", "e1", "price_change", "pending")
    stub = StubRanges({("front-door", "repair", "sedan_standard"): supported("380.00", "620.00")})
    f = finding(world.run(ranges=stub), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["mark_pending", "amount_unresolved"]
    assert f.cost_check.result == "not_evaluated" and f.cost_check.reason_code == "amount_unresolved"
    assert f.cost_check.amount is None and not_compared(f.cost_check)
    assert stub.calls == []  # no range was even looked up for a pending repricing
    assert f.mark_state_check.detail["effective_price_reason"] == "price_change_pending"


def test_a13_confirmed_price_change_compares_the_entered_amount():
    world = clean(amount="500.00")
    world.mark("m1", "e1", "price_change", "confirmed", amount="700.00")
    f = finding(world.run(), "e1")
    assert f.overall_result == "cost_outlier" and f.cost_check.amount == "700.00"
    assert (f.cost_check.direction, f.cost_check.absolute_deviation) == ("above", "80.00")
    assert codes(f.mark_state_check) == ["marks_clear", "price_change_confirmed"]
    assert world.items[0].printed_line_amount == "500.00"  # the printed amount is never overwritten
    reverse = clean(amount="900.00")
    reverse.mark("m1", "e1", "price_change", "confirmed", amount="500.00")
    assert finding(reverse.run(), "e1").overall_result == "ok"


def test_a14_manually_added_mark_behaves_like_a_confirmed_detection():
    detected, added = clean(), clean()
    detected.mark("m1", "e1", "price_change", "confirmed", amount="560.00")
    added.mark("m1", "e1", "price_change", "confirmed", amount="560.00", origin="human_added")
    a, b = finding(detected.run(), "e1"), finding(added.run(), "e1")
    assert a.overall_result == b.overall_result == "ok"
    assert a.cost_check == b.cost_check and a.photographic_check == b.photographic_check
    excluded = clean()
    excluded.mark("m1", "e1", "exclusion", "confirmed", origin="human_added")
    assert finding(excluded.run(), "e1").row_state == "excluded"


def test_a15_unlinked_mark_naming_the_row_withholds_it():
    world = clean()
    world.item("e2", amount="450.00")
    world.mark("m1", None, "exclusion", "pending", candidates=("e1", "e2"))
    result = world.run()
    for entry in ("e1", "e2"):
        f = finding(result, entry)
        assert f.overall_result == "insufficient_evidence" and codes(f) == ["mark_unlinked"]
    priced = clean()
    priced.mark("m1", None, "price_change", "pending", candidates=("e1",))
    assert codes(finding(priced.run(), "e1")) == ["mark_unlinked", "amount_unresolved"]


def test_a16_conflicting_marks_withhold_even_when_both_are_confirmed():
    world = clean()
    world.mark("m1", "e1", "exclusion", "confirmed")
    world.mark("m2", "e1", "price_change", "confirmed", amount="560.00")
    f = finding(world.run(), "e1")
    assert f.row_state == "active" and codes(f) == ["mark_conflicting", "amount_unresolved"]
    two = clean()
    two.mark("m1", "e1", "price_change", "confirmed", amount="560.00")
    two.mark("m2", "e1", "price_change", "pending")
    assert codes(finding(two.run(), "e1"))[:2] == ["mark_conflicting", "mark_pending"]


def test_a17_unreadable_amount_withholds_cost_and_keeps_the_photo_pass():
    f = finding(clean(amount=None).run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["amount_unreadable"]
    assert f.photographic_check.result == "passed" and f.cost_check.result == "not_evaluated"
    flagged = clean(amount="480.00", uncertain=(("printed_line_amount", "ocr_letter_digit_confusion"),))
    assert codes(finding(flagged.run(), "e1")) == ["amount_unreadable"]


# ---------------------------------------------------------------- extraction and additions (18-24)
def _hood(world: World) -> None:
    world.seen("hood", "not_applicable", damage=(("dent", 0.77),))


def test_a18_partial_completeness_proposes_no_additions():
    world = clean()
    _hood(world)
    world.complete("partial")
    result = world.run()
    check = result.assessment.missing_repairs_check
    assert (check.result, codes(check), check.rule_id) == ("not_evaluated", ["declaration_incomplete"], "A1")
    assert result.assessment.possible_additions == [] and result.assessment.suppressed_additions == []
    assert finding(result, "e1").overall_result == "ok"  # per-row checks still run


@pytest.mark.parametrize("state", ["unreadable", "complete"])
def test_a19_no_parsed_rows_is_never_an_empty_scope(state):
    world = World()
    _hood(world)
    world.complete(state)
    result = world.run()
    assert result.assessment.findings == [] and result.assessment.possible_additions == []
    assert codes(result.assessment.missing_repairs_check) == ["declaration_incomplete"]


def test_a20_human_confirmed_empty_scope_may_propose_additions():
    world = World()
    _hood(world)
    world.complete("explicitly_empty", human=True)
    result = world.run()
    (addition,) = result.assessment.possible_additions
    assert (addition.status, addition.part_code, addition.side) == ("proposed", "hood", "not_applicable")
    assert addition.reason.code == "addition_proposed" and addition.observation_ids == ["ob-hood-not_applicable-0"]
    assert not {"operation", "amount", "entry_id"} & set(ProposedRepairAddition.model_fields)
    assert result.assessment.missing_repairs_check.result == "failed"
    assert result.finding_counts == {"ok": 0, "unsupported": 0, "cost_outlier": 0, "insufficient_evidence": 0,
                                     "excluded": 0}
    assert result.proposed_addition_count == 1 and result.addition_rules[addition.candidate_id] == "A8"


def test_a21_ambiguous_row_that_could_hide_a_match_withholds_the_addition():
    unmapped = World()
    _hood(unmapped)
    unmapped.item("e1", part=None, side="unknown")
    (addition,) = unmapped.run().assessment.possible_additions
    assert (addition.status, addition.reason.code) == ("withheld", "addition_withheld_ambiguous_row")
    unsided = World()
    unsided.seen("front-door", "left")
    unsided.item("e1", side="unknown")
    (addition,) = unsided.run().assessment.possible_additions
    assert addition.reason.code == "addition_withheld_ambiguous_row"


def test_a22_confirmed_exclusion_on_the_same_part_and_side_suppresses_with_a_note():
    world = World()
    world.seen("back-door", "left", damage=(("dent", 0.69),))
    world.item("e1", "back-door", "left", "repair", "480.00")
    world.mark("m1", "e1", "exclusion", "confirmed")
    result = world.run()
    (suppressed,) = result.assessment.suppressed_additions
    assert suppressed.suppressed_by_entry_id == "e1" and result.assessment.possible_additions == []
    assert suppressed.reason.code == "addition_suppressed_confirmed_exclusion_same_part"
    f = finding(result, "e1")
    assert f.row_state == "excluded" and codes(f) == ["exclusion_confirmed",
                                                      "addition_suppressed_confirmed_exclusion_same_part"]
    assert any(r.kind == "observation" and r.ref_id == "ob-back-door-left-0" for r in f.evidence_refs)


def test_a23_exclusion_on_the_right_door_never_suppresses_left_door_damage():
    world = World()
    world.seen("front-door", "left")
    world.item("e1", side="right")
    world.mark("m1", "e1", "exclusion", "confirmed")
    result = world.run()
    (addition,) = result.assessment.possible_additions
    assert (addition.status, addition.part_code, addition.side) == ("proposed", "front-door", "left")
    assert result.assessment.suppressed_additions == []


def test_a24_two_operations_on_one_part_are_both_checked_with_no_spurious_addition():
    world = clean(amount="500.00")
    world.item("e2", operation="paint", amount="250.00")
    result = world.run()
    assert [finding(result, e).overall_result for e in ("e1", "e2")] == ["ok", "ok"]
    assert result.assessment.possible_additions == [] and result.assessment.suppressed_additions == []
    assert codes(result.assessment.missing_repairs_check) == ["no_additions_found"]


# ---------------------------------------------------------------- cost arithmetic (25-36)
def test_a25_amount_equal_to_lower_bound_is_within_range():
    check = cmp("380.00")
    assert (check.result, check.absolute_deviation, check.direction, check.normalised_score) == (
        "within_range", "0.00", None, "0.0000")


def test_a26_amount_equal_to_upper_bound_is_within_range():
    assert cmp("620.00").result == "within_range"


def test_a27_one_cent_below_the_lower_bound():
    check = cmp("379.99")
    assert (check.result, check.direction, check.absolute_deviation) == ("outside_range", "below", "0.01")


def test_a28_one_cent_above_the_upper_bound():
    check = cmp("620.01")
    assert (check.result, check.direction, check.absolute_deviation) == ("outside_range", "above", "0.01")


def test_a29_zero_width_interval_never_divides():
    point = supported("500.00", "500.00")
    inside = cmp("500.00", point)
    assert inside.result == "within_range" and inside.normalised_score is None
    assert inside.normalised_score_reason == "zero_width_interval"
    above = cmp("500.01", point)
    assert (above.direction, above.absolute_deviation, above.normalised_score) == ("above", "0.01", None)


@pytest.mark.parametrize(("case", "overrides", "code"), [
    ("a30", {"quantity": "2"}, "quantity_not_one"),
    ("a31", {"quantity": None}, "quantity_missing"),
    ("a32", {"currency": "MYR"}, "currency_unsupported"),
    ("a33", {"cost_basis": "single_part_with_tax_v1"}, "basis_mismatch"),
])
def test_a30_to_a33_incompatible_amounts_get_no_comparison_and_no_deviation(case, overrides, code):
    check = cmp("500.00", **overrides)
    assert (check.result, check.reason_code) == ("not_evaluated", code) and not_compared(check)


@pytest.mark.parametrize(("item_kw", "code"), [
    ({"quantity": "2"}, "quantity_not_one"), ({"quantity": None}, "quantity_missing"),
    ({"currency": "MYR"}, "currency_unsupported"), ({"basis": "single_part_with_tax_v1"}, "basis_mismatch"),
    ({"uncertain": (("quantity", "ocr_low_confidence"),)}, "quantity_missing"),
])
def test_a30_to_a33_in_the_engine_keep_the_photo_check_passed(item_kw, code):
    f = finding(clean(**item_kw).run(), "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == [code]
    assert f.photographic_check.result == "passed" and not_compared(f.cost_check)
    assert f.applied_range_id is None and f.applied_range_reason == code


def test_a34_range_withheld_for_support_keeps_the_photo_check_passed():
    world = World()
    world.seen("mirror", "left", damage=(("crack", 0.8),))
    world.item("e1", "mirror", "left", "replace", "210.00")
    result = world.run()
    f = finding(result, "e1")
    assert f.overall_result == "insufficient_evidence" and codes(f) == ["insufficient_support"]
    assert f.photographic_check.result == "passed" and codes(f.photographic_check) == ["damage_supported"]
    assert (f.cost_check.result, f.cost_check.independent_base_case_count) == ("insufficient_support", 3)
    assert not_compared(f.cost_check) and result.outcome_rules["e1"] == "R11"


def test_a35_absent_range_is_no_key_never_a_zero_range():
    world = World()
    world.seen("roof", "not_applicable")
    world.item("e1", "roof", "not_applicable", "repair", "300.00")
    f = finding(world.run(), "e1")
    assert codes(f) == ["no_key"] and f.cost_check.lower_amount is None and f.cost_check.upper_amount is None
    assert "0.00" not in {f.cost_check.lower_amount, f.cost_check.upper_amount}


def test_a36_unknown_vehicle_class_is_not_compared():
    world = clean()
    world.vehicle_class = "unknown"
    f = finding(world.run(), "e1")
    assert codes(f) == ["unknown_vehicle_class"] and not_compared(f.cost_check)


def test_a36b_ineligible_part_and_operation_pair_is_unsupported_combination():
    world = World()
    world.seen("front-wheel", "left")
    world.item("e1", "front-wheel", "left", "replace", "300.00")
    f = finding(world.run(), "e1")
    assert codes(f) == ["unsupported_combination"] and f.photographic_check.result == "passed"


# ---------------------------------------------------------------- revisions (37-42)
def test_a37_corrected_amount_creates_a_new_assessment_and_the_original_stays_readable():
    first = clean(amount="500.00")
    first.mark("m1", "e1", "price_change", "pending")
    original = first.run().assessment
    frozen = original.model_dump_json()
    second = World(input_revision=2)
    second.seen("front-door", "left")
    second.item("e1", amount="500.00")
    second.mark("m1", "e1", "price_change", "confirmed", amount="560.00")
    revised = second.run(assessment_revision=2).assessment
    assert (original.assessment_revision, revised.assessment_revision) == (1, 2)
    assert revised.findings[0].overall_result == "ok" and revised.findings[0].cost_check.amount == "560.00"
    assert original.model_dump_json() == frozen and original.findings[0].cost_check.amount is None
    assert original.findings[0].finding_id != revised.findings[0].finding_id
    with pytest.raises(ValidationError):
        original.findings[0].overall_result = "ok"  # machine records are immutable


def test_a38_confirmed_identity_reuses_image_artifacts_by_lineage_without_a_rerun():
    first = World(input_revision=1)
    first.unsided("front-door")
    first.item("e1", side="left")
    before = first.run()
    assert codes(finding(before, "e1")) == ["side_unresolved"]
    second = World(input_revision=2)
    second.seen("front-door", "left", damage=())  # identity + coverage confirmations and the rev-2 slot
    reused = [o for o in first.observations]
    second.observations = reused
    second._summary("ps-front-door-left", reused, "resolved", "front-door", "left", ["ic-front-door-left"])
    second.items = first.items
    lineage = [ReusedArtifact(artifact_id="art-damage-rev1", kind="damage_observations", source_input_revision=1,
                              producing_job_key="job-damage-rev1"),
               ReusedArtifact(artifact_id="art-lineitems-rev1", kind="line_items", source_input_revision=1,
                              producing_job_key="job-lineitems-rev1")]
    after = second.run(assessment_revision=2, reuse_lineage=lineage)
    f = finding(after, "e1")
    assert f.overall_result == "ok" and after.reuse_lineage == tuple(lineage)
    observed = {r.ref_id for r in f.evidence_refs if r.kind == "observation"}
    assert observed == {o.observation_id for o in first.observations}  # the same records, not re-produced
    assert all(o.input_revision == 1 and o.side == "unknown" for o in reused)  # model output unchanged (SI-12)
    assert {"ic-front-door-left", "cc-front-door-left"} <= {r.ref_id for r in f.evidence_refs if r.kind == "confirmation"}
    assert after.assessment.pinned_versions["damage_model"] == before.assessment.pinned_versions["damage_model"]
    with pytest.raises(ContractError) as refused:
        second.run(assessment_revision=2)  # earlier-revision records without declared lineage
    assert refused.value.reason_code == "mixed_input_revision"


def test_a39_dismissal_carries_only_to_an_unchanged_finding():
    world = clean()
    prior = world.run().assessment
    same = world.run(assessment_revision=2).assessment
    dismissed = {prior.findings[0].finding_id: "inadequate_photograph"}
    (carried,) = carry_forward_dismissals(prior, same, dismissed)
    assert carried.carried_from == prior.findings[0].finding_id and carried.finding_id == same.findings[0].finding_id
    changed_world = clean()
    changed_world.mark("m1", "e1", "price_change", "confirmed", amount="700.00")
    changed = changed_world.run(assessment_revision=2).assessment
    assert not dismissal_carries_forward(prior.findings[0], changed.findings[0])
    assert carry_forward_dismissals(prior, changed, dismissed) == []
    more_evidence = World()
    more_evidence.seen("front-door", "left", damage=(("dent", 0.8), ("scratch", 0.9)))  # a new supporting observation
    more_evidence.item("e1")
    extra = more_evidence.run(assessment_revision=2).assessment
    assert extra.findings[0].overall_result == "ok"  # same result, changed evidence
    assert not dismissal_carries_forward(prior.findings[0], extra.findings[0])
    assert content_hash(extra.findings[0]) != content_hash(prior.findings[0])
    tampered = same.findings[0].model_copy(update={"content_hash": "0" * 64})
    assert not dismissal_carries_forward(prior.findings[0], tampered)


def test_a40_duplicate_consolidate_delivery_builds_the_identical_assessment():
    world = clean()
    world.mark("m1", "e1", "price_change", "confirmed", amount="560.00")
    first, second = world.run(), world.run()
    assert first.job_key == second.job_key and re.fullmatch(JOB_KEY_PATTERN, first.job_key)
    assert first.assessment.model_dump_json() == second.assessment.model_dump_json()
    assert first.job_key == job_key_for(world.request())


def test_a41_superseded_revision_still_writes_a_full_historical_assessment():
    world = clean()
    current = world.run().assessment
    historical = world.run(superseded=True).assessment
    assert historical.superseded is True and current.superseded is False
    assert historical.findings == current.findings


def test_a42_finalization_against_a_stale_revision_is_refused():
    assessment = clean().run(review_revision=3).assessment
    preconditions = [{"code": c, "passed": True} for c in ("no_pending_marks", "no_unlinked_marks",
                                                             "reassessment_complete", "no_failed_stages")]
    base = dict(finalization_id="fz1", claim_id=assessment.claim_id, input_revision=assessment.input_revision,
                assessment_revision=assessment.assessment_revision, review_revision=assessment.review_revision,
                actor="surveyor:t", finalized_at=NOW)
    Finalization(**base, preconditions=preconditions + [{"code": "review_revision_current", "passed": True}])
    with pytest.raises(ValidationError):
        Finalization(**base, preconditions=preconditions + [{"code": "review_revision_current", "passed": False}])


# ---------------------------------------------------------------- invariants without a numbered case
def test_si07_si10_cost_outcomes_are_labelled_synthetic_and_never_fraud():
    for code in ("amount_in_range", "amount_above_range", "amount_below_range", "zero_width_interval"):
        assert REASON_CODES[code].notice == SYNTHETIC_COST_NOTICE
    assert "synthetic" in SYNTHETIC_COST_NOTICE and "never proof of fraud" in SYNTHETIC_COST_NOTICE
    assert not any("fraud" in e.display_text.lower() or "wrong" in e.display_text.lower()
                   for e in REASON_CODES.values())
    pinned = clean(amount="700.00").run().assessment.pinned_versions
    assert (pinned["cost_reference"], pinned["cost_basis"]) == ("synthetic", COST_BASIS)


def test_si09_fixture_inputs_are_stamped_fixture():
    world = clean()
    assert world.run().assessment.provenance.source_kind == "fixture"
    real = Provenance(source_kind="real", runtime_profile="full", producer_service="cmev-consolidator")
    with pytest.raises(ContractError) as refused:
        world.run(provenance=real)
    assert refused.value.reason_code == "fixture_provenance_required"


def test_si11_versions_are_pinned_and_a_later_table_never_changes_an_assessment():
    world = clean(amount="600.00")
    earlier = world.run()
    assert all(f.pinned_versions["cost_table"] == TABLE and f.pinned_versions["rules_config"]
               == CONFIG.rules_config_version for f in earlier.assessment.findings)
    narrower = table([dict(r, lower_amount="400.00", upper_amount="550.00") if r["range_id"] == "r-fd-repair" else r
                      for r in DEFAULT_ROWS], version="t-m8-2")
    with pytest.raises(ContractError) as refused:
        consolidate(world.request(), config=CONFIG, ranges=narrower)
    assert refused.value.reason_code == "cost_table_version_mismatch"
    later = consolidate(world.request(cost_table_version="t-m8-2", assessment_revision=2), config=CONFIG,
                        ranges=narrower)
    assert finding(later, "e1").overall_result == "cost_outlier"
    assert world.run().assessment == earlier.assessment and finding(earlier, "e1").overall_result == "ok"
    with pytest.raises(ContractError):
        consolidate(world.request(rules_config_version="m8-rules/9.9.9"), config=CONFIG, ranges=RANGES)


def test_passed_photo_check_alone_never_becomes_ok_while_cost_is_unmet():
    for world in (clean(amount=None), clean(quantity="2"), clean(currency="MYR")):
        f = finding(world.run(), "e1")
        assert f.photographic_check.result == "passed" and f.cost_check.result != "within_range"
        assert f.overall_result == "insufficient_evidence"


# ---------------------------------------------------------------- mapping
CASES = {
    1: "test_a01_supported_in_range_is_ok_with_zero_discrepancy_flags",
    2: "test_a02_confirmed_adequate_coverage_without_damage_is_unsupported",
    3: "test_a03_right_door_damage_never_supports_or_refutes_a_left_door_row",
    4: "test_a04_unresolved_side_on_the_observation_gives_side_unresolved",
    5: "test_a05_sharp_large_mask_in_a_cropped_view_is_coverage_inadequate",
    6: "test_a06_part_in_no_photograph_is_coverage_not_visible",
    7: "test_a07_damage_below_threshold_is_uncertain",
    8: "test_a08_failed_image_job_is_branch_failed_and_incomplete",
    9: "test_a09_pending_exclusion_withholds_and_does_not_exclude",
    10: "test_a10_confirmed_exclusion_is_excluded_never_ok",
    11: "test_a11_rejected_exclusion_row_is_checked_normally",
    12: "test_a12_pending_price_change_never_compares_the_printed_amount",
    13: "test_a13_confirmed_price_change_compares_the_entered_amount",
    14: "test_a14_manually_added_mark_behaves_like_a_confirmed_detection",
    15: "test_a15_unlinked_mark_naming_the_row_withholds_it",
    16: "test_a16_conflicting_marks_withhold_even_when_both_are_confirmed",
    17: "test_a17_unreadable_amount_withholds_cost_and_keeps_the_photo_pass",
    18: "test_a18_partial_completeness_proposes_no_additions",
    19: "test_a19_no_parsed_rows_is_never_an_empty_scope",
    20: "test_a20_human_confirmed_empty_scope_may_propose_additions",
    21: "test_a21_ambiguous_row_that_could_hide_a_match_withholds_the_addition",
    22: "test_a22_confirmed_exclusion_on_the_same_part_and_side_suppresses_with_a_note",
    23: "test_a23_exclusion_on_the_right_door_never_suppresses_left_door_damage",
    24: "test_a24_two_operations_on_one_part_are_both_checked_with_no_spurious_addition",
    25: "test_a25_amount_equal_to_lower_bound_is_within_range",
    26: "test_a26_amount_equal_to_upper_bound_is_within_range",
    27: "test_a27_one_cent_below_the_lower_bound",
    28: "test_a28_one_cent_above_the_upper_bound",
    29: "test_a29_zero_width_interval_never_divides",
    30: "test_a30_to_a33_incompatible_amounts_get_no_comparison_and_no_deviation",
    31: "test_a30_to_a33_incompatible_amounts_get_no_comparison_and_no_deviation",
    32: "test_a30_to_a33_incompatible_amounts_get_no_comparison_and_no_deviation",
    33: "test_a30_to_a33_incompatible_amounts_get_no_comparison_and_no_deviation",
    34: "test_a34_range_withheld_for_support_keeps_the_photo_check_passed",
    35: "test_a35_absent_range_is_no_key_never_a_zero_range",
    36: "test_a36_unknown_vehicle_class_is_not_compared",
    37: "test_a37_corrected_amount_creates_a_new_assessment_and_the_original_stays_readable",
    38: "test_a38_confirmed_identity_reuses_image_artifacts_by_lineage_without_a_rerun",
    39: "test_a39_dismissal_carries_only_to_an_unchanged_finding",
    40: "test_a40_duplicate_consolidate_delivery_builds_the_identical_assessment",
    41: "test_a41_superseded_revision_still_writes_a_full_historical_assessment",
    42: "test_a42_finalization_against_a_stale_revision_is_refused",
}
SAFETY_INVARIANTS = {
    "SI-01": ["test_a12_pending_price_change_never_compares_the_printed_amount",
              "test_a13_confirmed_price_change_compares_the_entered_amount"],
    "SI-02": ["test_a05_sharp_large_mask_in_a_cropped_view_is_coverage_inadequate",
              "test_a06_part_in_no_photograph_is_coverage_not_visible",
              "test_a07b_out_of_scope_or_identity_uncertain_evidence_blocks_a_negative"],
    "SI-03": ["test_a03_right_door_damage_never_supports_or_refutes_a_left_door_row",
              "test_a04_unresolved_side_on_the_observation_gives_side_unresolved",
              "test_a04b_side_is_never_invented_only_a_recorded_identity_confirmation_unblocks",
              "test_a23_exclusion_on_the_right_door_never_suppresses_left_door_damage"],
    "SI-04": ["test_a09_pending_exclusion_withholds_and_does_not_exclude",
              "test_a12_pending_price_change_never_compares_the_printed_amount",
              "test_a15_unlinked_mark_naming_the_row_withholds_it",
              "test_a16_conflicting_marks_withhold_even_when_both_are_confirmed"],
    "SI-05": ["test_a08_failed_image_job_is_branch_failed_and_incomplete"],
    "SI-06": ["test_a37_corrected_amount_creates_a_new_assessment_and_the_original_stays_readable"],
    "SI-07": ["test_si07_si10_cost_outcomes_are_labelled_synthetic_and_never_fraud"],
    "SI-08": ["test_a34_range_withheld_for_support_keeps_the_photo_check_passed",
              "test_a35_absent_range_is_no_key_never_a_zero_range"],
    "SI-09": ["test_si09_fixture_inputs_are_stamped_fixture"],
    "SI-10": ["test_si07_si10_cost_outcomes_are_labelled_synthetic_and_never_fraud",
              "test_a13_confirmed_price_change_compares_the_entered_amount"],
    "SI-11": ["test_si11_versions_are_pinned_and_a_later_table_never_changes_an_assessment"],
    "SI-12": ["test_a38_confirmed_identity_reuses_image_artifacts_by_lineage_without_a_rerun"],
}


def test_every_required_case_and_safety_invariant_is_mapped_to_a_test():
    assert sorted(CASES) == list(range(1, 43))
    assert sorted(SAFETY_INVARIANTS) == [f"SI-{n:02d}" for n in range(1, 13)]
    names = set(CASES.values()) | {n for tests in SAFETY_INVARIANTS.values() for n in tests}
    missing = [n for n in names if not callable(globals().get(n))]
    assert missing == []


def test_every_code_emitted_by_these_cases_is_catalogued():
    worlds = [clean(), clean(amount=None), clean(quantity="2")]
    excluded = clean()
    excluded.mark("m1", "e1", "exclusion", "confirmed")
    pending = clean()
    pending.mark("m1", "e1", "price_change", "pending")
    failed = clean()
    failed.image_state = "failed"
    for world in worlds + [excluded, pending, failed]:
        assert emitted_codes(world.run()) <= set(REASON_CODES)


def test_decimal_rounding_is_half_up_once_and_floats_are_refused():
    assert cmp("620.005").direction == "above" and cmp("620.005").absolute_deviation == "0.01"
    assert cmp("620.004").result == "within_range" and cmp("620.004").amount == "620.00"
    assert cmp(Decimal("379.995")).result == "within_range"
    assert cmp(500.0).reason_code == "amount_unreadable"  # a binary float is never money
    fine = supported("754.565", "1146.144")
    check = cmp("754.57", fine)
    assert (check.lower_amount, check.upper_amount, check.result) == ("754.57", "1146.14", "within_range")
    assert isinstance(check.normalised_score, str)
