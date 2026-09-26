"""SYNTHETIC estimate pages for M4 fixtures and the OCR smoke test.

Everything here is generated test material: a made-up workshop, made-up rows and
amounts, labelled "SYNTHETIC" on the page itself. It is never a real estimate, and a
result measured on it is not evidence about real photographed pages. Generation is
deterministic for a given seed. Each capture helper also returns the exact matrix from
the flat page frame to the produced image, so tests can check coordinates against truth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .geometry import _solve_homography, edge_to_centre

ROWS = (
    ("FRT BUMPER", "REPLACE", "1", "980.00"),
    ("FRT BUMPER", "PAINT", "1", "420.00"),
    ("HEADLAMP LH", "REPLACE", "1", "640.00"),
    ("BONNET", "REPAIR", "1", "350.00"),
    ("GRILLE", "REPLACE", "1", "180.00"),
    ("FENDER RH", "REPAIR", "1", "260.00"),
)
TOTALS = (("SUB TOTAL", "2830.00"), ("GST 9%", "254.70"), ("TOTAL", "3084.70"))


@dataclass(frozen=True)
class SyntheticLine:
    text: str
    box: tuple[float, float, float, float]  # page-frame pixel-edge box
    role: str  # header, column_header, cell, total, footer
    column: str | None = None


@dataclass(frozen=True)
class SyntheticPage:
    image: np.ndarray  # BGR uint8, flat page frame
    lines: tuple[SyntheticLine, ...]

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


def render_estimate_page(width_px: int = 1240) -> SyntheticPage:
    """A printed A4 estimate: header, ruled table (description, operation, qty, amount), totals."""
    height_px = round(width_px * 297 / 210)
    u = width_px / 1240.0
    image = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(image)
    lines: list[SyntheticLine] = []

    def text(x: float, y: float, value: str, size: float, role: str, column: str | None = None,
             anchor: str = "la") -> None:
        font = ImageFont.load_default(size=max(8, round(size * u)))
        draw.text((x * u, y * u), value, fill="black", font=font, anchor=anchor)
        x0, y0, x1, y1 = draw.textbbox((x * u, y * u), value, font=font, anchor=anchor)
        lines.append(SyntheticLine(value, (float(x0), float(y0), float(x1), float(y1)), role, column))

    text(100, 90, "SYNTHETIC WORKSHOP PTE LTD", 40, "header")
    text(100, 150, "SYNTHETIC TEST ESTIMATE - NOT A REAL DOCUMENT", 22, "header")
    text(100, 200, "ESTIMATE NO: SYN-0001", 22, "header")
    text(700, 200, "VEHICLE: SYNTH SEDAN", 22, "header")
    left, right, top, row_h = 90, 1150, 280, 62
    columns = {"description": 110, "operation": 560, "qty": 820, "amount": 1130}
    table_bottom = top + row_h * (len(ROWS) + 1)
    draw.rectangle([left * u, top * u, right * u, table_bottom * u], outline="black", width=max(1, round(2 * u)))
    for boundary in (520, 780, 900):
        draw.line([boundary * u, top * u, boundary * u, table_bottom * u], fill="black", width=max(1, round(2 * u)))
    headers = {"description": "DESCRIPTION", "operation": "OPERATION", "qty": "QTY", "amount": "AMOUNT"}
    for index, row in enumerate([tuple(headers.values()), *ROWS]):
        y = top + row_h * index
        if index:
            draw.line([left * u, y * u, right * u, y * u], fill="black", width=max(1, round(u)))
        role = "column_header" if index == 0 else "cell"
        for (column, x), value in zip(columns.items(), row):
            anchor = "ra" if column == "amount" else "la"
            text(x, y + 18, value, 24, role, column, anchor)
    y = table_bottom + 30
    for label, amount in TOTALS:
        text(780, y, label, 24, "total", "description")
        text(columns["amount"], y, amount, 24, "total", "amount", "ra")
        y += 50
    text(100, 1650, "SYNTHETIC FIXTURE PAGE GENERATED FOR TESTING", 18, "footer")
    return SyntheticPage(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), tuple(lines))


def _background(size: tuple[int, int], level: int, seed: int) -> np.ndarray:
    width, height = size
    rng = np.random.default_rng(seed)
    coarse = rng.normal(0, 12, (6, 8)).astype(np.float32)
    field = cv2.resize(coarse, (width, height), interpolation=cv2.INTER_CUBIC)
    bg = np.clip(level + field, 0, 255).astype(np.uint8)
    return cv2.merge([bg, np.clip(bg.astype(int) + 6, 0, 255).astype(np.uint8), np.clip(bg.astype(int) + 14, 0, 255).astype(np.uint8)])


def photograph(page: SyntheticPage, corners: np.ndarray, size: tuple[int, int], *, paper_gain: float = 0.86,
               background: int = 70, noise: float = 3.0, blur_sigma: float = 0.0, seed: int = 0
               ) -> tuple[np.ndarray, np.ndarray]:
    """Place the page on a darker background with the page corners at ``corners`` (tl, tr, br, bl).

    Returns the photo and the page->photo homography in pixel-edge coordinates.
    """
    src = np.array([[0, 0], [page.width, 0], [page.width, page.height], [0, page.height]], dtype=float)
    homography = _solve_homography(src, np.asarray(corners, dtype=float))
    width, height = size
    warped = cv2.warpPerspective(page.image, edge_to_centre(homography), (width, height), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full(page.image.shape[:2], 255, np.uint8), edge_to_centre(homography),
                               (width, height), flags=cv2.INTER_LINEAR)
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    photo = alpha * warped.astype(np.float32) * paper_gain + (1 - alpha) * _background(size, background, seed)
    if noise:
        photo += np.random.default_rng(seed + 1).normal(0, noise, photo.shape)
    photo = np.clip(photo, 0, 255).astype(np.uint8)
    if blur_sigma:
        photo = cv2.GaussianBlur(photo, (0, 0), blur_sigma)
    return photo, homography


def camera_corners(page: SyntheticPage, size: tuple[int, int], *, pitch_deg: float = 0.0, yaw_deg: float = 0.0,
                   roll_deg: float = 0.0, fill: float = 0.7, focal_factor: float = 0.9,
                   offset: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Page corners seen by a pinhole camera (square pixels, principal point at the centre).

    ``fill`` is roughly the fraction of the image height the page spans; ``offset``
    shifts the page in the image as a fraction of the image size.
    """
    width, height = size
    focal = focal_factor * max(width, height)
    distance = focal * page.height / (fill * height)
    half = np.array([page.width / 2, page.height / 2])
    corners = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype=float) * np.append(half, 0)
    p, y, r = (math.radians(v) for v in (pitch_deg, yaw_deg, roll_deg))
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    world = corners @ (rz @ ry @ rx).T + np.array([offset[0] * width * distance / focal,
                                                    offset[1] * height * distance / focal, distance])
    return np.column_stack([focal * world[:, 0] / world[:, 2] + width / 2, focal * world[:, 1] / world[:, 2] + height / 2])


