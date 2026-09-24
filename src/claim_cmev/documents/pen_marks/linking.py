"""M6 geometric linking of detected pen marks to M5 rows (pure, no I/O).

Module-06 processing steps 4 to 9: row bands, component scores, rules EX-1 and
PC-1 to PC-3, candidate retention, same-class duplicate merging and conflict counts.
Every mark is created ``pending``: the linker never confirms, never reads a handwritten
amount and never invents a detection confidence. A mark is linked only when one row is
unambiguous; otherwise ``entry_id`` is null and the candidate rows are retained.

All geometry is in normalised corrected-render coordinates. The component scores are
ratios, so they equal the pixel-space values; only the right-margin tolerance is given in
pixels and is converted with the page width.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from claim_cmev.contracts.common import (
    BoxNorm,
    Confidence,
    ContractError,
    ContractModel,
    Provenance,
    RecordId,
    deterministic_id,
)
from claim_cmev.contracts.documents import MarkType, PenMark, TrocrSuggestion

from .config import ColumnSet, LinkingConfig
from .rows import RowBox, conflicting_entry_ids, row_box_of

Box = tuple[float, float, float, float]
LINKED_REASON = "unambiguous_row_overlap"
BETWEEN_ROWS_REASON = "mark_between_rows"
NO_CANDIDATE_REASON = "no_candidate_row"
ROWS_UNAVAILABLE_REASON = "rows_unavailable"
VERSION_KEY = "link_config"
_PLACES = 6


class Detection(ContractModel):
    """One detector box on a corrected page render (after the detector's own NMS and cap)."""

    page_id: RecordId
    box_norm: BoxNorm
    mark_type: MarkType
    score: Confidence
    trocr_suggestion: TrocrSuggestion | None = None
    """Stretch S2 only; passed through untouched and never used as an amount."""


@dataclass(frozen=True)
class RowScore:
    """Component scores of one mark against one row (module-06 step 5)."""

    entry_id: str
    v: float
    c: float
    rc: float
    col: int
    amt: float
    link_score: float


@dataclass(frozen=True)
class MarkCandidate:
    """One ``pen_mark_candidate`` row: a retained candidate with the scores behind its rank."""

    mark_id: str
    entry_id: str
    rank: int
    link_score: float
    v: float
    c: float
    rc: float
    col: int
    amt: float


@dataclass(frozen=True)
class MergedDetection:
    """A same-class duplicate box merged into ``kept_mark_id`` and counted once."""

    kept_mark_id: str
    detection_index: int
    score: float
    iou: float


@dataclass(frozen=True)
class LinkingResult:
    marks: tuple[PenMark, ...]
    candidates: tuple[MarkCandidate, ...]
    merged_duplicates: tuple[MergedDetection, ...]
    below_threshold: tuple[int, ...]
    """Input indexes dropped by the per-class score threshold."""
    detection_index: Mapping[str, int]
    """mark_id to the index of its detection in the caller's sequence."""
    conflicting_entries: Mapping[str, str]
    """entry_id to conflict reason, for rows whose linked marks conflict."""
    counts: Mapping[str, int]
    """exclusion, price_change, linked, unlinked, conflicting (rows)."""
    link_config_version: str


def _round(value: float) -> float:
    return round(value, _PLACES)


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: Box, b: Box) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a: Box, b: Box) -> float:
    inter = _intersection(a, b)
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else (1.0 if a == b else 0.0)


def _column_match(columns: ColumnSet, centre_x: float, row: RowBox, amount_column: tuple[float, float] | None,
                  tolerance: float) -> int:
    if columns == "all":
        return int(row.row_box_norm[0] <= centre_x <= row.row_box_norm[2] + tolerance)
    if "amount" in columns and amount_column is not None:
        return int(amount_column[0] <= centre_x <= amount_column[1] + tolerance)
    return 0


