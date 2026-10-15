"""Detection and linking evaluation for the M6 pen-mark detector (numpy only, no model).

Detection is scored the usual way: a detection is a hit when it overlaps an unclaimed
ground-truth mark of its class at IoU 0.5 or more; average precision is the COCO 101-point
area under the precision-recall curve. Linking is scored with the project's own
``link_detections``, unchanged. What the linker returns depends strongly on the row boxes it
is given, so every linking result is named after its rows:

- ``oracle_cell_union``: the union of each row's printed field boxes, from ground truth.
  This is ``row_box(r)`` of module-06 step 4 and what M5 stores as ``row_box_norm``;
- ``oracle_table_band``: the dataset's full table-row bands, a labelled contrast;
- ``m5``: the rows a trained M5 candidate produced (``load_m5_rows``, ``with_m5_rows``).

The headline figures of the M6 specification, per-row detection and linking precision and
recall, are derived from the linked marks and do not depend on an IoU threshold.

Everything here counts; nothing decides. An undefined ratio is ``None``, never 0 or 1, and
every figure travels with its numerator and denominator.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from claim_cmev.contracts.common import Provenance, make_job_key
from claim_cmev.documents.pen_marks import Detection, LinkingConfig, LinkingResult, RowBox, link_detections
from claim_cmev.documents.pen_marks.detector import detections_from_arrays
from pipelines.documents.penmarks_data import (
    CLASS_TO_LABEL, HELD_OUT_SPLIT, PALETTE, ROWS_CELL_UNION, ROWS_M5, ROWS_TABLE_BAND, TARGET_CLASSES, PenMarkPage,
    with_table_band_rows,
)

IOU_THRESHOLD = 0.5
COCO_IOU_THRESHOLDS = tuple(round(0.5 + 0.05 * i, 2) for i in range(10))
THRESHOLD_GRID = np.round(np.arange(0.05, 0.96, 0.01), 2)
OUTCOMES = ("linked_correct", "linked_wrong", "unlinked_candidate", "unlinked_no_candidate", "not_detected")
FALSE_POSITIVE_KINDS = ("on_distractor", "duplicate", "wrong_class", "part_of_found_mark", "poorly_localised",
                        "background")
PART_OF_MARK_SHARE = 0.8
"""A false alarm is *part of a found mark* when at least this share of its box lies inside it."""
M5_ROWS_SCHEMA = "cmev-m5-rows-for-m6/1"
ROW_SOURCES = {
    ROWS_CELL_UNION: "oracle rows: the union of each row's printed field boxes (module-06 step 4, the box M5 "
                     "stores as row_box_norm), built from ground truth and standing in for M5 output",
    ROWS_TABLE_BAND: "contrast only: the dataset's full table-row bands, which touch each other; M5 does not "
                     "supply such rows",
    ROWS_M5: "rows produced by a trained M5 candidate through its serving adapter; a true row M5 did not "
             "produce is a row M6 is not given",
}
NOTEBOOK_PROVENANCE = Provenance(source_kind="synthetic", runtime_profile="full",
                                 producer_service="m6-penmarks-notebook",
                                 source_dataset_id="synthetic_motor_claims_500_w_annotation")


@dataclass(frozen=True)
class PagePrediction:
    """A detector's output for one page: pixel boxes ``[N, 4]``, class indexes and scores."""

    boxes: np.ndarray
    labels: np.ndarray
    scores: np.ndarray

    def of(self, label: int, minimum_score: float = 0.0) -> np.ndarray:
        """Indexes of the detections of one class at or above a score, highest score first."""
        chosen = np.flatnonzero((self.labels == label) & (self.scores >= minimum_score))
        return chosen[np.argsort(-self.scores[chosen], kind="stable")]


EMPTY = PagePrediction(np.zeros((0, 4)), np.zeros(0, dtype=int), np.zeros(0))


def _ratio(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else float(numerator) / float(denominator)


# --------------------------------------------------------------------------- matching

def iou_matrix(a: Any, b: Any) -> np.ndarray:
    """Intersection over union of every box of ``a`` with every box of ``b`` (x0, y0, x1, y1)."""
    a, b = np.asarray(a, dtype=float).reshape(-1, 4), np.asarray(b, dtype=float).reshape(-1, 4)
    width = np.clip(np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0]), 0, None)
    height = np.clip(np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1]), 0, None)
    inter = width * height
    area = lambda x: (x[:, 2] - x[:, 0]) * (x[:, 3] - x[:, 1])  # noqa: E731
    union = area(a)[:, None] + area(b)[None, :] - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def match_page(boxes: Any, scores: Any, truth: Any, iou_threshold: float) -> tuple[np.ndarray, np.ndarray]:
    """Greedy matching of one class on one page.

    Detections are taken in descending score. Each takes the still unclaimed mark it
    overlaps most, if that IoU reaches ``iou_threshold``. Returns, per detection, the index
    of its mark (-1 for a false alarm) and its best IoU with any mark. Because a detection
    never loses its mark to a lower score, removing low-scoring detections changes nothing
    for the others: one pass serves every score threshold.
    """
    overlaps = iou_matrix(boxes, truth)
    matched = np.full(len(overlaps), -1, dtype=int)
    best = overlaps.max(axis=1) if overlaps.shape[1] else np.zeros(len(overlaps))
    taken = np.zeros(overlaps.shape[1], dtype=bool)
    for index in np.argsort(-np.asarray(scores, dtype=float), kind="stable"):
        free = np.where(taken, -1.0, overlaps[index])
        if free.size and free.max() >= iou_threshold:
            matched[index] = int(free.argmax())
            taken[matched[index]] = True
    return matched, best


def class_detections(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction], mark_class: str,
                     iou_threshold: float = IOU_THRESHOLD) -> dict[str, Any]:
    """Every detection of one class over ``pages``, highest score first, flagged hit or false alarm."""
    label = CLASS_TO_LABEL[mark_class]
    scores, hits, ious, total = [], [], [], 0
    for page in pages:
        prediction = predictions.get(page.image_id, EMPTY)
        chosen, truth = prediction.of(label), page.boxes[page.labels == label]
        matched, best = match_page(prediction.boxes[chosen], prediction.scores[chosen], truth, iou_threshold)
        scores.extend(prediction.scores[chosen])
        hits.extend(matched >= 0)
        ious.extend(best)
        total += len(truth)
    order = np.argsort(-np.asarray(scores, dtype=float), kind="stable")
    return {"scores": np.asarray(scores, dtype=float)[order], "hits": np.asarray(hits, dtype=bool)[order],
            "ious": np.asarray(ious, dtype=float)[order], "marks": total}


