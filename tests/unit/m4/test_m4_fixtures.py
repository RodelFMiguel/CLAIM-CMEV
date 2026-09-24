"""The seven M4 page fixtures, generated deterministically (all SYNTHETIC).

flat scan, perspective photograph, rotated photograph (with and without a visible page
edge), glare, shadow, partly obscured print and an unreadable page. A crude blob
detector stands in for OCR, so these tests check M4's geometry, quality and status
logic, not OCR accuracy.
"""
import numpy as np
import pytest

from claim_cmev.documents.text_layout import StubOcrEngine, run_page_reading
from claim_cmev.documents.text_layout.synthetic import (
    add_glare,
    add_pen_strokes,
    add_shadow,
    camera_corners,
    make_unreadable,
    photograph,
    render_estimate_page,
    rotate_within_frame,
)
from m4_support import blob_regions, m4_config, png_bytes, request_for

CFG = m4_config()
PAGE = render_estimate_page(1240)
SIZE = (1200, 1600)
PHOTO, _ = photograph(PAGE, camera_corners(PAGE, SIZE, pitch_deg=25, yaw_deg=-15, roll_deg=3), SIZE, seed=1)
AMOUNTS = [line.box for line in PAGE.lines if line.column == "amount" and line.role == "cell"]
QTY = [line.box for line in PAGE.lines if line.column == "qty" and line.role == "cell"]


def _fixture(name: str) -> np.ndarray:
    if name == "flat_scan":
        return PAGE.image
    if name == "perspective_photo":
        return PHOTO
    if name == "rotated_photo":
        return photograph(PAGE, camera_corners(PAGE, SIZE, roll_deg=9, pitch_deg=5, fill=0.62), SIZE,
                          seed=2, blur_sigma=1.0)[0]
    if name == "rotated_scan":
        return rotate_within_frame(PAGE, 4.0)[0]
    if name == "glare":
        return add_glare(PHOTO, (600, 800), (160, 110))
    if name == "shadow":
        return add_shadow(PHOTO, 0.5, 0.5)
    if name == "obscured_some":
        return add_pen_strokes(PAGE.image, AMOUNTS[:2])
    if name == "obscured_many":
        return add_pen_strokes(PAGE.image, AMOUNTS + QTY)
    if name == "unreadable":
        return make_unreadable(PAGE.image)
    raise KeyError(name)


def _read(name: str):
    engine = StubOcrEngine(blob_regions)
    result = run_page_reading(request_for(png_bytes(_fixture(name))), engine, CFG)
    [page], [reading] = result.pages, result.page_readings
    return result, page, reading


@pytest.mark.parametrize("name, kind, applied", [
    ("flat_scan", "none", False),
    ("perspective_photo", "perspective", True),
    ("rotated_photo", "perspective", True),
    ("rotated_scan", "rotation", False),
])
def test_clean_fixtures_read_complete_with_the_right_correction(name, kind, applied):
    result, page, reading = _read(name)
    assert page.transform.geometry_correction == kind and page.transform.perspective_applied is applied
    assert page.quality.state == "complete", page.quality.reasons
    assert reading["quality"]["flags"] == []
    assert len(page.text_boxes) > 20 and result.processing_status == "succeeded"


@pytest.mark.parametrize("name, reason", [("glare", "glare_region"), ("shadow", "uneven_illumination")])
def test_glare_and_shadow_are_flagged_and_downgrade_the_page(name, reason):
    _, page, reading = _read(name)
    assert page.transform.perspective_applied
    assert page.quality.state == "partial" and reason in page.quality.reasons
    assert page.text_boxes  # still read: M5 may build rows and mark fields uncertain


def test_partly_obscured_print_is_a_low_confidence_region_not_a_pen_mark():
    _, page, _ = _read("obscured_some")
    flagged = [b for b in page.text_boxes if "low_confidence_region" in b.flags]
    assert len(flagged) == 2 and "low_confidence_region" in page.quality.reasons
    for box in flagged:  # the flagged boxes are the struck amounts
        cx = (box.box_norm[0] + box.box_norm[2]) / 2 * page.transform.corrected_width
        assert cx > 900
    everything = {f for b in page.text_boxes for f in b.flags} | set(page.quality.reasons)
    assert not any("pen" in code or "mark" in code for code in everything)
    _, heavy, _ = _read("obscured_many")
    assert heavy.quality.state == "partial" and "low_confidence_fraction" in heavy.quality.reasons


def test_unreadable_page_is_marked_unreadable_not_empty_success():
    result, page, reading = _read("unreadable")
    assert page.quality.state == "unreadable" and page.text_boxes == []
    assert {"ocr_no_text", "low_contrast"} <= set(page.quality.reasons)
    assert result.processing_status == "failed" and reading["page_status"] == "unreadable"


def test_fixtures_are_deterministic():
    first, second = _read("glare"), _read("glare")
    assert first[1] == second[1]
    assert [a.sha256 for a in first[0].artifacts] == [a.sha256 for a in second[0].artifacts]
