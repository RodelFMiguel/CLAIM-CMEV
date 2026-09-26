"""link_marks: EX-1, PC-1, PC-2, PC-3, candidates, duplicates, conflicts and operational cases.

Detections are fixture boxes. These tests exercise the deterministic rules only; no
detector exists and no detection or linking accuracy is measured here.
"""
import random

import pytest

from claim_cmev.contracts.common import ContractError, deterministic_id, make_job_key
from claim_cmev.contracts.documents import TrocrSuggestion
from claim_cmev.contracts.fixtures import fixture_bundle
from claim_cmev.documents.pen_marks import Detection, RowBox, link_marks, row_mark_states
from m6_support import (
    CLAIM, CONFIG, DETECTOR_VERSIONS, JOB_KEY, PROV, det, line_item, link, norm, ruled_rows, worked_example_rows,
)

# ruled_rows(): row i spans y [1000 + 60i, 1054 + 60i]; its amount token is x 2040..2290, 4 px inset.


def by_type(result, mark_type):
    return [m for m in result.marks if m.mark_type == mark_type]


def scores_of(result, mark_id):
    return {c.entry_id: c for c in result.candidates if c.mark_id == mark_id}


def test_worked_example_ex1_links_detection_a_and_pc3_keeps_detection_b_unlinked():
    rows = worked_example_rows()
    result = link([det((1850, 1300, 2320, 1362), "exclusion", 0.88),
                   det((2005, 1381, 2210, 1441), "price_change", 0.79)], rows)
    (a,), (b,) = by_type(result, "exclusion"), by_type(result, "price_change")
    assert (a.entry_id, a.rule_id, a.link_reason, a.state) == ("e-003", "EX-1", "unambiguous_row_overlap", "pending")
    assert a.model_entry_id == "e-003" and a.candidate_entry_ids == ["e-003", "e-004"]
    sa = scores_of(result, a.mark_id)
    # Spec: band(e-003) = [1261.2, 1368.5], v = 1.00, rc = 0.25, col = 1; e-004 stays below 0.50.
    assert sa["e-003"].v == 1.0 and sa["e-003"].rc == pytest.approx(0.25, abs=0.005) and sa["e-003"].col == 1
    assert sa["e-004"].v == pytest.approx(0.69, abs=0.005) and sa["e-004"].link_score < CONFIG.link.min_score
    assert sa["e-003"].link_score - sa["e-004"].link_score >= CONFIG.link.margin_to_second
    assert (b.entry_id, b.rule_id, b.link_reason, b.state) == (None, "PC-3", "mark_between_rows", "pending")
    assert set(b.candidate_entry_ids) == {"e-004", "e-005"} and b.model_entry_id is None
    sb = scores_of(result, b.mark_id)
    assert sb["e-004"].amt == pytest.approx(0.37, abs=0.005) and sb["e-005"].amt == pytest.approx(0.21, abs=0.005)
    assert result.counts == {"exclusion": 1, "price_change": 1, "linked": 1, "unlinked": 1, "conflicting": 0}


def test_pc1_links_a_price_change_written_over_one_amount():
    result = link([det((2030, 1058, 2280, 1104), "price_change", 0.8)], ruled_rows())
    (mark,) = result.marks
    assert (mark.entry_id, mark.rule_id, mark.link_reason) == ("e1", "PC-1", "unambiguous_row_overlap")
    scores = scores_of(result, mark.mark_id)
    assert scores["e1"].amt >= CONFIG.price_change.amount_overlap_min
    assert all(s.amt <= CONFIG.price_change.amount_overlap_second_max for e, s in scores.items() if e != "e1")


def test_pc1_is_blocked_when_a_second_amount_is_touched():
    result = link([det((2030, 1030, 2280, 1080), "price_change", 0.8)], ruled_rows())
    (mark,) = result.marks
    scores = scores_of(result, mark.mark_id)
    assert scores["e0"].amt > CONFIG.price_change.amount_overlap_second_max
    assert scores["e1"].amt > CONFIG.price_change.amount_overlap_second_max
    assert (mark.entry_id, mark.rule_id, mark.link_reason) == (None, "PC-3", "mark_between_rows")
    assert set(mark.candidate_entry_ids) == {"e0", "e1"}