def precision_recall(scores: np.ndarray, hits: np.ndarray, marks: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Precision and recall after each detection in descending score order, with those scores."""
    order = np.argsort(-np.asarray(scores, dtype=float), kind="stable")
    hits = np.asarray(hits, dtype=bool)[order]
    true_positives = np.cumsum(hits)
    precision = true_positives / np.arange(1, len(hits) + 1)
    recall = true_positives / marks if marks else np.zeros(len(hits))
    return precision, recall, np.asarray(scores, dtype=float)[order]


def average_precision(precision: np.ndarray, recall: np.ndarray, marks: int) -> float | None:
    """COCO's 101-point interpolated average precision; ``None`` when there is no mark to find.

    At each recall level 0.00, 0.01, ..., 1.00 take the best precision reached at that
    recall or beyond, then average the 101 values.
    """
    if marks == 0:
        return None
    if not len(precision):
        return 0.0
    envelope = np.maximum.accumulate(precision[::-1])[::-1]
    levels = np.linspace(0.0, 1.0, 101)
    positions = np.searchsorted(recall, levels - 1e-12, side="left")
    return float(np.where(positions < len(envelope), envelope[np.minimum(positions, len(envelope) - 1)], 0.0).mean())


def detection_report(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction]) -> dict[str, Any]:
    """AP at IoU 0.5 and averaged over IoU 0.50-0.95 per class, with counts and the IoU-0.5 curve."""
    report: dict[str, Any] = {}
    for mark_class in TARGET_CLASSES:
        values, curve = [], {}
        for threshold in COCO_IOU_THRESHOLDS:
            found = class_detections(pages, predictions, mark_class, threshold)
            precision, recall, scores = precision_recall(found["scores"], found["hits"], found["marks"])
            values.append(average_precision(precision, recall, found["marks"]))
            if threshold == IOU_THRESHOLD:
                curve = {"precision": precision.tolist(), "recall": recall.tolist(), "score": scores.tolist()}
        report[mark_class] = {"marks": found["marks"], "detections": len(found["scores"]), "ap50": values[0],
                              "ap50_95": None if values[0] is None else float(np.mean(values)), "curve": curve}
    for key in ("ap50", "ap50_95"):
        defined = [report[c][key] for c in TARGET_CLASSES if report[c][key] is not None]
        report[f"mean_{key}"] = float(np.mean(defined)) if defined else None
    return report


def threshold_table(scores: np.ndarray, hits: np.ndarray, marks: int,
                    grid: np.ndarray = THRESHOLD_GRID) -> dict[str, np.ndarray]:
    """Counts, precision, recall and F1 when only detections at or above each threshold are kept."""
    scores, hits = np.asarray(scores, dtype=float), np.asarray(hits, dtype=bool)
    kept = scores[None, :] >= np.asarray(grid, dtype=float)[:, None]
    true_positives = (kept & hits[None, :]).sum(axis=1)
    false_positives = (kept & ~hits[None, :]).sum(axis=1)
    detections = true_positives + false_positives
    precision = np.divide(true_positives, detections, out=np.full(len(grid), np.nan), where=detections > 0)
    recall = true_positives / marks if marks else np.full(len(grid), np.nan)
    denominator = 2 * true_positives + false_positives + (marks - true_positives)
    f1 = np.divide(2 * true_positives, denominator, out=np.zeros(len(grid)), where=denominator > 0)
    return {"threshold": np.asarray(grid, dtype=float), "true_positives": true_positives,
            "false_positives": false_positives, "false_negatives": marks - true_positives, "precision": precision,
            "recall": recall, "f1": f1}


def best_f1_run(table: Mapping[str, np.ndarray], tolerance: float = 0.005) -> dict[str, Any] | None:
    """The run of thresholds the selection rule chooses from, with its bounds; ``None`` without a hit.

    Thresholds whose F1 is within ``tolerance`` of the maximum count as equally good. The
    longest unbroken run of them is taken (the lowest of equally long runs). ``points`` is
    its length in grid points: a run of one point means the rule had nothing to choose
    between, and its result sits on a spike of the F1 curve, not on a plateau.
    """
    f1, grid = np.asarray(table["f1"], dtype=float), np.asarray(table["threshold"], dtype=float)
    if not len(f1) or f1.max() <= 0:
        return None
    good = f1 >= f1.max() - tolerance
    runs, start = [], None
    for index, flag in enumerate(np.append(good, False)):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index - 1))
            start = None
    first, last = max(runs, key=lambda run: (run[1] - run[0], -run[0]))
    return {"low": float(grid[first]), "high": float(grid[last]), "points": int(last - first + 1),
            "selected": float(grid[first + (last - first) // 2]), "best_f1": float(f1.max()),
            "equally_good_thresholds": int(good.sum())}


def select_threshold(table: Mapping[str, np.ndarray], tolerance: float = 0.005, fallback: float = 0.5) -> float:
    """The proposed selection rule: the middle of the widest run of thresholds with the best F1.

    Thresholds whose F1 is within ``tolerance`` of the maximum are equally good on this
    data. The longest unbroken run of them is taken and its middle threshold returned (the
    lower of two middles), so the choice does not sit at the edge of a plateau. With no hit
    at all there is nothing to select from and ``fallback`` is returned. ``best_f1_run``
    reports how wide that run was; F1 values the rule compares are measured on the same
    pages the threshold is later reported on, so the result is optimistic.
    """
    run = best_f1_run(table, tolerance)
    return float(fallback) if run is None else run["selected"]


def threshold_counts(table: Mapping[str, np.ndarray], thresholds: Iterable[float]) -> list[dict[str, Any]]:
    """Hits, false alarms and misses of ``table`` at each of ``thresholds`` (which must be grid points)."""
    grid, rows = np.asarray(table["threshold"], dtype=float), []
    for value in thresholds:
        index = int(np.abs(grid - value).argmin())
        if abs(grid[index] - value) > 1e-9:
            raise ValueError(f"threshold {value} is not on the grid {grid[0]:.2f} to {grid[-1]:.2f}")
        tp, fp, fn = (int(table[key][index]) for key in ("true_positives", "false_positives", "false_negatives"))
        rows.append({"threshold": float(grid[index]), "true_positives": tp, "false_positives": fp,
                     "false_negatives": fn, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
                     "f1": float(table["f1"][index])})
    return rows


def claim_halves(pages: Sequence[PenMarkPage], seed: int) -> tuple[list[PenMarkPage], list[PenMarkPage]]:
    """Split ``pages`` into two halves of whole claims, the same way for the same ``seed``."""
    if any(page.split == HELD_OUT_SPLIT for page in pages):
        raise ValueError("the held-out test split takes no part in checking a threshold")
    claims = sorted({page.dataset_claim_id for page in pages})
    order = np.random.default_rng(seed).permutation(len(claims))
    first = {claims[index] for index in order[:len(claims) // 2]}
    return ([p for p in pages if p.dataset_claim_id in first], [p for p in pages if p.dataset_claim_id not in first])


def threshold_transfer(select_pages: Sequence[PenMarkPage], score_pages: Sequence[PenMarkPage],
                       predictions: Mapping[str, PagePrediction], tolerance: float = 0.005,
                       fallback: float = 0.5) -> dict[str, dict[str, Any]]:
    """Select each class's threshold on ``select_pages`` and count what it keeps on ``score_pages``."""
    result = {}
    for mark_class in TARGET_CLASSES:
        chosen = class_detections(select_pages, predictions, mark_class)
        table = threshold_table(chosen["scores"], chosen["hits"], chosen["marks"])
        run = best_f1_run(table, tolerance)
        threshold = float(fallback) if run is None else run["selected"]
        scored = class_detections(score_pages, predictions, mark_class)
        kept = scored["scores"] >= threshold
        tp, fp = int(scored["hits"][kept].sum()), int((~scored["hits"][kept]).sum())
        result[mark_class] = {"threshold": threshold, "plateau_low": None if run is None else run["low"],
                              "plateau_high": None if run is None else run["high"], "marks": scored["marks"],
                              "true_positives": tp, "false_positives": fp, "recall": _ratio(tp, scored["marks"]),
                              "precision": _ratio(tp, tp + fp)}
    return result


def half_split_check(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction],
                     seeds: Sequence[int] = (20260922, 20260923)) -> list[dict[str, Any]]:
    """Is the selected threshold robust to the pages it was selected on?

    For each seed the claims are split in two; the rule selects a threshold on one half and
    the other half is scored with it, in both directions. Unlike the main table, no page
    then both chooses a threshold and reports on it. The halves are small; read the counts.
    """
    rows = []
    for seed in seeds:
        first, second = claim_halves(pages, seed)
        for select_pages, score_pages in ((first, second), (second, first)):
            for mark_class, result in threshold_transfer(select_pages, score_pages, predictions).items():
                rows.append({"seed": int(seed), "mark_class": mark_class, "selected_on_pages": len(select_pages),
                             "scored_on_pages": len(score_pages), **result})
    return rows


