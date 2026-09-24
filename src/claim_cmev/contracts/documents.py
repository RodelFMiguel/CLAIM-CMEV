"""Document-branch records: pages (M4), line items and completeness (M5), pen marks (M6).

Data contracts section 7. The printed amount, the surveyor's effective price and any
TrOCR suggestion are separate fields; a pending price change never falls back to the
printed amount (``effective_price_for``).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, NamedTuple

from pydantic import Field, model_validator

from .common import (
    ArtifactRef,
    BoxNorm,
    ClaimRecord,
    Confidence,
    ContractModel,
    Currency,
    Identifier,
    MappingStatus,
    Money,
    Operation,
    PartCode,
    ReasonCode,
    RecordId,
    Revision,
    Side,
    UtcDatetime,
    require_reasons,
    to_decimal,
)

TextGranularity = Literal["word", "line", "block", "mixed"]
"""What the OCR engine actually returned. ``mixed`` is recorded, never repaired (M4 step 10)."""
PageState = Literal["complete", "partial", "unreadable"]
CompletenessState = Literal["complete", "partial", "unreadable", "explicitly_empty"]
EffectivePriceSource = Literal["printed", "surveyor_entry", "unresolved"]
MarkType = Literal["exclusion", "price_change"]
MarkState = Literal["pending", "confirmed", "rejected"]

Point = tuple[float, float]
Quad = tuple[Point, Point, Point, Point]
"""Four corner points in pixels, clockwise from top left."""
Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]

IDENTITY_MATRIX: Matrix3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _apply(matrix: Matrix3, x: float, y: float) -> Point:
    (a, b, c), (d, e, f), (g, h, i) = matrix
    w = g * x + h * y + i
    if w == 0:
        raise ValueError("point maps to infinity under the stored homography")
    return ((a * x + b * y + c) / w, (d * x + e * y + f) / w)


class PageTransform(ContractModel):
    """Geometry between the uploaded page (or PDF render) and the corrected render.

    ``homography`` maps source pixels to corrected pixels and ``homography_inverse``
    maps back. ``render_scale`` maps PDF points to source pixels (dpi / 72) and is
    null for an image upload. Boxes are normalised on the corrected render.
    """

    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    corrected_width: int = Field(gt=0)
    corrected_height: int = Field(gt=0)
    rotation_degrees: float = 0.0
    exif_orientation: int | None = Field(default=None, ge=1, le=8)
    render_scale: float | None = Field(default=None, gt=0)
    geometry_correction: Literal["perspective", "rotation", "none"]
    correction_reason: ReasonCode
    homography: Matrix3 = IDENTITY_MATRIX
    homography_inverse: Matrix3 = IDENTITY_MATRIX

    @property
    def perspective_applied(self) -> bool:
        return self.geometry_correction == "perspective"

    def corrected_to_source(self, x: float, y: float) -> Point:
        return _apply(self.homography_inverse, x, y)

    def source_to_corrected(self, x: float, y: float) -> Point:
        return _apply(self.homography, x, y)

    def corrected_to_pdf_points(self, x: float, y: float) -> Point:
        """Map a corrected-render pixel to original PDF points (one-based page, top-left origin)."""
        if self.render_scale is None:
            raise ValueError("page has no PDF render scale; it was uploaded as an image")
        sx, sy = self.corrected_to_source(x, y)
        return (sx / self.render_scale, sy / self.render_scale)

    def norm_box_to_source_quad(self, box: tuple[float, float, float, float]) -> Quad:
        """Corners of a normalised corrected-render box, mapped back to source pixels."""
        x0, y0, x1, y1 = box
        w, h = self.corrected_width, self.corrected_height
        corners = ((x0 * w, y0 * h), (x1 * w, y0 * h), (x1 * w, y1 * h), (x0 * w, y1 * h))
        return tuple(self.corrected_to_source(x, y) for x, y in corners)  # type: ignore[return-value]


class TextBox(ContractModel):
    """One located text region exactly as the engine returned it."""

    box_id: RecordId
    order_index: int = Field(ge=0)
    text: str
    confidence: Confidence | None
    confidence_reason: ReasonCode | None = None
    box_norm: BoxNorm
    quad_rectified: Quad
    quad_original: Quad
    granularity: TextGranularity
    flags: list[ReasonCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _null_confidence_has_reason(self) -> TextBox:
        require_reasons(self.__dict__, [("confidence", "confidence_reason")])
        return self


class PageQuality(ContractModel):
    state: PageState
    reasons: list[ReasonCode] = Field(default_factory=list)
    box_count: int | None = Field(default=None, ge=0)
    mean_confidence: Confidence | None = None
    low_confidence_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    sharpness: float | None = None
    clipped_fraction: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _non_complete_has_reason(self) -> PageQuality:
        if self.state != "complete" and not self.reasons:
            raise ValueError(f"page state {self.state!r} needs at least one reason")
        return self


class DocumentPage(ClaimRecord):
    """Data contracts section 7.1. One corrected, OCR-read estimate page."""

    page_id: RecordId
    file_id: RecordId
    page_number: int = Field(ge=1)
    corrected_render_ref: ArtifactRef
    page_reading_ref: ArtifactRef
    source_render_ref: ArtifactRef | None = None
    transform: PageTransform
    extraction_route: Literal["ocr", "embedded_text"] = "ocr"
    text_granularity: TextGranularity
    text_boxes: list[TextBox]
    mean_text_confidence: Confidence | None
    mean_text_confidence_reason: ReasonCode | None = None
    quality: PageQuality

    @model_validator(mode="after")
    def _consistent(self) -> DocumentPage:
        require_reasons(self.__dict__, [("mean_text_confidence", "mean_text_confidence_reason")])
        if not self.text_boxes and self.quality.state != "unreadable":
            raise ValueError("a page with no text boxes is unreadable, never an empty complete page")
        ids = [b.box_id for b in self.text_boxes]
        if len(ids) != len(set(ids)):
            raise ValueError("text box ids must be unique within a page")
        orders = [b.order_index for b in self.text_boxes]
        if len(orders) != len(set(orders)):
            raise ValueError("reading order indexes must be unique within a page")
        if self.text_granularity != "mixed":
            wrong = [b.box_id for b in self.text_boxes if b.granularity != self.text_granularity]
            if wrong:
                raise ValueError(f"boxes {wrong} contradict page granularity {self.text_granularity!r}")
        return self


class FieldUncertainty(ContractModel):
    field: str = Field(min_length=1)
    reason: ReasonCode


# A null in any of these LineItem fields must be explained by a field_uncertainty entry.
LINE_ITEM_REASONED_NULLS = ("part_code", "operation", "quantity", "unit_price", "printed_line_amount", "amount_box_norm")
LINE_ITEM_FIELDS = frozenset({
    "row_box_norm", "amount_box_norm", "original_part_text", "original_operation_text", "part_code", "side",
    "operation", "quantity", "unit_price", "printed_line_amount", "currency", "cost_basis", "page_id",
})


class LineItem(ClaimRecord):
    """Data contracts section 7.2, replacing v1 ``DeclaredRepairEntry``."""

    entry_id: RecordId
    page_id: RecordId
    page_number: int = Field(ge=1)
    row_box_norm: BoxNorm
    amount_box_norm: BoxNorm | None
    original_part_text: str
    original_operation_text: str
    original_amount_text: str | None = None
    part_code: PartCode | None
    part_mapping_status: MappingStatus
    side: Side
    side_source: Literal["document_text", "human_correction", "absent"]
    operation: Operation | None
    operation_mapping_status: MappingStatus
    quantity: Money | None
    unit_price: Money | None
    printed_line_amount: Money | None
    effective_price: Money | None
    effective_price_source: EffectivePriceSource
    effective_price_reason: ReasonCode | None = None
    currency: Currency
    cost_basis: Identifier
    field_uncertainty: list[FieldUncertainty]
    field_confidence: dict[str, Confidence] | None = None
    entry_confidence: Confidence | None = None
    source_box_ids: list[RecordId] = Field(default_factory=list)
    lineage: RecordId | None = None

    @model_validator(mode="after")
    def _rules(self) -> LineItem:
        uncertain = {u.field for u in self.field_uncertainty}
        unknown_fields = uncertain - LINE_ITEM_FIELDS
        if unknown_fields:
            raise ValueError(f"field_uncertainty names unknown fields {sorted(unknown_fields)}")
        for name in LINE_ITEM_REASONED_NULLS:
            if getattr(self, name) is None and name not in uncertain:
                raise ValueError(f"{name} is null without a field_uncertainty reason")
        if (self.part_code is not None) != (self.part_mapping_status == "resolved"):
            raise ValueError("part_code is present exactly when part_mapping_status is 'resolved'")
        if (self.operation is not None) != (self.operation_mapping_status == "resolved"):
            raise ValueError("operation is present exactly when operation_mapping_status is 'resolved'")
        if (self.side == "unknown") != (self.side_source == "absent"):
            raise ValueError("side is 'unknown' exactly when side_source is 'absent'; no side is inferred")
        if self.effective_price_source == "unresolved":
            if self.effective_price is not None:
                raise ValueError("an unresolved effective price must be null")
            if not self.effective_price_reason:
                raise ValueError("an unresolved effective price needs effective_price_reason")
        elif self.effective_price is None:
            raise ValueError(f"effective_price_source {self.effective_price_source!r} needs an amount")
        elif self.effective_price_reason is not None:
            raise ValueError("effective_price_reason is only recorded when the price is unresolved")
        if self.effective_price_source == "printed" and (
                self.printed_line_amount is None
                or to_decimal(self.effective_price) != to_decimal(self.printed_line_amount)):
            raise ValueError("a 'printed' effective price must equal the readable printed amount")
        return self


class DeclarationCompleteness(ClaimRecord):
    """Data contracts section 7.3. Zero parsed rows is never ``explicitly_empty``."""

    state: CompletenessState
    reasons: list[ReasonCode]
    unparsed_region_count: int = Field(ge=0)
    layout_family: Identifier | None
    layout_family_reason: ReasonCode | None = None
    pages_covered: list[RecordId] = Field(default_factory=list)
    source: Literal["parser", "human_confirmation"]
    confirmed_by: str | None = None
    confirmed_at: UtcDatetime | None = None
    review_revision: Revision | None = None

    @model_validator(mode="after")
    def _rules(self) -> DeclarationCompleteness:
        require_reasons(self.__dict__, [("layout_family", "layout_family_reason")])
        if self.state != "complete" and not self.reasons:
            raise ValueError(f"completeness {self.state!r} needs at least one reason")
        if self.state == "explicitly_empty" and self.source != "human_confirmation":
            raise ValueError("only a surveyor confirmation produces 'explicitly_empty', never the parser")
        human = (self.confirmed_by, self.confirmed_at, self.review_revision)
        if self.source == "human_confirmation" and any(v is None for v in human):
            raise ValueError("a human completeness confirmation needs confirmed_by, confirmed_at and review_revision")
        if self.source == "parser" and any(v is not None for v in human):
            raise ValueError("a parser completeness state carries no human confirmation fields")
        return self


class TrocrSuggestion(ContractModel):
    """Stretch S2 suggestion. Never copied into ``confirmed_amount`` and never read by M8."""

    text: str
    confidence: Confidence | None
    model_version: str | None = None


LINKED_REASONS = frozenset({"unambiguous_row_overlap", "human_link"})
UNLINKED_REASONS = frozenset({"mark_between_rows", "no_candidate_row", "rows_unavailable", "page_failed"})
NO_CANDIDATE_REASONS = frozenset({"no_candidate_row", "rows_unavailable"})


class PenMark(ClaimRecord):
    """Data contracts section 7.4, plus M6's ``model_entry_id`` and ``rule_id``."""

    mark_id: RecordId
    page_id: RecordId
    box_norm: BoxNorm
    mark_type: MarkType
    detection_confidence: Confidence | None
    entry_id: RecordId | None
    candidate_entry_ids: list[RecordId]
    link_reason: ReasonCode
    state: MarkState
    confirmed_amount: Money | None = None
    confirmed_currency: Currency | None = None
    confirmed_cost_basis: Identifier | None = None
    trocr_suggestion: TrocrSuggestion | None = None
    decision_action_id: RecordId | None = None
    decided_by: str | None = None
    decided_at: UtcDatetime | None = None
    review_revision: Revision | None = None
    origin: Literal["detector", "human_added"]
    model_entry_id: RecordId | None = None
    rule_id: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> PenMark:
        if self.entry_id is None:
            if self.link_reason in LINKED_REASONS:
                raise ValueError(f"link_reason {self.link_reason!r} claims a link but entry_id is null")
            if not self.candidate_entry_ids and self.link_reason not in NO_CANDIDATE_REASONS:
                raise ValueError("an unlinked mark keeps its candidate_entry_ids")
        elif self.link_reason in UNLINKED_REASONS:
            raise ValueError(f"link_reason {self.link_reason!r} contradicts a linked entry_id")
        if self.origin == "human_added":
            if self.state != "confirmed":
                raise ValueError("a human-added mark is stored confirmed")
            if self.detection_confidence is not None:
                raise ValueError("a human-added mark has no detection confidence")
        elif self.detection_confidence is None:
            raise ValueError("a detector mark carries its detection confidence")
        decision = (self.decision_action_id, self.decided_by, self.decided_at, self.review_revision)
        if self.state == "pending" and any(v is not None for v in decision):
            raise ValueError("a pending mark has no decision fields")
        if self.state != "pending" and any(v is None for v in decision):
            raise ValueError("a decided mark records decision_action_id, decided_by, decided_at and review_revision")
        amount = (self.confirmed_amount, self.confirmed_currency, self.confirmed_cost_basis)
        if any(v is not None for v in amount) and any(v is None for v in amount):
            raise ValueError("confirmed_amount, confirmed_currency and confirmed_cost_basis travel together")
        if self.mark_type == "price_change" and self.state == "confirmed" and self.confirmed_amount is None:
            raise ValueError("a confirmed price change requires an exact human-entered amount")
        if self.confirmed_amount is not None and not (self.mark_type == "price_change" and self.state == "confirmed"):
            raise ValueError("only a confirmed price change carries a confirmed amount")
        return self


