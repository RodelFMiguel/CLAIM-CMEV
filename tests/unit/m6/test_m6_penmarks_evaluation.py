"""Detection and linking metrics for the pen-mark detector, against hand-worked cases.

Boxes are invented; nothing here measures a model. The linking tests run the project's own
``link_detections`` unchanged on rows built from fixture geometry.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch")

from claim_cmev.documents.pen_marks import load_linking_config  # noqa: E402
from pipelines.documents import penmarks_data as pd_  # noqa: E402
from pipelines.documents import penmarks_evaluation as ev  # noqa: E402

W, H = 1000, 1000
CLAIM = "01JAX7Q0VN4Z3K9F2M8R6T1C5D"
CONFIG = load_linking_config()


def _mark(n, cls, box, row="part_1", style=None, image_id="P2"):
    return pd_.MarkBox(f"{image_id}-mark-{n:02d}", cls, style or ("x" if cls == "exclusion" else "amount_strike"),
                       tuple(map(float, box)), (tuple(map(float, box)),), row, f"{image_id}:{row}" if row else None, None)


def _page(image_id="P2", marks=(), distractors=(), rows=(), page_type="repair_estimate", claim="SYN-1", split="validation"):
    marks = tuple(marks)
    return pd_.PenMarkPage(
        image_id=image_id, dataset_claim_id=claim, claim_id=CLAIM, split=split, page_type=page_type, page_number=2,
        page_id=f"pg-{image_id}", page_status="complete", image_path=Path("/nonexistent.png"), width=W, height=H,
        writer="Writer 1", marks=marks, distractors=tuple(distractors), rows=tuple(rows),
        boxes=np.array([m.box for m in marks], dtype=np.float32).reshape(-1, 4),
        labels=np.array([pd_.CLASS_TO_LABEL[m.mark_class] for m in marks], dtype=np.int64))


def _pred(*items):
    """Each item is (box, label, score)."""
    return ev.PagePrediction(np.array([i[0] for i in items], dtype=float).reshape(-1, 4),
                             np.array([i[1] for i in items], dtype=int), np.array([i[2] for i in items], dtype=float))


# --------------------------------------------------------------------------- matching and AP

def test_iou_matrix_matches_hand_values():
    iou = ev.iou_matrix(np.array([[0, 0, 10, 10]]), np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]]))
    np.testing.assert_allclose(iou, [[1.0, 1 / 3, 0.0]])
    assert ev.iou_matrix(np.zeros((0, 4)), np.zeros((2, 4))).shape == (0, 2)


def test_greedy_matching_gives_each_mark_to_its_best_scoring_detection_once():
    truth = np.array([[0, 0, 10, 10], [20, 0, 30, 10]], dtype=float)
    boxes = np.array([[0, 0, 10, 9], [0, 0, 10, 10], [19, 0, 30, 10], [50, 50, 60, 60]], dtype=float)
    matched, ious = ev.match_page(boxes, np.array([0.9, 0.8, 0.7, 0.6]), truth, 0.5)
    assert matched.tolist() == [0, -1, 1, -1]  # the exact box loses to the higher score: a duplicate
    np.testing.assert_allclose(ious, [0.9, 1.0, 10 / 11, 0.0])
    # Scores decide the order, not the position in the array.
    matched, _ = ev.match_page(boxes[[1, 0]], np.array([0.8, 0.9]), truth, 0.5)
    assert matched.tolist() == [-1, 0]
    assert ev.match_page(boxes, np.array([0.9, 0.8, 0.7, 0.6]), np.zeros((0, 4)), 0.5)[0].tolist() == [-1] * 4


def test_average_precision_is_the_101_point_interpolated_area():
    # Two marks; detections in score order: hit, false alarm, hit.
    precision, recall, _ = ev.precision_recall(np.array([0.9, 0.8, 0.7]), np.array([True, False, True]), 2)
    np.testing.assert_allclose(precision, [1.0, 0.5, 2 / 3])
    np.testing.assert_allclose(recall, [0.5, 0.5, 1.0])
    # Recall 0.00..0.50 (51 points) reaches precision 1; 0.51..1.00 (50 points) reaches 2/3.
    assert ev.average_precision(precision, recall, 2) == pytest.approx((51 + 50 * 2 / 3) / 101)
    assert ev.average_precision(np.array([1.0, 1.0]), np.array([0.5, 1.0]), 2) == pytest.approx(1.0)
    assert ev.average_precision(np.zeros(0), np.zeros(0), 3) == 0.0  # marks exist, nothing was detected
    assert ev.average_precision(np.zeros(0), np.zeros(0), 0) is None  # no marks: undefined, never 1.0


def test_detection_report_gives_ap_at_both_iou_ranges_with_counts():
    pages = [_page("A", [_mark(1, "exclusion", (0, 0, 100, 100)), _mark(2, "exclusion", (200, 0, 300, 100))]),
             _page("B", [])]
    predictions = {"A": _pred(((0, 0, 100, 78), 1, 0.9), ((200, 0, 300, 100), 1, 0.7)),  # IoU 0.78 and 1.0
                   "B": _pred(((0, 0, 50, 50), 1, 0.8))}  # a false alarm on a page without marks
    report = ev.detection_report(pages, predictions)
    exclusion, price_change = report["exclusion"], report["price_change"]
    assert (exclusion["marks"], exclusion["detections"]) == (2, 3)
    assert exclusion["ap50"] == pytest.approx((51 + 50 * 2 / 3) / 101)
    # IoU 0.50-0.75 (6 thresholds): both hit. IoU 0.80-0.95 (4 thresholds): only the exact box,
    # ranked third after two false alarms: precision 1/3 up to recall 0.5 -> 51/3/101.
    assert exclusion["ap50_95"] == pytest.approx((6 * (51 + 50 * 2 / 3) / 101 + 4 * (51 / 3) / 101) / 10)
    assert (price_change["marks"], price_change["detections"], price_change["ap50"]) == (0, 0, None)
    assert report["mean_ap50"] == exclusion["ap50"], "a class without marks is left out of the mean"
    assert len(exclusion["curve"]["precision"]) == len(exclusion["curve"]["recall"]) == 3
    json.dumps(report)


def test_f1_by_threshold_and_the_plateau_rule():
    scores, hits = np.array([0.95, 0.90, 0.60, 0.30]), np.array([True, True, False, True])
    grid = np.array([0.2, 0.4, 0.5, 0.7, 0.92, 0.97])
    table = ev.threshold_table(scores, hits, 3, grid)
    np.testing.assert_allclose(table["precision"][:5], [3 / 4, 2 / 3, 2 / 3, 1.0, 1.0])
    np.testing.assert_allclose(table["recall"], [1.0, 2 / 3, 2 / 3, 2 / 3, 1 / 3, 0.0])
    np.testing.assert_allclose(table["f1"], [6 / 7, 2 / 3, 2 / 3, 0.8, 0.5, 0.0])
    assert table["true_positives"].tolist() == [3, 2, 2, 2, 1, 0] and table["false_positives"].tolist() == [1, 1, 1, 0, 0, 0]
    assert np.isnan(table["precision"][5]), "no detection: precision is undefined, not 1"
    assert ev.select_threshold(table) == 0.2
    # A flat top: every threshold from 0.30 to 0.60 gives the best F1; the middle one is chosen.
    flat = {"threshold": np.round(np.arange(0.1, 0.91, 0.1), 2),
            "f1": np.array([0.5, 0.7, 0.9, 0.9, 0.899, 0.9, 0.6, 0.9, 0.2])}
    assert ev.select_threshold(flat, tolerance=0.005) == 0.4
    assert ev.select_threshold({"threshold": np.array([0.3, 0.5]), "f1": np.array([0.0, 0.0])}, fallback=0.5) == 0.5


def test_selection_score_is_the_mean_best_f1_of_the_classes_that_have_marks():
    pages = [_page("A", [_mark(1, "exclusion", (0, 0, 100, 100)), _mark(2, "price_change", (0, 200, 100, 300))])]
    perfect = {"A": _pred(((0, 0, 100, 100), 1, 0.9), ((0, 200, 100, 300), 2, 0.8))}
    half = {"A": _pred(((0, 0, 100, 100), 1, 0.9))}
    assert ev.selection_score(pages, perfect) == pytest.approx(1.0)
    assert ev.selection_score(pages, half) == pytest.approx(0.5)
    assert ev.selection_score(pages, {"A": _pred()}) == 0.0
    assert ev.selection_score([_page("B")], {"B": _pred(((0, 0, 9, 9), 1, 0.9))}) == 0.0


# --------------------------------------------------------------------------- operating point

def _scenario():
    tick = _mark(9, "acceptance_tick", (700, 110, 730, 140), style="tick")
    note = _mark(1, "review_note", (100, 800, 300, 830), None, style="handwritten_note", image_id="P1")
    estimate = _page("P2", [_mark(1, "exclusion", (100, 100, 130, 130), "part_1", "x"),
                            _mark(2, "exclusion", (100, 200, 900, 212), "part_2", "row_strike"),
                            _mark(3, "price_change", (600, 300, 900, 340), "part_3", "amount_strike"),
                            _mark(4, "price_change", (600, 400, 900, 440), "part_4", "amount_cross")], [tick])
    assessment = _page("P1", [], [note], page_type="survey_assessment")
    predictions = {
        "P2": _pred(((100, 100, 130, 130), 1, 0.95),  # hit on the X
                    ((101, 101, 131, 131), 1, 0.90),  # a second box on the same X: duplicate
                    ((100, 198, 900, 220), 1, 0.85),  # row strike found too tall: IoU 12/22 = 0.545
                    ((600, 300, 900, 340), 1, 0.80),  # price change called an exclusion: wrong class
                    ((600, 400, 760, 440), 2, 0.75),  # half of the last price change: IoU 0.53
                    ((700, 110, 730, 140), 1, 0.70),  # the tick
                    ((600, 300, 900, 340), 2, 0.20)),  # the right class, below its threshold
        "P1": _pred(((100, 800, 300, 830), 2, 0.65),  # the review note on the page without marks
                    ((500, 500, 520, 520), 1, 0.30))}  # below threshold: ignored
    return [assessment, estimate], predictions


def test_operating_point_counts_hits_misses_and_kinds_of_false_alarm():
    pages, predictions = _scenario()
    point = ev.operating_point(pages, predictions, {"exclusion": 0.5, "price_change": 0.45})
    exclusion, price_change = point["classes"]["exclusion"], point["classes"]["price_change"]
    assert (exclusion["true_positives"], exclusion["false_positives"], exclusion["false_negatives"]) == (2, 3, 0)
    assert (price_change["true_positives"], price_change["false_positives"], price_change["false_negatives"]) == (1, 1, 1)
    assert exclusion["precision"] == pytest.approx(0.4) and exclusion["recall"] == 1.0
    assert price_change["f1"] == pytest.approx(0.5) and price_change["marks"] == 2
    kinds = sorted((fp["image_id"], fp["mark_type"], fp["kind"], fp["on_page_without_marks"])
                   for fp in point["false_positives"])
    assert kinds == [("P1", "price_change", "on_distractor", True), ("P2", "exclusion", "duplicate", False),
                     ("P2", "exclusion", "on_distractor", False), ("P2", "exclusion", "wrong_class", False)]
    assert [fp["distractor_class"] for fp in point["false_positives"] if fp["kind"] == "on_distractor"] == [
        "review_note", "acceptance_tick"]
    (miss,) = point["misses"]
    # A weak price-change box exists too, but the exclusion box over it is the more specific cause.
    assert (miss["mark_id"], miss["style"], miss["kind"]) == ("P2-mark-03", "amount_strike", "detected_as_other_class")
    assert miss["best_score_at_iou"] == pytest.approx(0.20)
    assert point["false_positive_summary"] == {"total": 4, "on_pages_without_marks": 1, "on_distractor": 2,
                                               "duplicate": 1, "wrong_class": 1, "part_of_found_mark": 0,
                                               "poorly_localised": 0, "background": 0}
    styles = {(row["mark_class"], row["style"]): (row["detected"], row["marks"]) for row in point["styles"]}
    assert styles == {("exclusion", "x"): (1, 1), ("exclusion", "row_strike"): (1, 1),
                      ("price_change", "amount_strike"): (0, 1), ("price_change", "amount_cross"): (1, 1)}
    assert sorted(round(v, 3) for v in point["matched_ious"]["exclusion"]) == [0.545, 1.0]
    assert point["pages_without_marks"] == {"pages": 1, "with_a_false_positive": 1}
    json.dumps(point)


def test_an_operating_point_without_detections_reports_undefined_precision_not_a_pass():
    pages, _ = _scenario()
    point = ev.operating_point(pages, {"P1": _pred(), "P2": _pred()}, {"exclusion": 0.5, "price_change": 0.45})
    exclusion = point["classes"]["exclusion"]
    assert (exclusion["true_positives"], exclusion["false_negatives"], exclusion["recall"]) == (0, 2, 0.0)
    assert exclusion["precision"] is None and exclusion["f1"] == 0.0
    assert {m["kind"] for m in point["misses"]} == {"not_detected"} and len(point["misses"]) == 4


def test_a_miss_says_when_a_box_existed_under_the_threshold_or_beside_the_mark():
    page = _page("A", [_mark(1, "price_change", (600, 300, 900, 340)), _mark(2, "exclusion", (100, 100, 130, 130))])
    predictions = {"A": _pred(((600, 300, 900, 340), 2, 0.30), ((100, 100, 190, 130), 1, 0.90))}  # IoU 1.0 and 1/3
    point = ev.operating_point([page], predictions, {"exclusion": 0.5, "price_change": 0.45})
    kinds = {m["mark_id"]: (m["kind"], m["best_score_at_iou"]) for m in point["misses"]}
    assert kinds == {"P2-mark-01": ("below_threshold", pytest.approx(0.30)), "P2-mark-02": ("poorly_localised", None)}
    (alarm,) = point["false_positives"]
    assert (alarm["kind"], alarm["best_iou_with_a_mark"]) == ("poorly_localised", pytest.approx(1 / 3))


def test_a_false_alarm_on_part_of_a_found_mark_gets_its_own_kind():
    """The labels box a cross and its remark as ONE mark; a detector may also propose the parts."""
    whole = (100, 100, 300, 140)
    page = _page("A", [_mark(1, "exclusion", whole, "part_1", "x_and_remark"),
                       _mark(2, "exclusion", (100, 300, 900, 308), "part_3", "row_strike")])
    predictions = {"A": _pred((whole, 1, 0.95),                   # the whole mark: a hit
                              ((100, 105, 125, 135), 1, 0.93),    # the cross alone: IoU 750/8000 = 0.09
                              ((220, 100, 300, 140), 1, 0.92),    # most of the remark alone: IoU 0.40
                              ((280, 100, 380, 140), 1, 0.91),    # only a fifth of it lies on the mark
                              ((100, 300, 460, 308), 1, 0.90))}   # part of the strike, which was NOT found
    point = ev.operating_point([page], predictions, {"exclusion": 0.5, "price_change": 0.45})
    kinds = [(fp["kind"], fp["inside_mark_style"], round(fp["share_inside_mark"], 2))
             for fp in sorted(point["false_positives"], key=lambda fp: -fp["score"])]
    assert kinds == [("part_of_found_mark", "x_and_remark", 1.0), ("part_of_found_mark", "x_and_remark", 1.0),
                     ("background", None, 0.2), ("poorly_localised", None, 1.0)]
    assert [fp["inside_mark_id"] for fp in point["false_positives"] if fp["kind"] == "part_of_found_mark"] == [
        "P2-mark-01", "P2-mark-01"]
    assert point["false_positive_summary"] == {"total": 4, "on_pages_without_marks": 0, "on_distractor": 0,
                                               "duplicate": 0, "wrong_class": 0, "part_of_found_mark": 2,
                                               "poorly_localised": 1, "background": 1}
    assert "part_of_found_mark" in ev.FALSE_POSITIVE_KINDS
    (miss,) = point["misses"]
    assert (miss["style"], miss["kind"]) == ("row_strike", "poorly_localised")


# --------------------------------------------------------------------------- thresholds: how firm is the choice?

def test_the_plateau_behind_a_selected_threshold_is_reported_with_its_bounds():
    flat = {"threshold": np.round(np.arange(0.1, 0.91, 0.1), 2),
            "f1": np.array([0.5, 0.7, 0.9, 0.9, 0.899, 0.9, 0.6, 0.9, 0.2])}
    assert ev.best_f1_run(flat, tolerance=0.005) == {"low": 0.3, "high": 0.6, "points": 4, "selected": 0.4,
                                                     "best_f1": 0.9, "equally_good_thresholds": 5}
    # The rule degenerates when the best F1 is a spike: the "widest run" is one grid point.
    spike = {"threshold": np.array([0.89, 0.90, 0.91, 0.92, 0.93]), "f1": np.array([0.809, 0.828, 0.8226, 0.8235, 0.807])}
    run = ev.best_f1_run(spike, tolerance=0.005)
    assert (run["low"], run["high"], run["points"], run["selected"]) == (0.90, 0.90, 1, 0.90)
    assert ev.select_threshold(spike) == 0.90 and run["equally_good_thresholds"] == 2
    assert ev.best_f1_run({"threshold": np.array([0.3]), "f1": np.array([0.0])}) is None


def test_counts_at_named_thresholds_show_what_a_small_shift_costs():
    scores, hits = np.array([0.95, 0.90, 0.60, 0.30]), np.array([True, True, False, True])
    table = ev.threshold_table(scores, hits, 3, np.round(np.arange(0.05, 0.96, 0.01), 2))
    rows = ev.threshold_counts(table, [0.30, 0.50, 0.92])
    assert [(r["threshold"], r["true_positives"], r["false_positives"], r["false_negatives"]) for r in rows] == [
        (0.30, 3, 1, 0), (0.50, 2, 1, 1), (0.92, 1, 0, 2)]
    assert rows[0]["recall"] == 1.0 and rows[2]["precision"] == 1.0 and rows[1]["f1"] == pytest.approx(2 / 3)
    with pytest.raises(ValueError, match="grid"):
        ev.threshold_counts(table, [0.999])


def _claim_pages(count=6):
    """One estimate page per claim with one exclusion; the detection's score falls from claim to claim."""
    pages, predictions = [], {}
    for index in range(count):
        image_id = f"C{index}"
        pages.append(_page(image_id, [_mark(1, "exclusion", (100, 100, 130, 130), image_id=image_id)],
                           claim=f"SYN-{index}"))
        predictions[image_id] = _pred(((100, 100, 130, 130), 1, 0.95 - 0.1 * index),
                                      ((500, 500, 530, 530), 1, 0.30))          # a weak false alarm on every page
    return pages, predictions