def compare_detection_sets(pages: Sequence[PenMarkPage], reference: Mapping[str, PagePrediction],
                           other: Mapping[str, PagePrediction], thresholds: Mapping[str, float],
                           iou_threshold: float = IOU_THRESHOLD) -> dict[str, Any]:
    """How two detectors' kept boxes differ, page by page, as sets.

    Per page and class the boxes at or above the class threshold are paired greedily by
    IoU (at least ``iou_threshold``). A reference box without a partner *vanished*, a box
    of ``other`` without one *appeared*; a page with either counts as changed.
    """
    kept = {"reference": 0, "other": 0}
    matched = vanished = appeared = 0
    score_shift = box_shift = 0.0
    changed = []
    for page in pages:
        differs = False
        for mark_class in TARGET_CLASSES:
            label = CLASS_TO_LABEL[mark_class]
            a, b = reference.get(page.image_id, EMPTY), other.get(page.image_id, EMPTY)
            ia, ib = a.of(label, thresholds[mark_class]), b.of(label, thresholds[mark_class])
            kept["reference"] += len(ia)
            kept["other"] += len(ib)
            partner, _ = match_page(b.boxes[ib], b.scores[ib], a.boxes[ia], iou_threshold)
            for position, target in enumerate(partner):
                if target >= 0:
                    matched += 1
                    score_shift = max(score_shift, abs(float(b.scores[ib][position] - a.scores[ia][target])))
                    box_shift = max(box_shift, float(np.abs(b.boxes[ib][position] - a.boxes[ia][target]).max()))
            page_matched = int((partner >= 0).sum())
            vanished += len(ia) - page_matched
            appeared += len(ib) - page_matched
            differs |= len(ia) != page_matched or len(ib) != page_matched
        if differs:
            changed.append(page.image_id)
    return {"pages": len(pages), "pages_changed": len(changed), "changed_pages": changed,
            "kept_in_reference": kept["reference"], "kept_in_other": kept["other"], "matched": matched,
            "vanished": vanished, "appeared": appeared, "max_score_shift": score_shift,
            "max_box_shift_px": box_shift, "thresholds": {c: float(thresholds[c]) for c in TARGET_CLASSES},
            "iou_threshold": iou_threshold}


def selection_score(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction],
                    grid: np.ndarray = THRESHOLD_GRID) -> float:
    """Model-selection metric: the best F1 at IoU 0.5 of each class that has marks, averaged."""
    best = []
    for mark_class in TARGET_CLASSES:
        found = class_detections(pages, predictions, mark_class)
        if found["marks"]:
            best.append(float(threshold_table(found["scores"], found["hits"], found["marks"], grid)["f1"].max()))
    return float(np.mean(best)) if best else 0.0


# --------------------------------------------------------------------------- operating point

