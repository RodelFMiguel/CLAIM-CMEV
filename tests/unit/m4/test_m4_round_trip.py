"""Coordinate round trip for pages (integration contracts section 11, data contracts section 4).

Each case feeds the adapter an engine that reports the synthetic lines exactly where they
lie in the corrected render. The stored boxes must then land on the true place in the
uploaded page (render frame), in the stored image pixels or PDF points, and map forward
again within ``transforms.round_trip_tolerance_px``: flat scan, perspective-corrected
photo, deskewed photo with EXIF rotation, and PDF pages with and without /Rotate.
"""
from io import BytesIO

import numpy as np
import pytest
from pypdf import PdfReader, PdfWriter

from claim_cmev.documents.text_layout import (
    PageTransform,
    StubOcrEngine,
    correct_page_geometry,
    render_pages,
    run_page_reading,
)
from claim_cmev.documents.text_layout.synthetic import (
    camera_corners,
    photograph,
    render_estimate_page,
    rotate_within_frame,
)
from claim_cmev.vision.transforms import box_to_quad, map_points, orientation_matrix, quad_to_box
from m4_support import line_quads, m4_config, pdf_bytes, png_bytes, regions_in_rectified, request_for

CFG = m4_config()
TOL = CFG.transforms.round_trip_tolerance_px
PAGE = render_estimate_page(1240)
LINES = line_quads(PAGE, roles=("column_header", "cell", "total"))


def _read(data: bytes, media_type: str, page_to_render: np.ndarray):
    """Run the adapter with truth regions; return the page record, reading and transform."""
    [rendered] = render_pages(data, media_type, CFG.page.render_dpi, CFG.page.max_pages_per_job)
    geometry = correct_page_geometry(rendered.image, CFG)
    engine = StubOcrEngine(regions_in_rectified(LINES, geometry.homography @ page_to_render))
    result = run_page_reading(request_for(data, media_type), engine, CFG)
    [page], [reading] = result.pages, result.page_readings
    return rendered, geometry, page, reading, PageTransform.from_dict(reading["transform"])


