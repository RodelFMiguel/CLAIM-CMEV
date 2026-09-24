"""M5 ``parse_pages``: declared repair rows from M4 text boxes, steps 1 to 7 of the spec.

Pure and deterministic: contract ``DocumentPage`` records in, validated ``LineItem`` and
``DeclarationCompleteness`` records out, no I/O. Uncertain rows are kept and flagged,
never dropped; a missing value is null with a reason, never zero; a missing quantity is
never one; the printed amount is never recomputed or corrected; zero rows is never an
empty estimate. Entry ids derive from the job key plus the page, the row band index and
the row's source text, so a retry on unchanged pages reproduces them.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal

from claim_cmev.contracts.common import ContractError, Provenance, deterministic_id
from claim_cmev.contracts.documents import (
    DeclarationCompleteness,
    DocumentPage,
    FieldUncertainty,
    LineItem,
    effective_price_for,
)

from .completeness import CompletenessFacts, decide_completeness
from .config import FamilySpec, LayoutFamilyConfig
from .table import (
    Band,
    Box,
    FamilyDecision,
    HeaderMatch,
    RawRow,
    Segment,
    detect_family,
    group_bands,
    is_header,
    match_header,
    page_boxes,
    segment_page,
    union_box,
)
from .text import normalise_text, starts_with_phrase
from .values import ParsedValue, parse_decimal
from .vocabulary import EstimateVocabulary, MappingResult, SideResult

M5_CODE_VERSION = "m5-line-items/0.1.0"
ENGINE = "parser"
TASK = "line_items_extract"
RowKind = Literal["item", "heading", "total", "tax", "repeated_header", "uncertain"]
FieldStatus = Literal["resolved", "uncertain", "missing"]
LINE_ITEM_KINDS = frozenset({"item", "uncertain"})
ITEM_FIELD = {"description": "part_code", "operation": "operation", "qty": "quantity",
              "unit_price": "unit_price", "amount": "printed_line_amount"}
_SOURCE_RANK = {"real": 0, "synthetic": 1, "fixture": 2, "explainer": 3}
WHOLE_PAGE = (0.0, 0.0, 1.0, 1.0)


@dataclass(frozen=True)
class MissingPage:
    """A page M4 was asked for but could not render: it has no ``DocumentPage``."""

    page_id: str
    page_number: int
    reason: str = "page_not_rendered"


@dataclass(frozen=True)
class FieldResult:
    """One ``line_item_field`` row: parsed value, original text, source boxes, status, reason."""

    field: str  # description | operation | qty | unit_price | amount
    value: str | None  # mapped code or exact decimal string
    original_text: str
    source_box_ids: tuple[str, ...]
    status: FieldStatus
    reason: str | None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedRow:
    """Every classified table row, including the non-item rows excluded from line items."""

    entry_id: str
    page_id: str
    page_number: int
    row_band_index: int
    band_indexes: tuple[int, ...]
    row_kind: RowKind
    layout_family: str
    original_text: str
    row_box_norm: tuple[float, float, float, float]
    source_box_ids: tuple[str, ...]
    flags: tuple[str, ...]
    fields: tuple[FieldResult, ...]
    label: str = ""

    def field(self, name: str) -> FieldResult | None:
        return next((f for f in self.fields if f.field == name), None)


@dataclass(frozen=True)
class UnparsedRegion:
    """Visible content not turned into rows, for manual reading in M9."""

    page_id: str
    page_number: int
    reason: str
    box_norm: tuple[float, float, float, float] | None
    box_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageParse:
    page_id: str
    page_number: int
    page_state: Literal["complete", "partial", "unreadable", "missing"]
    status: Literal["parsed", "unreadable", "missing", "unsupported_layout", "ambiguous_layout_family",
                    "continuation_without_header", "page_without_table"]
    layout_family: str | None
    layout_family_reason: str | None  # unsupported_layout | ambiguous_layout_family, readable pages only
    reasons: tuple[str, ...] = ()  # the page's own reasons (M4 quality reasons, or why it was not parsed)
    header_band_indexes: tuple[int, ...] = ()
    terminated: bool = False
    family_scores: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseResult:
    line_items: tuple[LineItem, ...]
    completeness: DeclarationCompleteness
    unparsed_regions: tuple[UnparsedRegion, ...]
    layout_family: str | None
    rows: tuple[ParsedRow, ...]
    pages: tuple[PageParse, ...]
    versions: dict[str, str]

    @property
    def line_item_ids(self) -> tuple[str, ...]:
        return tuple(item.entry_id for item in self.line_items)


# ---------------------------------------------------------------------------- inputs
def merged_versions(requested: Mapping[str, str], config: LayoutFamilyConfig,
                    vocabulary: EstimateVocabulary) -> dict[str, str]:
    """Request versions verbatim plus the rules actually used; a conflicting pin is refused."""
    actual = {"layout_families": config.config_version, "vocabulary": vocabulary.version,
              "parser_code": M5_CODE_VERSION, "extraction_method": ENGINE, "taxonomy": vocabulary.taxonomy_version}
    for key, value in requested.items():
        if key in actual and value != actual[key]:
            raise ContractError("version_mismatch", f"request pins {key}={value!r} but this parser runs {actual[key]!r}")
    return {**requested, **actual}


def entry_id_for(job_key: str, page_id: str, row_band_index: int, source_texts: Sequence[str]) -> str:
    """Spec: short hash of the page, the row band index and the normalised source text, under the job key."""
    text = "|".join(" ".join(t.split()) for t in source_texts)
    return deterministic_id("li", job_key, page_id, row_band_index, text)


def _check_inputs(pages: Sequence[DocumentPage], missing: Sequence[MissingPage], job_key: str, claim_id: str,
                  input_revision: int, provenance: Provenance) -> None:
    if not job_key.startswith(f"{claim_id}:{input_revision}:"):
        raise ContractError("job_key_mismatch", "the job key names another claim or input revision")
    ids = [p.page_id for p in pages] + [m.page_id for m in missing]
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate_page", "each page is parsed once")
    for page in pages:
        if page.claim_id != claim_id:
            raise ContractError("page_claim_mismatch", f"page {page.page_id} belongs to another claim")
        if page.input_revision > input_revision:
            raise ContractError("page_revision_mismatch", f"page {page.page_id} is from a later input revision")
        if _SOURCE_RANK[page.provenance.source_kind] > _SOURCE_RANK[provenance.source_kind]:
            raise ContractError("provenance_mismatch", f"page {page.page_id} is {page.provenance.source_kind}; "
                                f"line items cannot be labelled {provenance.source_kind}")


def _ordered(pages: Sequence[DocumentPage]) -> list[DocumentPage]:
    """Files in order of first appearance, pages by page number within a file."""
    first = {}
    for n, page in enumerate(pages):
        first.setdefault(page.file_id, n)
    return sorted(pages, key=lambda p: (first[p.file_id], p.page_number, p.page_id))


# ---------------------------------------------------------------------------- rows
def _join(boxes: Sequence[Box]) -> str:
    return boxes[0].text if len(boxes) == 1 else " ".join(b.text for b in boxes)


def _min_confidence(boxes: Sequence[Box]) -> float | None:
    values = [b.confidence for b in boxes if b.confidence is not None]
    return min(values) if values else None


@dataclass
class _Builder:
    config: LayoutFamilyConfig
    vocabulary: EstimateVocabulary
    job_key: str
    claim_id: str
    input_revision: int
    currency: str
    cost_basis: str
    versions: dict[str, str]
    provenance: Provenance

    def _low_confidence(self, boxes: Sequence[Box]) -> bool:
        confidence = _min_confidence(boxes)
        return confidence is not None and confidence < self.config.parser.min_box_confidence

    def numeric(self, family: FamilySpec, name: str, boxes: Sequence[Box], located: frozenset[str],
                affected: Mapping[str, str]) -> tuple[FieldResult, ParsedValue | None]:
        ids = tuple(b.box_id for b in boxes)
        text = _join(boxes) if boxes else ""
        if name not in located:
            return FieldResult(name, None, "", (), "missing", "column_not_located"), None
        if name in affected:
            return FieldResult(name, None, text, ids, "uncertain", affected[name]), None
        if not boxes:
            return FieldResult(name, None, "", (), "missing", "value_missing"), None
        parser, numbers = self.config.parser, family.numbers
        parsed = parse_decimal(
            text, decimal_separator=numbers.decimal_separator, thousands_separator=numbers.thousands_separator,
            max_decimal_places=family.decimal_places(parser),
            currency_markers={} if name == "qty" else family.currency_markers,
            units=numbers.quantity_units if name == "qty" else (), confidence=_min_confidence(boxes),
            min_confidence=parser.min_box_confidence, expected_currency=self.currency)
        status: FieldStatus = "resolved" if parsed.value is not None else "uncertain"
        return FieldResult(name, parsed.value, text, ids, status, parsed.reason), parsed

    def row(self, raw: RawRow, page: DocumentPage, family: FamilySpec) -> tuple[ParsedRow, LineItem | None]:
        cells: dict[str, list[Box]] = {}
        affected: dict[str, str] = {}
        for a in raw.assignments:
            cells.setdefault(a.field, []).append(a.box)  # type: ignore[arg-type]
            for name in a.affected:
                affected.setdefault(name, "spanning_box" if a.status == "spanning" else "overlapping_columns")
        located = raw.binding.fields
        boxes = raw.boxes
        texts = [b.text for b in boxes]
        entry_id = entry_id_for(self.job_key, page.page_id, raw.band_index, texts)
        x0, x1 = raw.binding.header_extent
        bx0, by0, bx1, by1 = union_box(boxes)
        row_box = (min(x0, bx0), by0, max(x1, bx1), by1)
        flags = set(raw.flags)

        if raw.kind in ("total", "tax", "heading"):
            amount, _ = self.numeric(family, "amount", cells.get("amount", []), located, affected)
            fields = (FieldResult("description", None, raw.label, (), "resolved" if raw.label else "missing", None),
                      amount)
            parsed = ParsedRow(entry_id, page.page_id, page.page_number, raw.band_index,
                               tuple(b.index for b in raw.bands), raw.kind, family.family_id, " ".join(texts),
                               row_box, tuple(b.box_id for b in boxes), tuple(sorted(flags)), fields, raw.label)
            return parsed, None

        uncertainty: list[tuple[str, str]] = []

        def flag_field(name: str, reason: str | None) -> None:
            if reason and (name, reason) not in uncertainty:
                uncertainty.append((name, reason))

        # step 4: description -> part and printed side
        desc_boxes = cells.get("description", [])
        desc_text = _join(desc_boxes) if desc_boxes else ""
        desc_ids = tuple(b.box_id for b in desc_boxes)
        if "description" in affected:
            reason = affected["description"]
            part, side = MappingResult(None, "unmapped", reason), SideResult("unknown", "absent", reason)
            description = FieldResult("description", None, desc_text, desc_ids, "uncertain", reason)
            flag_field("original_part_text", reason)
        elif not desc_boxes:
            part = MappingResult(None, "unmapped", "value_missing")
            side = SideResult("unknown", "absent", "side_absent_in_text")
            description = FieldResult("description", None, "", (), "missing", "value_missing")
            flag_field("original_part_text", "value_missing")
        else:
            part, side = self.vocabulary.map_part(desc_text)
            if self._low_confidence(desc_boxes):  # a low-confidence read never resolves a part or a side
                if part.code is not None:
                    part = MappingResult(None, "ambiguous", "ocr_low_confidence", part.candidates, part.matched_alias)
                if side.side != "unknown":
                    side = SideResult("unknown", "absent", "ocr_low_confidence", side.text)
            description = FieldResult("description", part.code, desc_text, desc_ids,
                                      "resolved" if part.code else "uncertain", part.reason, part.candidates)
        if raw.kind == "continuation":
            flag_field("original_part_text", "unlinked_continuation")
        flag_field("part_code", part.reason if part.code is None else None)
        if side.side == "unknown" and (side.reason != "side_absent_in_text" or self.vocabulary.is_sided(part.code)):
            flag_field("side", side.reason)

        op_boxes = cells.get("operation", [])
        op_text = _join(op_boxes) if op_boxes else ""
        op_ids = tuple(b.box_id for b in op_boxes)
        if "operation" not in located:
            op = MappingResult(None, "unmapped", "column_not_located")
            operation = FieldResult("operation", None, "", (), "missing", op.reason)
        elif "operation" in affected:
            op = MappingResult(None, "unmapped", affected["operation"])
            operation = FieldResult("operation", None, op_text, op_ids, "uncertain", op.reason)
        elif not op_boxes:
            op = MappingResult(None, "unmapped", "value_missing")
            operation = FieldResult("operation", None, "", (), "missing", op.reason)
        else:
            op = self.vocabulary.map_operation(op_text)
            if op.code is not None and self._low_confidence(op_boxes):
                op = MappingResult(None, "ambiguous", "ocr_low_confidence", op.candidates, op.matched_alias)
            operation = FieldResult("operation", op.code, op_text, op_ids, "resolved" if op.code else "uncertain",
                                    op.reason, op.candidates)
        flag_field("operation", op.reason if op.code is None else None)

        # step 5: exact values
        qty, _ = self.numeric(family, "qty", cells.get("qty", []), located, affected)
        unit, _ = self.numeric(family, "unit_price", cells.get("unit_price", []), located, affected)
        amount_boxes = cells.get("amount", [])
        amount, _ = self.numeric(family, "amount", amount_boxes, located, affected)
        for result in (qty, unit, amount):
            if result.value is None:
                flag_field(ITEM_FIELD[result.field], result.reason)

        # step 6: validate, never correct
        if qty.value is not None and unit.value is not None and amount.value is not None:
            product = Decimal(qty.value) * Decimal(unit.value)
            if abs(product - Decimal(amount.value)) > self.config.parser.amount_tolerance:
                flags.add("arithmetic_mismatch")
                flag_field("printed_line_amount", "arithmetic_mismatch")
                amount = replace(amount, status="uncertain", reason="arithmetic_mismatch")
        fields = (description, operation, qty, unit, amount)
        satisfied = {
            "description": bool(desc_boxes) and "description" not in affected,
            "operation": bool(op_boxes) and "operation" not in affected,
            "qty": qty.value is not None,
            "unit_price": unit.value is not None,
            "amount": amount.status == "resolved",
        }
        required_ok = all(satisfied.get(name, True) for name in family.header.required_columns)
        kind: RowKind = "item" if raw.kind == "item" and required_ok else "uncertain"
        if not required_ok:
            flags.add("uncertain_required_field")

        if amount_boxes and "amount" not in affected and "amount" in located:
            amount_box = union_box(amount_boxes)
        else:
            amount_box = None
            flag_field("amount_box_norm", affected.get("amount") or
                       ("column_not_located" if "amount" not in located else "value_missing"))
        confidence = {ITEM_FIELD[name]: c for name, bxs in cells.items() if name in ITEM_FIELD
                      and (c := _min_confidence(bxs)) is not None}
        price = effective_price_for(amount.value, [])
        item = LineItem(
            claim_id=self.claim_id, input_revision=self.input_revision, provenance=self.provenance,
            versions=self.versions, entry_id=entry_id, page_id=page.page_id, page_number=page.page_number,
            row_box_norm=row_box, amount_box_norm=amount_box, original_part_text=desc_text,
            original_operation_text=op_text, original_amount_text=amount.original_text or None,
            part_code=part.code, part_mapping_status=part.status, side=side.side, side_source=side.source,
            operation=op.code, operation_mapping_status=op.status, quantity=qty.value, unit_price=unit.value,
            printed_line_amount=amount.value, effective_price=price.effective_price,
            effective_price_source=price.effective_price_source, effective_price_reason=price.reason,
            currency=self.currency, cost_basis=self.cost_basis,
            field_uncertainty=[FieldUncertainty(field=f, reason=r) for f, r in uncertainty],
            field_confidence=confidence or None, entry_confidence=None, source_box_ids=[b.box_id for b in boxes])
        parsed = ParsedRow(entry_id, page.page_id, page.page_number, raw.band_index,
                           tuple(b.index for b in raw.bands), kind, family.family_id, " ".join(texts), row_box,
                           tuple(b.box_id for b in boxes), tuple(sorted(flags)), fields, raw.label)
        return parsed, item

    def header_row(self, page: DocumentPage, bands: Sequence[Band], header: HeaderMatch,
                   family: FamilySpec) -> ParsedRow:
        band = bands[header.band_index]
        texts = [b.text for b in band.boxes]
        return ParsedRow(entry_id_for(self.job_key, page.page_id, band.index, texts), page.page_id,
                         page.page_number, band.index, (band.index,), "repeated_header", family.family_id,
                         " ".join(texts), union_box(band.boxes), tuple(b.box_id for b in band.boxes), (), (),
                         normalise_text(" ".join(texts)))


# ---------------------------------------------------------------------------- pages
def _page_decision(boxes: list[Box], config: LayoutFamilyConfig) -> tuple[list[Band], FamilyDecision]:
    parser = config.parser
    bands = group_bands(boxes, parser.band_tolerance_frac)
    decision = detect_family(bands, config)
    if decision.family_id is None:
        return bands, decision
    family = config.family(decision.family_id)
    tolerance = family.band_tolerance(parser)
    if tolerance == parser.band_tolerance_frac:
        return bands, decision
    bands = group_bands(boxes, tolerance)  # the family's own row grouping, then its headers again
    headers = tuple(m for m in (match_header(b, family, parser.header_word_gap_frac) for b in bands)
                    if is_header(m, family, parser))
    if not headers:
        return bands, FamilyDecision(None, "unsupported_layout", (), decision.scores)
    return bands, replace(decision, headers=headers)


def _subtotal_check(rows: list[ParsedRow], config: LayoutFamilyConfig) -> tuple[list[ParsedRow], int]:
    """Compare each printed subtotal with the exact sum of the rows above it (not a correction)."""
    checked, span, mismatches = [], [], 0
    for row in rows:
        if row.row_kind in LINE_ITEM_KINDS:
            span.append(row)
        if row.row_kind not in ("total", "tax"):
            checked.append(row)
            continue
        family = config.family(row.layout_family)
        amount = row.field("amount")
        is_subtotal = row.row_kind == "total" and any(
            starts_with_phrase(row.label, normalise_text(label)) for label in family.subtotal_labels)
        flag = None
        if is_subtotal and amount is not None and amount.value is not None:
            values = [f.value for r in span if (f := r.field("amount")) is not None and f.status == "resolved"]
            if all(r.row_kind == "item" for r in span) and len(values) == len(span):
                total = sum((Decimal(v) for v in values), Decimal("0"))
                if abs(total - Decimal(amount.value)) > config.parser.amount_tolerance:
                    flag, mismatches = "subtotal_mismatch", mismatches + 1
                else:
                    flag = "subtotal_matched"
            else:
                flag = "subtotal_not_checked"
        checked.append(replace(row, flags=tuple(sorted({*row.flags, flag}))) if flag else row)
        span = []
    return checked, mismatches


def parse_pages(
    pages: Sequence[DocumentPage], *, config: LayoutFamilyConfig, vocabulary: EstimateVocabulary, job_key: str,
    claim_id: str, input_revision: int, currency: str, cost_basis: str, versions: Mapping[str, str],
    provenance: Provenance | Mapping[str, Any], missing_pages: Sequence[MissingPage] = (),
) -> ParseResult:
    """Parse every page of one claim input revision into line items and a completeness state.

    ``currency`` and ``cost_basis`` are the claim's (``ClaimInput``); the parser never
    assumes them. ``missing_pages`` are pages M4 could not render (no ``DocumentPage``);
    they count as unreadable pages. Raises ``ContractError`` for inconsistent inputs.
    """
    provenance = provenance if isinstance(provenance, Provenance) else Provenance.model_validate(provenance)
    _check_inputs(pages, missing_pages, job_key, claim_id, input_revision, provenance)
    builder = _Builder(config, vocabulary, job_key, claim_id, input_revision, currency, cost_basis,
                       merged_versions(versions, config, vocabulary), provenance)
    ordered = _ordered(pages)

    prepared: list[tuple[DocumentPage, list[Band], FamilyDecision | None]] = []
    for page in ordered:
        if page.quality.state == "unreadable" or not page.text_boxes:
            prepared.append((page, [], None))
        else:
            prepared.append((page, *_page_decision(page_boxes(page), config)))
    families = sorted({d.family_id for _, _, d in prepared if d is not None and d.family_id})
    if len(families) == 1:
        layout_family, layout_reason = families[0], None
    elif families:
        layout_family, layout_reason = None, "mixed_layout_families"
    else:
        ambiguous = any(d is not None and d.reason == "ambiguous_layout_family" for _, _, d in prepared)
        layout_family, layout_reason = None, "ambiguous_layout_family" if ambiguous else "unsupported_layout"

    regions: list[UnparsedRegion] = []
    page_parses: list[PageParse] = []
    rows: list[ParsedRow] = []
    items: list[LineItem] = []
    table_open = table_found = False
    first_header_seen = False
    counts = {"continuation_without_header": 0, "page_without_table": 0, "outside": 0, "uncertain": 0}
    for index, (page, bands, decision) in enumerate(prepared):
        all_ids = tuple(b.box_id for b in page.text_boxes)
        if decision is None:
            reasons = tuple(page.quality.reasons) or ("no_text_boxes",)
            regions.append(UnparsedRegion(page.page_id, page.page_number, "page_unreadable", WHOLE_PAGE, all_ids))
            page_parses.append(PageParse(page.page_id, page.page_number, "unreadable", "unreadable", None, None,
                                         reasons=reasons))
            continue
        if decision.family_id is None:
            if table_open:
                status = "continuation_without_header"
            elif families:
                status = "page_without_table"
            else:
                status = decision.reason
            if status in counts:
                counts[status] += 1
            regions.append(UnparsedRegion(page.page_id, page.page_number, status, WHOLE_PAGE, all_ids))
            page_parses.append(PageParse(page.page_id, page.page_number, page.quality.state, status, None,
                                         decision.reason, reasons=(status,), family_scores=dict(decision.scores)))
            continue
        family = config.family(decision.family_id)  # mixed families: each page parses with its own family
        segments: list[Segment] = segment_page(index, bands, decision.headers, family, config.parser)
        for segment in segments:
            if first_header_seen:
                rows.append(builder.header_row(page, bands, segment.header, family))
            first_header_seen = True
            for assignment in segment.outside:
                box = assignment.box
                regions.append(UnparsedRegion(page.page_id, page.page_number, "text_outside_columns",
                                              (box.x0, box.y0, box.x1, box.y1), (box.box_id,)))
                counts["outside"] += 1
            for raw in segment.rows:
                parsed, item = builder.row(raw, page, family)
                rows.append(parsed)
                if item is not None:
                    items.append(item)
                    counts["uncertain"] += parsed.row_kind == "uncertain"
        table_found = True
        table_open = not segments[-1].terminated
        page_parses.append(PageParse(page.page_id, page.page_number, page.quality.state, "parsed",
                                     family.family_id, None, tuple(page.quality.reasons),
                                     tuple(s.header.band_index for s in segments), segments[-1].terminated,
                                     dict(decision.scores)))

    for missing in missing_pages:
        regions.append(UnparsedRegion(missing.page_id, missing.page_number, missing.reason, None))
        page_parses.append(PageParse(missing.page_id, missing.page_number, "missing", "missing", None, None,
                                     reasons=(missing.reason,)))
    rows, mismatches = _subtotal_check(rows, config)
    facts = CompletenessFacts(
        page_count=len(ordered) + len(missing_pages),
        unreadable_pages=sum(d is None for _, _, d in prepared) + len(missing_pages),
        partial_pages=sum(p.quality.state == "partial" for p in ordered),
        layout_family=layout_family, layout_family_reason=layout_reason, table_found=table_found,
        table_terminated=table_found and not table_open,
        continuation_without_header=counts["continuation_without_header"],
        pages_without_table=counts["page_without_table"], uncertain_required_rows=counts["uncertain"],
        line_item_count=len(items), unparsed_table_regions=counts["outside"], subtotal_mismatches=mismatches)
    state, reasons = decide_completeness(facts)
    completeness = DeclarationCompleteness(
        claim_id=claim_id, input_revision=input_revision, provenance=provenance, versions=builder.versions,
        state=state, reasons=list(reasons), unparsed_region_count=len(regions), layout_family=layout_family,
        layout_family_reason=layout_reason,
        pages_covered=[p.page_id for p in page_parses if p.status == "parsed"], source="parser")
    return ParseResult(tuple(items), completeness, tuple(regions), layout_family, tuple(rows), tuple(page_parses),
                       dict(builder.versions))


# ---------------------------------------------------------------------------- outputs
def line_items_event_payload(result: ParseResult, engine: str = ENGINE) -> dict[str, Any]:
    """Payload of ``cmev.evt.line-items-extracted.v1`` (integration contracts section 5.4)."""
    summaries = []
    for item in result.line_items:
        summaries.append({
            "entry_id": item.entry_id, "page_id": item.page_id, "row_box_norm": list(item.row_box_norm),
            "part_code": item.part_code, "part_mapping_status": item.part_mapping_status, "side": item.side,
            "operation": item.operation, "operation_mapping_status": item.operation_mapping_status,
            "quantity": item.quantity, "unit_price": item.unit_price,
            "printed_line_amount": item.printed_line_amount, "currency": item.currency, "cost_basis": item.cost_basis,
            "field_uncertainty": [{"field": u.field, "reason": u.reason} for u in item.field_uncertainty],
        })
    completeness = result.completeness
    return {
        "line_item_ids": list(result.line_item_ids), "line_items": summaries, "layout_family": result.layout_family,
        "declaration_completeness": completeness.state, "completeness_reasons": list(completeness.reasons),
        "unparsed_region_count": completeness.unparsed_region_count, "engine": engine, "branch_stage": "line_items",
    }


def pen_mark_row_boxes(result: ParseResult) -> list[dict[str, Any]]:
    """``row_boxes`` for ``cmev.cmd.pen-marks-detect.v1``: every line item's row and amount box."""
    return [{"entry_id": i.entry_id, "page_id": i.page_id, "row_box_norm": list(i.row_box_norm),
             "amount_box_norm": None if i.amount_box_norm is None else list(i.amount_box_norm)}
            for i in result.line_items]