def _overlap_share(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    """Share of ``box`` covered by each of ``others``."""
    if not len(others):
        return np.zeros(0)
    width = np.clip(np.minimum(box[2], others[:, 2]) - np.maximum(box[0], others[:, 0]), 0, None)
    height = np.clip(np.minimum(box[3], others[:, 3]) - np.maximum(box[1], others[:, 1]), 0, None)
    return width * height / max(float((box[2] - box[0]) * (box[3] - box[1])), 1e-9)


def operating_point(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction],
                    thresholds: Mapping[str, float], iou_threshold: float = IOU_THRESHOLD) -> dict[str, Any]:
    """Hits, false alarms and misses when each class keeps only detections at its threshold.

    Every false alarm gets one kind, tested in this order: ``duplicate`` (a second box on a
    mark that is already found), ``wrong_class`` (on a mark of the other class),
    ``part_of_found_mark`` (at least 80 % of it lies inside a mark of its class that another
    kept detection found: the cross or the remark alone of a cross-plus-remark mark),
    ``poorly_localised`` (IoU 0.1 or more with a mark of its class, below the IoU
    threshold), ``on_distractor`` (at least 30 % of it lies on a tick or a review note),
    else ``background``. Every miss gets one kind as well: ``detected_as_other_class``,
    ``poorly_localised``, ``below_threshold`` (a matching box exists under the score
    threshold) or ``not_detected``.
    """
    classes = {c: {"marks": 0, "true_positives": 0, "false_positives": 0, "false_negatives": 0} for c in TARGET_CLASSES}
    false_positives, misses, hits = [], [], []
    matched_ious: dict[str, list[float]] = {c: [] for c in TARGET_CLASSES}
    style_counts: dict[tuple[str, str], list[int]] = {}
    writer_counts: dict[str, list[int]] = {}
    empty_pages = empty_pages_hit = 0
    for page in pages:
        prediction = predictions.get(page.image_id, EMPTY)
        page_false = 0
        distractors = np.array([d.box for d in page.distractors], dtype=float).reshape(-1, 4)
        for mark_class in TARGET_CLASSES:
            label, other = CLASS_TO_LABEL[mark_class], [c for c in TARGET_CLASSES if c != mark_class][0]
            marks = [m for m in page.marks if m.mark_class == mark_class]
            truth = np.array([m.box for m in marks], dtype=float).reshape(-1, 4)
            other_truth = page.boxes[page.labels == CLASS_TO_LABEL[other]]
            chosen = prediction.of(label, thresholds[mark_class])
            boxes, scores = prediction.boxes[chosen], prediction.scores[chosen]
            matched, best = match_page(boxes, scores, truth, iou_threshold)
            overlaps = iou_matrix(boxes, truth)
            found_marks = set(matched[matched >= 0].tolist())
            for position, index in enumerate(chosen):
                record = {"image_id": page.image_id, "mark_type": mark_class, "score": float(scores[position]),
                          "box": [float(v) for v in boxes[position]], "detection_index": int(index)}
                if matched[position] >= 0:
                    mark = marks[matched[position]]
                    matched_ious[mark_class].append(float(overlaps[position, matched[position]]))
                    hits.append({**record, "mark_id": mark.mark_id, "style": mark.style,
                                 "iou": float(overlaps[position, matched[position]])})
                    continue
                on_other = iou_matrix(boxes[position:position + 1], other_truth)
                share = _overlap_share(boxes[position], distractors)
                inside = _overlap_share(boxes[position], truth)  # share of this box lying in each mark of its class
                holder = int(inside.argmax()) if inside.size else None
                kind, distractor, part_of = "background", None, None
                if best[position] >= iou_threshold:
                    kind = "duplicate"
                elif on_other.size and on_other.max() >= iou_threshold:
                    kind = "wrong_class"
                elif holder is not None and inside[holder] >= PART_OF_MARK_SHARE and holder in found_marks:
                    kind, part_of = "part_of_found_mark", marks[holder]
                elif best[position] >= 0.1:
                    kind = "poorly_localised"
                elif share.size and share.max() >= 0.3:
                    kind, distractor = "on_distractor", page.distractors[int(share.argmax())].mark_class
                false_positives.append({**record, "kind": kind, "distractor_class": distractor,
                                        "best_iou_with_a_mark": float(best[position]),
                                        "share_inside_mark": float(inside[holder]) if holder is not None else 0.0,
                                        "inside_mark_id": None if part_of is None else part_of.mark_id,
                                        "inside_mark_style": None if part_of is None else part_of.style,
                                        "on_page_without_marks": not page.marks})
                page_false += 1
            # What happened to each mark that was not found.
            low = prediction.of(label)
            low_overlaps = iou_matrix(truth, prediction.boxes[low])
            kept_other = prediction.of(CLASS_TO_LABEL[other], thresholds[other])
            other_overlaps = iou_matrix(truth, prediction.boxes[kept_other])
            for position, mark in enumerate(marks):
                found = position in set(matched.tolist())
                style_counts.setdefault((mark_class, mark.style), [0, 0])[1] += 1
                writer_counts.setdefault(page.writer, [0, 0])[1] += 1
                if found:
                    style_counts[(mark_class, mark.style)][0] += 1
                    writer_counts[page.writer][0] += 1
                    continue
                kind = "not_detected"
                if other_overlaps.shape[1] and other_overlaps[position].max() >= iou_threshold:
                    kind = "detected_as_other_class"
                elif overlaps.shape[0] and overlaps[:, position].max() >= 0.1:
                    kind = "poorly_localised"
                elif low_overlaps.shape[1] and low_overlaps[position].max() >= iou_threshold:
                    kind = "below_threshold"
                best_low = int(low_overlaps[position].argmax()) if low_overlaps.shape[1] else None
                misses.append({"image_id": page.image_id, "mark_id": mark.mark_id, "mark_class": mark_class,
                               "style": mark.style, "box": [float(v) for v in mark.box], "kind": kind,
                               "best_score_at_iou": None if best_low is None or low_overlaps[position, best_low]
                               < iou_threshold else float(prediction.scores[low][best_low])})
            summary = classes[mark_class]
            summary["marks"] += len(marks)
            summary["true_positives"] += int((matched >= 0).sum())
            summary["false_positives"] += int((matched < 0).sum())
            summary["false_negatives"] += len(marks) - int((matched >= 0).sum())
        if not page.marks:
            empty_pages += 1
            empty_pages_hit += page_false > 0
    for summary in classes.values():
        tp, fp, fn = summary["true_positives"], summary["false_positives"], summary["false_negatives"]
        summary.update(precision=_ratio(tp, tp + fp), recall=_ratio(tp, tp + fn), f1=_ratio(2 * tp, 2 * tp + fp + fn))
    kinds = Counter(fp["kind"] for fp in false_positives)
    return {
        "thresholds": {c: float(thresholds[c]) for c in TARGET_CLASSES}, "iou_threshold": iou_threshold,
        "classes": classes, "hits": hits, "false_positives": false_positives, "misses": misses,
        "matched_ious": matched_ious,
        "false_positive_summary": {"total": len(false_positives),
                                   "on_pages_without_marks": sum(fp["on_page_without_marks"] for fp in false_positives),
                                   **{kind: kinds.get(kind, 0) for kind in FALSE_POSITIVE_KINDS}},
        "pages_without_marks": {"pages": empty_pages, "with_a_false_positive": int(empty_pages_hit)},
        "styles": [{"mark_class": c, "style": s, "detected": found, "marks": total, "recall": _ratio(found, total)}
                   for (c, s), (found, total) in style_counts.items()],
        "writers": [{"writer": w, "detected": found, "marks": total, "recall": _ratio(found, total)}
                    for w, (found, total) in sorted(writer_counts.items())],
    }


