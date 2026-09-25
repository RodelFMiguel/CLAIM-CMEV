"""Regression cases for omitted declaration regions reported in review."""
from m5_support import header_cells, cell_box, row_cells, item, make_page, parse

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