def test_pc2_links_a_clear_band_mark_in_the_right_margin():
    detection = det((2295, 1064, 2335, 1086), "price_change", 0.7)
    result = link([detection], ruled_rows())
    (mark,) = result.marks
    scores = scores_of(result, mark.mark_id)
    assert all(s.amt == 0 for s in scores.values())  # PC-1 cannot fire
    assert scores["e1"].col == 1  # within right_margin_tolerance_px of the amount column
    assert (mark.entry_id, mark.rule_id) == ("e1", "PC-2")
    assert mark.candidate_entry_ids == ["e1", "e0"]  # the weaker candidate is retained as evidence
    # Without the right-margin tolerance the column no longer matches and nothing clears the score.
    strict = CONFIG.with_overrides({"column": {"right_margin_tolerance_px": 0}})
    (unlinked,) = link([detection], ruled_rows(), config=strict).marks
    assert (unlinked.entry_id, unlinked.rule_id, unlinked.link_reason) == (None, "PC-3", "mark_between_rows")


def test_pc2_needs_the_larger_price_change_margin():
    # A lead that satisfies the exclusion margin but not the price-change margin stays unlinked.
    rows = ruled_rows()
    loose = CONFIG.with_overrides({"price_change": {"margin_to_second": 0.45}})
    (mark,) = link([det((2295, 1064, 2335, 1086), "price_change", 0.7)], rows, config=loose).marks
    assert mark.entry_id is None and mark.rule_id == "PC-3"


def test_ex1_exclusion_straddling_two_rows_stays_unlinked_with_both_candidates():
    result = link([det((1850, 1030, 2320, 1080), "exclusion", 0.9)], ruled_rows())
    (mark,) = result.marks
    scores = scores_of(result, mark.mark_id)
    assert scores["e0"].link_score >= CONFIG.link.min_score and scores["e1"].link_score >= CONFIG.link.min_score
    assert (mark.entry_id, mark.rule_id, mark.link_reason) == (None, "EX-1", "mark_between_rows")
    assert mark.candidate_entry_ids == ["e1", "e0"]


def test_no_candidate_row_when_nothing_clears_the_candidate_threshold():
    result = link([det((1850, 200, 2320, 260), "exclusion", 0.9), det((2040, 300, 2280, 340), "price_change", 0.9)],
                  ruled_rows())
    for mark in result.marks:
        assert (mark.entry_id, mark.candidate_entry_ids, mark.link_reason) == (None, [], "no_candidate_row")
    assert result.candidates == () and result.counts["unlinked"] == 2


def test_rows_unavailable_for_a_page_without_rows():
    rows = ruled_rows(page_id="dp1")
    result = link([det((1850, 1062, 2300, 1112), "exclusion", 0.9, page_id="dp2")], rows)
    (mark,) = result.marks
    assert (mark.entry_id, mark.candidate_entry_ids, mark.link_reason, mark.rule_id) == (
        None, [], "rows_unavailable", None)
    (none_at_all,) = link([det((1850, 1062, 2300, 1112), "exclusion", 0.9)], []).marks
    assert none_at_all.link_reason == "rows_unavailable" and none_at_all.state == "pending"


def test_multi_page_marks_link_only_to_rows_on_their_own_page():
    rows = ruled_rows(page_id="dp1", prefix="a") + ruled_rows(page_id="dp2", prefix="b", page_number=2)
    box = (1850, 1062, 2300, 1112)
    result = link([det(box, page_id="dp2"), det(box, page_id="dp1")], rows)
    linked = {m.page_id: m for m in result.marks}
    assert linked["dp1"].entry_id == "a1" and linked["dp2"].entry_id == "b1"
    assert all(e.startswith("b") for e in linked["dp2"].candidate_entry_ids)
    assert linked["dp1"].mark_id != linked["dp2"].mark_id
    assert [m.page_id for m in result.marks] == ["dp1", "dp2"]  # page order follows the rows


def test_score_thresholds_come_from_config_and_confidence_is_carried_exactly():
    boxes = [(1850, 1002, 2300, 1052), (1850, 1062, 2300, 1112)]
    detections = [det(boxes[0], "exclusion", 0.49), det(boxes[1], "exclusion", 0.50),
                  det((2030, 1118, 2280, 1164), "price_change", 0.45),
                  det((2030, 1178, 2280, 1224), "price_change", 0.44)]
    result = link(detections, ruled_rows())
    assert result.below_threshold == (0, 3)
    assert sorted(m.detection_confidence for m in result.marks) == [0.45, 0.50]
    assert {result.detection_index[m.mark_id] for m in result.marks} == {1, 2}