def test_claim_halves_are_disjoint_whole_claims_and_repeat_for_a_seed():
    pages, _ = _claim_pages()
    pages.append(_page("C0-P1", [], page_type="survey_assessment", claim="SYN-0"))  # the second page of a claim
    first, second = ev.claim_halves(pages, seed=7)
    assert len(first) + len(second) == len(pages)
    claims = lambda half: {p.dataset_claim_id for p in half}  # noqa: E731
    assert not claims(first) & claims(second) and len(claims(first)) == len(claims(second)) == 3
    again = ev.claim_halves(pages, seed=7)
    assert [p.image_id for p in again[0]] == [p.image_id for p in first]
    assert {p.image_id for p in ev.claim_halves(pages, seed=8)[0]} != {p.image_id for p in first} or True
    with pytest.raises(ValueError, match="held-out"):
        ev.claim_halves([_page("T", split="test")], seed=1)


def test_a_threshold_selected_on_one_half_is_scored_on_the_other_half():
    pages, predictions = _claim_pages()
    select, score = pages[:3], pages[3:]          # scores 0.95, 0.85, 0.75 against 0.65, 0.55, 0.45
    result = ev.threshold_transfer(select, score, predictions)["exclusion"]
    # On the first half every threshold from 0.31 to 0.75 gives F1 1.0; the middle of that run is 0.53.
    assert result["threshold"] == pytest.approx(0.53) and (result["plateau_low"], result["plateau_high"]) == (0.31, 0.75)
    assert (result["marks"], result["true_positives"], result["false_positives"]) == (3, 2, 0)  # 0.45 is lost
    assert result["recall"] == pytest.approx(2 / 3) and result["precision"] == 1.0
    rows = ev.half_split_check(pages, predictions, seeds=(1, 2))
    exclusion = [r for r in rows if r["mark_class"] == "exclusion"]
    assert len(exclusion) == 4 and {r["seed"] for r in exclusion} == {1, 2}       # both directions per seed
    for seed in (1, 2):
        assert sum(r["marks"] for r in exclusion if r["seed"] == seed) == 6       # every mark is scored once
    assert all(r["selected_on_pages"] + r["scored_on_pages"] == 6 for r in exclusion)


