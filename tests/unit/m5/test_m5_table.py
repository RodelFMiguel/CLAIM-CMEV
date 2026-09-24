"""Steps 1 to 3 on fixed boxes: bands, header detection, family choice, column binding, row segments."""
import pytest

from claim_cmev.documents.line_items.table import (
    Box,
    assign_box,
    bind_columns,
    detect_family,
    group_bands,
    match_header,
    page_boxes,
    segment_page,
)

from m5_support import CONFIG, H, estimate_cells, header_cells, item, make_page, row_cells

PARSER = CONFIG.parser
FAMILY_A = CONFIG.family("family-a-ruled-grid")


def _bands(cells, granularity="line"):
    return group_bands(page_boxes(make_page(cells, granularity=granularity)), PARSER.band_tolerance_frac)


def _box(x0, x1, y0=0.5, y1=0.51, text="X", box_id="b"):
    return Box(box_id, 0, text, 0.9, x0, y0, x1, y1)


# ------------------------------------------------------------------ step 2: bands
def test_bands_cut_where_centre_gap_exceeds_the_tolerance():
    tol_px = PARSER.band_tolerance_frac * H  # 8.4 px on a 1400 px page
    cells = [("A", (60, 100, 100, 120), 0.9), ("B", (400, 100 + tol_px - 1, 440, 120 + tol_px - 1), 0.9),
             ("C", (60, 100 + 2 * tol_px + 5, 100, 120 + 2 * tol_px + 5), 0.9)]
    bands = _bands(cells)
    assert [[b.text for b in band.boxes] for band in bands] == [["A", "B"], ["C"]]
    assert [band.index for band in bands] == [0, 1]


def test_band_membership_does_not_depend_on_box_order():
    cells = estimate_cells([item("FRT BUMPER", amount="980.00"), item("BONNET", "REPAIR", amount="350.00")])
    forward = _bands(cells)
    backward = _bands(list(reversed(cells)))
    assert [[b.text for b in band.boxes] for band in forward] == [[b.text for b in band.boxes] for band in backward]


# ------------------------------------------------------------------ step 1: header and family
def test_standard_grid_matches_family_a_with_every_column():
    bands = _bands(estimate_cells([item("FRT BUMPER", amount="980.00")]))
    decision = detect_family(bands, CONFIG)
    assert decision.family_id == "family-a-ruled-grid" and decision.reason is None
    assert decision.scores == {"family-a-ruled-grid": 5}
    [header] = decision.headers
    assert header.fields == ("description", "operation", "qty", "unit_price", "amount")


def test_word_boxes_join_into_a_multi_word_alias():
    words = [("DESCRIPTION", (60, 100, 170, 120), 0.99), ("OPERATION", (420, 100, 510, 120), 0.99),
             ("QTY", (580, 100, 610, 120), 0.99), ("UNIT", (660, 100, 700, 120), 0.99),
             ("PRICE", (705, 100, 755, 120), 0.99), ("AMOUNT", (840, 100, 900, 120), 0.99)]
    [band] = _bands(words, granularity="word")
    header = match_header(band, FAMILY_A, PARSER.header_word_gap_frac)
    assert header.fields == ("description", "operation", "qty", "unit_price", "amount")
    unit = header.columns[3]
    assert (unit.alias, unit.x0, unit.x1) == ("UNIT PRICE", 0.66, 0.755)


def test_a_line_box_is_never_split_to_find_header_words():
    [band] = _bands([("DESCRIPTION OPERATION QTY AMOUNT", (60, 100, 900, 120), 0.99)])
    assert match_header(band, FAMILY_A, PARSER.header_word_gap_frac).columns == ()
    assert detect_family([band], CONFIG).reason == "unsupported_layout"


def test_header_without_a_required_column_is_not_a_header():
    headers = {"description": "DESCRIPTION", "operation": "OPERATION", "qty": "QTY", "unit_price": "UNIT PRICE"}
    decision = detect_family(_bands(header_cells(100, headers)), CONFIG)
    assert (decision.family_id, decision.reason) == (None, "unsupported_layout")


