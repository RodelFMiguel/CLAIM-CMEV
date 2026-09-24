"""Connected damage regions from a damage class-index mask (M2 steps 6-7).

Pure and deterministic: pixels below the confidence floor become background, each
damage class is split into connected components separately (classes are never
merged), small components are dropped with a counted reason, and an excess over the
per-photo cap is dropped by ascending area. One region is one area on one photograph,
never a count of physical damage.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from ...contracts.common import DAMAGE_CODES, ContractError
from .config import RegionConfig

BACKGROUND_ID = 0
"""Background is class id 0 in both the M1 part mask and the M2 damage mask."""


@dataclass(frozen=True)
class DamageRegion:
    """One surviving connected component. ``index`` is 1-based and stable."""

    index: int
    damage_code: str
    class_id: int
    pixel_count: int
    mean_confidence: float
    bbox_model: tuple[int, int, int, int]
    """Pixel-edge box ``[x_min, y_min, x_max, y_max)`` in the model frame."""


@dataclass(frozen=True)
class RegionSet:
    labels: NDArray[np.uint16]
    """Component-index raster on the mask grid: 0 is no region, ``i`` is region ``i``."""
    regions: tuple[DamageRegion, ...]
    dropped_low_confidence_pixels: int
    below_min_pixels_count: int
    max_components_exceeded_count: int

    @property
    def dropped_region_count(self) -> int:
        return self.below_min_pixels_count + self.max_components_exceeded_count

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons = []
        if self.dropped_low_confidence_pixels:
            reasons.append("below_min_damage_confidence")
        if self.below_min_pixels_count:
            reasons.append("below_min_pixels")
        if self.max_components_exceeded_count:
            reasons.append("max_components_exceeded")
        return tuple(reasons)


def check_damage_classes(damage_classes: Mapping[int, str]) -> None:
    if BACKGROUND_ID in damage_classes:
        raise ContractError("mask_encoding_mismatch", "class id 0 is background, not a damage class")
    unknown = sorted(set(damage_classes.values()) - set(DAMAGE_CODES))
    if unknown:
        raise ContractError("taxonomy_version_mismatch", f"not CarDD damage codes: {unknown}")


def extract_regions(damage_mask: NDArray, confidence: NDArray, damage_classes: Mapping[int, str],
                    config: RegionConfig) -> RegionSet:
    """Threshold, split per class into connected components, filter and index them."""
    if damage_mask.ndim != 2 or confidence.shape != damage_mask.shape:
        raise ContractError("mask_geometry_mismatch", "damage mask and confidence map must share one 2-D grid")
    if not np.issubdtype(damage_mask.dtype, np.integer):
        raise ContractError("mask_encoding_mismatch", "a damage mask holds integer class ids")
    check_damage_classes(damage_classes)
    present = {int(v) for v in np.unique(damage_mask)} - {BACKGROUND_ID}
    if present - set(damage_classes):
        raise ContractError("mask_encoding_mismatch", f"class ids {sorted(present - set(damage_classes))} "
                                                      "are not in the damage class map")

    confident = confidence >= config.min_damage_confidence
    damaged = damage_mask != BACKGROUND_ID
    dropped_low_confidence = int(np.count_nonzero(damaged & ~confident))
    kept_mask = np.where(confident, damage_mask, BACKGROUND_ID)

    # (class_id, component label, pixels, mean confidence, bbox) per component. Order is
    # class id, then the component's first pixel in raster order, independent of the
    # OpenCV labelling algorithm.
    class_labels: dict[int, np.ndarray] = {}
    candidates: list[tuple[int, int, int, float, tuple[int, int, int, int]]] = []
    for class_id in sorted(present):
        binary = (kept_mask == class_id).astype(np.uint8)
        if not binary.any():
            continue
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=config.connectivity)
        class_labels[class_id] = labels
        flat = labels.ravel()
        found, first = np.unique(flat, return_index=True)
        conf_sum = np.bincount(flat, weights=confidence.ravel().astype(np.float64), minlength=count)
        for label, _ in sorted(zip(found.tolist(), first.tolist()), key=lambda item: item[1]):
            if label == 0:
                continue
            x, y, w, h, area = (int(v) for v in stats[label])
            candidates.append((class_id, label, area, float(conf_sum[label] / area), (x, y, x + w, y + h)))

    below = [c for c in candidates if c[2] < config.min_damage_pixels]
    surviving = [c for c in candidates if c[2] >= config.min_damage_pixels]
    exceeded = 0
    if len(surviving) > config.max_components_per_photo:
        order = sorted(range(len(surviving)), key=lambda i: (-surviving[i][2], i))
        keep = sorted(order[:config.max_components_per_photo])
        exceeded = len(surviving) - len(keep)
        surviving = [surviving[i] for i in keep]
    if len(surviving) > np.iinfo(np.uint16).max:  # pragma: no cover - capped far lower by configuration
        raise ContractError("mask_encoding_mismatch", "more regions than a 16-bit component raster holds")

    out = np.zeros(damage_mask.shape, dtype=np.uint16)
    regions = []
    for index, (class_id, label, area, mean_conf, bbox) in enumerate(surviving, start=1):
        out[class_labels[class_id] == label] = index
        regions.append(DamageRegion(index=index, damage_code=damage_classes[class_id], class_id=class_id,
                                    pixel_count=area, mean_confidence=mean_conf, bbox_model=bbox))
    return RegionSet(labels=out, regions=tuple(regions), dropped_low_confidence_pixels=dropped_low_confidence,
                     below_min_pixels_count=len(below), max_components_exceeded_count=exceeded)
