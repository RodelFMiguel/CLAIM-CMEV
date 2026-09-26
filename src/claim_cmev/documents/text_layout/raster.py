"""Page sources for M4 steps 3-4: PDF rasterisation and image decoding.

A PDF page is rasterised with pypdfium2 at the configured DPI; an image is decoded and
EXIF-oriented as page 1. Each rendered page keeps an exact matrix from render pixels
back to the uploaded file: stored image pixels, or PDF user-space points (bottom-left
origin, as pdfium reports them, so /Rotate and crop-box offsets are included). The
uploaded bytes are only read, never rewritten.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from claim_cmev.contracts.common import deterministic_id
from claim_cmev.vision.transforms import decode_oriented, orientation_matrix

PDF_MAGIC = b"%PDF"


def page_id_for(file_id: str, page_number: int) -> str:
    """Deterministic page identity from ``(file_id, page_number)``; retries reuse it."""
    if page_number < 1:
        raise ValueError("page numbers are one-based")
    return deterministic_id("pg", file_id, page_number)


@dataclass(frozen=True)
class SourceFrame:
    """How one rendered page's pixels relate to the uploaded file."""

    source_kind: Literal["image", "pdf"]
    source_frame: Literal["image_pixels", "pdf_points"]
    render_to_source: np.ndarray
    stored_width: int | None = None
    stored_height: int | None = None
    exif_orientation: int | None = None
    render_dpi: int | None = None
    render_scale: float | None = None
    pdf_page_width_pt: float | None = None
    pdf_page_height_pt: float | None = None
    pdf_rotation: int | None = None


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    image: np.ndarray  # BGR uint8, the render frame
    source: SourceFrame


@dataclass(frozen=True)
class PageFailure:
    page_number: int
    reason: str
    message: str


PageOutcome = RenderedPage | PageFailure


def render_pages(
    data: bytes,
    media_type: str,
    render_dpi: int,
    max_pages: int,
    page_numbers: Sequence[int] | None = None,
    page_count_hint: int | None = None,
) -> list[PageOutcome]:
    """Render every requested page, or record why each one could not be rendered."""
    is_pdf = data[:1024].lstrip().startswith(PDF_MAGIC)
    if media_type == "application/pdf" and not is_pdf:
        return [PageFailure(n, "media_type_mismatch", "declared PDF but the bytes are not a PDF")
                for n in (page_numbers or [1])]
    if is_pdf:
        return _render_pdf(data, render_dpi, max_pages, page_numbers, page_count_hint)
    wanted = list(page_numbers or [1])
    outcomes: list[PageOutcome] = [PageFailure(n, "page_not_found", "an image file has only page 1")
                                   for n in wanted if n != 1]
    if 1 in wanted:
        outcomes.insert(0, _decode_image(data))
    return outcomes


def _decode_image(data: bytes) -> PageOutcome:
    try:
        oriented_rgb, orientation, (stored_w, stored_h) = decode_oriented(data)
    except Exception as exc:  # decoder errors are data errors, recorded, never raised
        return PageFailure(1, "decode_failed", f"image could not be decoded: {type(exc).__name__}")
    image = np.ascontiguousarray(oriented_rgb[:, :, ::-1])
    source = SourceFrame(
        source_kind="image", source_frame="image_pixels",
        render_to_source=np.linalg.inv(orientation_matrix(orientation, stored_w, stored_h)),
        stored_width=stored_w, stored_height=stored_h, exif_orientation=orientation,
    )
    return RenderedPage(1, image, source)


def _render_pdf(data: bytes, render_dpi: int, max_pages: int, page_numbers: Sequence[int] | None,
                page_count_hint: int | None) -> list[PageOutcome]:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        count = len(page_numbers) if page_numbers else max(1, page_count_hint or 1)
        numbers = list(page_numbers) if page_numbers else list(range(1, count + 1))
        return [PageFailure(n, "rasterise_failed", f"PDF could not be opened: {type(exc).__name__}") for n in numbers]
    outcomes: list[PageOutcome] = []
    try:
        total = len(document)
        numbers = list(page_numbers) if page_numbers else list(range(1, total + 1))
        for index, number in enumerate(numbers):
            if not 1 <= number <= total:
                outcomes.append(PageFailure(number, "page_not_found", f"PDF has {total} pages"))
            elif index >= max_pages:
                outcomes.append(PageFailure(number, "page_limit_exceeded", f"more than {max_pages} pages in one job"))
            else:
                outcomes.append(_render_pdf_page(document, number, render_dpi))
    finally:
        document.close()
    return outcomes


def _render_pdf_page(document, number: int, render_dpi: int) -> PageOutcome:
    page = document[number - 1]
    try:
        bitmap = page.render(scale=render_dpi / 72.0)
        rgb = np.asarray(bitmap.to_pil().convert("RGB"))
        height, width = rgb.shape[:2]
        width_pt, height_pt = page.get_size()
        source = SourceFrame(
            source_kind="pdf", source_frame="pdf_points",
            render_to_source=_device_to_page_matrix(page, width, height),
            render_dpi=render_dpi, render_scale=width / width_pt,
            pdf_page_width_pt=float(width_pt), pdf_page_height_pt=float(height_pt),
            pdf_rotation=int(page.get_rotation()),
        )
        return RenderedPage(number, np.ascontiguousarray(rgb[:, :, ::-1]), source)
    except Exception as exc:
        return PageFailure(number, "rasterise_failed", f"page {number} could not be rasterised: {type(exc).__name__}")
    finally:
        page.close()


def _device_to_page_matrix(page, width: int, height: int) -> np.ndarray:
    """Affine map from render pixels to PDF user space, taken from pdfium itself."""
    import ctypes

    import pypdfium2.raw as raw

    corners = []
    for dx, dy in ((0, 0), (width, 0), (0, height)):
        px, py = ctypes.c_double(), ctypes.c_double()
        if not raw.FPDF_DeviceToPage(page.raw, 0, 0, width, height, 0, dx, dy, ctypes.byref(px), ctypes.byref(py)):
            raise RuntimeError("FPDF_DeviceToPage failed")
        corners.append((px.value, py.value))
    (x0, y0), (x1, y1), (x2, y2) = corners
    return np.array([[(x1 - x0) / width, (x2 - x0) / height, x0],
                     [(y1 - y0) / width, (y2 - y0) / height, y0],
                     [0.0, 0.0, 1.0]])
