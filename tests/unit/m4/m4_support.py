"""Shared helpers for the M4 tests. Every page here is SYNTHETIC test material."""
from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

import cv2
import numpy as np
from PIL import Image

from claim_cmev.contracts.common import make_job_key
from claim_cmev.documents.text_layout import (
    OcrRegion,
    PageFileInput,
    PageReadingConfig,
    PageReadRequest,
    load_page_reading_config,
)
from claim_cmev.vision.transforms import EXIF_ORIENTATION_TAG, box_to_quad, map_points

CLAIM_ID = "01J8Z3N4V6W8X0Y2Z4A6B8C0D2"  # ULID-shaped synthetic claim id
FILE_ID = "file-synthetic-estimate-1"
URI_PREFIX = "s3://cmev-evidence/"
PROVENANCE = {"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "cmev-worker-ocr"}
VERSIONS = {"code": "test", "taxonomy": "none"}


def m4_config(**sections: dict[str, Any]) -> PageReadingConfig:
    """Repository configuration scaled for 150 dpi synthetic pages, plus overrides."""
    base = {"page": {"rectified_width_px": 1240, "render_dpi": 150},
            "quality": {"min_source_page_width_px": 500}}
    for key, value in sections.items():
        base.setdefault(key, {}).update(value)
    return load_page_reading_config().with_overrides(base)


def png_bytes(image_bgr: np.ndarray, exif_orientation: int | None = None) -> bytes:
    image = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
    buffer = BytesIO()
    if exif_orientation is None:
        image.save(buffer, "PNG")
    else:
        exif = Image.Exif()
        exif[EXIF_ORIENTATION_TAG] = exif_orientation
        image.save(buffer, "PNG", exif=exif)
    return buffer.getvalue()


def pdf_bytes(pages_bgr: list[np.ndarray], dpi: int = 150) -> bytes:
    images = [Image.fromarray(cv2.cvtColor(p, cv2.COLOR_BGR2RGB)) for p in pages_bgr]
    buffer = BytesIO()
    images[0].save(buffer, "PDF", resolution=dpi, save_all=True, append_images=images[1:])
    return buffer.getvalue()


def request_for(data: bytes, media_type: str = "image/png", *, versions: dict[str, str] | None = None,
                page_numbers: tuple[int, ...] | None = None, sha256: str | None = None,
                provenance: dict[str, Any] | None = None, file_id: str = FILE_ID) -> PageReadRequest:
    versions = dict(versions or VERSIONS)
    return PageReadRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=make_job_key(CLAIM_ID, 1, "page_read", versions),
        file=PageFileInput(file_id=file_id, media_type=media_type, sha256=sha256 or hashlib.sha256(data).hexdigest(),
                           data=data, page_numbers=page_numbers),
        versions=versions, provenance=provenance or PROVENANCE, object_uri_prefix=URI_PREFIX)


def line_quads(page, roles: tuple[str, ...] | None = None) -> list[tuple[str, np.ndarray]]:
    return [(line.text, box_to_quad(line.box)) for line in page.lines if roles is None or line.role in roles]


def regions_in_rectified(lines: list[tuple[str, np.ndarray]], page_to_rectified: np.ndarray,
                         confidence: float | None = 0.95, level: str = "line") -> list[OcrRegion]:
    """Truth regions: where each synthetic line really lies in the corrected render."""
    return [OcrRegion(tuple(map(tuple, map_points(page_to_rectified, quad))), text, confidence, level)
            for text, quad in lines]


def blob_regions(image: np.ndarray, confidence: float = 0.93, obscured_confidence: float = 0.3) -> list[OcrRegion]:
    """A crude, deterministic text-line detector standing in for OCR in fixture tests.

    Ink blobs are merged horizontally into line segments; a segment covered by blue pen
    ink gets ``obscured_confidence``, imitating the confidence drop of obscured print.
    The text is unknown and reported as ``?``.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    h, w = binary.shape
    rules = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 8), 1)))
    rules |= cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 20))))
    text = cv2.bitwise_and(binary, cv2.bitwise_not(cv2.dilate(rules, np.ones((3, 3), np.uint8))))
    merged = cv2.dilate(text, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3)))
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    b, g, r = (image[..., i].astype(int) for i in range(3))
    blue = (b - r) > 60
    regions = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < 8 or not 6 <= bh <= 90:
            continue
        covered = blue[y:y + bh, x:x + bw].mean() > 0.05
        quad = ((x, y), (x + bw, y), (x + bw, y + bh), (x, y + bh))
        regions.append(OcrRegion(tuple((float(px), float(py)) for px, py in quad), "?",
                                 obscured_confidence if covered else confidence, "line"))
    return sorted(regions, key=lambda reg: (reg.quad[0][1], reg.quad[0][0]))
