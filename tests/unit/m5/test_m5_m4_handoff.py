"""M4 -> M5 box handoff: M4's own pages and records fed into parse_pages.

Three sources, none of them evidence about real workshop estimates: M4's SYNTHETIC page
renderer read by its deterministic stub engine through ``run_page_reading``; real
PaddleOCR 2.10.0 boxes from M4's smoke test on the same SYNTHETIC pages; and the
hand-authored contract fixture pages.
"""
import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest

from claim_cmev.contracts.common import Provenance, make_job_key
from claim_cmev.contracts.documents import DocumentPage, PageQuality, PageTransform, TextBox
from claim_cmev.contracts.fixtures import SCENARIOS, fixture_bundle
from claim_cmev.documents.line_items import TASK

from m5_support import CLAIM_ID, PAGE_PROVENANCE, artifact, parse, uncertainty

DATA = Path(__file__).parent / "data" / "paddleocr_synthetic_pages.json"
EXPECTED = [("front-bumper", "unknown", "replace", "980.00"), ("front-bumper", "unknown", "paint", "420.00"),
            ("headlight", "left", "replace", "640.00"), ("hood", "unknown", "repair", "350.00"),
            ("grille", "unknown", "replace", "180.00"), ("fender", "right", "repair", "260.00")]
SYNTHETIC = Provenance(source_kind="synthetic", runtime_profile="lean", producer_service="cmev-worker-lineitems")


def _summary(result):
    return [(i.part_code, i.side, i.operation, i.printed_line_amount) for i in result.line_items]


def test_m4_synthetic_page_through_run_page_reading_parses_every_row():
    cv2 = pytest.importorskip("cv2")
    from PIL import Image

    from claim_cmev.documents.text_layout import (
        OcrRegion,
        PageFileInput,
        PageReadRequest,
        StubOcrEngine,
        load_page_reading_config,
        run_page_reading,
    )
    from claim_cmev.documents.text_layout.synthetic import render_estimate_page
    from claim_cmev.vision.transforms import box_to_quad

    page = render_estimate_page(1240)
    buffer = BytesIO()
    Image.fromarray(cv2.cvtColor(page.image, cv2.COLOR_BGR2RGB)).save(buffer, "PNG")
    data = buffer.getvalue()
    regions = [OcrRegion(tuple(map(tuple, box_to_quad(line.box))), line.text, 0.95, "line") for line in page.lines]
    config = load_page_reading_config().with_overrides(
        {"page": {"rectified_width_px": 1240, "render_dpi": 150}, "quality": {"min_source_page_width_px": 500}})
    versions = {"code": "test"}
    request = PageReadRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=make_job_key(CLAIM_ID, 1, "page_read", versions),
        file=PageFileInput("file-synthetic-1", "image/png", hashlib.sha256(data).hexdigest(), data),
        versions=versions, provenance=PAGE_PROVENANCE, object_uri_prefix="s3://cmev-evidence/")
    read = run_page_reading(request, StubOcrEngine(regions), config)
    assert read.processing_status == "succeeded"
    [document_page] = read.pages
    assert document_page.text_granularity == "line"

    result = parse(read.pages)
    assert result.layout_family == "family-a-ruled-grid"
    assert _summary(result) == EXPECTED
    assert all(i.quantity == "1" for i in result.line_items)
    assert all(uncertainty(i).get("unit_price") == ["column_not_located"] for i in result.line_items)
    assert (result.completeness.state, result.completeness.reasons) == ("complete", [])
    assert "subtotal_matched" in result.rows[-1].flags  # SUB TOTAL 2830.00, then GST and TOTAL ignored
    box_ids = {b.box_id for b in document_page.text_boxes}
    assert all(set(i.source_box_ids) <= box_ids for i in result.line_items)  # every field traces to M4 boxes


