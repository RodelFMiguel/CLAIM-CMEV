"""Page quality signals (M4 step 13): screening signals with reason codes, not proof.

Image signals are measured on the corrected render at a fixed evaluation width so
thresholds do not depend on the capture resolution:

* ``sharpness``: one minus the no-reference blur effect (``blur_effect``), in [0, 1];
* ``clipped_pixel_fraction``: share of pixels at or above ``clip_level``;
* ``glare_fraction``: clipped share counted only when the paper itself is not clipped;
* ``illumination_ratio``: 5th/95th percentile of the estimated paper background;
* ``contrast_range``: 99.5th minus 0.5th grey percentile (ink to paper);
* ``source_page_width_px``: page width in render pixels before any upscaling.

OCR-derived signals count boxes and confidences; a missing confidence is never invented.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from .config import QualityConfig
from .geometry import to_gray

FLAG_REASONS = {
    "blur": "page_blurred",
    "glare": "glare_region",
    "uneven_illumination": "uneven_illumination",
    "low_contrast": "low_contrast",
    "low_resolution": "low_resolution",
}


@dataclass(frozen=True)
class ImageSignals:
    sharpness: float
    clipped_pixel_fraction: float
    paper_level: float
    glare_fraction: float
    illumination_ratio: float
    contrast_range: float
    source_page_width_px: float

    def to_dict(self) -> dict[str, float]:
        return {k: round(float(v), 6) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class TextSignals:
    box_count: int
    confidence_count: int
    mean_confidence: float | None
    low_confidence_count: int
    low_confidence_fraction: float | None  # denominator: boxes that carry a confidence

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


def image_signals(image: np.ndarray, source_page_width_px: float, cfg: QualityConfig) -> ImageSignals:
    gray = to_gray(image)
    scale = min(1.0, cfg.eval_width_px / gray.shape[1])
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    low, high = np.percentile(small, [0.5, 99.5])
    background = _paper_background(small)
    paper = float(np.median(background))
    clipped = float(np.mean(small >= cfg.clip_level))
    glare = clipped if paper <= cfg.clip_level - cfg.glare_margin else 0.0
    lo, hi = np.percentile(background, [5, 95])
    return ImageSignals(sharpness=1.0 - blur_effect(small), clipped_pixel_fraction=clipped, paper_level=paper,
                        glare_fraction=glare, illumination_ratio=float(lo / max(hi, 1.0)),
                        contrast_range=float(high - low), source_page_width_px=float(source_page_width_px))


def blur_effect(gray: np.ndarray, size: int = 11) -> float:
    """No-reference blur (Crete-Roffet et al. 2007): 0 sharp to 1 blurred, contrast invariant.

    Measures how much neighbouring-pixel variation survives a further box blur; the
    worse of the two axes is reported.
    """
    img = gray.astype(np.float64)
    worst = 0.0
    for axis, ksize in ((0, (1, size)), (1, (size, 1))):
        original = np.abs(np.diff(img, axis=axis))
        reblurred = np.abs(np.diff(cv2.blur(img, ksize), axis=axis))
        total = original.sum()
        lost = np.maximum(0.0, original - reblurred).sum()
        worst = max(worst, float((total - lost) / total) if total > 0 else 1.0)
    return worst


def _paper_background(gray: np.ndarray) -> np.ndarray:
    """Estimate paper brightness by closing away dark print, then smoothing."""
    scale = 256.0 / gray.shape[1]
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    closed = cv2.morphologyEx(small, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    return cv2.medianBlur(closed, 15)


def image_flags(signals: ImageSignals, cfg: QualityConfig) -> list[str]:
    flags = []
    if signals.sharpness < cfg.min_sharpness:
        flags.append("blur")
    if signals.glare_fraction > cfg.max_glare_fraction:
        flags.append("glare")
    if signals.illumination_ratio < cfg.min_illumination_ratio:
        flags.append("uneven_illumination")
    if signals.contrast_range < cfg.min_contrast_range:
        flags.append("low_contrast")
    if signals.source_page_width_px < cfg.min_source_page_width_px:
        flags.append("low_resolution")
    return flags


def text_signals(confidences: Sequence[float | None], min_box_confidence: float) -> TextSignals:
    known = [c for c in confidences if c is not None]
    low = sum(1 for c in known if c < min_box_confidence)
    return TextSignals(
        box_count=len(confidences), confidence_count=len(known),
        mean_confidence=float(np.mean(known)) if known else None,
        low_confidence_count=low, low_confidence_fraction=low / len(known) if known else None,
    )