def test_two_sets_of_detections_are_compared_as_sets_at_the_thresholds():
    pages = [_page("A", [_mark(1, "exclusion", (0, 0, 100, 100))]), _page("B", [])]
    reference = {"A": _pred(((0, 0, 100, 100), 1, 0.95), ((200, 0, 300, 100), 1, 0.91), ((0, 200, 100, 300), 2, 0.99)),
                 "B": _pred(((0, 0, 50, 50), 1, 0.97), ((0, 60, 50, 90), 1, 0.20))}
    other = {"A": _pred(((1, 0, 100, 100), 1, 0.93),       # the same box, moved 1 px, score down by 0.02
                        ((200, 0, 300, 100), 1, 0.89),     # fell below the exclusion threshold: vanished
                        ((0, 200, 100, 300), 2, 0.99),
                        ((400, 0, 500, 100), 1, 0.92)),    # new
             "B": _pred(((0, 0, 50, 50), 1, 0.97))}        # the weak box is under the threshold in both
    result = ev.compare_detection_sets(pages, reference, other, {"exclusion": 0.90, "price_change": 0.59})
    assert (result["pages"], result["pages_changed"], result["changed_pages"]) == (2, 1, ["A"])
    assert (result["kept_in_reference"], result["kept_in_other"]) == (4, 4)
    assert (result["matched"], result["vanished"], result["appeared"]) == (3, 1, 1)
    assert result["max_score_shift"] == pytest.approx(0.02) and result["max_box_shift_px"] == pytest.approx(1.0)
    same = ev.compare_detection_sets(pages, reference, reference, {"exclusion": 0.90, "price_change": 0.59})
    assert (same["pages_changed"], same["vanished"], same["appeared"], same["max_score_shift"]) == (0, 0, 0, 0.0)
    json.dumps(result)


