"""M4 configuration strictness, deterministic page identity and page rasterisation."""
import hashlib
from io import BytesIO

import numpy as np
import pytest
import yaml
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from claim_cmev.documents.text_layout import load_page_reading_config, page_id_for, render_pages
from claim_cmev.documents.text_layout.config import DEFAULT_CONFIG_PATH, PageReadingConfig
from claim_cmev.documents.text_layout.raster import PageFailure, RenderedPage
from claim_cmev.documents.text_layout.synthetic import render_estimate_page
from claim_cmev.vision.transforms import map_points
from m4_support import pdf_bytes, png_bytes


def test_repository_config_loads_with_spec_defaults():
    cfg = load_page_reading_config()
    assert cfg.config_version.startswith("m4-page-reading/")
    assert cfg.ocr.engine == "paddleocr" and cfg.ocr.min_box_confidence == 0.50
    assert cfg.ocr.max_low_confidence_fraction == 0.15 and cfg.ocr.fallback_engine.enabled is False
    assert cfg.page.render_dpi == 300 and cfg.page.rectified_width_px == 2480
    assert cfg.page.expected_text_box_granularity == "line"
    assert len(cfg.sha256) == 64


@pytest.mark.parametrize("mutate", [
    lambda d: d["page"].update(surprise=1),
    lambda d: d["ocr"].pop("min_box_confidence"),
    lambda d: d["page"].update(expected_text_box_granularity="character"),
    lambda d: d["quality"].update(partial_on_flags=["unknown_flag"]),
])
def test_config_rejects_unknown_missing_and_invalid_keys(mutate):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    mutate(data)
    with pytest.raises(ValidationError):
        PageReadingConfig.model_validate(data)


def test_page_ids_are_deterministic_from_file_and_page_number():
    assert page_id_for("file-a", 1) == page_id_for("file-a", 1)
    assert len({page_id_for("file-a", 1), page_id_for("file-a", 2), page_id_for("file-b", 1)}) == 3
    with pytest.raises(ValueError):
        page_id_for("file-a", 0)


def _pdf_page(dpi_source: int = 75):
    page = render_estimate_page(620)
    return page, pdf_bytes([page.image], dpi=dpi_source)


def test_pdf_is_rasterised_at_the_configured_dpi_with_a_points_matrix():
    page, data = _pdf_page()
    [rendered] = render_pages(data, "application/pdf", 150, 10)
    assert isinstance(rendered, RenderedPage) and rendered.page_number == 1
    height, width = rendered.image.shape[:2]
    assert abs(width - 1240) <= 1 and abs(height - page.height * 2) <= 1
    source = rendered.source
    assert (source.source_kind, source.source_frame, source.render_dpi) == ("pdf", "pdf_points", 150)
    assert source.render_scale == pytest.approx(150 / 72, rel=2e-3)
    w_pt, h_pt = source.pdf_page_width_pt, source.pdf_page_height_pt
    assert w_pt == pytest.approx(620 / 75 * 72, abs=0.1)
    corners = map_points(source.render_to_source, [(0, 0), (width, height)])
    assert np.allclose(corners, [(0, h_pt), (w_pt, 0)], atol=0.05)  # PDF user space: bottom-left origin


def test_pdf_page_rotation_is_carried_by_the_points_matrix():
    page, data = _pdf_page()
    reader, writer = PdfReader(BytesIO(data)), PdfWriter()
    writer.add_page(reader.pages[0].rotate(90))
    buffer = BytesIO()
    writer.write(buffer)
    [rendered] = render_pages(buffer.getvalue(), "application/pdf", 150, 10)
    height, width = rendered.image.shape[:2]
    assert abs(width - page.height * 2) <= 1 and abs(height - 1240) <= 1  # displayed rotated
    assert rendered.source.pdf_rotation == 90
    scale = rendered.source.render_scale
    for dx, dy in [(0, 0), (width, 0), (0, height), (300, 700)]:
        ux, uy = map_points(rendered.source.render_to_source, [(dx, dy)])[0]
        assert (ux, uy) == pytest.approx((dy / scale, dx / scale), abs=0.6)  # /Rotate 90: user=(dy, dx)


def test_pdf_page_selection_limits_and_failures_are_recorded_per_page():
    page = render_estimate_page(310)
    data = pdf_bytes([page.image] * 3, dpi=38)
    outcomes = render_pages(data, "application/pdf", 72, max_pages=2)
    assert [type(o) for o in outcomes] == [RenderedPage, RenderedPage, PageFailure]
    assert outcomes[2].reason == "page_limit_exceeded" and outcomes[2].page_number == 3
    [missing] = render_pages(data, "application/pdf", 72, 10, page_numbers=(5,))
    assert (missing.page_number, missing.reason) == (5, "page_not_found")
    broken = render_pages(b"%PDF-1.4\nnot really a pdf", "application/pdf", 72, 10, page_count_hint=2)
    assert [(o.page_number, o.reason) for o in broken] == [(1, "rasterise_failed"), (2, "rasterise_failed")]
    [mismatch] = render_pages(png_bytes(page.image), "application/pdf", 72, 10)
    assert mismatch.reason == "media_type_mismatch"


def test_image_decoding_records_orientation_and_never_rewrites_the_upload():
    page = render_estimate_page(310)
    stored = np.ascontiguousarray(np.rot90(page.image, k=1))  # EXIF 6 displays it upright again
    data = png_bytes(stored, exif_orientation=6)
    before = hashlib.sha256(data).hexdigest()
    [rendered] = render_pages(data, "image/png", 150, 10)
    assert hashlib.sha256(data).hexdigest() == before
    assert rendered.source.exif_orientation == 6
    assert (rendered.source.stored_width, rendered.source.stored_height) == (stored.shape[1], stored.shape[0])
    assert np.array_equal(rendered.image, page.image)
    [failure] = render_pages(b"\x89PNG\r\n\x1a\nbroken", "image/png", 150, 10)
    assert (failure.page_number, failure.reason) == (1, "decode_failed")
    outcomes = render_pages(data, "image/png", 150, 10, page_numbers=(1, 2))
    assert isinstance(outcomes[0], RenderedPage) and outcomes[1].reason == "page_not_found"
