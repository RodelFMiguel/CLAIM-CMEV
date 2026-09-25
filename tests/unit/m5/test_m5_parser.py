"""parse_pages end to end on fixed box fixtures: steps 4 to 7, completeness, identity and outputs."""
from datetime import UTC, datetime

import pytest

from claim_cmev.contracts.common import ContractError, Provenance, make_job_key
from claim_cmev.contracts.documents import DeclarationCompleteness, LineItem, PageQuality
from claim_cmev.contracts.events import Envelope
from claim_cmev.contracts.events.registry import validate_message
from claim_cmev.documents.line_items import (
    M5_CODE_VERSION,
    TASK,
    MissingPage,
    entry_id_for,
    line_items_event_payload,
    pen_mark_row_boxes,
)

from m5_support import (
    CLAIM_ID,
    CONFIG,
    JOB_KEY,
    PROVENANCE,
    VERSIONS,
    cell_box,
    estimate_cells,
    header_cells,
    item,
    make_page,
    parse,
    row_cells,
    uncertainty,
)

ROWS = [item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"),
        item("FRT BUMPER", "PAINT", "1", "420.00", "420.00"),
        item("FRT DOOR LH", "REPAIR", "1", "480.00", "480.00"),
        item("BONNET", "REPAIR", "1", "350.00", "350.00")]


def _items(result):
    return {i.original_part_text + "/" + i.original_operation_text: i for i in result.line_items}


# ------------------------------------------------------------------ the standard page
def test_standard_page_yields_validated_line_items_and_a_complete_declaration():
    result = parse([make_page(estimate_cells(ROWS))])
    assert result.layout_family == "family-a-ruled-grid"
    assert [r.row_kind for r in result.rows] == ["item", "item", "item", "item", "total"]
    items = _items(result)
    bumper = items["FRT BUMPER/REPLACE"]
    assert isinstance(bumper, LineItem) and bumper.provenance.source_kind == "fixture"
    assert (bumper.part_code, bumper.part_mapping_status, bumper.operation) == ("front-bumper", "resolved", "replace")
    assert (bumper.quantity, bumper.unit_price, bumper.printed_line_amount) == ("1", "980.00", "980.00")
    assert (bumper.effective_price, bumper.effective_price_source, bumper.effective_price_reason) == (
        "980.00", "printed", None)
    assert (bumper.currency, bumper.cost_basis) == ("SGD", "single_part_pre_tax_no_discount_v1")
    assert (bumper.side, bumper.side_source) == ("not_applicable", "absent")
    assert bumper.field_uncertainty == []  # an unsided part without a printed side is not flagged
    door = items["FRT DOOR LH/REPAIR"]
    assert (door.part_code, door.side, door.side_source) == ("front-door", "left", "document_text")
    assert door.field_confidence == {"part_code": 0.95, "operation": 0.95, "quantity": 0.95, "unit_price": 0.95,
                                     "printed_line_amount": 0.95}
    assert door.entry_confidence is None  # the parser supplies no row score of its own
    decl = result.completeness
    assert isinstance(decl, DeclarationCompleteness)
    assert (decl.state, decl.reasons, decl.source, decl.unparsed_region_count) == ("complete", [], "parser", 0)
    assert decl.layout_family == "family-a-ruled-grid" and decl.pages_covered == ["dp-file-1-1"]
    assert "subtotal_matched" in result.rows[-1].flags


def test_boxes_are_normalised_on_the_corrected_render_for_m6_linking():
    result = parse([make_page(estimate_cells(ROWS))])
    bumper = result.line_items[0]
    assert bumper.amount_box_norm == (0.84, 200 / 1400, 0.9, 220 / 1400)  # "980.00", right aligned
    x0, y0, x1, y1 = bumper.row_box_norm
    assert (x0, x1) == (0.06, 0.9) and (y0, y1) == (200 / 1400, 220 / 1400)  # header extent, row height
    assert len(bumper.source_box_ids) == 5


def test_versions_record_every_rule_that_produced_a_row():
    result = parse([make_page(estimate_cells(ROWS))])
    expected = {"code": "test", "layout_families": CONFIG.config_version, "vocabulary": "m5-estimate-vocabulary/0.2.0",
                "parser_code": M5_CODE_VERSION, "extraction_method": "parser", "taxonomy": "parts-1.0.0"}
    assert result.versions == expected
    assert all(i.versions == expected for i in result.line_items) and result.completeness.versions == expected