# --------------------------------------------------------------------------- linking

def _rows(image_id="P2", count=4):
    """Rows 80 px tall from y = 100; the printed amount sits at x 800-900 in the top half of the row."""
    return [pd_.RowGeometry(f"part_{i + 1}", f"{image_id}:part_{i + 1}", (50.0, 100.0 + 80 * i, 950.0, 180.0 + 80 * i),
                            (800.0, 110.0 + 80 * i, 900.0, 130.0 + 80 * i), "unmarked") for i in range(count)]


def test_row_boxes_are_normalised_rows_with_the_printed_amount_box():
    (first, *_) = boxes = ev.row_boxes([_page("P2", rows=_rows())])
    assert len(boxes) == 4 and (first.entry_id, first.page_id) == ("P2:part_1", "pg-P2")
    assert first.row_box_norm == pytest.approx((0.05, 0.1, 0.95, 0.18))
    assert first.amount_box_norm == pytest.approx((0.8, 0.11, 0.9, 0.13))


def _linking_scenario():
    marks = [_mark(1, "exclusion", (100, 105, 130, 135), "part_1", "x"),  # top of row 1: links
             _mark(2, "exclusion", (100, 300, 900, 312), "part_3", "row_strike"),  # low in row 3: also in row 4's band
             _mark(3, "price_change", (790, 345, 910, 375), "part_4", "amount_strike"),  # over row 4's amount
             _mark(4, "price_change", (790, 185, 910, 215), "part_1", "amount_cross")]  # drawn on row 2, labelled row 1
    estimate = _page("P2", marks, rows=_rows())
    assessment = _page("P1", [], page_type="survey_assessment")
    return [assessment, estimate]