def test_every_detector_mark_is_pending_with_detector_provenance_and_versions():
    result = link([det((1850, 1002, 2300, 1052)), det((2030, 1058, 2280, 1104), "price_change", 0.8),
                   det((1850, 1030, 2320, 1080))], ruled_rows())
    assert result.marks
    for mark in result.marks:
        assert mark.state == "pending" and mark.origin == "detector" and mark.detection_confidence is not None
        assert (mark.decision_action_id, mark.decided_by, mark.decided_at, mark.review_revision) == (None,) * 4
        assert mark.confirmed_amount is None and mark.trocr_suggestion is None
        assert mark.provenance.source_kind == "fixture"
        assert mark.versions == {**DETECTOR_VERSIONS, "link_config": CONFIG.link_config_version}


def test_trocr_suggestion_is_passed_through_but_never_becomes_an_amount():
    suggestion = TrocrSuggestion(text="980", confidence=0.61, model_version="trocr-base-handwritten")
    detection = Detection(page_id="dp1", box_norm=norm((2030, 1058, 2280, 1104)), mark_type="price_change",
                          score=0.8, trocr_suggestion=suggestion)
    (mark,) = link([detection], ruled_rows()).marks
    assert mark.trocr_suggestion == suggestion and mark.confirmed_amount is None and mark.state == "pending"


def test_ids_are_deterministic_and_order_independent():
    detections = [det((1850, 1002, 2300, 1052)), det((2030, 1058, 2280, 1104), "price_change", 0.8),
                  det((1850, 1030, 2320, 1080)), det((2295, 1184, 2335, 1206), "price_change", 0.7)]
    first = link(detections, ruled_rows()).marks
    shuffled = detections[:]
    random.Random(7).shuffle(shuffled)
    assert link(shuffled, ruled_rows()).marks == first
    assert first[0].mark_id == deterministic_id("pm", JOB_KEY, "dp1", 0)
    assert len({m.mark_id for m in first}) == len(first)


def test_job_key_and_link_config_version_are_checked():
    rows = ruled_rows()
    other_revision = make_job_key(CLAIM, 2, "pen_marks_detect", DETECTOR_VERSIONS)
    with pytest.raises(ContractError) as err:
        link_marks([], rows, config=CONFIG, job_key=other_revision, claim_id=CLAIM, input_revision=1,
                   provenance=PROV, versions=DETECTOR_VERSIONS)
    assert err.value.reason_code == "job_key_mismatch"
    with pytest.raises(ContractError) as err:
        link_marks([], rows, config=CONFIG, job_key=JOB_KEY, claim_id=CLAIM, input_revision=1, provenance=PROV,
                   versions={**DETECTOR_VERSIONS, "link_config": "m6-linking/other"})
    assert err.value.reason_code == "link_config_version_mismatch"
    with pytest.raises(ContractError) as err:
        link([], rows + rows[:1])
    assert err.value.reason_code == "duplicate_entry_id"
    with pytest.raises(ContractError) as err:
        link([{"page_id": "dp1", "box_norm": (0.5, 0.5, 0.4, 0.6), "mark_type": "exclusion", "score": 0.9}], rows)
    assert err.value.reason_code == "detection_invalid"


def test_zero_detections_is_a_valid_empty_proposal():
    result = link([], ruled_rows())
    assert result.marks == () and result.counts == {"exclusion": 0, "price_change": 0, "linked": 0,
                                                      "unlinked": 0, "conflicting": 0}


# --- duplicates and conflicts -----------------------------------------------------------------
def test_same_class_duplicates_at_dedupe_iou_are_merged_and_recorded():
    result = link([det((1850, 1062, 2300, 1112), "exclusion", 0.81), det((1860, 1064, 2310, 1114), "exclusion", 0.9)],
                  ruled_rows())
    (mark,) = result.marks
    assert mark.detection_confidence == 0.9 and mark.entry_id == "e1"
    (merged,) = result.merged_duplicates
    assert merged.kept_mark_id == mark.mark_id and merged.detection_index == 0 and merged.score == 0.81
    assert merged.iou >= CONFIG.detection.dedupe_iou