def test_two_operations_on_one_part_are_both_kept():
    result = parse([make_page(estimate_cells(ROWS))])
    bumpers = [i for i in result.line_items if i.part_code == "front-bumper"]
    assert [b.operation for b in bumpers] == ["replace", "paint"]
    assert len({b.entry_id for b in bumpers}) == 2


def test_totals_tax_and_headings_are_excluded_from_line_items():
    cells = header_cells(160) + [("PARTS", (60, 200, 110, 220), 0.97)] + row_cells(ROWS[0], 240)
    cells += [("SUB TOTAL", (640, 290, 740, 310), 0.98), ("980.00", cell_box("amount", "980.00", 290), 0.98),
              ("GST 9%", (640, 330, 700, 350), 0.99), ("88.20", cell_box("amount", "88.20", 330), 0.99)]
    result = parse([make_page(cells)])
    assert [r.row_kind for r in result.rows] == ["heading", "item", "total", "tax"]
    assert len(result.line_items) == 1 and result.completeness.state == "complete"


def test_tax_line_terminates_the_table():
    cells = header_cells(160) + row_cells(ROWS[0], 200) + [("GST", (640, 250, 680, 270), 0.99),
                                                          ("88.20", cell_box("amount", "88.20", 250), 0.99)]
    result = parse([make_page(cells)])
    assert [r.row_kind for r in result.rows] == ["item", "tax"] and result.completeness.state == "complete"


# ------------------------------------------------------------------ uncertainty kept, never dropped
def test_worked_example_letter_digit_confusion_keeps_the_row_with_a_null_amount():
    rows = [ROWS[0], item("FRT DOOR LH", "REPAIR", "1", "", "48O.OO")]
    cells = header_cells(160) + row_cells(rows[0], 200) + row_cells(rows[1], 240, low=("amount",))
    cells += [("SUB TOTAL", (640, 290, 740, 310), 0.98)]
    result = parse([make_page(cells)])
    door = result.line_items[1]
    assert (door.part_code, door.side, door.side_source) == ("front-door", "left", "document_text")
    assert door.printed_line_amount is None and door.original_amount_text == "48O.OO"
    assert (door.effective_price, door.effective_price_source, door.effective_price_reason) == (
        None, "unresolved", "amount_unreadable")
    assert uncertainty(door)["printed_line_amount"] == ["ocr_letter_digit_confusion"]
    assert door.amount_box_norm is not None  # M6 can still link a pen mark over the obscured amount
    assert result.rows[1].row_kind == "uncertain"
    assert (result.completeness.state, result.completeness.reasons) == ("partial", ["uncertain_required_field"])


def test_missing_values_stay_null_never_zero_and_quantity_is_never_one():
    row = item("BONNET", "REPAIR", "", "", "")
    cells = header_cells(160) + row_cells(row, 200) + [("SUB TOTAL", (640, 250, 740, 270), 0.98)]
    result = parse([make_page(cells)])
    [bonnet] = result.line_items
    assert (bonnet.quantity, bonnet.unit_price, bonnet.printed_line_amount, bonnet.effective_price) == (
        None, None, None, None)
    reasons = uncertainty(bonnet)
    assert reasons["quantity"] == ["value_missing"] and reasons["printed_line_amount"] == ["value_missing"]
    assert reasons["amount_box_norm"] == ["value_missing"]
    assert result.rows[0].row_kind == "uncertain" and result.completeness.state == "partial"


def test_column_absent_from_the_header_is_column_not_located():
    headers = {"description": "DESCRIPTION", "operation": "OPERATION", "qty": "QTY", "amount": "AMOUNT"}
    rows = [item("FRT BUMPER", "REPLACE", "1", "", "980.00")]
    result = parse([make_page(estimate_cells(rows, headers=headers))])
    [bumper] = result.line_items
    assert bumper.unit_price is None and uncertainty(bumper)["unit_price"] == ["column_not_located"]
    assert result.completeness.state == "complete"  # unit price is not a required field


