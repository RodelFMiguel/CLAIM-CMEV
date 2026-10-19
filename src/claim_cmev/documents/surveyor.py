"""Surveyor name extraction from document text boxes.

Pure, deterministic extraction of the surveyor or loss adjuster name from OCR text boxes
located on uploaded estimate or survey report pages.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SINGLE_PATTERN = re.compile(
    r"^\s*(?:surveyor|loss\s+adjuster|inspected\s+by)\s*[:\-]\s+([A-Za-z][A-Za-z0-9\.\s\-]{1,35})$",
    re.IGNORECASE,
)
_LABEL_PATTERN = re.compile(
    r"^\s*(?:surveyor|loss\s+adjuster|inspected\s+by)\s*[:\-]?\s*$",
    re.IGNORECASE,
)
_STOP_WORDS = frozenset(
    {"report", "assessment", "details", "name", "date", "type", "claim", "vehicle", "none", "not applicable"}
)


def extract_surveyor_name(boxes: Sequence[Mapping[str, Any] | Any]) -> str | None:
    """Extract surveyor name from a list of OCR text box dictionaries or records.

    Each box is expected to provide text (str) and normalised coordinates box_norm [x0, y0, x1, y1].
    """
    if not boxes:
        return None

    # Normalise box access
    norm_boxes = []
    for b in boxes:
        if isinstance(b, Mapping):
            text = str(b.get("text", "")).strip()
            coords = b.get("box_norm") or [0.0, 0.0, 0.0, 0.0]
        else:
            text = str(getattr(b, "text", "")).strip()
            coords = getattr(b, "box_norm", None) or [0.0, 0.0, 0.0, 0.0]
        if text:
            norm_boxes.append({"text": text, "box_norm": coords})

    # 1. Single box with label + name (e.g. "Surveyor: H. Tan" or "Surveyor - S. Kumar")
    for b in norm_boxes:
        m = _SINGLE_PATTERN.match(b["text"])
        if m:
            val = m.group(1).strip()
            if val.lower() not in _STOP_WORDS and len(val) >= 2:
                return val

    # 2. Separate label box and value box (e.g. "Surveyor" in box A, "H. Tan" in box B)
    for b in norm_boxes:
        if _LABEL_PATTERN.match(b["text"]):
            bx0, by0, bx1, by1 = b["box_norm"]
            b_cy = (by0 + by1) / 2.0

            horiz_candidates = []
            vert_candidates = []

            for other in norm_boxes:
                if other is b:
                    continue
                otext = other["text"]
                if not otext or len(otext) < 2 or otext.lower() in _STOP_WORDS:
                    continue
                ox0, oy0, ox1, oy1 = other["box_norm"]
                o_cy = (oy0 + oy1) / 2.0

                # Same horizontal row (centres within 0.025 vertical fraction, positioned to the right)
                if abs(o_cy - b_cy) < 0.025 and ox0 >= bx0 + 0.005:
                    dist = ox0 - bx1
                    horiz_candidates.append((dist, otext))

                # Vertically stacked directly below (within 0.04 vertical fraction, overlapping x)
                if 0 <= oy0 - by1 < 0.04 and min(bx1, ox1) - max(bx0, ox0) > -0.05:
                    vert_candidates.append((oy0 - by1, otext))

            if horiz_candidates:
                horiz_candidates.sort(key=lambda c: c[0])
                for _, val in horiz_candidates:
                    if val.lower() not in _STOP_WORDS and len(val) >= 2:
                        return val

            if vert_candidates:
                vert_candidates.sort(key=lambda c: c[0])
                for _, val in vert_candidates:
                    if val.lower() not in _STOP_WORDS and len(val) >= 2:
                        return val

    return None


def extract_surveyor_from_pages(pages: Sequence[Any]) -> str | None:
    """Extract surveyor name from a list of DocumentPage records or dicts."""
    if not pages:
        return None
    for p in pages:
        boxes = getattr(p, "text_boxes", None) if not isinstance(p, Mapping) else p.get("text_boxes")
        if boxes:
            name = extract_surveyor_name(boxes)
            if name:
                return name
    return None