def score_row(mark_box: Box, mark_type: str, row: RowBox, *, config: LinkingConfig,
              amount_column: tuple[float, float] | None, tolerance: float) -> RowScore:
    """Module-06 step 5 for one mark and one row. ``tolerance`` is normalised to page width."""
    rx0, ry0, rx1, ry1 = row.row_box_norm
    height = ry1 - ry0
    band = (ry0 - config.band.extend_above_row_heights * height, ry1 + config.band.extend_below_row_heights * height)
    mark_height = mark_box[3] - mark_box[1]
    overlap = max(0.0, min(mark_box[3], band[1]) - max(mark_box[1], band[0]))
    v = overlap / mark_height if mark_height > 0 else 0.0
    inside = _intersection(mark_box, row.row_box_norm)
    c = inside / _area(mark_box) if _area(mark_box) > 0 else 0.0
    rc = inside / _area(row.row_box_norm) if _area(row.row_box_norm) > 0 else 0.0
    columns = config.column.exclusion_columns if mark_type == "exclusion" else config.column.price_change_columns
    col = _column_match(columns, (mark_box[0] + mark_box[2]) / 2, row, amount_column, tolerance)
    token = row.amount_box_norm
    amt = _intersection(mark_box, token) / _area(token) if token is not None and _area(token) > 0 else 0.0
    v, c, rc, amt = map(_round, (v, c, rc, amt))
    weights = config.link.weights
    link_score = _round(weights.v * v + weights.c * c + weights.rc * rc + weights.col * col)
    return RowScore(row.entry_id, v, c, rc, col, amt, link_score)


def decide_link(mark_type: str, scores: Sequence[RowScore], *, config: LinkingConfig) -> tuple[str | None, str]:
    """Apply EX-1 or PC-1..PC-3 to one mark's row scores. Returns (entry_id or None, rule_id)."""
    ranked = sorted(scores, key=lambda s: (-s.link_score, -s.amt))
    qualifying = [s for s in scores if s.link_score >= config.link.min_score]
    lead = _round(ranked[0].link_score - (ranked[1].link_score if len(ranked) > 1 else 0.0)) if ranked else 0.0
    if mark_type == "exclusion":
        if len(qualifying) == 1 and lead >= config.link.margin_to_second:
            return qualifying[0].entry_id, "EX-1"
        return None, "EX-1"
    touched = [s for s in scores if s.amt >= config.price_change.amount_overlap_min]
    if len(touched) == 1 and all(s.amt <= config.price_change.amount_overlap_second_max
                                 for s in scores if s is not touched[0]):
        return touched[0].entry_id, "PC-1"
    if len(qualifying) == 1 and lead >= config.price_change.margin_to_second:
        return qualifying[0].entry_id, "PC-2"
    return None, "PC-3"


def _detection(value: Any) -> Detection:
    if isinstance(value, Detection):
        return value
    try:
        return Detection.model_validate(value)
    except ValueError as exc:
        raise ContractError("detection_invalid", str(exc)) from exc


def _canonical_key(item: tuple[int, Detection]) -> tuple:
    index, d = item
    x0, y0, x1, y1 = d.box_norm
    return (y0, x0, y1, x1, d.mark_type, -d.score, index)