def test_arithmetic_mismatch_is_flagged_and_nothing_is_recomputed():
    rows = [item("FRT BUMPER", "REPLACE", "2", "400.00", "980.00")]
    result = parse([make_page(estimate_cells(rows))])
    [bumper] = result.line_items
    assert (bumper.quantity, bumper.unit_price, bumper.printed_line_amount) == ("2", "400.00", "980.00")
    assert uncertainty(bumper)["printed_line_amount"] == ["arithmetic_mismatch"]
    assert "arithmetic_mismatch" in result.rows[0].flags and result.rows[0].row_kind == "uncertain"
    assert "uncertain_required_field" in result.completeness.reasons


def test_subtotal_mismatch_catches_a_confident_misread_without_correcting_it():
    rows = [item("FRT BUMPER", "REPLACE", "1", "", "980.00"), item("FRT BUMPER", "PAINT", "1", "", "20.00")]
    result = parse([make_page(estimate_cells(rows, subtotal="1400.00"))])
    assert [i.printed_line_amount for i in result.line_items] == ["980.00", "20.00"]
    assert "subtotal_mismatch" in result.rows[-1].flags
    assert (result.completeness.state, result.completeness.reasons) == ("partial", ["subtotal_mismatch"])


def test_spanning_box_is_flagged_and_its_fields_left_for_manual_correction():
    cells = header_cells(160) + [("FRT BUMPER", (60, 200, 160, 220), 0.95), ("REPLACE", (420, 200, 490, 220), 0.95),
                                 ("1 980.00 980.00", (575, 200, 900, 220), 0.95)]
    cells += [("SUB TOTAL", (640, 250, 740, 270), 0.98)]
    result = parse([make_page(cells)])
    [bumper] = result.line_items
    assert (bumper.part_code, bumper.operation) == ("front-bumper", "replace")
    assert (bumper.quantity, bumper.unit_price, bumper.printed_line_amount) == (None, None, None)
    reasons = uncertainty(bumper)
    assert reasons["quantity"] == reasons["unit_price"] == reasons["printed_line_amount"] == ["spanning_box"]
    assert reasons["amount_box_norm"] == ["spanning_box"]
    assert "spanning_box" in result.rows[0].flags and result.rows[0].row_kind == "uncertain"
    assert "1 980.00 980.00" in result.rows[0].original_text


def test_unlinked_continuation_is_published_as_an_uncertain_row():
    cells = header_cells(160) + row_cells(ROWS[0], 200) + [("ASSEMBLY", (60, 240, 140, 256), 0.95)]
    cells += [("SUB TOTAL", (640, 290, 740, 310), 0.98)]
    result = parse([make_page(cells)])
    assert [r.row_kind for r in result.rows] == ["item", "uncertain", "total"]
    orphan = result.line_items[1]
    assert orphan.original_part_text == "ASSEMBLY"
    assert "unlinked_continuation" in uncertainty(orphan)["original_part_text"]
    assert result.completeness.state == "partial"


def test_wrapped_description_joins_the_row_text():
    cells = header_cells(160) + row_cells(item("FRONT BUMPER COVER", "REPLACE", "1", "", "980.00"), 200)
    cells += [("ASSEMBLY", (60, 222, 140, 238), 0.95), ("SUB TOTAL", (640, 270, 740, 290), 0.98),
              ("980.00", cell_box("amount", "980.00", 270), 0.98)]
    result = parse([make_page(cells)])
    [bumper] = result.line_items
    assert bumper.original_part_text == "FRONT BUMPER COVER ASSEMBLY" and bumper.part_code == "front-bumper"
    assert result.rows[0].band_indexes == (1, 2) and result.completeness.state == "complete"


def test_currency_conflict_is_withheld_not_converted():
    rows = [item("FRT BUMPER", "REPLACE", "1", "", "USD 980.00")]
    result = parse([make_page(estimate_cells(rows, subtotal=None))])
    [bumper] = result.line_items
    assert bumper.printed_line_amount is None and bumper.currency == "SGD"
    assert uncertainty(bumper)["printed_line_amount"] == ["non_numeric_text"]
    s_dollar = parse([make_page(estimate_cells([item("BONNET", "REPAIR", "1", "", "S$ 350.00")], subtotal=None))])
    assert s_dollar.line_items[0].printed_line_amount == "350.00"


