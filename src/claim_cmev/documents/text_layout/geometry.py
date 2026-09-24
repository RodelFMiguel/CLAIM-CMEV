"""Page geometry correction (M4 steps 5-8), pure and testable without an OCR engine.

Perspective correction runs only on a reliable page boundary; otherwise the page is
at most deskewed, because a bad warp destroys the table geometry M5 and M6 rely on.
Homographies map render pixel-edge coordinates to rectified pixel-edge coordinates
(see ``claim_cmev.vision.transforms``); the inverse is stored alongside.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from .config import PageConfig, PageReadingConfig

CorrectionKind = Literal["perspective", "rotation", "none"]
_EDGE_FROM_CENTRE = np.array([[1.0, 0.0, 0.5], [0.0, 1.0, 0.5], [0.0, 0.0, 1.0]])


@dataclass(frozen=True)
class BoundaryResult:
    found: bool
    reliable: bool
    reason: str
    quad: np.ndarray | None = None  # tl, tr, br, bl in render pixel-edge coordinates
    area_fraction: float | None = None
    corner_angles_deg: tuple[float, ...] | None = None
    contrast: float | None = None


@dataclass(frozen=True)
class SkewEstimate:
    reliable: bool
    reason: str
    angle_deg: float | None = None  # positive: text lines descend to the right
    peak_ratio: float | None = None


@dataclass(frozen=True)
class GeometryResult:
    kind: CorrectionKind
    reason: str
    homography: np.ndarray  # render -> rectified
    inverse: np.ndarray  # rectified -> render
    image: np.ndarray
    boundary: BoundaryResult
    skew: SkewEstimate | None
    rotation_deg: float  # rotation applied to the render, positive clockwise as displayed
    skew_uncorrected: bool
    source_page_width_px: float
    aspect_method: str | None = None  # perspective only: focal_estimate or side_lengths

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def correction_applied(self) -> bool:
        """Data contracts: whether perspective correction was applied."""
        return self.kind == "perspective"


def to_gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def edge_to_centre(matrix: np.ndarray) -> np.ndarray:
    """Convert an edge-coordinate homography to OpenCV's pixel-centre convention."""
    return np.linalg.inv(_EDGE_FROM_CENTRE) @ matrix @ _EDGE_FROM_CENTRE