class EffectivePrice(NamedTuple):
    effective_price: str | None
    effective_price_source: str
    reason: str | None


def effective_price_for(printed_line_amount: str | None, marks: Iterable[PenMark], *,
                        entry_id: str | None = None) -> EffectivePrice:
    """The v2 effective-price rule (data contracts section 7.2).

    ``marks`` are the row's linked marks. When ``entry_id`` is given, marks linked to
    other rows are ignored, and an unlinked price change that names this row as a
    candidate also leaves the price unresolved. Rejected marks never count. A pending
    price change never falls back to the printed amount.
    """
    linked, unlinked_candidates = [], []
    for mark in marks:
        if mark.state == "rejected":
            continue
        if entry_id is None or mark.entry_id == entry_id:
            linked.append(mark)
        elif mark.entry_id is None and entry_id in mark.candidate_entry_ids:
            unlinked_candidates.append(mark)
    price_changes = [m for m in linked if m.mark_type == "price_change"]
    exclusions = [m for m in linked if m.mark_type == "exclusion"]
    if len(price_changes) > 1 or (price_changes and exclusions):
        return EffectivePrice(None, "unresolved", "mark_conflicting")
    if price_changes:
        mark = price_changes[0]
        if mark.state == "pending":
            return EffectivePrice(None, "unresolved", "price_change_pending")
        return EffectivePrice(mark.confirmed_amount, "surveyor_entry", None)
    if any(m.mark_type == "price_change" for m in unlinked_candidates):
        return EffectivePrice(None, "unresolved", "price_change_unlinked")
    if printed_line_amount is None:
        return EffectivePrice(None, "unresolved", "amount_unreadable")
    return EffectivePrice(printed_line_amount, "printed", None)


def with_effective_price(item: LineItem, marks: Iterable[PenMark]) -> LineItem:
    """Return a validated copy of ``item`` with the effective price derived from ``marks``."""
    price = effective_price_for(item.printed_line_amount, marks, entry_id=item.entry_id)
    data = item.model_dump()
    data.update(effective_price=price.effective_price, effective_price_source=price.effective_price_source,
                effective_price_reason=price.reason)
    return LineItem.model_validate(data)