def link_detections(
    detections: Sequence[Detection | Mapping[str, Any]],
    rows: Sequence[Any],
    *,
    config: LinkingConfig,
    job_key: str,
    claim_id: str,
    input_revision: int,
    provenance: Provenance,
    versions: Mapping[str, str],
    page_widths_px: Mapping[str, int] | None = None,
) -> LinkingResult:
    """Link detections to rows and return pending marks plus candidate scores and diagnostics.

    ``rows`` are M5 ``LineItem`` records, ``RowBox`` records or ``row_boxes`` mappings from
    ``cmev.cmd.pen-marks-detect.v1``. Mark IDs are ``deterministic_id("pm", job_key, page_id,
    index)`` with ``index`` the detection's position in canonical (top-to-bottom) order on its
    page among detections that pass the score threshold, so a replay reproduces them.
    ``versions`` is copied onto every mark; ``versions["link_config"]`` is set to the config
    version and must not contradict it.
    """
    if not job_key.startswith(f"{claim_id}:{input_revision}:"):
        raise ContractError("job_key_mismatch", f"job key {job_key!r} is not for {claim_id} revision {input_revision}")
    versions = dict(versions)
    if versions.setdefault(VERSION_KEY, config.link_config_version) != config.link_config_version:
        raise ContractError("link_config_version_mismatch",
                            f"versions[{VERSION_KEY!r}]={versions[VERSION_KEY]!r} but config is "
                            f"{config.link_config_version!r}")
    row_boxes = [row_box_of(r) for r in rows]
    seen: set[str] = set()
    rows_by_page: dict[str, list[RowBox]] = {}
    for row in row_boxes:
        if row.entry_id in seen:
            raise ContractError("duplicate_entry_id", row.entry_id)
        seen.add(row.entry_id)
        rows_by_page.setdefault(row.page_id, []).append(row)

    parsed = [_detection(d) for d in detections]
    below = tuple(i for i, d in enumerate(parsed) if d.score < config.score_threshold(d.mark_type))
    by_page: dict[str, list[tuple[int, Detection]]] = {}
    for i, d in enumerate(parsed):
        if i not in below:
            by_page.setdefault(d.page_id, []).append((i, d))
    page_order = list(rows_by_page) + sorted(set(by_page) - set(rows_by_page))

    marks: list[PenMark] = []
    candidates: list[MarkCandidate] = []
    merged: list[MergedDetection] = []
    detection_index: dict[str, int] = {}
    widths = page_widths_px or {}
    for page_id in page_order:
        ordered = sorted(by_page.get(page_id, []), key=_canonical_key)
        ids = {index: deterministic_id("pm", job_key, page_id, position) for position, (index, _) in enumerate(ordered)}
        kept: list[tuple[int, Detection]] = []
        for index, d in sorted(ordered, key=lambda item: (-item[1].score, _canonical_key(item))):
            overlaps = [(iou(d.box_norm, k.box_norm), k_index) for k_index, k in kept if k.mark_type == d.mark_type]
            best = max(overlaps, default=None)
            if best is not None and best[0] >= config.detection.dedupe_iou:
                merged.append(MergedDetection(ids[best[1]], index, d.score, _round(best[0])))
            else:
                kept.append((index, d))
        page_rows = rows_by_page.get(page_id, [])
        amount_boxes = [r.amount_box_norm for r in page_rows if r.amount_box_norm is not None]
        amount_column = (min(b[0] for b in amount_boxes), max(b[2] for b in amount_boxes)) if amount_boxes else None
        tolerance = config.column.right_margin_tolerance_px / widths.get(page_id, config.column.default_page_width_px)
        for index, d in sorted(kept, key=_canonical_key):
            mark_id = ids[index]
            detection_index[mark_id] = index
            if not page_rows:
                entry_id, rule_id, retained, reason = None, None, [], ROWS_UNAVAILABLE_REASON
            else:
                scores = [score_row(d.box_norm, d.mark_type, r, config=config, amount_column=amount_column,
                                    tolerance=tolerance) for r in page_rows]
                entry_id, rule_id = decide_link(d.mark_type, scores, config=config)
                retained = [s for s in sorted(scores, key=lambda s: (-s.link_score, -s.amt))
                            if s.link_score >= config.link.candidate_min_score or s.amt > 0]
                reason = LINKED_REASON if entry_id else (BETWEEN_ROWS_REASON if retained else NO_CANDIDATE_REASON)
            candidates.extend(MarkCandidate(mark_id, s.entry_id, rank, s.link_score, s.v, s.c, s.rc, s.col, s.amt)
                              for rank, s in enumerate(retained, start=1))
            marks.append(PenMark(
                claim_id=claim_id, input_revision=input_revision, provenance=provenance, versions=versions,
                mark_id=mark_id, page_id=page_id, box_norm=d.box_norm, mark_type=d.mark_type,
                detection_confidence=d.score, entry_id=entry_id, candidate_entry_ids=[s.entry_id for s in retained],
                link_reason=reason, state="pending", origin="detector", model_entry_id=entry_id, rule_id=rule_id,
                trocr_suggestion=d.trocr_suggestion))

    conflicting = conflicting_entry_ids(marks, max_marks_per_row=config.row.max_marks_per_row)
    counts = {
        "exclusion": sum(m.mark_type == "exclusion" for m in marks),
        "price_change": sum(m.mark_type == "price_change" for m in marks),
        "linked": sum(m.entry_id is not None for m in marks),
        "unlinked": sum(m.entry_id is None for m in marks),
        "conflicting": len(conflicting),
    }
    return LinkingResult(tuple(marks), tuple(candidates), tuple(merged), below, detection_index, conflicting, counts,
                         config.link_config_version)


def link_marks(
    detections: Sequence[Detection | Mapping[str, Any]],
    rows: Sequence[Any],
    *,
    config: LinkingConfig,
    job_key: str,
    claim_id: str,
    input_revision: int,
    provenance: Provenance,
    versions: Mapping[str, str],
    page_widths_px: Mapping[str, int] | None = None,
) -> list[PenMark]:
    """Pending ``PenMark`` records for ``detections``; see ``link_detections`` for the details."""
    return list(link_detections(detections, rows, config=config, job_key=job_key, claim_id=claim_id,
                                input_revision=input_revision, provenance=provenance, versions=versions,
                                page_widths_px=page_widths_px).marks)