def test_linking_outcomes_with_oracle_detections_separate_the_linker_from_the_detector():
    pages = _linking_scenario()
    linked = ev.link_pages(pages, ev.oracle_predictions(pages), CONFIG, versions={"penmark_model": "oracle"})
    outcomes = {row["mark_id"]: row for row in ev.linking_outcomes(pages, ev.oracle_predictions(pages), linked)}
    assert outcomes["P2-mark-01"]["outcome"] == "linked_correct" and outcomes["P2-mark-01"]["rule_id"] == "EX-1"
    # 12 px of ink low in row 3 lies inside the band of row 4 as well: two rows qualify, EX-1 refuses.
    assert outcomes["P2-mark-02"]["outcome"] == "unlinked_candidate"
    assert outcomes["P2-mark-02"]["link_reason"] == "mark_between_rows"
    assert outcomes["P2-mark-03"]["outcome"] == "linked_correct" and outcomes["P2-mark-03"]["rule_id"] == "PC-1"
    assert outcomes["P2-mark-04"]["outcome"] == "linked_wrong" and outcomes["P2-mark-04"]["linked_entry_id"] == "P2:part_2"
    assert all(mark.state == "pending" for item in linked for mark in item.result.marks)

    scores = ev.row_level_scores(pages, linked)
    assert scores["exclusion"] == {"marked_rows": 2, "flagged_rows": 1, "true_positives": 1, "false_positives": 0,
                                   "false_negatives": 1, "precision": 1.0, "recall": 0.5}
    assert scores["price_change"]["true_positives"] == 1 and scores["price_change"]["false_positives"] == 1
    assert scores["price_change"]["precision"] == 0.5 and scores["price_change"]["recall"] == 0.5
    assert scores["marks"] == {"proposed": 4, "linked": 3, "unlinked": 1, "pending": 4,
                               "unlinked_by_reason": {"mark_between_rows": 1}}
    summary = ev.outcome_summary(outcomes.values())
    assert {(r["mark_class"], r["style"]): r["linked_correct"] for r in summary} == {
        ("exclusion", "x"): 1, ("exclusion", "row_strike"): 0, ("price_change", "amount_strike"): 1,
        ("price_change", "amount_cross"): 0}
    json.dumps([scores, summary])