# --------------------------------------------------------------------------- linking

@dataclass(frozen=True)
class LinkedClaim:
    """The linker's result for one claim, with the page and array index behind every detection."""

    claim_id: str
    job_key: str
    pages: tuple[PenMarkPage, ...]
    detections: tuple[Detection, ...]
    sources: tuple[tuple[str, int], ...]  # (image_id, index in that page's prediction) per detection
    thresholds: Mapping[str, float]  # the per-class score thresholds the linker applied
    result: LinkingResult


class M5RowsError(ValueError):
    """An M5 rows file is not the one this evaluation can use."""


def find_m5_rows_file(evaluation_root: str | Path, split: str = "validation") -> Path | None:
    """The newest ``lineitems-*/rows_for_m6_<split>.json`` under ``evaluation_root``, or ``None``."""
    found = sorted(Path(evaluation_root).glob(f"lineitems-*/rows_for_m6_{split}.json"),
                   key=lambda path: (path.stat().st_mtime, str(path)))
    return found[-1] if found else None


def load_m5_rows(path: str | Path, *, split: str, m4_cache_manifest_sha256: str | None,
                 quick_run: bool) -> dict[str, Any]:
    """Read the rows an M5 model would hand to M6 and refuse a file that does not belong here.

    Refused: another schema, another split, rows read from another M4 cache than the pages
    were detected on, and a file of a quick M5 run unless this run is a quick run too. The
    returned record gains ``path``. Nothing in the file is a label: it is M5 *output*.
    """
    path = Path(path)
    if not path.is_file():
        raise M5RowsError(f"M5 rows file {path} does not exist")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema") != M5_ROWS_SCHEMA:
        found = record.get("schema") if isinstance(record, dict) else type(record).__name__
        raise M5RowsError(f"{path.name}: schema {found!r} is not {M5_ROWS_SCHEMA!r}")
    if record.get("split") != split:
        raise M5RowsError(f"{path.name}: rows of split {record.get('split')!r}, this evaluation reads {split!r}")
    if record.get("m4_cache_manifest_sha256") != m4_cache_manifest_sha256:
        raise M5RowsError(f"{path.name}: M5 read another M4 cache ({str(record.get('m4_cache_manifest_sha256'))[:12]}...) "
                          f"than these pages come from ({str(m4_cache_manifest_sha256)[:12]}...)")
    if record.get("quick_run") and not quick_run:
        raise M5RowsError(f"{path.name}: written by a quick M5 run (a plumbing check); not usable for a full run")
    if not isinstance(record.get("pages"), list):
        raise M5RowsError(f"{path.name}: 'pages' is not a list")
    return {**record, "path": str(path)}


def with_m5_rows(pages: Sequence[PenMarkPage], record: Mapping[str, Any]) -> tuple[list[PenMarkPage], dict[str, Any]]:
    """Copies of ``pages`` whose rows carry the geometry M5 produced, and how many rows that covers.

    Scoring needs to know which true row an M5 row is. A true row takes the M5 row whose
    centre lies inside the true row's table band (the nearest centre when M5 produced
    several there), keeping its own ``entry_id``. A true row without an M5 row is dropped:
    M6 is not given it, so a mark on it cannot be linked to it. A page M5 could not read, or
    that the file does not list, has no rows at all (the linker's ``rows_unavailable``).
    The marks stay as they are, so the denominators still count every marked row.
    """
    listed = {entry["image_id"]: entry for entry in record["pages"]}
    out, heights = [], []
    counts = {"true_rows": 0, "with": 0, "m5_rows": 0, "unmatched_m5": 0, "pages": 0}
    unreadable, absent = [], []
    for page in pages:
        entry = listed.get(page.image_id)
        if page.rows:
            counts["pages"] += 1
            counts["true_rows"] += len(page.rows)
            if entry is None:
                absent.append(page.image_id)
            elif entry.get("page_status") == "unreadable":
                unreadable.append(page.image_id)
        if entry is not None and entry.get("page_id") != page.page_id:
            raise M5RowsError(f"page {page.image_id}: the M5 rows are for page id {entry.get('page_id')!r}, "
                              f"this page is {page.page_id!r}")
        size = np.array([page.width, page.height] * 2, dtype=float)
        supplied = [] if entry is None else list(entry.get("row_boxes", []))
        centres = [float((b["row_box_norm"][1] + b["row_box_norm"][3]) / 2 * page.height) for b in supplied]
        counts["m5_rows"] += len(supplied)
        used: set[int] = set()
        rows = []
        for row in page.rows:
            if row.band is None:
                raise M5RowsError(f"page {page.image_id}: row {row.row_id} has no table band to place an M5 row in")
            middle = (row.band[1] + row.band[3]) / 2
            inside = [i for i, y in enumerate(centres) if row.band[1] <= y <= row.band[3] and i not in used]
            if not inside:
                continue
            best = min(inside, key=lambda i: (abs(centres[i] - middle), i))
            used.add(best)
            box = tuple(float(v) for v in np.asarray(supplied[best]["row_box_norm"], dtype=float) * size)
            amount = supplied[best].get("amount_box_norm")
            rows.append(replace(row, box=box, amount_box=None if amount is None else tuple(
                float(v) for v in np.asarray(amount, dtype=float) * size)))
            heights.append(box[3] - box[1])
        counts["with"] += len(rows)
        counts["unmatched_m5"] += len(supplied) - len(used)
        out.append(replace(page, rows=tuple(rows), rows_source=ROWS_M5))
    summary = {"true_rows": counts["true_rows"], "true_rows_with_m5_row": counts["with"],
               "true_rows_without_m5_row": counts["true_rows"] - counts["with"],
               "m5_rows_on_these_pages": counts["m5_rows"], "m5_rows_matching_no_true_row": counts["unmatched_m5"],
               "pages_with_true_rows": counts["pages"], "pages_m5_could_not_read": unreadable,
               "pages_not_in_file": absent, "median_row_height_px": float(np.median(heights)) if heights else None,
               "m5_model_version": record.get("m5_model_version"), "m5_registry_entry": record.get("m5_registry_entry"),
               "file": record.get("path")}
    return out, summary


