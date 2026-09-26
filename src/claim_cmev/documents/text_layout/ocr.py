"""OCR engine protocol, a deterministic stub engine and the granularity assertion.

An engine returns regions exactly as it produced them: quadrilateral, text, confidence
or None, and the level it actually reports (word, line or block). M4 never splits,
merges or re-levels a region: a line box is never turned into invented word boxes.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import cv2
import numpy as np

Granularity = Literal["word", "line", "block"]
ActualGranularity = Literal["word", "line", "block", "mixed"]
SourceKind = Literal["real", "synthetic", "fixture"]


class OcrEngineError(RuntimeError):
    """Engine could not be loaded or run; ``reason_code`` is a stable machine code."""

    def __init__(self, reason_code: str, message: str = ""):
        super().__init__(f"{reason_code}: {message}" if message else reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OcrEngineInfo:
    engine: str
    engine_version: str
    granularity: Granularity  # what this engine configuration returns
    lang: str
    weights: Mapping[str, str] = field(default_factory=dict)  # detection/recognition/angle identifiers
    source_kind: SourceKind = "real"

    def version_entries(self) -> dict[str, str]:
        """Entries merged into every page row's ``versions`` map."""
        entries = {"ocr_engine": self.engine, "ocr_version": self.engine_version, "ocr_lang": self.lang}
        entries.update({f"ocr_weights_{k}": v for k, v in sorted(self.weights.items())})
        return entries


@dataclass(frozen=True)
class OcrRegion:
    quad: tuple[tuple[float, float], ...]  # engine-input pixel-edge coordinates
    text: str
    confidence: float | None
    level: Granularity


@runtime_checkable
class OcrEngine(Protocol):
    @property
    def info(self) -> OcrEngineInfo: ...

    def read(self, image: np.ndarray) -> Sequence[OcrRegion]:
        """Read a BGR uint8 image; raise on failure, never return [] for a failure."""
        ...


class StubOcrEngine:
    """Deterministic engine for tests and fixture mode; always labelled ``fixture``."""

    def __init__(self, regions: Sequence[OcrRegion] | Callable[[np.ndarray], Sequence[OcrRegion]] = (), *,
                 granularity: Granularity = "line", engine: str = "stub-ocr", engine_version: str = "0.0.0",
                 error: Exception | None = None):
        self._regions = regions
        self._error = error
        self._info = OcrEngineInfo(engine, engine_version, granularity, "en", {"fixture": "none"}, "fixture")
        self.calls = 0

    @property
    def info(self) -> OcrEngineInfo:
        return self._info

    def read(self, image: np.ndarray) -> Sequence[OcrRegion]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._regions(image) if callable(self._regions) else self._regions)


def canonical_quad(points: Sequence[Sequence[float]]) -> tuple[tuple[float, float], ...]:
    """Four points ordered clockwise (as displayed) starting nearest the top-left."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    if len(pts) != 4:
        raise OcrEngineError("ocr_output_invalid", f"expected a four-point quadrilateral, got {len(pts)} points")
    centre = pts.mean(axis=0)
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0]))]
    pts = np.roll(pts, -int(np.argmin(pts.sum(axis=1))), axis=0)
    return tuple((float(x), float(y)) for x, y in pts)


@dataclass(frozen=True)
class GranularityCheck:
    actual: ActualGranularity | None  # None when the engine returned no region at all
    ok: bool
    detail: str | None  # machine detail code when not ok
    message: str


def check_granularity(regions: Sequence[OcrRegion], declared: Granularity, expected: Granularity,
                      nested_containment: float) -> GranularityCheck:
    """Assert the engine output has one level, the one declared and configured.

    Three failures, all reported by the caller as ``granularity_mismatch``:
    a region whose level contradicts the engine's declaration (a line engine can never
    yield word-level output), nested regions (a word box inside a line box), and an
    actual level that differs from ``page.expected_text_box_granularity``.
    """
    if not regions:
        return GranularityCheck(None, True, None, "no regions returned")
    levels = sorted({r.level for r in regions})
    actual: ActualGranularity = levels[0] if len(levels) == 1 else "mixed"  # type: ignore[assignment]
    if actual != declared:
        return GranularityCheck(actual, False, "engine_granularity_contradiction",
                                f"engine declared {declared!r} but returned {', '.join(levels)} regions")
    nested = find_nested_regions(regions, nested_containment)
    if nested:
        inner, outer = nested[0]
        return GranularityCheck("mixed", False, "nested_text_boxes",
                                f"{len(nested)} region(s) nested inside others, e.g. {regions[inner].text!r} "
                                f"inside {regions[outer].text!r}")
    if actual != expected:
        return GranularityCheck(actual, False, "unexpected_granularity",
                                f"engine returned {actual!r} boxes; configuration expects {expected!r}")
    return GranularityCheck(actual, True, None, f"{actual} granularity as configured")


def find_nested_regions(regions: Sequence[OcrRegion], containment: float) -> list[tuple[int, int]]:
    """Pairs (inner, outer) where ``containment`` of inner's area lies inside outer."""
    quads = [np.asarray(r.quad, dtype=np.float32).reshape(-1, 2) for r in regions]
    bounds = np.array([[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()] for q in quads])
    areas = [abs(cv2.contourArea(q)) for q in quads]
    pairs = []
    for i, qi in enumerate(quads):
        if areas[i] <= 0:
            continue
        overlap = ((bounds[:, 0] < bounds[i, 2]) & (bounds[:, 2] > bounds[i, 0])
                   & (bounds[:, 1] < bounds[i, 3]) & (bounds[:, 3] > bounds[i, 1]))
        for j in np.nonzero(overlap)[0]:
            if j == i or areas[j] < areas[i]:
                continue
            inter, _ = cv2.intersectConvexConvex(_convex(qi), _convex(quads[j]))
            if inter / areas[i] >= containment:
                pairs.append((i, int(j)))
    return pairs


def _convex(quad: np.ndarray) -> np.ndarray:
    return cv2.convexHull(quad.astype(np.float32)).reshape(-1, 2)