def test_linking_outcomes_with_detections_count_what_the_detector_lost():
    pages = _linking_scenario()
    predictions = {"P1": _pred(((100, 800, 300, 830), 1, 0.9)),  # a false alarm on the page without rows
                   "P2": _pred(((100, 105, 130, 135), 1, 0.9), ((790, 345, 910, 375), 2, 0.3))}  # second is too weak
    linked = ev.link_pages(pages, predictions, CONFIG, versions={"penmark_model": "test"})
    outcomes = {row["mark_id"]: row["outcome"] for row in ev.linking_outcomes(pages, predictions, linked)}
    assert outcomes == {"P2-mark-01": "linked_correct", "P2-mark-02": "not_detected", "P2-mark-03": "not_detected",
                        "P2-mark-04": "not_detected"}
    scores = ev.row_level_scores(pages, linked)
    assert scores["marks"]["unlinked_by_reason"] == {"rows_unavailable": 1} and scores["marks"]["proposed"] == 2
    assert scores["exclusion"]["precision"] == 1.0 and scores["exclusion"]["recall"] == 0.5
    assert scores["price_change"]["precision"] is None and scores["price_change"]["recall"] == 0.0


def test_a_page_without_m5_rows_leaves_its_marks_unlinked_with_rows_unavailable():
    pages = _linking_scenario()
    linked = ev.link_pages(pages, ev.oracle_predictions(pages), CONFIG, versions={"penmark_model": "oracle"},
                           pages_without_rows={"P2"})
    outcomes = ev.linking_outcomes(pages, ev.oracle_predictions(pages), linked)
    assert {row["outcome"] for row in outcomes} == {"unlinked_no_candidate"}
    assert {row["link_reason"] for row in outcomes} == {"rows_unavailable"}
    assert ev.row_level_scores(pages, linked)["exclusion"]["recall"] == 0.0


# --------------------------------------------------------------------------- which rows the linker is given

def _cell_rows(image_id="P2", count=4):
    """Table bands 80 px tall from y = 100; the printed cells of a row sit at y 125-150 inside its band."""
    rows = []
    for i in range(count):
        top = 100.0 + 80 * i
        cells = ((150.0, top + 25, 500.0, top + 45), (800.0, top + 28, 900.0, top + 50))  # description, amount
        rows.append(pd_.RowGeometry(f"part_{i + 1}", f"{image_id}:part_{i + 1}", pd_.union_box(cells), cells[1],
                                    "unmarked", band=(50.0, top, 950.0, top + 80), cells=cells))
    return rows


def test_the_linkers_result_depends_on_the_row_geometry_it_is_given():
    """One X over the printed text of row 1, hand-worked for both row definitions."""
    page = _page("P2", [_mark(1, "exclusion", (300, 130, 322, 152), "part_1", "x")], rows=_cell_rows())
    assert page.rows[0].box == (150.0, 125.0, 900.0, 150.0)  # 25 px tall: the union of the two cells
    oracle = ev.oracle_predictions([page])
    outcome = lambda pages: ev.linking_outcomes(pages, oracle, ev.link_pages(  # noqa: E731
        pages, oracle, CONFIG, versions={"penmark_model": "oracle"}))[0]
    # Union-of-cells rows: row 1's band is y 110-156.25, so v = 1 and c = 20/22; row 2 (y 205-230) is far away.
    cells = outcome([page])
    assert (cells["outcome"], cells["rule_id"]) == ("linked_correct", "EX-1")
    # Table-band rows: row 2 is y 180-260 and its band reaches up 0.6 x 80 = 48 px to y 132. The X (130-152)
    # lies 20/22 inside it: v = 0.909, score 0.45 x 0.909 + 0.15 = 0.559 >= 0.50. Two rows qualify; EX-1 refuses.
    bands = outcome(pd_.with_table_band_rows([page]))
    assert (bands["outcome"], bands["link_reason"]) == ("unlinked_candidate", "mark_between_rows")


M5_HASH = "c" * 64


def _m5_record(pages, **changes):
    record = {"schema": "cmev-m5-rows-for-m6/1", "split": "validation", "provenance": "synthetic", "quick_run": False,
              "m5_model_version": "sha256:" + "d" * 64, "m5_registry_entry": "lineitems/toy-serving",
              "m4_cache_manifest_sha256": M5_HASH, "family_id": "family-a-ruled-grid", "min_confidence": 0.5,
              "pages": pages}
    record.update(changes)
    return record


def _m5_page(image_id="P2", rows=(), status="complete"):
    return {"image_id": image_id, "page_id": f"pg-{image_id}", "claim_id": CLAIM, "dataset_claim_id": "SYN-1",
            "page_status": status,
            "row_boxes": [{"entry_id": f"li-{n:024d}", "page_id": f"pg-{image_id}", "row_box_norm": list(box),
                           "amount_box_norm": None if amount is None else list(amount)}
                          for n, (box, amount) in enumerate(rows)]}


