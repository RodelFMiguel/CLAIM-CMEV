"""Regression cases for omitted declaration regions reported in review."""
import pytest
import yaml

from claim_cmev.documents.line_items import load_layout_families
from claim_cmev.documents.line_items.config import DEFAULT_LAYOUT_FAMILIES_PATH

from m5_support import CONFIG, header_cells, cell_box, row_cells, item, make_page, parse


def _label(text, top, amount=None, x0=640):
    cells = [(text, (x0, top, x0 + 10 * len(text), top + 20), 0.98)]
    if amount is not None:
        cells.append((amount, cell_box("amount", amount, top), 0.98))
    return cells


def _desc(text, top, amount=None):
    """A footer line printed in the description column, optionally with an amount-column number."""
    cells = [(text, cell_box("description", text, top), 0.97)]
    if amount is not None:
        cells.append((amount, cell_box("amount", amount, top), 0.97))
    return cells


def test_sections_after_subtotal_are_not_lost():
    # Section 1: PARTS, subtotal; Section 2: LABOUR with two more declared rows, subtotal, GST, TOTAL.
    cells = [("SYNTHETIC TEST WORKSHOP", (60, 60, 400, 90), 0.99)]
    cells += header_cells(160)
    cells += [("PARTS", cell_box("description", "PARTS", 200), 0.97)]
    cells += row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 240)
    cells += row_cells(item("HEADLAMP LH", "REPLACE", "1", "860.00", "860.00"), 280)
    cells += [("SUB TOTAL", (640, 320, 740, 340), 0.98), ("1840.00", cell_box("amount", "1840.00", 320), 0.98)]
    cells += [("LABOUR", cell_box("description", "LABOUR", 380), 0.97)]
    cells += row_cells(item("FRT DOOR LH", "REPAIR", "1", "480.00", "480.00"), 420)
    cells += row_cells(item("FENDER LH", "PAINT", "1", "350.00", "350.00"), 460)
    cells += [("SUB TOTAL", (640, 500, 740, 520), 0.98), ("830.00", cell_box("amount", "830.00", 500), 0.98)]
    cells += [("GST", (640, 540, 700, 560), 0.98), ("241.10", cell_box("amount", "241.10", 540), 0.98)]
    cells += [("TOTAL", (640, 580, 720, 600), 0.98), ("2911.10", cell_box("amount", "2911.10", 580), 0.98)]
    page = make_page(cells)
    r = parse([page])

    assert len(r.line_items) == 4
    assert {i.original_part_text for i in r.line_items} == {"FRT BUMPER", "HEADLAMP LH", "FRT DOOR LH", "FENDER LH"}
    assert r.completeness.state == "complete"
    assert r.completeness.unparsed_region_count == 0
    assert [x.row_kind for x in r.rows] == ["heading", "item", "item", "total", "heading", "item", "item", "total",
                                            "tax", "total"]


def test_unbound_prefix_on_continuation_page_withholds_completeness():
    p1 = header_cells(160)
    p1 += row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 200)
    p1 += row_cells(item("HEADLAMP LH", "REPLACE", "1", "860.00", "860.00"), 240)   # no total: table continues
    p2 = row_cells(item("FRT DOOR LH", "REPAIR", "1", "480.00", "480.00"), 100)     # continuation rows, no header yet
    p2 += row_cells(item("FENDER LH", "PAINT", "1", "350.00", "350.00"), 140)
    p2 += header_cells(220)                                                            # second table/section header
    p2 += row_cells(item("BONNET", "PAINT", "1", "300.00", "300.00"), 260)
    p2 += [("TOTAL", (640, 300, 720, 320), 0.98), ("2970.00", cell_box("amount", "2970.00", 300), 0.98)]
    r = parse([make_page(p1, page_number=1), make_page(p2, page_number=2)])

    assert r.completeness.state == "partial"
    assert "continuation_without_header" in r.completeness.reasons
    assert r.unparsed_regions and r.unparsed_regions[0].box_ids


# ------------------------------------------------------------------ continuation after a closed table
def _continuation_pages(p1_tail):
    p1 = header_cells(160)
    p1 += row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 200)
    p1 += row_cells(item("HEADLAMP LH", "REPLACE", "1", "860.00", "860.00"), 240)
    p1 += p1_tail
    p2 = row_cells(item("FRT DOOR LH", "REPAIR", "1", "480.00", "480.00"), 100)  # above page 2's header
    p2 += row_cells(item("FENDER LH", "PAINT", "1", "350.00", "350.00"), 140)
    p2 += header_cells(220)
    p2 += row_cells(item("BONNET", "PAINT", "1", "300.00", "300.00"), 260)
    p2 += _label("TOTAL", 300, "2970.00")
    return make_page(p1, page_number=1), make_page(p2, page_number=2)


