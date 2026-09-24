"""Photo coordinate transforms shared by the image models (integration contracts 8.2 and 11).

Coordinates are continuous pixel-edge coordinates: origin at the top-left corner of
the top-left pixel, x to the right, y down, so pixel column ``i`` spans ``[i, i + 1)``.
A normalised coordinate divides by the frame width or height. Three frames exist:

* ``stored``: the pixel grid of the uploaded bytes, before EXIF orientation;
* ``oriented``: the photo as displayed, after EXIF orientation (the "original photo");
* ``model``: the fixed model frame after scaling and padding, where M1/M2 masks live.

Every map is an exact 3x3 matrix, so an inverse is computed, never re-estimated. The
uploaded bytes are never rewritten; orientation is applied to a decoded copy only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any, Literal

import numpy as np

EXIF_ORIENTATION_TAG = 0x0112
ResizePolicy = Literal["longest_edge_pad", "longest_edge_centre_pad"]
RESIZE_POLICIES: tuple[str, ...] = ("longest_edge_pad", "longest_edge_centre_pad")


def read_exif_orientation(data: bytes) -> int:
    """EXIF orientation 1-8 of the uploaded bytes; 1 when absent or out of range."""
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        value = image.getexif().get(EXIF_ORIENTATION_TAG, 1)
    return value if isinstance(value, int) and 1 <= value <= 8 else 1


def oriented_size(orientation: int, width: int, height: int) -> tuple[int, int]:
    """Width and height after EXIF orientation; values 5-8 swap the axes."""
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


def orientation_matrix(orientation: int, width: int, height: int) -> np.ndarray:
    """Matrix mapping stored pixel-edge coordinates to oriented coordinates."""
    w, h = float(width), float(height)
    rows = {
        1: ((1, 0, 0), (0, 1, 0)),
        2: ((-1, 0, w), (0, 1, 0)),     # mirror horizontal
        3: ((-1, 0, w), (0, -1, h)),    # rotate 180
        4: ((1, 0, 0), (0, -1, h)),     # mirror vertical
        5: ((0, 1, 0), (1, 0, 0)),      # transpose
        6: ((0, -1, h), (1, 0, 0)),     # rotate 90 clockwise
        7: ((0, -1, h), (-1, 0, w)),    # transverse
        8: ((0, 1, 0), (-1, 0, w)),     # rotate 90 counter-clockwise
    }
    if orientation not in rows:
        raise ValueError(f"EXIF orientation must be 1-8, got {orientation!r}")
    top, middle = rows[orientation]
    return np.array([top, middle, (0, 0, 1)], dtype=float)


def apply_exif_orientation(image: np.ndarray, orientation: int) -> np.ndarray:
    """Return a re-oriented copy of a decoded image (rows, columns, ...)."""
    ops = {
        1: lambda a: a,
        2: lambda a: a[:, ::-1],
        3: lambda a: a[::-1, ::-1],
        4: lambda a: a[::-1],
        5: lambda a: a.swapaxes(0, 1),
        6: lambda a: np.rot90(a, k=-1),
        7: lambda a: a.swapaxes(0, 1)[::-1, ::-1],
        8: lambda a: np.rot90(a, k=1),
    }
    if orientation not in ops:
        raise ValueError(f"EXIF orientation must be 1-8, got {orientation!r}")
    return np.ascontiguousarray(ops[orientation](image))


def map_points(matrix: np.ndarray, points: Any) -> np.ndarray:
    """Apply a 3x3 projective matrix to an (N, 2) array of points."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    homogeneous = np.hstack([pts, np.ones((len(pts), 1))]) @ np.asarray(matrix, dtype=float).T
    return homogeneous[:, :2] / homogeneous[:, 2:3]


def box_to_quad(box: Any) -> np.ndarray:
    """[x_min, y_min, x_max, y_max] to four corners, clockwise from top-left."""
    x0, y0, x1, y1 = (float(v) for v in box)
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])


def quad_to_box(quad: Any) -> tuple[float, float, float, float]:
    """Axis-aligned bounds of a mapped quadrilateral (exact for 90-degree rotations)."""
    pts = np.asarray(quad, dtype=float).reshape(-1, 2)
    return (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))


def normalise_box(box: Any, width: float, height: float) -> tuple[float, float, float, float]:
    """Pixel box to [0, 1] coordinates of a frame, clipped to the frame."""
    x0, y0, x1, y1 = (float(v) for v in box)
    clip = lambda v: min(1.0, max(0.0, v))  # noqa: E731
    return (clip(x0 / width), clip(y0 / height), clip(x1 / width), clip(y1 / height))


