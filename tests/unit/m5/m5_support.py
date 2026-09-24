"""Fixed box fixtures for the M5 parser tests. Every page here is hand-built test material."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from claim_cmev.contracts.common import ArtifactRef, Provenance, make_job_key
from claim_cmev.contracts.documents import DocumentPage, PageQuality, PageTransform, TextBox
from claim_cmev.documents.line_items import (
    TASK,
    load_estimate_vocabulary,
    load_layout_families,
    parse_pages,
)

CLAIM_ID = "01J8Z3N4V6W8X0Y2Z4A6B8C0D2"  # ULID-shaped test claim id
VERSIONS = {"code": "test"}
JOB_KEY = make_job_key(CLAIM_ID, 1, TASK, VERSIONS)
PROVENANCE = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="cmev-worker-lineitems")
PAGE_PROVENANCE = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="cmev-worker-ocr")
BASIS = "single_part_pre_tax_no_discount_v1"
W, H = 1000, 1400  # corrected render, pixels

CONFIG = load_layout_families()
VOCABULARY = load_estimate_vocabulary()

# Family A column x ranges (pixels) for cells; numbers are right aligned.
COLS_A = {"description": (60, 380), "operation": (420, 540), "qty": (570, 610), "unit_price": (640, 760),
          "amount": (800, 900)}
HEADERS_A = {"description": "DESCRIPTION", "operation": "OPERATION", "qty": "QTY", "unit_price": "UNIT PRICE",
             "amount": "AMOUNT"}
NUMERIC = ("qty", "unit_price", "amount")

Cell = tuple  # (text, (x0, y0, x1, y1) in pixels, confidence)


def artifact(name: str) -> ArtifactRef:
    return ArtifactRef(artifact_id=name, object_uri=f"s3://fixture/{name}", sha256="0" * 64,
                       media_type="image/png", byte_count=1)


def cell_box(field: str, text: str, top: float, height: float = 20, cols: Mapping[str, tuple] = COLS_A) -> tuple:
    x0, x1 = cols[field]
    width = min(x1 - x0, 10 * max(1, len(text)))
    if field in NUMERIC:
        return (x1 - width, top, x1, top + height)
    return (x0, top, x0 + width, top + height)


def row_cells(values: Mapping[str, str], top: float, confidence: float = 0.95,
              cols: Mapping[str, tuple] = COLS_A, low: Sequence[str] = ()) -> list[Cell]:
    return [(text, cell_box(field, text, top, cols=cols), 0.41 if field in low else confidence)
            for field, text in values.items() if text]


def header_cells(top: float, headers: Mapping[str, str] = HEADERS_A, cols: Mapping[str, tuple] = COLS_A) -> list[Cell]:
    return row_cells(headers, top, 0.99, cols)


def item(description: str, operation: str = "REPLACE", qty: str = "1", unit: str = "", amount: str = "") -> dict:
    return {"description": description, "operation": operation, "qty": qty, "unit_price": unit, "amount": amount}


def estimate_cells(rows: Sequence[Mapping[str, str]], *, top: float = 200, pitch: float = 40,
                   subtotal: str | None = "auto", headers: Mapping[str, str] = HEADERS_A,
                   cols: Mapping[str, tuple] = COLS_A, title: bool = True) -> list[Cell]:
    cells: list[Cell] = [("SYNTHETIC TEST WORKSHOP", (60, 60, 400, 90), 0.99)] if title else []
    cells += header_cells(top - pitch, headers, cols)
    for r, row in enumerate(rows):
        cells += row_cells(row, top + r * pitch, cols=cols)
    if subtotal is not None:
        y = top + len(rows) * pitch + 10
        if subtotal == "auto":
            from decimal import Decimal
            subtotal = str(sum((Decimal(r["amount"]) for r in rows if r.get("amount")), Decimal("0")))
        cells.append(("SUB TOTAL", (640, y, 740, y + 20), 0.98))
        if subtotal:
            cells.append((subtotal, cell_box("amount", subtotal, y, cols=cols), 0.98))
    return cells


def make_page(cells: Sequence[Cell], *, page_number: int = 1, file_id: str = "file-1", state: str = "complete",
              reasons: Sequence[str] = (), granularity: str = "line", page_id: str | None = None,
              provenance: Provenance = PAGE_PROVENANCE, input_revision: int = 1) -> DocumentPage:
    page_id = page_id or f"dp-{file_id}-{page_number}"
    boxes = []
    for i, cell in enumerate(cells):
        text, (x0, y0, x1, y1), confidence = cell
        quad = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        boxes.append(TextBox(box_id=f"{page_id}-bx{i:03d}", order_index=i, text=text, confidence=confidence,
                             confidence_reason=None if confidence is not None else "engine_supplies_no_confidence",
                             box_norm=(x0 / W, y0 / H, x1 / W, y1 / H), quad_rectified=quad, quad_original=quad,
                             granularity=granularity))
    if not boxes:
        state, reasons = "unreadable", tuple(reasons) or ("ocr_no_text",)
    return DocumentPage(
        claim_id=CLAIM_ID, input_revision=input_revision, provenance=provenance, versions={"ocr": "fixture"},
        page_id=page_id, file_id=file_id, page_number=page_number, corrected_render_ref=artifact(f"{page_id}.png"),
        page_reading_ref=artifact(f"{page_id}.json"),
        transform=PageTransform(source_width=W, source_height=H, corrected_width=W, corrected_height=H,
                                geometry_correction="none", correction_reason="flat_scan"),
        text_granularity=granularity if boxes else "line", text_boxes=boxes,
        mean_text_confidence=0.95 if boxes else None,
        mean_text_confidence_reason=None if boxes else "no_text_boxes",
        quality=PageQuality(state=state, reasons=list(reasons)))


def parse(pages: Sequence[DocumentPage], **overrides: Any):
    kwargs: dict[str, Any] = dict(config=CONFIG, vocabulary=VOCABULARY, job_key=JOB_KEY, claim_id=CLAIM_ID,
                                  input_revision=1, currency="SGD", cost_basis=BASIS, versions=VERSIONS,
                                  provenance=PROVENANCE)
    kwargs.update(overrides)
    return parse_pages(pages, **kwargs)


def uncertainty(item_record) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for u in item_record.field_uncertainty:
        out.setdefault(u.field, []).append(u.reason)
    return out
