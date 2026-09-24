"""The four M3 view-screening signals for one part on one photograph.

Part area, sharpness, lighting and crop are stored as signals, never as proof that a
whole panel was visible. Obstruction is not measurable from a mask and is never
inferred. A signal that could not be computed makes the view ``not_run`` (unless a
computed signal already fails), so a missing measurement never counts as a pass.

The sharpness signal (variance of the Laplacian inside the part bounding box) depends
on crop size and image content; its threshold is uncalibrated on real photographs.
"""
from __future__ import annotations

from collections.abc import Mapping

import cv2
import numpy as np
from numpy.typing import NDArray

from ...contracts.common import ContractError
from ...contracts.imaging import ViewScreen
from .config import SummaryConfig

SIGNAL_NAMES = ("part_area_fraction", "blur_score", "mean_luma", "clipped_fraction", "border_touch_fraction")
_NOT_COMPUTED = {"part_area_fraction": "part_area_not_computed", "blur_score": "sharpness_not_computed",
                 "mean_luma": "lighting_not_computed", "clipped_fraction": "lighting_not_computed",
                 "border_touch_fraction": "crop_not_computed"}
_DIGITS = 6
_MIN_SHARPNESS_CROP = 3  # the Laplacian kernel needs at least a 3 x 3 neighbourhood
_LUMA_MIN, _LUMA_MAX = 0, 255  # 8-bit clipping levels


def _grey(image: NDArray) -> NDArray[np.float64]:
    if image.ndim == 3 and image.shape[2] == 3:
        image = cv2.cvtColor(image.astype(np.uint8) if image.dtype != np.uint8 else image, cv2.COLOR_RGB2GRAY)
    elif image.ndim != 2:
        raise ContractError("mask_geometry_mismatch", "a screening image is 2-D grey or 3-channel RGB")
    return image.astype(np.float64)


def compute_view_signals(part_mask: NDArray, part_class_id: int, image: NDArray | None = None, *,
                         content_box: tuple[int, int, int, int] | None = None) -> dict[str, float]:
    """Screening signals for class ``part_class_id`` of a model-frame part mask.

    ``image`` is the photo on the same model-frame grid (grey or RGB, 0-255); without
    it only the mask signals are computed. ``content_box`` is the model-frame box that
    holds photo pixels (letterbox padding excluded); the crop signal measures contact
    with that box's edge, which is the photograph's border.
    """
    if part_mask.ndim != 2:
        raise ContractError("mask_geometry_mismatch", "a part mask is a 2-D class-index array")
    height, width = part_mask.shape
    mask = part_mask == part_class_id
    signals: dict[str, float] = {"part_area_fraction": round(float(np.count_nonzero(mask)) / mask.size, _DIGITS)}
    if not mask.any():
        return signals

    x0, y0, x1, y1 = content_box or (0, 0, width, height)
    padded = np.pad(mask, 1, constant_values=False)
    interior = (padded[1:-1, 1:-1] & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:])
    boundary = mask & ~interior
    rows, cols = np.nonzero(boundary)
    on_edge = (rows == y0) | (rows == y1 - 1) | (cols == x0) | (cols == x1 - 1)
    signals["border_touch_fraction"] = round(float(np.count_nonzero(on_edge)) / len(rows), _DIGITS)

    if image is not None:
        grey = _grey(image)
        if grey.shape != part_mask.shape:
            raise ContractError("mask_geometry_mismatch", f"image grid {grey.shape} != part grid {part_mask.shape}")
        ys, xs = np.nonzero(mask)
        crop = grey[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        if min(crop.shape) >= _MIN_SHARPNESS_CROP:
            signals["blur_score"] = round(float(cv2.Laplacian(crop, cv2.CV_64F).var()), _DIGITS)
        values = grey[mask]
        signals["mean_luma"] = round(float(values.mean()), _DIGITS)
        clipped = np.count_nonzero((values <= _LUMA_MIN) | (values >= _LUMA_MAX))
        signals["clipped_fraction"] = round(float(clipped) / values.size, _DIGITS)
    return signals


def screen_view(photo_id: str, signals: Mapping[str, float], config: SummaryConfig) -> ViewScreen:
    """Apply the versioned thresholds: ``pass``, ``fail`` with signal names, or ``not_run``."""
    s = config.summary
    failing: list[str] = []
    checks = (
        ("part_area_fraction", lambda v: v < s.min_part_area_fraction, "part_too_small"),
        ("blur_score", lambda v: v < s.min_blur_score, "low_sharpness"),
        ("mean_luma", lambda v: v < s.min_mean_luma, "too_dark"),
        ("mean_luma", lambda v: v > s.max_mean_luma, "too_bright"),
        ("clipped_fraction", lambda v: v > s.max_clipped_fraction, "clipped_exposure"),
        ("border_touch_fraction", lambda v: v > s.max_border_touch_fraction, "cropped_at_border"),
    )
    for name, fails, reason in checks:
        if name in signals and fails(float(signals[name])):
            failing.append(reason)
    missing = sorted({_NOT_COMPUTED[name] for name in SIGNAL_NAMES if name not in signals})
    result = "fail" if failing else "not_run" if missing else "pass"
    kept = {name: float(signals[name]) for name in SIGNAL_NAMES if name in signals}
    return ViewScreen(photo_id=photo_id, screen_result=result, signals=kept, reasons=[*failing, *missing])