def test_low_confidence_text_never_resolves_a_part_side_or_operation():
    row = item("FRT DOOR LH", "REPAIR", "1", "", "480.00")
    cells = header_cells(160) + row_cells(row, 200, low=("description", "operation"))
    cells += [("SUB TOTAL", (640, 250, 740, 270), 0.98), ("480.00", cell_box("amount", "480.00", 250), 0.98)]
    [door] = parse([make_page(cells)]).line_items
    assert (door.part_code, door.part_mapping_status, door.side, door.side_source) == (
        None, "ambiguous", "unknown", "absent")
    assert (door.operation, door.operation_mapping_status) == (None, "ambiguous")
    reasons = uncertainty(door)
    assert reasons["part_code"] == reasons["side"] == reasons["operation"] == ["ocr_low_confidence"]
    assert door.original_part_text == "FRT DOOR LH"  # the text is kept for the surveyor


def test_sided_part_without_a_printed_side_is_flagged():
    result = parse([make_page(estimate_cells([item("FRT DOOR", "REPAIR", "1", "", "480.00")]))])
    assert uncertainty(result.line_items[0])["side"] == ["side_absent_in_text"]


def test_word_granularity_page_parses_the_same_rows():
    words = [("DESCRIPTION", (60, 160, 170, 180), 0.99), ("OPERATION", (420, 160, 510, 180), 0.99),
             ("QTY", (580, 160, 610, 180), 0.99), ("UNIT", (660, 160, 700, 180), 0.99),
             ("PRICE", (705, 160, 755, 180), 0.99), ("AMOUNT", (840, 160, 900, 180), 0.99),
             ("FRT", (60, 200, 90, 220), 0.95), ("BUMPER", (95, 200, 155, 220), 0.95),
             ("REPLACE", (420, 200, 490, 220), 0.95), ("1", (600, 200, 610, 220), 0.95),
             ("980.00", (700, 200, 760, 220), 0.95), ("S$", (815, 200, 835, 220), 0.95),
             ("980.00", (840, 200, 900, 220), 0.95), ("SUB", (640, 250, 670, 270), 0.95),
             ("TOTAL", (675, 250, 725, 270), 0.95), ("980.00", (840, 250, 900, 270), 0.95)]
    result = parse([make_page(words, granularity="word")])
    [bumper] = result.line_items
    assert (bumper.original_part_text, bumper.part_code) == ("FRT BUMPER", "front-bumper")
    assert (bumper.unit_price, bumper.printed_line_amount, bumper.original_amount_text) == (
        "980.00", "980.00", "S$ 980.00")
    assert result.completeness.state == "complete" and "subtotal_matched" in result.rows[-1].flags


def test_header_anchored_mode_reports_text_outside_the_columns():
    anchored = CONFIG.with_overrides({"families": [
        CONFIG.family("family-a-ruled-grid").model_dump(mode="python") | {"column_binding": {"mode": "header_anchored"}}]})
    cells = estimate_cells([item("FRONT BUMPER REINFORCEMENT BAR", "REPLACE", "1", "", "980.00")])
    result = parse([make_page(cells)], config=anchored)
    assert any(r.reason == "text_outside_columns" for r in result.unparsed_regions)
    assert "unparsed_table_text" in result.completeness.reasons
    gutter = parse([make_page(cells)])
    assert gutter.line_items[0].original_part_text == "FRONT BUMPER REINFORCEMENT BAR"


# ------------------------------------------------------------------ pages and completeness
def test_repeated_header_across_pages_restarts_binding_and_keeps_every_row():
    page1 = make_page(estimate_cells(ROWS[:2], subtotal=None), page_number=1)
    shifted = {"description": (40, 300), "operation": (330, 450), "qty": (480, 520), "unit_price": (560, 680),
               "amount": (720, 830)}
    page2 = make_page(estimate_cells(ROWS[2:], cols=shifted, subtotal="2230.00"), page_number=2)
    result = parse([page2, page1])  # delivery order does not matter
    assert [r.row_kind for r in result.rows] == ["item", "item", "repeated_header", "item", "item", "total"]
    assert [i.page_number for i in result.line_items] == [1, 1, 2, 2]
    assert "subtotal_matched" in result.rows[-1].flags  # summed across both pages
    decl = result.completeness
    assert (decl.state, decl.pages_covered) == ("complete", ["dp-file-1-1", "dp-file-1-2"])