def rotate_within_frame(page: SyntheticPage, angle_deg: float, fill: int = 255) -> tuple[np.ndarray, np.ndarray]:
    """A scan skewed by ``angle_deg`` (positive: lines descend to the right) with no visible edge.

    The page is rotated about its centre inside a white canvas of the same size, so no
    page boundary exists. Returns the image and the page->image matrix.
    """
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    centre = np.array([page.width / 2, page.height / 2])
    matrix = np.eye(3)
    matrix[:2, :2] = [[c, -s], [s, c]]
    matrix[:2, 2] = centre - matrix[:2, :2] @ centre
    image = cv2.warpPerspective(page.image, edge_to_centre(matrix), (page.width, page.height),
                                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(fill,) * 3)
    return image, matrix


def add_glare(image: np.ndarray, centre: tuple[float, float], axes: tuple[float, float]) -> np.ndarray:
    """Saturated specular patch that washes out print underneath it."""
    mask = np.zeros(image.shape[:2], np.float32)
    cv2.ellipse(mask, (round(centre[0]), round(centre[1])), (round(axes[0]), round(axes[1])), 0, 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), max(axes) * 0.15)
    boosted = image.astype(np.float32) + mask[..., None] * 400.0
    return np.clip(boosted, 0, 255).astype(np.uint8)


def add_shadow(image: np.ndarray, fraction: float = 0.5, depth: float = 0.45) -> np.ndarray:
    """Darken the right part of the image with a soft edge, like a hand or phone shadow."""
    width = image.shape[1]
    ramp = np.clip((np.arange(width) - width * (1 - fraction)) / (width * 0.08), 0, 1).astype(np.float32)
    gain = 1.0 - depth * ramp
    return np.clip(image.astype(np.float32) * gain[None, :, None], 0, 255).astype(np.uint8)


def add_pen_strokes(image: np.ndarray, boxes: list[tuple[float, float, float, float]], seed: int = 0) -> np.ndarray:
    """Blue ballpoint-like scribbles over the given boxes (obscured print)."""
    out = image.copy()
    rng = np.random.default_rng(seed)
    for x0, y0, x1, y1 in boxes:
        for k in range(5):
            ya = y0 + (y1 - y0) * (0.2 + 0.15 * k) + rng.normal(0, 1.5)
            yb = y0 + (y1 - y0) * (0.8 - 0.15 * k) + rng.normal(0, 1.5)
            cv2.line(out, (round(x0 - 6), round(ya)), (round(x1 + 6), round(yb)), (170, 60, 20), 4, cv2.LINE_AA)
    return out


def make_unreadable(image: np.ndarray, seed: int = 0) -> np.ndarray:
    """Heavy defocus plus fog: print is gone, the paper is still there."""
    blurred = cv2.GaussianBlur(image, (0, 0), 12)
    fog = 0.25 * blurred.astype(np.float32) + 0.75 * 190.0
    fog += np.random.default_rng(seed).normal(0, 2.0, fog.shape)
    return np.clip(fog, 0, 255).astype(np.uint8)