@pytest.mark.parametrize("tail, reason", [
    ([], "continuation_without_header"),
    (_label("SUB TOTAL", 280, "1840.00"), "text_before_header"),
    (_label("GST", 280, "128.80"), "text_before_header"),
    (_label("TOTAL", 280, "1840.00"), "text_before_header"),
    (_label("SUB TOTAL", 280, "1840.00") + _label("GST", 320, "165.60") + _label("TOTAL", 360, "2005.60"),
     "text_before_header"),
], ids=["open", "sub_total", "gst", "total", "sub_total_gst_total"])
def test_rows_above_the_header_of_a_continuation_page_are_never_silently_dropped(tail, reason):
    page1, page2 = _continuation_pages(tail)
    r = parse([page1, page2])

    assert r.completeness.state == "partial" and reason in r.completeness.reasons
    [region] = r.unparsed_regions
    assert (region.page_id, region.reason) == (page2.page_id, reason)
    text = {b.box_id: b.text for b in page2.text_boxes}
    assert {text[i] for i in region.box_ids} >= {"FRT DOOR LH", "480.00", "FENDER LH", "350.00"}
    assert r.completeness.unparsed_region_count == 1
    # no positional guess: rows above the header are for manual reading, not line items
    assert [i.original_part_text for i in r.line_items] == ["FRT BUMPER", "HEADLAMP LH", "BONNET"]


def test_digit_free_letterhead_above_a_repeated_header_after_a_closed_table_is_page_furniture():
    p1 = header_cells(160) + row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 200)
    p1 += _label("SUB TOTAL", 240, "980.00")
    p2 = [("SYNTHETIC TEST WORKSHOP", (60, 60, 400, 90), 0.99)] + header_cells(160)
    p2 += row_cells(item("BONNET", "PAINT", "1", "300.00", "300.00"), 200) + _label("TOTAL", 240, "1280.00")
    r = parse([make_page(p1, page_number=1), make_page(p2, page_number=2)])
    assert (r.completeness.state, r.completeness.reasons) == ("complete", [])
    assert len(r.line_items) == 2


# ------------------------------------------------------------------ footer after the final TOTAL (N5)
def _footer_estimate(footer):
    cells = [("SYNTHETIC TEST WORKSHOP", (60, 60, 400, 90), 0.99)]
    cells += header_cells(160)
    cells += row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 200)
    cells += row_cells(item("BONNET", "PAINT", "1", "300.00", "300.00"), 240)
    cells += _label("TOTAL", 280, "1280.00")
    return make_page(cells + footer)


@pytest.mark.parametrize("footer, footer_rows", [
    ([], []),
    (_desc("LESS EXCESS", 340, "500.00"), ["LESS EXCESS"]),
    (_desc("AMOUNT PAYABLE BY INSURER", 340, "780.00"), ["AMOUNT PAYABLE BY INSURER"]),
    (_desc("LESS DEDUCTIBLE", 340, "300.00"), ["LESS DEDUCTIBLE"]),
    (_desc("AGREED SETTLEMENT", 340, "1200.00"), ["AGREED SETTLEMENT"]),
    (_desc("THANK YOU", 340), []),
    (_desc("LESS EXCESS", 340, "500.00") + _label("GST", 380, "0.00"), ["LESS EXCESS"]),
    (_desc("LESS EXCESS", 340, "500.00") + _desc("BALANCE PAYABLE", 380, "780.00"), ["LESS EXCESS", "BALANCE PAYABLE"]),
], ids=["none", "excess", "payable", "deductible", "settlement", "prose", "excess_then_gst", "excess_then_balance"])
def test_recognised_footer_lines_after_the_final_total_are_never_line_items(footer, footer_rows):
    r = parse([_footer_estimate(footer)])

    assert [(i.original_part_text, i.printed_line_amount) for i in r.line_items] == [
        ("FRT BUMPER", "980.00"), ("BONNET", "300.00")]
    assert (r.completeness.state, r.completeness.reasons, r.completeness.unparsed_region_count) == ("complete", [], 0)
    footers = [x for x in r.rows if x.row_kind == "footer"]
    assert [x.label for x in footers] == footer_rows
    assert all(x.field("amount").value is not None for x in footers)  # retained with the printed number
    assert r.pages[0].terminated