def test_continuation_page_without_a_header_is_partial():
    page1 = make_page(estimate_cells(ROWS[:2], subtotal=None), page_number=1)
    page2 = make_page(row_cells(ROWS[2], 200) + [("SUB TOTAL", (640, 250, 740, 270), 0.98)], page_number=2)
    result = parse([page1, page2])
    assert "continuation_without_header" in result.completeness.reasons
    assert [p.status for p in result.pages] == ["parsed", "continuation_without_header"]
    assert result.unparsed_regions[0].reason == "continuation_without_header"
    assert len(result.line_items) == 2  # no positional guess on the headerless page


def test_table_that_never_terminates_is_partial():
    result = parse([make_page(estimate_cells(ROWS, subtotal=None))])
    assert (result.completeness.state, result.completeness.reasons) == ("partial", ["table_not_terminated"])


def test_unsupported_layout_emits_no_guessed_rows():
    cells = [("ITEM CODE", (60, 160, 150, 180), 0.99), ("PART NAME", (300, 160, 390, 180), 0.99),
             ("FRT BUMPER", (300, 200, 400, 220), 0.95), ("980.00", (840, 200, 900, 220), 0.95)]
    result = parse([make_page(cells)])
    assert result.line_items == () and result.layout_family is None
    decl = result.completeness
    assert (decl.state, decl.layout_family, decl.layout_family_reason) == ("partial", None, "unsupported_layout")
    assert decl.reasons == ["unsupported_layout", "no_rows_matched"]
    [region] = result.unparsed_regions
    assert (region.reason, region.box_norm, len(region.box_ids)) == ("unsupported_layout", (0.0, 0.0, 1.0, 1.0), 4)


def test_zero_rows_on_a_readable_page_is_partial_never_explicitly_empty():
    result = parse([make_page(header_cells(160) + [("SUB TOTAL", (640, 200, 740, 220), 0.98),
                                                   ("0.00", cell_box("amount", "0.00", 200), 0.98)])])
    assert result.line_items == ()
    assert (result.completeness.state, result.completeness.reasons) == ("partial", ["no_rows_matched"])
    assert result.completeness.state != "explicitly_empty"


def test_every_page_unreadable_is_unreadable():
    result = parse([make_page([], page_number=1), make_page([], page_number=2)])
    decl = result.completeness
    assert (decl.state, decl.reasons, decl.layout_family_reason) == (
        "unreadable", ["all_pages_unreadable"], "unsupported_layout")
    assert [r.reason for r in result.unparsed_regions] == ["page_unreadable", "page_unreadable"]
    assert result.line_items == ()


def test_an_unreadable_or_unrendered_continuation_page_makes_the_declaration_partial():
    page1 = make_page(estimate_cells(ROWS, subtotal=None), page_number=1)
    result = parse([page1, make_page([], page_number=2)])
    assert result.completeness.state == "partial" and "page_unreadable" in result.completeness.reasons
    missing = parse([page1], missing_pages=[MissingPage("dp-file-1-2", 2, "render_failed")])
    assert "page_unreadable" in missing.completeness.reasons
    assert missing.pages[-1].status == "missing" and missing.unparsed_regions[-1].reason == "render_failed"
    only_missing = parse([], missing_pages=[MissingPage("dp-file-1-1", 1)])
    assert only_missing.completeness.state == "unreadable"
    assert parse([]).completeness.reasons == ["no_pages"]


def test_partial_page_from_m4_makes_the_declaration_partial():
    page = make_page(estimate_cells(ROWS), state="partial", reasons=["low_confidence_fraction"])
    result = parse([page])
    assert (result.completeness.state, result.completeness.reasons) == ("partial", ["page_partial"])
    assert len(result.line_items) == 4


def test_a_page_outside_the_table_is_surfaced():
    result = parse([make_page(estimate_cells(ROWS), page_number=1),
                    make_page([("TERMS AND CONDITIONS", (60, 100, 300, 120), 0.99)], page_number=2)])
    assert "page_without_table" in result.completeness.reasons
    assert result.pages[1].status == "page_without_table"


# ------------------------------------------------------------------ identity and retries
def test_retry_on_unchanged_pages_reproduces_identical_records():
    pages = [make_page(estimate_cells(ROWS))]
    first, second = parse(pages), parse(pages)
    assert first.line_item_ids == second.line_item_ids
    assert [i.model_dump() for i in first.line_items] == [i.model_dump() for i in second.line_items]
    assert first.completeness.model_dump() == second.completeness.model_dump()
    assert first.rows == second.rows