def test_duplicate_header_column_is_refused():
    cells = header_cells(100) + [("AMOUNT", (700, 100, 760, 120), 0.99)]
    [band] = _bands(cells)
    match = match_header(band, FAMILY_A, PARSER.header_word_gap_frac)
    assert match.duplicate_fields == ("amount",)
    assert detect_family([band], CONFIG).family_id is None


def test_equal_scores_for_two_families_is_ambiguous_not_a_coin_toss():
    twin = FAMILY_A.model_dump(mode="python") | {"family_id": "family-z-twin"}
    config = CONFIG.with_overrides({"families": [FAMILY_A.model_dump(mode="python"), twin]})
    decision = detect_family(_bands(estimate_cells([item("FRT BUMPER", amount="1.00")])), config)
    assert (decision.family_id, decision.reason) == (None, "ambiguous_layout_family")
    assert decision.scores == {"family-a-ruled-grid": 5, "family-z-twin": 5}


@pytest.mark.parametrize("headers, family", [
    ({"line_no": "NO.", "description": "PARTICULARS", "operation": "ACTION", "qty": "QTY", "unit_price": "RATE",
      "amount": "AMT (S$)"}, "family-b-numbered-rate"),
    ({"description": "JOB DESCRIPTION", "operation": "WORK", "amount": "PRICE"}, "family-c-compact-quote"),
])
def test_other_proposed_families_are_detected(headers, family):
    cols = {"line_no": (20, 50), "description": (60, 380), "operation": (420, 540), "qty": (570, 610),
            "unit_price": (640, 760), "amount": (800, 900)}
    decision = detect_family(_bands(header_cells(100, headers, cols)), CONFIG)
    assert decision.family_id == family


# ------------------------------------------------------------------ step 3: columns
def test_gutter_columns_reach_the_next_header_minus_the_tolerance():
    [band] = _bands(header_cells(100))
    binding = bind_columns(match_header(band, FAMILY_A, 0.015), "gutter", 0.03)
    bounds = [(c.field, round(c.x0, 3), round(c.x1, 3)) for c in binding.columns]
    assert bounds == [("description", 0.0, 0.39), ("operation", 0.39, 0.55), ("qty", 0.55, 0.63),
                      ("unit_price", 0.63, 0.81), ("amount", 0.81, 1.0)]
    assert binding.overlapping == ()


def test_header_anchored_columns_widen_the_header_and_record_overlaps():
    [band] = _bands(header_cells(100))
    binding = bind_columns(match_header(band, FAMILY_A, 0.015), "header_anchored", 0.03)
    desc = binding.columns[0]
    assert (round(desc.x0, 3), round(desc.x1, 3)) == (0.03, 0.2)
    tight = [("DESCRIPTION", (60, 100, 170, 120), 0.99), ("OPERATION", (200, 100, 290, 120), 0.99),
             ("QTY", (580, 100, 610, 120), 0.99), ("AMOUNT", (840, 100, 900, 120), 0.99)]
    [band] = _bands(tight)
    tight_binding = bind_columns(match_header(band, FAMILY_A, 0.015), "header_anchored", 0.03)
    assert tight_binding.overlapping == (("description", "operation"),)


def test_box_goes_to_the_column_containing_its_centre():
    [band] = _bands(header_cells(100))
    binding = bind_columns(match_header(band, FAMILY_A, 0.015), "gutter", 0.03)
    assert assign_box(_box(0.06, 0.30), binding).field == "description"
    assert assign_box(_box(0.83, 0.90), binding).field == "amount"
    small_intrusion = assign_box(_box(0.06, 0.41), binding)  # 0.02 into operation: within tolerance
    assert (small_intrusion.status, small_intrusion.field) == ("assigned", "description")


def test_spanning_box_is_flagged_not_split():
    [band] = _bands(header_cells(100))
    binding = bind_columns(match_header(band, FAMILY_A, 0.015), "gutter", 0.03)
    merged = assign_box(_box(0.57, 0.90, text="1 980.00"), binding)  # qty, unit price and amount in one box
    assert merged.status == "spanning"
    assert merged.affected == ("qty", "unit_price", "amount") and merged.field == "qty"


