"""Table geometry for M5 steps 1 to 3: bands, header detection, column binding, row segments.

Everything works in ``box_norm`` units on the corrected render, so tolerances are
fractions of the rectified page width (x) and height (y). Engine boxes are used exactly
as M4 returned them: a line box is never split into invented words, and a box spanning
several columns is flagged, not divided.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from claim_cmev.contracts.documents import DocumentPage

from .config import FamilySpec, LayoutFamilyConfig, ParserSettings
from .text import normalise_text, starts_with_phrase

AssignmentStatus = Literal["assigned", "spanning", "overlap", "outside"]
RawKind = Literal["item", "heading", "total", "tax", "repeated_header", "continuation"]


@dataclass(frozen=True)
class Box:
    box_id: str
    order_index: int
    text: str
    confidence: float | None
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def norm_text(self) -> str:
        return normalise_text(self.text)


def page_boxes(page: DocumentPage) -> list[Box]:
    return [Box(b.box_id, b.order_index, b.text, b.confidence, *b.box_norm) for b in page.text_boxes]


def union_box(boxes: Sequence[Box]) -> tuple[float, float, float, float]:
    return (min(b.x0 for b in boxes), min(b.y0 for b in boxes), max(b.x1 for b in boxes), max(b.y1 for b in boxes))


@dataclass(frozen=True)
class Band:
    """One candidate row: boxes whose centre y values chain within the band tolerance."""

    index: int
    boxes: tuple[Box, ...]  # left to right

    @property
    def y0(self) -> float:
        return min(b.y0 for b in self.boxes)

    @property
    def y1(self) -> float:
        return max(b.y1 for b in self.boxes)


def group_bands(boxes: Sequence[Box], band_tolerance: float) -> list[Band]:
    """Step 6: sort by centre y and cut wherever consecutive centres differ by more than the tolerance."""
    ordered = sorted(boxes, key=lambda b: (b.cy, b.x0, b.box_id))
    groups: list[list[Box]] = []
    previous: float | None = None
    for box in ordered:
        if previous is None or box.cy - previous > band_tolerance:
            groups.append([])
        groups[-1].append(box)
        previous = box.cy
    return [Band(i, tuple(sorted(g, key=lambda b: (b.x0, b.box_id)))) for i, g in enumerate(groups)]


# ------------------------------------------------------------------ step 1: header
@dataclass(frozen=True)
class HeaderColumn:
    field: str
    alias: str
    x0: float
    x1: float
    box_ids: tuple[str, ...]


@dataclass(frozen=True)
class HeaderMatch:
    family_id: str
    band_index: int
    columns: tuple[HeaderColumn, ...]  # left to right
    duplicate_fields: tuple[str, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(c.field for c in self.columns)


def match_header(band: Band, family: FamilySpec, word_gap: float) -> HeaderMatch:
    """Match header aliases in one band; adjacent word boxes may join into a multi-word alias."""
    table = {normalise_text(a): f for f, aliases in family.header.aliases.items() for a in aliases}
    longest = max(len(k.split()) for k in table)
    boxes = band.boxes
    found: list[HeaderColumn] = []
    i = 0
    while i < len(boxes):
        for j in range(min(len(boxes), i + longest), i, -1):
            run = boxes[i:j]
            if any(run[k + 1].x0 - run[k].x1 > word_gap for k in range(len(run) - 1)):
                continue
            key = normalise_text(" ".join(b.text for b in run))
            if key in table:
                found.append(HeaderColumn(table[key], key, min(b.x0 for b in run), max(b.x1 for b in run),
                                          tuple(b.box_id for b in run)))
                i = j
                break
        else:
            i += 1
    fields = [c.field for c in found]
    duplicates = tuple(sorted({f for f in fields if fields.count(f) > 1}))
    return HeaderMatch(family.family_id, band.index, tuple(found), duplicates)


def is_header(match: HeaderMatch, family: FamilySpec, parser: ParserSettings) -> bool:
    fields = set(match.fields)
    return (not match.duplicate_fields and len(fields) >= family.min_matched(parser)
            and set(family.header.required_columns) <= fields)


@dataclass(frozen=True)
class FamilyDecision:
    family_id: str | None
    reason: str | None  # unsupported_layout | ambiguous_layout_family when family_id is None
    headers: tuple[HeaderMatch, ...] = ()
    scores: dict[str, int] = field(default_factory=dict)


def detect_family(bands: Sequence[Band], config: LayoutFamilyConfig) -> FamilyDecision:
    """Keep the family with the most matched header columns; a tie is a failure, not a coin toss."""
    parser = config.parser
    scores: dict[str, int] = {}
    headers: dict[str, tuple[HeaderMatch, ...]] = {}
    for family in config.families:
        matches = tuple(m for m in (match_header(b, family, parser.header_word_gap_frac) for b in bands)
                        if is_header(m, family, parser))
        if matches:
            scores[family.family_id] = max(len(set(m.fields)) for m in matches)
            headers[family.family_id] = matches
    if not scores:
        return FamilyDecision(None, "unsupported_layout", (), scores)
    best = max(scores.values())
    winners = sorted(f for f, s in scores.items() if s == best)
    if len(winners) > 1:
        return FamilyDecision(None, "ambiguous_layout_family", (), scores)
    return FamilyDecision(winners[0], None, headers[winners[0]], scores)


# ------------------------------------------------------------------ step 3: columns
@dataclass(frozen=True)
class ColumnInterval:
    field: str
    x0: float
    x1: float
    header_x0: float
    header_x1: float


@dataclass(frozen=True)
class ColumnBinding:
    family_id: str
    mode: str
    header_band_index: int
    columns: tuple[ColumnInterval, ...]  # left to right
    tolerance: float
    overlapping: tuple[tuple[str, str], ...] = ()

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(c.field for c in self.columns)

    @property
    def header_extent(self) -> tuple[float, float]:
        return (min(c.header_x0 for c in self.columns), max(c.header_x1 for c in self.columns))


def bind_columns(header: HeaderMatch, mode: str, tolerance: float) -> ColumnBinding:
    """Column x intervals from the header boxes (step 3).

    ``header_anchored`` (spec): each header's x extent widened by ``tolerance``; widened
    intervals that overlap are recorded. ``gutter`` (proposed addition): contiguous
    columns whose boundary sits ``tolerance`` left of the next header's left edge (the
    midpoint of the gap when headers are closer than that), so a left-aligned
    description wider than its header word still binds; the outer columns reach the
    page edges. Overlapping header boxes are recorded in both modes.
    """
    cols = sorted(header.columns, key=lambda c: (c.x0, c.x1))
    overlapping = []
    intervals = []
    boundaries = []
    for left, right in zip(cols, cols[1:]):
        edge = right.x0 - tolerance
        boundaries.append(edge if edge >= left.x1 else (left.x1 + right.x0) / 2)
    for k, col in enumerate(cols):
        if mode == "header_anchored":
            x0, x1 = max(0.0, col.x0 - tolerance), min(1.0, col.x1 + tolerance)
        else:
            x0 = 0.0 if k == 0 else boundaries[k - 1]
            x1 = 1.0 if k == len(cols) - 1 else boundaries[k]
        intervals.append(ColumnInterval(col.field, x0, x1, col.x0, col.x1))
    for a, b in zip(intervals, intervals[1:]):
        if a.x1 > b.x0 or a.header_x1 > b.header_x0:
            overlapping.append((a.field, b.field))
    return ColumnBinding(header.family_id, mode, header.band_index, tuple(intervals), tolerance, tuple(overlapping))


@dataclass(frozen=True)
class BoxAssignment:
    box: Box
    status: AssignmentStatus
    field: str | None  # the column whose text receives this box (None when outside)
    affected: tuple[str, ...] = ()  # columns made uncertain by a spanning or overlap box


def _overlap(box: Box, col: ColumnInterval) -> float:
    return max(0.0, min(box.x1, col.x1) - max(box.x0, col.x0))


def assign_box(box: Box, binding: ColumnBinding) -> BoxAssignment:
    """Steps 8 and 9: the column containing the box centre; spanning boxes are flagged, never split."""
    containing = [c for c in binding.columns if c.x0 <= box.cx <= c.x1]
    if not containing:
        return BoxAssignment(box, "outside", None)
    if len(containing) > 1:
        return BoxAssignment(box, "overlap", containing[0].field, tuple(c.field for c in containing))
    home = containing[0]
    others = [c for c in binding.columns if c is not home and _overlap(box, c) > binding.tolerance]
    if others:
        affected = sorted([home, *others], key=lambda c: c.x0)
        return BoxAssignment(box, "spanning", affected[0].field, tuple(c.field for c in affected))
    return BoxAssignment(box, "assigned", home.field)


# ------------------------------------------------------------------ step 2 + classification
@dataclass(frozen=True)
class RawRow:
    """A classified table row before value parsing: one anchor band plus attached wraps."""

    page_index: int
    kind: RawKind
    bands: tuple[Band, ...]
    assignments: tuple[BoxAssignment, ...]
    binding: ColumnBinding
    flags: tuple[str, ...] = ()
    label: str = ""  # normalised text of the band's word boxes (row pattern matching)

    @property
    def band_index(self) -> int:
        return self.bands[0].index

    @property
    def boxes(self) -> tuple[Box, ...]:
        return tuple(a.box for a in self.assignments)


@dataclass(frozen=True)
class Segment:
    """One table run: a header band, its rows, and whether a terminator ended it."""

    page_index: int
    header: HeaderMatch
    binding: ColumnBinding
    rows: tuple[RawRow, ...]
    terminated: bool
    outside: tuple[BoxAssignment, ...] = ()  # in-table boxes bound to no column


def band_label(band: Band) -> str:
    """Normalised text of the boxes that contain a letter (numbers such as amounts excluded)."""
    return normalise_text(" ".join(b.text for b in band.boxes if any(c.isalpha() for c in b.text)))


def row_pattern_kind(label: str, family: FamilySpec) -> str | None:
    """``total`` or ``tax`` when the band label starts with a configured pattern."""
    for kind in ("total", "tax"):
        if any(starts_with_phrase(label, normalise_text(p)) for p in family.row_patterns.get(kind, ())):
            return kind
    return None


def is_heading(label: str, family: FamilySpec) -> bool:
    return label in {normalise_text(p) for p in family.row_patterns.get("heading", ())}


def segment_page(page_index: int, bands: Sequence[Band], headers: Sequence[HeaderMatch], family: FamilySpec,
                 parser: ParserSettings) -> list[Segment]:
    """Steps 1.4, 2.7 and the row classes of step 20 for one page of a matched family."""
    header_at = {h.band_index: h for h in headers}
    starts = sorted(header_at)
    wrap_gap = family.wrap_gap(parser)
    segments = []
    for n, start in enumerate(starts):
        header = header_at[start]
        binding = bind_columns(header, family.column_binding.mode, family.x_tolerance(parser))
        end = starts[n + 1] if n + 1 < len(starts) else len(bands)
        body = bands[start + 1:end]
        rows: list[RawRow] = []
        outside: list[BoxAssignment] = []
        terminated = False
        for k, band in enumerate(body):
            assignments = tuple(assign_box(b, binding) for b in band.boxes)
            outside.extend(a for a in assignments if a.status == "outside")
            placed = tuple(a for a in assignments if a.status != "outside")
            if not placed:
                continue  # text wholly outside the columns: an unparsed region, not a row
            label = band_label(band)
            kind = row_pattern_kind(label, family)
            flags = tuple(sorted({f for a in placed for f in _assignment_flags(a)} |
                                 ({"text_outside_columns"} if len(placed) < len(assignments) else set())))
            if kind in family.terminator_kinds:
                rows.append(RawRow(page_index, kind, (band,), placed, binding, flags, label))
                terminated = True
                continue
            description_only = all(a.status == "assigned" and a.field == "description" for a in placed)
            if description_only and is_heading(label, family):
                rows.append(RawRow(page_index, "heading", (band,), placed, binding, flags, label))
                continue
            if description_only and terminated:
                continue  # footer prose; later numeric item bands are still inspected
            if description_only:
                previous = rows[-1] if rows else None
                attach = (previous is not None and previous.kind == "item"
                          and previous.bands[-1].index == band.index - 1
                          and band.y0 - previous.bands[-1].y1 <= wrap_gap)
                if attach and k + 1 < len(body):
                    below = body[k + 1]
                    below_placed = [a for a in (assign_box(b, binding) for b in below.boxes) if a.status != "outside"]
                    below_is_item = (bool(below_placed) and row_pattern_kind(band_label(below), family) is None
                                     and not all(a.status == "assigned" and a.field == "description"
                                                 for a in below_placed))
                    if below_is_item and below.y0 - band.y1 <= wrap_gap:
                        attach = False  # equally close to the item row below: ambiguous, never guessed
                if attach:
                    rows[-1] = replace(previous, bands=(*previous.bands, band),
                                       assignments=(*previous.assignments, *placed),
                                       flags=tuple(sorted({*previous.flags, *flags, "wrapped_description"})))
                    continue
                rows.append(RawRow(page_index, "continuation", (band,), placed, binding,
                                   tuple(sorted({*flags, "unlinked_continuation"})), label))
                continue
            terminated = False  # a new item section requires its own terminator
            rows.append(RawRow(page_index, "item", (band,), placed, binding, flags, label))
        segments.append(Segment(page_index, header, binding, tuple(rows), terminated, tuple(outside)))
    return segments


def _assignment_flags(a: BoxAssignment) -> tuple[str, ...]:
    return {"spanning": ("spanning_box",), "overlap": ("overlapping_columns",)}.get(a.status, ())
