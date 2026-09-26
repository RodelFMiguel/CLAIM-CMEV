"""Shared builders for the M6 tests (imported by name; pytest prepends this directory).

Geometry is written in corrected-render pixels on a 2480 x 3508 page, as in the M6
specification's worked example, then normalised. Every record is a fixture.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from claim_cmev.contracts.common import COST_BASIS, Provenance, make_job_key
from claim_cmev.contracts.documents import LineItem, PenMark, effective_price_for
from claim_cmev.documents.pen_marks import Detection, link_detections, load_linking_config, row_mark_states

W, H = 2480, 3508
CLAIM = "01JAX7Q0VN4Z3K9F2M8R6T1C5D"
PROV = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="cmev-worker-penmarks")
API_PROV = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="cmev-api")
DETECTOR_VERSIONS = {"penmark_model": "fixture-penmarks/0.0.0", "code": "0.2.0"}
HUMAN_VERSIONS = {"review_api": "fixture-api/0.0.0", "code": "0.2.0"}
ACTOR = "surveyor:fixture"
T0 = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
CONFIG = load_linking_config()
JOB_KEY = make_job_key(CLAIM, 1, "pen_marks_detect", {**DETECTOR_VERSIONS, "link_config": CONFIG.link_config_version})


def norm(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return (x0 / W, y0 / H, x1 / W, y1 / H)


def line_item(entry_id: str, top: float, bottom: float, *, page_id: str = "dp1", page_number: int = 1,
              printed: str | None = "480.00", amount_box: bool = True, currency: str = "SGD",
              inset: float = 4) -> LineItem:
    """A row spanning x 620..2300 with its printed amount at x 2040..2290, ``inset`` px inside the row."""
    uncertain = []
    if printed is None:
        uncertain.append({"field": "printed_line_amount", "reason": "ocr_letter_digit_confusion"})
    if not amount_box:
        uncertain.append({"field": "amount_box_norm", "reason": "column_not_located"})
    return LineItem(
        claim_id=CLAIM, input_revision=1, provenance=PROV, versions={"parser_config": "fixture-parser/0.0.0"},
        entry_id=entry_id, page_id=page_id, page_number=page_number, row_box_norm=norm((620, top, 2300, bottom)),
        amount_box_norm=norm((2040, top + inset, 2290, bottom - inset)) if amount_box else None,
        original_part_text="FRT BUMPER", original_operation_text="REPLACE", original_amount_text=printed,
        part_code="front-bumper", part_mapping_status="resolved", side="not_applicable", side_source="document_text",
        operation="replace", operation_mapping_status="resolved", quantity="1", unit_price=printed,
        printed_line_amount=printed, effective_price=printed,
        effective_price_source="printed" if printed else "unresolved",
        effective_price_reason=None if printed else "amount_unreadable", currency=currency, cost_basis=COST_BASIS,
        field_uncertainty=uncertain)


def ruled_rows(count: int = 4, *, page_id: str = "dp1", top: float = 1000, pitch: float = 60, height: float = 54,
               prefix: str = "e", page_number: int = 1) -> list[LineItem]:
    """Evenly spaced rows: row i spans [top + i*pitch, top + i*pitch + height]."""
    return [line_item(f"{prefix}{i}", top + i * pitch, top + i * pitch + height, page_id=page_id,
                      page_number=page_number, printed=f"{100 * (i + 1)}.00") for i in range(count)]


def worked_example_rows() -> list[LineItem]:
    """Rows e-003, e-004 and e-005 of the M6 specification worked example.

    Row boxes are exact; the e-004 and e-005 amount tokens are exact, the e-003 token
    (spec [2040,1300,2290,1342]) is approximated by a 4 px inset, which detection A covers anyway.
    """
    return [line_item("e-003", 1296, 1354, printed="620.00", inset=4),
            line_item("e-004", 1354, 1412, printed="860.00", inset=8),
            line_item("e-005", 1420, 1478, printed="1150.00", inset=8)]


def det(box_px: tuple[float, float, float, float], mark_type: str = "exclusion", score: float = 0.9,
        page_id: str = "dp1") -> Detection:
    return Detection(page_id=page_id, box_norm=norm(box_px), mark_type=mark_type, score=score)


def link(detections, rows, **kw):
    kw.setdefault("page_widths_px", {"dp1": W, "dp2": W})
    return link_detections(detections, rows, config=kw.pop("config", CONFIG), job_key=JOB_KEY, claim_id=CLAIM,
                           input_revision=1, provenance=PROV, versions=DETECTOR_VERSIONS, **kw)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def assert_no_printed_fallback(rows: list[LineItem], marks: list[PenMark]) -> None:
    """The safety invariant: an unresolved price change never yields the printed amount.

    For every row: a pending linked price change, a conflicting row, or an unlinked
    non-rejected price change naming the row leaves the effective price null and
    unresolved. A confirmed price change yields exactly the typed amount.
    """
    states = row_mark_states(rows, marks, max_marks_per_row=CONFIG.row.max_marks_per_row)
    for row in rows:
        price = effective_price_for(row.printed_line_amount, marks, entry_id=row.entry_id)
        linked_pc = [m for m in marks if m.entry_id == row.entry_id and m.mark_type == "price_change"
                     and m.state != "rejected"]
        unlinked_pc = [m for m in marks if m.entry_id is None and m.mark_type == "price_change"
                       and m.state != "rejected" and row.entry_id in m.candidate_entry_ids]
        unresolved = (any(m.state == "pending" for m in linked_pc) or len(linked_pc) > 1 or unlinked_pc
                      or (linked_pc and states[row.entry_id].state == "conflicting"))
        if unresolved:
            assert price.effective_price is None, (row.entry_id, price)
            assert price.effective_price_source == "unresolved"
            assert price.reason in {"price_change_pending", "mark_conflicting", "price_change_unlinked"}
        elif linked_pc:
            (mark,) = linked_pc
            assert mark.state == "confirmed" and price.effective_price == mark.confirmed_amount
            assert price.effective_price_source == "surveyor_entry"
        if price.effective_price_source == "printed":
            assert not linked_pc and not unlinked_pc, (row.entry_id, price)