def test_overlap_zone_and_outside_boxes_in_header_anchored_mode():
    tight = [("DESCRIPTION", (60, 100, 170, 120), 0.99), ("OPERATION", (200, 100, 290, 120), 0.99),
             ("QTY", (580, 100, 610, 120), 0.99), ("AMOUNT", (840, 100, 900, 120), 0.99)]
    [band] = _bands(tight)
    binding = bind_columns(match_header(band, FAMILY_A, 0.015), "header_anchored", 0.03)
    in_both = assign_box(_box(0.18, 0.195), binding)
    assert (in_both.status, in_both.affected) == ("overlap", ("description", "operation"))
    assert assign_box(_box(0.40, 0.45), binding).status == "outside"


# ------------------------------------------------------------------ segments and row classes
def _segments(cells):
    bands = _bands(cells)
    decision = detect_family(bands, CONFIG)
    return segment_page(0, bands, decision.headers, CONFIG.family(decision.family_id), PARSER)


def _kinds(segment):
    return [(row.kind, " ".join(b.text for b in row.boxes)) for row in segment.rows]


def test_terminator_ends_the_table_and_later_text_is_outside_it():
    cells = estimate_cells([item("FRT BUMPER", amount="980.00")], subtotal="980.00")
    cells += [("GST 9%", (640, 330, 700, 350), 0.99), ("88.20", (850, 330, 900, 350), 0.99),
              ("THANK YOU", (60, 400, 160, 420), 0.99)]
    [segment] = _segments(cells)
    assert segment.terminated
    assert _kinds(segment) == [("item", "FRT BUMPER REPLACE 1 980.00"), ("total", "SUB TOTAL 980.00")]


def test_wrapped_description_attaches_only_when_unambiguous():
    cells = header_cells(160) + row_cells(item("FRONT BUMPER COVER", amount="980.00"), 200)
    cells += [("ASSEMBLY", (60, 222, 140, 238), 0.95)]  # 2 px below, next row 40 px below
    cells += row_cells(item("BONNET", "REPAIR", amount="350.00"), 260)
    [segment] = _segments(cells)
    first, second = segment.rows
    assert first.kind == "item" and [b.index for b in first.bands] == [1, 2]  # header is band 0
    assert "wrapped_description" in first.flags
    assert second.kind == "item"


def test_continuation_too_far_below_stays_an_unlinked_row():
    cells = header_cells(160) + row_cells(item("FRONT BUMPER COVER", amount="980.00"), 200)
    cells += [("ASSEMBLY", (60, 240, 140, 256), 0.95)]  # 20 px gap > 14 px wrap gap
    [segment] = _segments(cells)
    assert [row.kind for row in segment.rows] == ["item", "continuation"]
    assert "unlinked_continuation" in segment.rows[1].flags


def test_continuation_equally_close_to_the_row_below_stays_unlinked():
    cells = header_cells(160) + row_cells(item("FRONT BUMPER COVER", amount="980.00"), 200)
    cells += [("ASSEMBLY", (60, 230, 140, 246), 0.95)]  # 10 px below row 1 and 10 px above row 2
    cells += row_cells(item("BONNET", "REPAIR", amount="350.00"), 256)
    [segment] = _segments(cells)
    assert [row.kind for row in segment.rows] == ["item", "continuation", "item"]


def test_heading_rows_are_recognised():
    cells = header_cells(160) + [("BODY WORK:", (60, 200, 160, 220), 0.97)]
    cells += row_cells(item("FRT BUMPER", amount="980.00"), 240)
    [segment] = _segments(cells)
    assert [row.kind for row in segment.rows] == ["heading", "item"]


def test_second_header_on_a_page_restarts_the_column_binding():
    cells = estimate_cells([item("FRT BUMPER", amount="980.00")], subtotal=None)
    shifted = {"description": (60, 300), "operation": (320, 440), "qty": (470, 510), "unit_price": (560, 680),
               "amount": (720, 820)}
    cells += header_cells(400, cols=shifted) + row_cells(item("BONNET", "REPAIR", amount="350.00"), 440, cols=shifted)
    first, second = _segments(cells)
    assert not first.terminated and not second.terminated
    assert second.binding.columns[-1].header_x0 != first.binding.columns[-1].header_x0
    assert _kinds(second) == [("item", "BONNET REPAIR 1 350.00")]