def order_corners(points: np.ndarray) -> np.ndarray:
    """Order four points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(points, dtype=float).reshape(4, 2)
    total, diff = pts.sum(axis=1), pts[:, 1] - pts[:, 0]
    return np.array([pts[np.argmin(total)], pts[np.argmin(diff)], pts[np.argmax(total)], pts[np.argmax(diff)]])


def corner_angles(quad: np.ndarray) -> tuple[float, ...]:
    angles = []
    for i in range(4):
        a, b = quad[i - 1] - quad[i], quad[(i + 1) % 4] - quad[i]
        cosine = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))
    return tuple(angles)


def polygon_area(quad: np.ndarray) -> float:
    x, y = quad[:, 0], quad[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def detect_page_boundary(image: np.ndarray, cfg: PageConfig) -> BoundaryResult:
    """Greyscale, blur, Canny, contours, largest convex quadrilateral, then the gates."""
    gray = to_gray(image)
    height, width = gray.shape
    scale = min(1.0, cfg.boundary_detect_max_side_px / max(height, width))
    small = cv2.resize(gray, (max(1, round(width * scale)), max(1, round(height * scale))),
                       interpolation=cv2.INTER_AREA) if scale < 1 else gray
    sx, sy = small.shape[1] / width, small.shape[0] / height
    edges = cv2.Canny(cv2.GaussianBlur(small, (5, 5), 0), cfg.canny_low, cfg.canny_high)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hulls = sorted((cv2.convexHull(c) for c in contours), key=cv2.contourArea, reverse=True)
    small_quad = None
    for hull in hulls[:5]:
        approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            small_quad = approx.reshape(4, 2).astype(float)
            break
    if small_quad is None:
        return BoundaryResult(False, False, "boundary_not_found")
    small_quad = order_corners(small_quad + 0.5)  # pixel centres to edge coordinates
    quad = small_quad / np.array([sx, sy])
    area = polygon_area(quad) / float(width * height)
    angles = corner_angles(quad)
    contrast, touches = _boundary_contrast(small, small_quad, cfg.boundary_strip_px)
    common = dict(found=True, quad=quad, area_fraction=area, corner_angles_deg=angles, contrast=contrast)
    if area < cfg.min_page_area_fraction:
        return BoundaryResult(reliable=False, reason="boundary_area_below_minimum", **common)
    if max(abs(a - 90.0) for a in angles) > cfg.max_corner_angle_deviation_deg:
        return BoundaryResult(reliable=False, reason="boundary_corner_angle_out_of_range", **common)
    if touches:
        return BoundaryResult(reliable=False, reason="boundary_touches_frame", **common)
    if contrast is None or contrast < cfg.min_boundary_contrast:
        return BoundaryResult(reliable=False, reason="boundary_low_contrast", **common)
    return BoundaryResult(reliable=True, reason="boundary_reliable", **common)


def _boundary_contrast(small: np.ndarray, quad: np.ndarray, strip: int) -> tuple[float | None, bool]:
    """Median grey step across the boundary; ``touches`` when little lies outside it."""
    mask = np.zeros(small.shape, np.uint8)
    cv2.fillConvexPoly(mask, np.round(quad - 0.5).astype(np.int32), 255)
    gap = 2

    def grow(m: np.ndarray, n: int, op) -> np.ndarray:
        return op(m, np.ones((2 * n + 1, 2 * n + 1), np.uint8))

    inner = (grow(mask, gap, cv2.erode) > 0) & ~(grow(mask, gap + strip, cv2.erode) > 0)
    outer = (grow(mask, gap + strip, cv2.dilate) > 0) & ~(grow(mask, gap, cv2.dilate) > 0)
    if inner.sum() == 0:
        return None, True
    touches = outer.sum() < 0.25 * inner.sum()
    if outer.sum() == 0:
        return None, touches
    return abs(float(np.median(small[inner])) - float(np.median(small[outer]))), touches


def estimate_skew(image: np.ndarray, cfg: PageConfig) -> SkewEstimate:
    """Dominant text-line angle by projection-profile sharpness over foreground pixels."""
    gray = to_gray(image)
    scale = min(1.0, 1000.0 / gray.shape[1])
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    binary = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    ys, xs = np.nonzero(binary)
    if len(xs) < cfg.skew_min_foreground_px:
        return SkewEstimate(False, "skew_no_text_foreground")
    stride = max(1, len(xs) // 60000)
    xs, ys = xs[::stride].astype(float), ys[::stride].astype(float)

    def score(angle: float) -> float:
        t = math.radians(angle)
        projected = ys * math.cos(t) - xs * math.sin(t)
        counts = np.bincount((projected - projected.min()).astype(np.int64))
        return float(np.dot(counts, counts))

    limit = cfg.skew_search_range_deg
    coarse = np.arange(-limit, limit + 1e-9, 0.5)
    scores = np.array([score(a) for a in coarse])
    best = float(coarse[int(np.argmax(scores))])
    fine = np.arange(best - 0.5, best + 0.5 + 1e-9, 0.05)
    angle = float(fine[int(np.argmax([score(a) for a in fine]))])
    ratio = float(scores.max() / max(np.median(scores), 1e-12))
    if ratio < cfg.skew_min_peak_ratio:
        return SkewEstimate(False, "skew_estimate_unreliable", round(angle, 2), ratio)
    return SkewEstimate(True, "skew_estimated", round(angle, 2), ratio)


def _paper_level(gray: np.ndarray) -> int:
    return int(np.percentile(gray, 90))


def correct_page_geometry(image: np.ndarray, config: PageReadingConfig | PageConfig) -> GeometryResult:
    """Perspective-correct a reliable page, else deskew within limits, else leave it.

    The reason code is always recorded; ``none`` is a normal outcome for a flat scan and
    the honest outcome for a page whose boundary could not be found.
    """
    cfg = config.page if isinstance(config, PageReadingConfig) else config
    height, width = image.shape[:2]
    boundary = detect_page_boundary(image, cfg)
    if boundary.reliable and boundary.quad is not None:
        tl, tr, br, bl = boundary.quad
        page_w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
        aspect, aspect_method = page_aspect(boundary.quad, width, height)
        out_w = cfg.rectified_width_px
        out_h = max(1, round(out_w * aspect))
        target = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], dtype=np.float64)
        homography = _solve_homography(boundary.quad, target)
        rectified = cv2.warpPerspective(image, edge_to_centre(homography), (out_w, out_h),
                                        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return GeometryResult("perspective", "reliable_page_boundary", homography, np.linalg.inv(homography),
                              rectified, boundary, None, 0.0, False, float(page_w), aspect_method)
    skew = estimate_skew(image, cfg)
    identity = np.eye(3)

    def unchanged(reason: str, uncorrected: bool = False) -> GeometryResult:
        return GeometryResult("none", reason, identity, identity, image, boundary, skew, 0.0, uncorrected, float(width))

    if not skew.reliable or skew.angle_deg is None:
        return unchanged(skew.reason)
    if abs(skew.angle_deg) < cfg.min_deskew_angle_deg:
        return unchanged("skew_below_threshold")
    if abs(skew.angle_deg) > cfg.max_deskew_angle_deg:
        return unchanged("skew_exceeds_limit", uncorrected=True)
    homography, (out_w, out_h) = _deskew_homography(width, height, skew.angle_deg)
    paper = _paper_level(to_gray(image))
    rectified = cv2.warpPerspective(image, edge_to_centre(homography), (out_w, out_h), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=(paper, paper, paper))
    return GeometryResult("rotation", "boundary_unreliable_deskewed", homography, np.linalg.inv(homography),
                          rectified, boundary, skew, -skew.angle_deg, False, float(width))


def page_aspect(quad: np.ndarray, width: int, height: int) -> tuple[float, str]:
    """Physical height/width of the page seen as ``quad`` (tl, tr, br, bl).

    Side-length ratios are biased under perspective, so the focal length is estimated
    from the quadrilateral (Zhang and He, whiteboard scanning, 2007) assuming square
    pixels and the principal point at the image centre. The estimate is used only when
    it is plausible; otherwise the side-length ratio is returned. The method is recorded.
    """
    tl, tr, br, bl = (np.asarray(p, dtype=float) for p in quad)
    side = float((np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)))
    u0, v0 = width / 2.0, height / 2.0
    m1, m2, m3, m4 = (np.array([p[0] - u0, p[1] - v0, 1.0]) for p in (tl, tr, bl, br))
    k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
    k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    n2, n3 = k2 * m2 - m1, k3 * m3 - m1
    if abs(n2[2] * n3[2]) < 1e-12:
        return side, "side_lengths"
    focal_sq = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2])
    diagonal = math.hypot(width, height)
    if not focal_sq > 0 or not 0.3 * diagonal <= math.sqrt(focal_sq) <= 5.0 * diagonal:
        return side, "side_lengths"
    inv = np.diag([1.0 / focal_sq, 1.0 / focal_sq, 1.0])
    ratio = math.sqrt(float(n3 @ inv @ n3) / float(n2 @ inv @ n2))
    if abs(ratio / side - 1.0) > 0.25:
        return side, "side_lengths"
    return ratio, "focal_estimate"


def _solve_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Exact four-point homography in float64 (getPerspectiveTransform rounds to float32)."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.extend([u, v])
    h = np.linalg.solve(np.array(rows, dtype=float), np.array(rhs, dtype=float))
    return np.append(h, 1.0).reshape(3, 3)


def _deskew_homography(width: int, height: int, angle_deg: float) -> tuple[np.ndarray, tuple[int, int]]:
    """Rotate by -angle about the centre into a canvas that keeps every source pixel."""
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    out_w = math.ceil(width * abs(c) + height * abs(s))
    out_h = math.ceil(width * abs(s) + height * abs(c))
    rotation = np.array([[c, s], [-s, c]])
    shift = np.array([out_w / 2, out_h / 2]) - rotation @ np.array([width / 2, height / 2])
    matrix = np.eye(3)
    matrix[:2, :2], matrix[:2, 2] = rotation, shift
    return matrix, (out_w, out_h)


def draw_boundary_debug(image: np.ndarray, boundary: BoundaryResult) -> np.ndarray:
    """Optional debug artifact: the detected quadrilateral over the render."""
    debug = image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if boundary.quad is not None:
        colour = (0, 160, 0) if boundary.reliable else (0, 0, 220)
        cv2.polylines(debug, [np.round(boundary.quad - 0.5).astype(np.int32)], True, colour, 3)
    return debug