def test_distinct_same_class_boxes_below_dedupe_iou_stay_separate_marks():
    result = link([det((700, 1062, 1100, 1112)), det((1850, 1062, 2300, 1112))], ruled_rows())
    assert [m.entry_id for m in result.marks] == ["e1", "e1"] and result.merged_duplicates == ()
    states = row_mark_states(ruled_rows(), result.marks)
    assert states["e1"].state == "pending"  # two exclusions within max_marks_per_row do not conflict
    third = link([det((700, 1062, 1100, 1112)), det((1200, 1062, 1600, 1112)), det((1850, 1062, 2300, 1112))],
                 ruled_rows())
    assert third.conflicting_entries == {"e1": "too_many_marks"} and third.counts["conflicting"] == 1


def test_exclusion_and_price_change_on_one_row_conflict_and_are_never_merged():
    box = (2030, 1058, 2280, 1104)
    result = link([det(box, "exclusion", 0.9), det(box, "price_change", 0.9)], ruled_rows())
    assert len(result.marks) == 2 and {m.entry_id for m in result.marks} == {"e1"}
    assert all(m.state == "pending" for m in result.marks)
    assert result.conflicting_entries == {"e1": "exclusion_and_price_change"} and result.counts["conflicting"] == 1


def test_two_price_changes_on_one_row_conflict():
    result = link([det((2040, 1064, 2150, 1110), "price_change", 0.8), det((2170, 1064, 2290, 1110), "price_change", 0.8)],
                  ruled_rows())
    assert [(m.entry_id, m.rule_id) for m in result.marks] == [("e1", "PC-1"), ("e1", "PC-1")]
    assert result.conflicting_entries == {"e1": "multiple_price_changes"}


# --- row inputs -------------------------------------------------------------------------------
def test_rows_may_be_line_items_row_boxes_or_command_payload_mappings():
    items = ruled_rows()
    boxes = [RowBox(entry_id=i.entry_id, page_id=i.page_id, row_box_norm=i.row_box_norm,
                    amount_box_norm=i.amount_box_norm) for i in items]
    payload = [b.model_dump(mode="json") for b in boxes]
    detections = [det((1850, 1002, 2300, 1052)), det((2005, 1152, 2210, 1196), "price_change", 0.7)]
    assert link(detections, items).marks == link(detections, boxes).marks == link(detections, payload).marks


def test_page_width_defaults_to_the_configured_width():
    detections = [det((2295, 1064, 2335, 1086), "price_change", 0.7)]
    assert link(detections, ruled_rows(), page_widths_px={}).marks == link(detections, ruled_rows()).marks


def test_row_without_an_amount_box_scores_no_amount_overlap():
    rows = ruled_rows(3)
    rows[1] = line_item("e1", 1060, 1114, amount_box=False)
    result = link([det((2030, 1058, 2280, 1104), "price_change", 0.8)], rows)
    (mark,) = result.marks
    scores = scores_of(result, mark.mark_id)
    assert scores["e1"].amt == 0.0 and scores["e1"].col == 1  # column from the page's other amount boxes
    assert mark.rule_id in {"PC-2", "PC-3"}  # never PC-1 without the amount token


@pytest.mark.parametrize("scenario", ["exclusion_and_supported", "partial_extraction", "pending_price_change"])
def test_linker_reproduces_the_contract_fixture_links(scenario):
    """The shared fixture bundles were written with the spec geometry; the linker must agree."""
    bundle = fixture_bundle(scenario, CLAIM)
    detections = [Detection(page_id=m.page_id, box_norm=m.box_norm, mark_type=m.mark_type,
                            score=m.detection_confidence) for m in bundle.pen_marks]
    job_key = make_job_key(CLAIM, 1, "pen_marks_detect", DETECTOR_VERSIONS)
    marks = link_marks(detections, bundle.line_items, config=CONFIG, job_key=job_key, claim_id=CLAIM,
                       input_revision=1, provenance=PROV, versions=DETECTOR_VERSIONS, page_widths_px={})
    expected = sorted((m.mark_type, m.entry_id, m.link_reason, m.rule_id, tuple(sorted(m.candidate_entry_ids)))
                      for m in bundle.pen_marks)
    got = sorted((m.mark_type, m.entry_id, m.link_reason, m.rule_id,
                  tuple(sorted(m.candidate_entry_ids)) if m.entry_id is None else tuple(sorted(
                      c for c in m.candidate_entry_ids if c == m.entry_id))) for m in marks)
    assert got == expected