def rows_source(pages: Iterable[PenMarkPage]) -> str:
    """The one row definition ``pages`` carry; mixing definitions in one evaluation is refused."""
    names = {page.rows_source for page in pages}
    if len(names) != 1:
        raise ValueError(f"pages carry {sorted(names) or 'no'} row definitions; an evaluation uses exactly one")
    return names.pop()


def row_boxes(pages: Iterable[PenMarkPage]) -> list[RowBox]:
    """The rows of ``pages`` as the ``RowBox`` records M6 receives from M5.

    Which box a row has is the pages' ``rows_source``: by default the union of the row's
    printed field boxes from ground truth (oracle rows, not M5 output). The amount box is
    the row's printed amount. Both are normalised by the corrected page size, the frame of
    ``LineItem.row_box_norm``.
    """
    boxes = []
    for page in pages:
        size = np.array([page.width, page.height] * 2, dtype=float)
        norm = lambda box: tuple(float(v) for v in np.clip(np.asarray(box, dtype=float) / size, 0.0, 1.0))  # noqa: E731
        boxes.extend(RowBox(entry_id=row.entry_id, page_id=page.page_id, row_box_norm=norm(row.box),
                            amount_box_norm=None if row.amount_box is None else norm(row.amount_box))
                     for row in page.rows)
    return boxes


def oracle_predictions(pages: Iterable[PenMarkPage]) -> dict[str, PagePrediction]:
    """The ground-truth mark boxes as if a perfect detector had returned them with score 1."""
    return {page.image_id: PagePrediction(page.boxes.astype(float), page.labels.astype(int), np.ones(len(page.labels)))
            for page in pages}


def linking_config_with_thresholds(config: LinkingConfig, thresholds: Mapping[str, float],
                                   label: str) -> LinkingConfig:
    """A copy of ``config`` with other per-class score thresholds, under a version that says so.

    The copy lives in memory. ``configs/pipeline/m6_linking.yaml`` is not written, and a mark
    linked with the copy never carries the file's version label.
    """
    return config.with_overrides({
        "link_config_version": f"{config.link_config_version}+thresholds.{label}",
        "detection": {"score_threshold": {c: float(thresholds[c]) for c in TARGET_CLASSES}}})


def link_pages(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction], config: LinkingConfig, *,
               versions: Mapping[str, str], provenance: Provenance = NOTEBOOK_PROVENANCE,
               pages_without_rows: Iterable[str] = ()) -> list[LinkedClaim]:
    """Run ``link_detections`` once per claim, as the M6 worker would for one claim revision.

    ``pages_without_rows`` names pages (image ids) for which no row is supplied, the case of
    a page M5 could not read: their marks stay unlinked with ``rows_unavailable``.
    """
    skipped = set(pages_without_rows)
    by_claim: dict[str, list[PenMarkPage]] = {}
    for page in pages:
        by_claim.setdefault(page.claim_id, []).append(page)
    linked = []
    for claim_id, members in by_claim.items():
        detections, sources = [], []
        for page in members:
            prediction = predictions.get(page.image_id, EMPTY)
            for index in range(len(prediction.scores)):
                detections.extend(detections_from_arrays(
                    page.page_id, prediction.boxes[index:index + 1], prediction.labels[index:index + 1],
                    prediction.scores[index:index + 1], width=page.width, height=page.height))
                sources.append((page.image_id, index))
        stamped = {**versions, "link_config": config.link_config_version}
        job_key = make_job_key(claim_id, 1, "pen_marks_detect", stamped)
        result = link_detections(
            detections, row_boxes(p for p in members if p.image_id not in skipped), config=config, job_key=job_key,
            claim_id=claim_id, input_revision=1, provenance=provenance, versions=stamped,
            page_widths_px={p.page_id: p.width for p in members})
        linked.append(LinkedClaim(claim_id, job_key, tuple(members), tuple(detections), tuple(sources),
                                  {c: config.score_threshold(c) for c in TARGET_CLASSES}, result))
    return linked


def linking_outcomes(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction],
                     linked: Sequence[LinkedClaim], iou_threshold: float = IOU_THRESHOLD) -> list[dict[str, Any]]:
    """What became of every ground-truth mark: one row per mark with one of ``OUTCOMES``.

    A mark is *detected* when a detection of its class, at or above the linker's score
    threshold, matches it at ``iou_threshold``. Its outcome is then read from the pending
    mark the linker created for that detection.
    """
    mark_of: dict[tuple[str, int], Any] = {}
    thresholds: Mapping[str, float] = linked[0].thresholds if linked else {}
    for item in linked:
        by_id = {mark.mark_id: mark for mark in item.result.marks}
        for mark_id, index in item.result.detection_index.items():
            mark_of[item.sources[index]] = by_id[mark_id]
        for merged in item.result.merged_duplicates:
            mark_of[item.sources[merged.detection_index]] = by_id[merged.kept_mark_id]
    rows = []
    for page in pages:
        prediction = predictions.get(page.image_id, EMPTY)
        for mark_class in TARGET_CLASSES:
            marks = [m for m in page.marks if m.mark_class == mark_class]
            chosen = prediction.of(CLASS_TO_LABEL[mark_class], thresholds.get(mark_class, 0.0))
            matched, _ = match_page(prediction.boxes[chosen], prediction.scores[chosen],
                                    np.array([m.box for m in marks], dtype=float).reshape(-1, 4), iou_threshold)
            detection_of = {int(g): int(chosen[d]) for d, g in enumerate(matched) if g >= 0}
            for position, mark in enumerate(marks):
                row = {"image_id": page.image_id, "mark_id": mark.mark_id, "mark_class": mark_class,
                       "style": mark.style, "writer": page.writer, "true_entry_id": mark.entry_id,
                       "outcome": "not_detected", "linked_entry_id": None, "link_reason": None, "rule_id": None,
                       "pen_mark_id": None, "state": None}
                pen_mark = mark_of.get((page.image_id, detection_of[position])) if position in detection_of else None
                if pen_mark is not None:
                    if pen_mark.entry_id is not None:
                        outcome = "linked_correct" if pen_mark.entry_id == mark.entry_id else "linked_wrong"
                    else:
                        outcome = ("unlinked_candidate" if mark.entry_id in pen_mark.candidate_entry_ids
                                   else "unlinked_no_candidate")
                    row.update(outcome=outcome, linked_entry_id=pen_mark.entry_id, link_reason=pen_mark.link_reason,
                               rule_id=pen_mark.rule_id, pen_mark_id=pen_mark.mark_id, state=pen_mark.state)
                rows.append(row)
    return rows