def _quad_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Order-independent distance between two quads (a rotation changes the first corner)."""
    d = np.linalg.norm(np.asarray(a)[:, None, :] - np.asarray(b)[None, :, :], axis=2)
    return float(max(d.min(axis=1).max(), d.min(axis=0).max()))


def _check(rendered, page, reading, transform, page_to_render, page_to_source):
    texts = [text for text, _ in LINES]
    assert len(reading["boxes"]) == len(page.text_boxes) == len(LINES)  # never split or merged
    for box, contract_box in zip(reading["boxes"], page.text_boxes):
        truth_quad = dict(zip(texts, (q for _, q in LINES)))[box["text"]] if texts.count(box["text"]) == 1 else None
        rectified, original, source = (np.array(box[k]) for k in ("quad_rectified", "quad_original", "quad_source"))
        # forward again from the uploaded page and from the file itself
        assert np.abs(transform.original_to_rectified_points(original) - rectified).max() <= TOL
        assert np.abs(transform.source_to_rectified_points(source) - rectified).max() <= TOL
        assert transform.round_trip_error(rectified) <= TOL
        # the contract record's own transform agrees
        back = np.array([page.transform.corrected_to_source(x, y) for x, y in contract_box.quad_rectified])
        assert np.abs(back - original).max() <= TOL
        if truth_quad is not None:  # lands on the true region of the original
            assert _quad_distance(original, map_points(page_to_render, truth_quad)) <= TOL
            assert _quad_distance(source, map_points(page_to_source, truth_quad)) <= TOL
    # and the region really holds print in the uploaded page
    x0, y0, x1, y1 = (int(round(v)) for v in quad_to_box(np.array(reading["boxes"][0]["quad_original"])))
    assert rendered.image[max(0, y0):y1, max(0, x0):x1].min() < 128


def test_flat_scan_round_trip():
    rendered, geometry, page, reading, transform = _read(png_bytes(PAGE.image), "image/png", np.eye(3))
    assert geometry.kind == "none" and not page.transform.perspective_applied
    _check(rendered, page, reading, transform, np.eye(3), np.eye(3))


def test_perspective_corrected_photo_round_trip():
    size = (1200, 1600)
    photo, page_to_photo = photograph(PAGE, camera_corners(PAGE, size, pitch_deg=25, yaw_deg=-15, roll_deg=3),
                                      size, seed=1)
    rendered, geometry, page, reading, transform = _read(png_bytes(photo), "image/png", page_to_photo)
    assert geometry.kind == "perspective" and page.transform.perspective_applied
    assert reading["transform"]["rectified_width"] == CFG.page.rectified_width_px
    _check(rendered, page, reading, transform, page_to_photo, page_to_photo)


def test_deskewed_photo_with_exif_rotation_round_trip():
    display, page_to_display = rotate_within_frame(PAGE, 4.0)
    stored = np.ascontiguousarray(np.rot90(display, k=1))  # the camera stored it sideways; EXIF 6 fixes it
    data = png_bytes(stored, exif_orientation=6)
    rendered, geometry, page, reading, transform = _read(data, "image/png", page_to_display)
    assert geometry.kind == "rotation" and page.transform.exif_orientation == 6
    display_to_stored = np.linalg.inv(orientation_matrix(6, stored.shape[1], stored.shape[0]))
    _check(rendered, page, reading, transform, page_to_display, display_to_stored @ page_to_display)
    x0, y0, x1, y1 = (int(round(v)) for v in quad_to_box(np.array(reading["boxes"][0]["quad_source"])))
    assert stored[y0:y1, x0:x1].min() < 128  # the stored (sideways) pixels hold the print too


@pytest.mark.parametrize("rotate", [0, 90])
def test_pdf_page_round_trip_to_pdf_points(rotate):
    data = pdf_bytes([PAGE.image], dpi=150)
    if rotate:
        writer = PdfWriter()
        writer.add_page(PdfReader(BytesIO(data)).pages[0].rotate(rotate))
        buffer = BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
    [rendered] = render_pages(data, "application/pdf", CFG.page.render_dpi, 10)
    height_pt = PAGE.height * 72 / 150
    page_to_points = np.array([[72 / 150, 0, 0], [0, -72 / 150, height_pt], [0, 0, 1]])  # user space, unrotated
    points_to_render = np.linalg.inv(rendered.source.render_to_source)
    page_to_render = points_to_render @ page_to_points
    rendered, geometry, page, reading, transform = _read(data, "application/pdf", page_to_render)
    assert reading["transform"]["source_frame"] == "pdf_points"
    assert page.transform.render_scale == pytest.approx(150 / 72, rel=2e-3)
    _check(rendered, page, reading, transform, page_to_render, page_to_points)
    box = page.text_boxes[0]
    x, y = box.quad_rectified[0]
    px, py = page.transform.corrected_to_pdf_points(x, y)
    ux, uy = map_points(rendered.source.render_to_source, [page.transform.corrected_to_source(x, y)])[0]
    assert (px, py) == pytest.approx((ux, uy), abs=0.5)


def test_normalised_boxes_are_on_the_corrected_render():
    rendered, geometry, page, reading, transform = _read(png_bytes(PAGE.image), "image/png", np.eye(3))
    for box in page.text_boxes:
        x0, y0, x1, y1 = box.box_norm
        bx = quad_to_box(np.array(box.quad_rectified))
        assert (x0 * geometry.width, y0 * geometry.height) == pytest.approx(bx[:2], abs=0.01)
        assert (x1 * geometry.width, y1 * geometry.height) == pytest.approx(bx[2:], abs=0.01)
    assert box_to_quad(page.text_boxes[0].box_norm).shape == (4, 2)