@pytest.mark.parametrize("footer", [
    row_cells(item("FOG LAMP LH", "REPLACE", "1", "90.00", "90.00"), 340),  # an item-shaped row past the total
    _desc("ADMIN CHARGE", 340, "45.00"),                                   # unrecognised label with an amount
    [("REF 2026/0915", (420, 340, 540, 360), 0.97)],                       # number in the operation column
], ids=["item_shaped", "unrecognised_label", "operation_column_number"])
def test_unrecognised_numeric_line_after_the_final_total_is_an_unparsed_region(footer):
    page = _footer_estimate(footer)
    r = parse([page])

    assert [i.original_part_text for i in r.line_items] == ["FRT BUMPER", "BONNET"]
    [region] = r.unparsed_regions
    assert region.reason == "text_after_final_total"
    assert set(region.box_ids) == {b.box_id for b in page.text_boxes[-len(footer):]}
    assert (r.completeness.state, r.completeness.reasons) == ("partial", ["text_after_final_total"])
    assert r.completeness.unparsed_region_count == 1


def test_footer_line_after_an_intermediate_subtotal_is_not_an_item_and_later_sections_are_kept():
    cells = header_cells(160)
    cells += row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 200)
    cells += _label("SUB TOTAL", 240, "980.00") + _desc("LESS EXCESS", 280, "500.00")
    cells += [("LABOUR", cell_box("description", "LABOUR", 320), 0.97)]
    cells += row_cells(item("FRT DOOR LH", "REPAIR", "1", "480.00", "480.00"), 360)
    cells += _label("SUB TOTAL", 400, "480.00") + _label("TOTAL", 440, "1460.00")
    r = parse([make_page(cells)])
    assert [i.original_part_text for i in r.line_items] == ["FRT BUMPER", "FRT DOOR LH"]
    assert [x.row_kind for x in r.rows] == ["item", "total", "footer", "heading", "item", "total", "total"]
    assert (r.completeness.state, r.completeness.reasons) == ("complete", [])


def test_mid_table_discount_does_not_end_the_declaration():
    cells = header_cells(160)
    cells += row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 200)
    cells += _label("LESS DISCOUNT", 240, "50.00")
    cells += row_cells(item("HEADLAMP LH", "REPLACE", "1", "860.00", "860.00"), 280)
    cells += row_cells(item("BONNET", "PAINT", "1", "300.00", "300.00"), 320)
    cells += _label("TOTAL", 360, "2090.00")
    r = parse([make_page(cells)])
    assert [i.original_part_text for i in r.line_items] == ["FRT BUMPER", "HEADLAMP LH", "BONNET"]
    assert [x.row_kind for x in r.rows] == ["item", "total", "item", "item", "total"]
    assert (r.completeness.state, r.completeness.reasons) == ("complete", [])


@pytest.mark.parametrize("section_total", ["TOTAL PARTS", "TOTAL"])
def test_items_in_a_later_section_are_kept_after_a_section_total(section_total):
    # "TOTAL PARTS" is not a closing total; after a closing "TOTAL" a section heading reopens it.
    cells = header_cells(160)
    cells += row_cells(item("FRT BUMPER", "REPLACE", "1", "980.00", "980.00"), 200)
    cells += _label(section_total, 240, "980.00")
    cells += [("LABOUR", cell_box("description", "LABOUR", 280), 0.97)]
    cells += row_cells(item("FRT DOOR LH", "REPAIR", "1", "480.00", "480.00"), 320)
    cells += _label("GRAND TOTAL", 360, "1460.00")
    r = parse([make_page(cells)])
    assert [i.original_part_text for i in r.line_items] == ["FRT BUMPER", "FRT DOOR LH"]
    assert (r.completeness.state, r.completeness.reasons, r.completeness.unparsed_region_count) == ("complete", [], 0)


def test_final_total_labels_allow_only_numbers_or_a_currency_marker_after_the_label():
    from claim_cmev.documents.line_items.table import is_final_total

    family = CONFIG.family("family-a-ruled-grid")
    assert is_final_total("TOTAL", family) and is_final_total("GRAND TOTAL (S$)", family)
    assert is_final_total("NETT TOTAL SGD", family)
    assert not is_final_total("TOTAL PARTS", family) and not is_final_total("SUB TOTAL", family)
    assert not is_final_total("LESS DISCOUNT", family)


def test_a_final_total_label_must_be_a_total_row_pattern(tmp_path):
    data = yaml.safe_load(DEFAULT_LAYOUT_FAMILIES_PATH.read_text(encoding="utf-8"))
    data["families"][0]["final_total_labels"] = ["AMOUNT PAYABLE"]
    path = tmp_path / "families.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="final total label"):
        load_layout_families(path)