def outcome_summary(outcomes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Outcome counts per class and style, in first-seen order."""
    table: dict[tuple[str, str], Counter] = {}
    for row in outcomes:
        table.setdefault((row["mark_class"], row["style"]), Counter())[row["outcome"]] += 1
    return [{"mark_class": c, "style": s, "marks": sum(counts.values()), **{o: counts.get(o, 0) for o in OUTCOMES}}
            for (c, s), counts in table.items()]


def row_level_scores(pages: Sequence[PenMarkPage], linked: Sequence[LinkedClaim]) -> dict[str, Any]:
    """Per-row detection and linking precision and recall, before any human correction.

    A row is *marked* with a class when the ground truth puts a mark of that class on it, and
    *flagged* when the linker linked a pending mark of that class to it. Precision is the
    share of flagged rows that are marked; recall the share of marked rows that are flagged.
    An unlinked mark flags no row: it waits for the surveyor and is counted separately.
    """
    marks = [mark for item in linked for mark in item.result.marks]
    scores: dict[str, Any] = {}
    for mark_class in TARGET_CLASSES:
        truth = {m.entry_id for page in pages for m in page.marks if m.mark_class == mark_class}
        flagged = {m.entry_id for m in marks if m.mark_type == mark_class and m.entry_id is not None}
        tp, fp, fn = len(truth & flagged), len(flagged - truth), len(truth - flagged)
        scores[mark_class] = {"marked_rows": len(truth), "flagged_rows": len(flagged), "true_positives": tp,
                              "false_positives": fp, "false_negatives": fn, "precision": _ratio(tp, tp + fp),
                              "recall": _ratio(tp, tp + fn)}
    scores["marks"] = {"proposed": len(marks), "linked": sum(m.entry_id is not None for m in marks),
                       "unlinked": sum(m.entry_id is None for m in marks),
                       "pending": sum(m.state == "pending" for m in marks),
                       "unlinked_by_reason": dict(Counter(m.link_reason for m in marks if m.entry_id is None))}
    return scores


def linking_report(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction], config: LinkingConfig, *,
                   versions: Mapping[str, str], pages_without_rows: Iterable[str] = ()) -> dict[str, Any]:
    """Row-level scores, per-mark outcomes and their summary for one set of detections."""
    skipped = set(pages_without_rows)
    linked = link_pages(pages, predictions, config, versions=versions, pages_without_rows=skipped)
    outcomes = linking_outcomes(pages, predictions, linked)
    source = rows_source(pages)
    return {"link_config_version": config.link_config_version, "link_config_sha256": config.sha256,
            "rows_source": source, "rows": ROW_SOURCES.get(source, source),
            "rows_given": sum(len(p.rows) for p in pages if p.image_id not in skipped),
            "pages_without_rows": sorted(skipped), "row_level": row_level_scores(pages, linked),
            "outcomes_by_style": outcome_summary(outcomes), "outcome_totals": dict(Counter(o["outcome"] for o in outcomes)),
            "conflicting_rows": sum(len(item.result.conflicting_entries) for item in linked),
            "merged_duplicates": sum(len(item.result.merged_duplicates) for item in linked)}


def linking_by_rows(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction], config: LinkingConfig, *,
                    versions: Mapping[str, str], m5_rows: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Linking with each row definition, for the detector's boxes and for oracle detections.

    ``pages`` carry the primary rows (the union of each row's printed cells). The contrast
    replaces them by the full table bands; ``m5_rows`` (a ``load_m5_rows`` record) adds the
    rows a trained M5 candidate produced. Every entry is named after its rows.
    """
    primary = rows_source(pages)
    if primary != ROWS_CELL_UNION:
        raise ValueError(f"the primary rows are {ROWS_CELL_UNION!r}; these pages carry {primary!r}")
    variants: list[tuple[str, Sequence[PenMarkPage], dict[str, Any]]] = [
        (ROWS_CELL_UNION, pages, {}), (ROWS_TABLE_BAND, with_table_band_rows(pages), {})]
    if m5_rows is not None:
        m5_pages, summary = with_m5_rows(pages, m5_rows)
        variants.append((ROWS_M5, m5_pages, {"source": summary}))
    oracle = oracle_predictions(pages)
    result: dict[str, Any] = {"primary_rows": f"{ROWS_CELL_UNION}_rows"}
    for name, members, extra in variants:
        result[f"{name}_rows"] = {
            "rows": ROW_SOURCES[name], **extra,
            "detector": linking_report(members, predictions, config, versions=versions),
            "oracle_detections": linking_report(members, oracle, config, versions=versions)}
    return result


def linking_table(linking: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The side-by-side table of ``linking_by_rows``: one line per rows, detections and class."""
    rows = []
    for name, entry in linking.items():
        if name == "primary_rows":
            continue
        for detections in ("oracle_detections", "detector"):
            report = entry[detections]
            for mark_class in TARGET_CLASSES:
                score = report["row_level"][mark_class]
                rows.append({"rows": name, "detections": detections, "mark_class": mark_class,
                             "rows_given": report["rows_given"], "marked_rows": score["marked_rows"],
                             "flagged_rows": score["flagged_rows"], "true_positives": score["true_positives"],
                             "false_positives": score["false_positives"], "recall": score["recall"],
                             "precision": score["precision"],
                             "unlinked_marks": report["row_level"]["marks"]["unlinked"],
                             "conflicting_rows": report["conflicting_rows"]})
    return rows


def evaluate_pages(pages: Sequence[PenMarkPage], predictions: Mapping[str, PagePrediction],
                   thresholds: Mapping[str, float], link_config: LinkingConfig, *, versions: Mapping[str, str],
                   threshold_label: str = "selected", m5_rows: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The whole evaluation of one partition as a JSON-ready report.

    Used unchanged for the validation split and, once, for the held-out test split. The
    thresholds are inputs: they are selected on validation before this is called for test.
    ``linking`` holds one entry per row definition given to the linker (``linking_by_rows``).
    """
    config = linking_config_with_thresholds(link_config, thresholds, label=threshold_label)
    point = operating_point(pages, predictions, thresholds)
    return {
        "splits": sorted({p.split for p in pages}),
        "pages": {"total": len(pages), "with_marks": sum(bool(p.marks) for p in pages),
                  "without_marks": sum(not p.marks for p in pages),
                  "m4_unreadable": sum(p.page_status == "unreadable" for p in pages)},
        "marks": {c: sum(m.mark_class == c for p in pages for m in p.marks) for c in TARGET_CLASSES},
        "thresholds": {c: float(thresholds[c]) for c in TARGET_CLASSES}, "iou_threshold": IOU_THRESHOLD,
        "detection": detection_report(pages, predictions),
        "operating_point": point,
        "linking": linking_by_rows(pages, predictions, config, versions=versions, m5_rows=m5_rows),
    }


# --------------------------------------------------------------------------- drawing

def draw_boxes(axis: Any, boxes: Any, colour: str, *, linewidth: float = 1.6, linestyle: str = "-",
               labels: Sequence[str] | None = None, fontsize: float = 7, pad: float = 0.0,
               label_side: str = "above") -> None:
    """Outline pixel boxes on a matplotlib axis; ``pad`` grows each outline so thin marks stay visible.

    ``label_side`` puts a label ``above`` the box at its left end, ``below`` it at its left
    end, or above it at its ``right`` end. Boxes that share a corner (a mark and a part of
    it) can then carry labels that do not cover each other.
    """
    from matplotlib.patches import Rectangle

    if label_side not in ("above", "below", "right"):
        raise ValueError(f"label_side is 'above', 'below' or 'right', not {label_side!r}")
    for index, (x0, y0, x1, y1) in enumerate(np.asarray(boxes, dtype=float).reshape(-1, 4)):
        axis.add_patch(Rectangle((x0 - pad, y0 - pad), x1 - x0 + 2 * pad, y1 - y0 + 2 * pad, fill=False,
                                 edgecolor=colour, linewidth=linewidth, linestyle=linestyle))
        if labels is not None:
            x, y, va, ha = {"above": (x0 - pad, y0 - pad - 2, "bottom", "left"),
                            "below": (x0 - pad, y1 + pad + 2, "top", "left"),
                            "right": (x1 + pad, y0 - pad - 2, "bottom", "right")}[label_side]
            axis.text(x, y, labels[index], color=colour, fontsize=fontsize, va=va, ha=ha,
                      bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 0.5})


def draw_detection_page(axis: Any, pixels: np.ndarray, page: PenMarkPage, point: Mapping[str, Any],
                        crop: Sequence[float] | None = None) -> dict[str, Any]:
    """One page with hits (green), false alarms (vermilion) and misses (purple, dashed), numbered.

    Every box carries a small number and ``lines`` in the result says what each number is:
    long captions on the page would cover each other. A hit is numbered above its box, a
    false alarm below its box and a miss at the right end, so the number of a found mark
    and that of a box on a part of it stay apart.
    """
    axis.imshow(pixels)
    on_page = lambda items: [i for i in items if i["image_id"] == page.image_id]  # noqa: E731
    hits, alarms, missed = on_page(point["hits"]), on_page(point["false_positives"]), on_page(point["misses"])
    short = {"exclusion": "exclusion", "price_change": "price change"}
    position = lambda item: (item["box"][1], item["box"][0])  # noqa: E731
    hits, alarms, missed = sorted(hits, key=position), sorted(alarms, key=position), sorted(missed, key=position)
    numbers = iter(range(1, len(hits) + len(alarms) + len(missed) + 1))
    lines = []
    tags = [str(next(numbers)) for _ in hits]
    lines += [f"{t}  found: {short[h['mark_type']]} {h['score']:.2f} ({h['style']})" for t, h in zip(tags, hits)]
    draw_boxes(axis, [h["box"] for h in hits], PALETTE["true_positive"], pad=3, labels=tags, fontsize=8)
    tags = [str(next(numbers)) for _ in alarms]
    lines += [f"{t}  false: {short[a['mark_type']]} {a['score']:.2f}, {a['kind'].replace('_', ' ')}"
              + (f" ({a['inside_mark_style']})" if a.get("inside_mark_style") else "") for t, a in zip(tags, alarms)]
    draw_boxes(axis, [a["box"] for a in alarms], PALETTE["false_positive"], pad=3, labels=tags, fontsize=8,
               label_side="below")
    tags = [str(next(numbers)) for _ in missed]
    lines += [f"{t}  missed: {short[m['mark_class']]} ({m['style']}), {m['kind'].replace('_', ' ')}"
              for t, m in zip(tags, missed)]
    draw_boxes(axis, [m["box"] for m in missed], PALETTE["missed"], pad=6, linestyle="--", labels=tags, fontsize=8,
               label_side="right")
    if crop is not None:
        axis.set_xlim(crop[0], crop[2])
        axis.set_ylim(crop[3], crop[1])
    axis.set_xticks([])
    axis.set_yticks([])
    return {"hits": len(hits), "false_positives": len(alarms), "misses": len(missed), "lines": lines}


__all__ = ["COCO_IOU_THRESHOLDS", "EMPTY", "FALSE_POSITIVE_KINDS", "IOU_THRESHOLD", "M5_ROWS_SCHEMA",
           "NOTEBOOK_PROVENANCE", "OUTCOMES", "PART_OF_MARK_SHARE", "ROW_SOURCES", "THRESHOLD_GRID", "LinkedClaim",
           "M5RowsError", "PagePrediction", "average_precision", "best_f1_run", "claim_halves", "class_detections",
           "compare_detection_sets", "detection_report", "draw_boxes", "draw_detection_page", "evaluate_pages",
           "find_m5_rows_file", "half_split_check", "iou_matrix", "link_pages", "linking_by_rows",
           "linking_config_with_thresholds", "linking_outcomes", "linking_report", "linking_table", "load_m5_rows",
           "match_page", "operating_point", "oracle_predictions", "outcome_summary", "precision_recall", "row_boxes",
           "row_level_scores", "rows_source", "select_threshold", "selection_score", "threshold_counts",
           "threshold_table", "threshold_transfer", "with_m5_rows"]