def _paddle_page(name: str) -> DocumentPage:
    data = json.loads(DATA.read_text(encoding="utf-8"))["pages"][name]
    width, height = data["rectified_width"], data["rectified_height"]
    boxes = []
    for i, box in enumerate(data["boxes"]):
        x0, y0, x1, y1 = box["box_norm"]
        quad = ((x0 * width, y0 * height), (x1 * width, y0 * height), (x1 * width, y1 * height),
                (x0 * width, y1 * height))
        boxes.append(TextBox(box_id=f"{name}-bx{i:03d}", order_index=i, text=box["text"],
                             confidence=box["confidence"], box_norm=tuple(box["box_norm"]), quad_rectified=quad,
                             quad_original=quad, granularity=data["granularity"], flags=box["flags"]))
    provenance = Provenance(source_kind="synthetic", runtime_profile="lean", producer_service="cmev-worker-ocr")
    return DocumentPage(
        claim_id=CLAIM_ID, input_revision=1, provenance=provenance, versions={"ocr_engine": "paddleocr"},
        page_id=f"dp-{name}", file_id=f"file-{name}", page_number=1, corrected_render_ref=artifact(f"{name}.png"),
        page_reading_ref=artifact(f"{name}.json"),
        transform=PageTransform(source_width=width, source_height=height, corrected_width=width,
                                corrected_height=height, geometry_correction="none", correction_reason="fixture"),
        text_granularity=data["granularity"], text_boxes=boxes, mean_text_confidence=0.95,
        quality=PageQuality(state=data["page_status"]))


def test_real_paddleocr_boxes_on_the_flat_page_keep_a_confident_misread_visible():
    result = parse([_paddle_page("flat_scan")], provenance=SYNTHETIC)
    assert _summary(result) == EXPECTED
    assert [i.quantity for i in result.line_items] == ["7", "1", "1", "1", "1", "1"]  # engine read 1 as 7 at 0.868
    assert result.completeness.state == "complete"  # quantity is not required; M8 withholds quantity != 1


def test_real_paddleocr_boxes_on_the_perspective_photo():
    result = parse([_paddle_page("perspective_photo")], provenance=SYNTHETIC)
    assert _summary(result) == EXPECTED  # includes the engine's leading-space ' REPLACE'
    assert result.completeness.state == "complete" and "subtotal_matched" in result.rows[-1].flags


def test_real_paddleocr_boxes_under_pen_strokes():
    result = parse([_paddle_page("obscured_print_extra")], provenance=SYNTHETIC)
    first = result.line_items[0]
    assert first.printed_line_amount is None and first.original_amount_text == "900.003"
    assert uncertainty(first)["printed_line_amount"] == ["too_many_decimal_places"]
    # 420.00 -> 20.00 and 640.00 -> 40.0 were read with confidence 0.99: the parser cannot
    # know, and the uncertain first row prevents the subtotal comparison that would show it.
    assert [i.printed_line_amount for i in result.line_items[1:3]] == ["20.00", "40.0"]
    assert "subtotal_not_checked" in result.rows[-1].flags
    assert (result.completeness.state, result.completeness.reasons) == ("partial", ["uncertain_required_field"])


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_contract_fixture_pages_parse_to_the_fixture_rows(scenario):
    bundle = fixture_bundle(scenario, CLAIM_ID)
    fixture = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="cmev-worker-lineitems")
    result = parse(bundle.pages, provenance=fixture)
    assert result.layout_family == "family-a-ruled-grid"
    assert len(result.line_items) == len(bundle.line_items)
    for parsed, authored in zip(result.line_items, bundle.line_items):
        assert parsed.original_part_text == authored.original_part_text
        assert parsed.original_operation_text == authored.original_operation_text
        assert parsed.printed_line_amount == authored.printed_line_amount
        assert parsed.part_code == authored.part_code or authored.part_code is None
        assert set(parsed.source_box_ids) == set(authored.source_box_ids)
    if scenario == "partial_extraction":
        door = result.line_items[1]
        assert uncertainty(door)["printed_line_amount"] == ["ocr_letter_digit_confusion"]
        lamp = result.line_items[2]
        assert (lamp.part_mapping_status, lamp.operation_mapping_status) == ("ambiguous", "ambiguous")
        assert result.completeness.state == "partial"
    else:
        assert result.completeness.state == "complete"