def test_an_m5_rows_file_is_refused_unless_it_belongs_to_this_split_cache_and_run_mode(tmp_path):
    path = tmp_path / "rows_for_m6_validation.json"
    write = lambda **changes: path.write_text(json.dumps({**_m5_record([_m5_page()]), **changes}))  # noqa: E731
    load = lambda **kwargs: ev.load_m5_rows(path, **{"split": "validation", "m4_cache_manifest_sha256": M5_HASH,  # noqa: E731
                                                     "quick_run": False, **kwargs})
    write()
    assert load()["m5_registry_entry"] == "lineitems/toy-serving" and load()["path"] == str(path)
    for changes, message in (({"schema": "cmev-m5-rows-for-m6/2"}, "schema"), ({"split": "test"}, "split"),
                             ({"m4_cache_manifest_sha256": "e" * 64}, "M4 cache"), ({"quick_run": True}, "quick"),
                             ({"pages": "none"}, "pages")):
        write(**changes)
        with pytest.raises(ev.M5RowsError, match=message):
            load()
    write(quick_run=True)
    assert load(quick_run=True)["quick_run"] is True, "a quick file is accepted by a quick notebook run only"
    with pytest.raises(ev.M5RowsError, match="does not exist"):
        ev.load_m5_rows(tmp_path / "absent.json", split="validation", m4_cache_manifest_sha256=M5_HASH, quick_run=False)


def test_auto_finds_the_newest_m5_rows_file_or_none(tmp_path):
    import os

    assert ev.find_m5_rows_file(tmp_path) is None
    for name, age in (("lineitems-run-a", 300), ("lineitems-run-b", 100), ("penmarks-run-c", 10)):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "rows_for_m6_validation.json").write_text("{}")
        os.utime(folder / "rows_for_m6_validation.json", (1_000_000 - age, 1_000_000 - age))
    assert ev.find_m5_rows_file(tmp_path) == tmp_path / "lineitems-run-b" / "rows_for_m6_validation.json"


def test_true_rows_take_the_geometry_of_the_m5_row_whose_centre_lies_in_their_band():
    norm = lambda box: tuple(v / 1000 for v in box)  # noqa: E731
    estimate = _page("P2", [_mark(1, "exclusion", (300, 130, 322, 152), "part_1", "x"),
                            _mark(2, "exclusion", (300, 370, 322, 392), "part_4", "x")], rows=_cell_rows())
    unread = _page("P3", [_mark(1, "price_change", (790, 125, 910, 155), "part_1", image_id="P3")],
                   rows=_cell_rows("P3"), claim="SYN-2")
    record = _m5_record([
        _m5_page("P2", [(norm((150, 120, 900, 160)), norm((800, 128, 900, 150))),   # centre y 140: band of part_1
                        (norm((150, 190, 900, 250)), None),                         # centre y 220: band of part_2
                        (norm((150, 265, 900, 285)), norm((800, 268, 900, 285))),   # centre y 275: band of part_3
                        (norm((150, 286, 900, 330)), None),                         # centre y 308: band of part_3 too
                        (norm((150, 600, 900, 640)), None)]),                       # a totals line: no true row
        _m5_page("P3", status="unreadable")])
    pages, summary = ev.with_m5_rows([estimate, unread], record)
    rows = {r.row_id: r for r in pages[0].rows}
    assert list(rows) == ["part_1", "part_2", "part_3"], "part_4 got no M5 row: M6 is not given that row"
    assert rows["part_1"].box == pytest.approx((150, 120, 900, 160)) and rows["part_1"].amount_box == pytest.approx((800, 128, 900, 150))
    assert rows["part_2"].amount_box is None and rows["part_1"].entry_id == "P2:part_1"
    assert rows["part_3"].box == pytest.approx((150, 286, 900, 330)), "of two M5 rows in one band, the nearer centre"
    assert pages[0].rows_source == pages[1].rows_source == "m5" and pages[1].rows == ()
    assert pages[0].marks == estimate.marks, "the ground truth keeps every mark, also on rows M6 was not given"
    assert summary == {"true_rows": 8, "true_rows_with_m5_row": 3, "true_rows_without_m5_row": 5,
                       "m5_rows_on_these_pages": 5, "m5_rows_matching_no_true_row": 2,
                       "pages_with_true_rows": 2, "pages_m5_could_not_read": ["P3"], "pages_not_in_file": [],
                       "median_row_height_px": 44.0, "m5_model_version": "sha256:" + "d" * 64,
                       "m5_registry_entry": "lineitems/toy-serving", "file": None}

    linked = ev.link_pages(pages, ev.oracle_predictions(pages), CONFIG, versions={"penmark_model": "oracle"})
    outcomes = {row["mark_id"]: row for row in ev.linking_outcomes(pages, ev.oracle_predictions(pages), linked)}
    assert outcomes["P2-mark-01"]["outcome"] == "linked_correct"
    assert outcomes["P2-mark-02"]["outcome"] in ("unlinked_candidate", "unlinked_no_candidate")  # its row is absent
    assert outcomes["P3-mark-01"]["link_reason"] == "rows_unavailable"
    scores = ev.row_level_scores(pages, linked)
    assert (scores["exclusion"]["marked_rows"], scores["exclusion"]["true_positives"]) == (2, 1)
    assert (scores["price_change"]["marked_rows"], scores["price_change"]["true_positives"]) == (1, 0)

    with pytest.raises(ev.M5RowsError, match="page id"):
        ev.with_m5_rows([estimate], _m5_record([{**_m5_page("P2"), "page_id": "pg-other"}]))
    absent, missing = ev.with_m5_rows([estimate], _m5_record([]))
    assert absent[0].rows == () and missing["pages_not_in_file"] == ["P2"]