def denormalise_box(box: Any, width: float, height: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    return (x0 * width, y0 * height, x1 * width, y1 * height)


@dataclass(frozen=True)
class ImageTransform:
    """Persisted preprocessing record (M1 ``image_preprocessing``) for one photo.

    ``offset_x``/``offset_y`` locate the resized photo inside the model frame;
    ``pad_x``/``pad_y`` are the total padding added along each axis. Scale is kept
    per axis because the resized size is rounded to whole pixels.
    """

    stored_width: int
    stored_height: int
    exif_orientation: int
    oriented_width: int
    oriented_height: int
    resize_policy: str
    scale: float
    scale_x: float
    scale_y: float
    resized_width: int
    resized_height: int
    offset_x: int
    offset_y: int
    pad_x: int
    pad_y: int
    frame_width: int
    frame_height: int
    coordinate_convention: str = "pixel_edge_top_left"

    def stored_to_oriented(self) -> np.ndarray:
        return orientation_matrix(self.exif_orientation, self.stored_width, self.stored_height)

    def oriented_to_model(self) -> np.ndarray:
        return np.array([[self.scale_x, 0, self.offset_x], [0, self.scale_y, self.offset_y], [0, 0, 1]], dtype=float)

    def model_to_oriented(self) -> np.ndarray:
        return np.linalg.inv(self.oriented_to_model())

    def oriented_to_stored(self) -> np.ndarray:
        return np.linalg.inv(self.stored_to_oriented())

    def model_to_stored(self) -> np.ndarray:
        return self.oriented_to_stored() @ self.model_to_oriented()

    def content_box(self) -> tuple[int, int, int, int]:
        """Model-frame box that holds photo pixels; everything else is padding."""
        return (self.offset_x, self.offset_y, self.offset_x + self.resized_width, self.offset_y + self.resized_height)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ImageTransform":
        return cls(**data)


def plan_model_frame(
    stored_width: int,
    stored_height: int,
    exif_orientation: int,
    frame_size: int | tuple[int, int],
    policy: str = "longest_edge_pad",
) -> ImageTransform:
    """Scale the longest oriented edge to the frame, preserving aspect, then pad."""
    if policy not in RESIZE_POLICIES:
        raise ValueError(f"unknown resize policy {policy!r}")
    frame_w, frame_h = (frame_size, frame_size) if isinstance(frame_size, int) else frame_size
    ow, oh = oriented_size(exif_orientation, stored_width, stored_height)
    scale = min(frame_w / ow, frame_h / oh)
    rw, rh = max(1, min(frame_w, round(ow * scale))), max(1, min(frame_h, round(oh * scale)))
    pad_x, pad_y = frame_w - rw, frame_h - rh
    offset_x, offset_y = (pad_x // 2, pad_y // 2) if policy == "longest_edge_centre_pad" else (0, 0)
    return ImageTransform(
        stored_width=stored_width, stored_height=stored_height, exif_orientation=exif_orientation,
        oriented_width=ow, oriented_height=oh, resize_policy=policy, scale=scale,
        scale_x=rw / ow, scale_y=rh / oh, resized_width=rw, resized_height=rh,
        offset_x=offset_x, offset_y=offset_y, pad_x=pad_x, pad_y=pad_y, frame_width=frame_w, frame_height=frame_h,
    )


def letterbox(oriented: np.ndarray, transform: ImageTransform, pad_value: Any = 0) -> np.ndarray:
    """Resize an oriented image into the model frame described by ``transform``."""
    import cv2

    if oriented.shape[:2] != (transform.oriented_height, transform.oriented_width):
        raise ValueError("image size does not match the transform's oriented size")
    shrinking = transform.resized_width < transform.oriented_width
    resized = cv2.resize(oriented, (transform.resized_width, transform.resized_height),
                         interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR)
    shape = (transform.frame_height, transform.frame_width) + oriented.shape[2:]
    frame = np.empty(shape, dtype=oriented.dtype)
    frame[...] = pad_value
    x0, y0, x1, y1 = transform.content_box()
    frame[y0:y1, x0:x1] = resized
    return frame


def decode_oriented(data: bytes) -> tuple[np.ndarray, int, tuple[int, int]]:
    """Decode uploaded bytes to an RGB array with EXIF orientation applied.

    Returns the oriented array, the orientation used and the stored (width, height).
    """
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        image.load()
        value = image.getexif().get(EXIF_ORIENTATION_TAG, 1)
        orientation = value if isinstance(value, int) and 1 <= value <= 8 else 1
        stored = np.asarray(image.convert("RGB"))
    return apply_exif_orientation(stored, orientation), orientation, (stored.shape[1], stored.shape[0])


def prepare_model_frame(
    data: bytes, frame_size: int | tuple[int, int], policy: str = "longest_edge_pad", pad_value: Any = 0,
) -> tuple[np.ndarray, ImageTransform]:
    """Decode, orient and letterbox a photo; the transform maps masks back to it."""
    oriented, orientation, (sw, sh) = decode_oriented(data)
    transform = plan_model_frame(sw, sh, orientation, frame_size, policy)
    return letterbox(oriented, transform, pad_value), transform


def round_trip_error(forward: np.ndarray, inverse: np.ndarray, points: Any) -> float:
    """Largest distance after mapping points forward and back; 0 for an exact pair."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    back = map_points(inverse, map_points(forward, pts))
    return float(np.max(np.linalg.norm(back - pts, axis=1))) if len(pts) else 0.0