def test_entry_ids_do_not_depend_on_box_delivery_order():
    cells = estimate_cells(ROWS)
    forward = parse([make_page(cells)])
    page = make_page(cells)
    shuffled = page.model_copy(update={"text_boxes": list(reversed(page.text_boxes))})
    assert parse([shuffled]).line_item_ids == forward.line_item_ids


def test_entry_id_is_the_job_key_page_band_and_row_text():
    result = parse([make_page(estimate_cells(ROWS))])
    bumper = result.line_items[0]
    texts = [t for t in ("FRT BUMPER", "REPLACE", "1", "980.00", "980.00")]
    assert bumper.entry_id == entry_id_for(JOB_KEY, bumper.page_id, result.rows[0].row_band_index, texts)
    other_job = make_job_key(CLAIM_ID, 1, TASK, {"code": "other"})
    rerun = parse([make_page(estimate_cells(ROWS))], job_key=other_job, versions={"code": "other"})
    assert rerun.line_item_ids[0] != bumper.entry_id


# ------------------------------------------------------------------ input checks
def test_conflicting_version_pin_is_refused():
    with pytest.raises(ContractError) as err:
        parse([make_page(estimate_cells(ROWS))], versions={"code": "test", "vocabulary": "old-vocab"})
    assert err.value.reason_code == "version_mismatch"


def test_inputs_from_another_claim_or_job_are_refused():
    page = make_page(estimate_cells(ROWS))
    with pytest.raises(ContractError, match="job_key_mismatch"):
        parse([page], job_key=make_job_key(CLAIM_ID, 2, TASK, VERSIONS))
    other = page.model_copy(update={"claim_id": "01J8Z3N4V6W8X0Y2Z4A6B8C0D3"})
    with pytest.raises(ContractError, match="page_claim_mismatch"):
        parse([other])
    with pytest.raises(ContractError, match="duplicate_page"):
        parse([page, page])


def test_fixture_pages_can_never_yield_real_line_items():
    real = Provenance(source_kind="real", runtime_profile="full", producer_service="cmev-worker-lineitems")
    with pytest.raises(ContractError, match="provenance_mismatch"):
        parse([make_page(estimate_cells(ROWS))], provenance=real)


def test_pages_reused_from_an_earlier_revision_are_accepted():
    result = parse([make_page(estimate_cells(ROWS))], input_revision=2,
                   job_key=make_job_key(CLAIM_ID, 2, TASK, VERSIONS))
    assert {i.input_revision for i in result.line_items} == {2}


# ------------------------------------------------------------------ outputs
def test_event_payload_validates_against_the_line_items_extracted_schema():
    result = parse([make_page(estimate_cells([ROWS[0], item("HD LAMP BRKT", "R/R", "", "", "135.00")]))])
    envelope = Envelope.build("cmev.evt.line-items-extracted.v1", claim_id=CLAIM_ID, input_revision=1, task=TASK,
                              versions=result.versions, provenance=PROVENANCE, trace_id="trace-m5-test",
                              occurred_at=datetime(2026, 9, 24, tzinfo=UTC))
    message = envelope.message(line_items_event_payload(result))
    validate_message("cmev.evt.line-items-extracted.v1", message)
    payload = message["payload"]
    assert payload["line_item_ids"] == list(result.line_item_ids)
    assert payload["declaration_completeness"] == result.completeness.state
    lamp = payload["line_items"][1]
    assert (lamp["part_code"], lamp["part_mapping_status"], lamp["operation_mapping_status"]) == (
        None, "ambiguous", "ambiguous")


def test_row_boxes_for_the_pen_mark_command():
    result = parse([make_page(estimate_cells(ROWS))])
    boxes = pen_mark_row_boxes(result)
    assert [b["entry_id"] for b in boxes] == list(result.line_item_ids)
    assert all(b["amount_box_norm"] is not None and len(b["row_box_norm"]) == 4 for b in boxes)


def test_unreadable_page_quality_is_respected_even_with_boxes_absent():
    quality = PageQuality(state="unreadable", reasons=["granularity_mismatch"])
    page = make_page([])
    page = page.model_copy(update={"quality": quality})
    result = parse([page])
    assert result.pages[0].reasons == ("granularity_mismatch",) and result.pages[0].status == "unreadable"
    assert result.completeness.state == "unreadable"