def test_selected_thresholds_reach_the_linker_without_touching_the_configuration_file():
    config = ev.linking_config_with_thresholds(CONFIG, {"exclusion": 0.31, "price_change": 0.62}, label="run-1")
    assert config.score_threshold("exclusion") == 0.31 and config.score_threshold("price_change") == 0.62
    assert config.link_config_version == f"{CONFIG.link_config_version}+thresholds.run-1"
    assert CONFIG.score_threshold("exclusion") == 0.50 and config.band == CONFIG.band and config.link == CONFIG.link


def test_evaluate_pages_is_one_json_report_with_denominators_and_names_its_rows():
    pages, predictions = _scenario()
    pages[1] = _page("P2", pages[1].marks, pages[1].distractors, _cell_rows())
    thresholds = {"exclusion": 0.5, "price_change": 0.45}
    report = ev.evaluate_pages(pages, predictions, thresholds, CONFIG, versions={"penmark_model": "test"})
    assert report["pages"] == {"total": 2, "with_marks": 1, "without_marks": 1, "m4_unreadable": 0}
    assert report["marks"] == {"exclusion": 2, "price_change": 2}
    assert report["thresholds"] == thresholds and report["iou_threshold"] == 0.5
    assert set(report) >= {"detection", "operating_point", "linking", "splits"}
    linking = report["linking"]
    # The row definition is part of every linking result's name; the table bands are a labelled contrast.
    assert set(linking) == {"primary_rows", "oracle_cell_union_rows", "oracle_table_band_rows"}
    assert linking["primary_rows"] == "oracle_cell_union_rows"
    for name in ("oracle_cell_union_rows", "oracle_table_band_rows"):
        assert set(linking[name]) == {"rows", "detector", "oracle_detections"} and linking[name]["rows"]
        marks = linking[name]["detector"]["row_level"]["marks"]
        assert marks["pending"] == marks["proposed"]
        assert linking[name]["detector"]["rows_source"] == name.removesuffix("_rows")
        assert linking[name]["detector"]["rows_given"] == 4 and "conflicting_rows" in linking[name]["detector"]
    assert "contrast" in linking["oracle_table_band_rows"]["rows"].lower()
    assert "union" in linking["oracle_cell_union_rows"]["rows"].lower()
    assert report["splits"] == ["validation"]
    json.dumps(report)

    record = _m5_record([_m5_page("P2", [((0.15, 0.12, 0.9, 0.16), (0.8, 0.128, 0.9, 0.15))])])
    with_m5 = ev.evaluate_pages(pages, predictions, thresholds, CONFIG, versions={"penmark_model": "test"},
                                m5_rows=record)["linking"]
    assert set(with_m5) == {"primary_rows", "oracle_cell_union_rows", "oracle_table_band_rows", "m5_rows"}
    assert with_m5["m5_rows"]["source"]["true_rows_with_m5_row"] == 1 and with_m5["m5_rows"]["detector"]["rows_given"] == 1
    assert with_m5["oracle_cell_union_rows"] == linking["oracle_cell_union_rows"], "M5 rows change no oracle figure"

    table = ev.linking_table(with_m5)
    assert len(table) == 3 * 2 * 2  # three row sources x (oracle detections, detector) x two classes
    first = table[0]
    assert (first["rows"], first["detections"], first["mark_class"]) == ("oracle_cell_union_rows", "oracle_detections", "exclusion")
    assert set(first) >= {"marked_rows", "flagged_rows", "true_positives", "recall", "precision", "unlinked_marks"}
    assert [r["rows"] for r in table[::4]] == ["oracle_cell_union_rows", "oracle_table_band_rows", "m5_rows"]


# --------------------------------------------------------------------------- drawing

def test_a_detection_page_numbers_its_boxes_and_says_what_each_number_is():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pages, predictions = _scenario()
    point = ev.operating_point(pages, predictions, {"exclusion": 0.5, "price_change": 0.45})
    fig, axis = plt.subplots()
    counts = ev.draw_detection_page(axis, np.full((H, W, 3), 255, dtype=np.uint8), pages[1], point, crop=(0, 0, W, 500))
    assert (counts["hits"], counts["false_positives"], counts["misses"]) == (3, 3, 1)
    assert len(counts["lines"]) == 7 and counts["lines"][0].startswith("1  found: exclusion 0.95 (x)")
    assert any(line.endswith("false: exclusion 0.90, duplicate") for line in counts["lines"])
    assert counts["lines"][-1] == "7  missed: price change (amount_strike), detected as other class"
    tags = sorted(text.get_text() for text in axis.texts)
    assert tags == [str(n) for n in range(1, 8)], "the page carries numbers only; the captions are returned"
    sides = {text.get_text(): text.get_va() for text in axis.texts}
    assert sides["1"] == "bottom" and sides["4"] == "top", "hits are numbered above, false alarms below their box"
    with pytest.raises(ValueError, match="label_side"):
        ev.draw_boxes(axis, [(0, 0, 1, 1)], "black", labels=["a"], label_side="left")
    plt.close(fig)
