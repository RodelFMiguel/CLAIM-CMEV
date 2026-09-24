"""The stored page transform (data contracts section 4): rectified <-> render <-> source.

Frames, all pixel-edge coordinates with a top-left origin unless stated:

* ``rectified``: the corrected render that OCR read and M5/M6 use; boxes are
  normalised on this frame;
* ``render``: the uploaded page after EXIF orientation, or the rasterised PDF page
  (``render.png``); ``quad_original`` lives here;
* ``source``: the uploaded file itself, either stored image pixels (before EXIF
  orientation) or PDF user-space points (bottom-left origin, as in the PDF).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from claim_cmev.vision.transforms import map_points

from .geometry import GeometryResult
from .raster import SourceFrame


def _matrix(value: Any) -> list[list[float]]:
    return [[float(v) for v in row] for row in np.asarray(value, dtype=float)]


@dataclass(frozen=True)
class PageTransform:
    source: SourceFrame
    render_width: int
    render_height: int
    rectified_width: int
    rectified_height: int
    geometry_correction: str
    render_to_rectified: np.ndarray
    rectified_to_render: np.ndarray

    @classmethod
    def build(cls, source: SourceFrame, render_shape: tuple[int, ...], geometry: GeometryResult) -> "PageTransform":
        return cls(source, int(render_shape[1]), int(render_shape[0]), geometry.width, geometry.height,
                   geometry.kind, geometry.homography, geometry.inverse)

    @property
    def rectified_to_source(self) -> np.ndarray:
        return self.source.render_to_source @ self.rectified_to_render

    @property
    def source_to_rectified(self) -> np.ndarray:
        return self.render_to_rectified @ np.linalg.inv(self.source.render_to_source)

    def rectified_to_original_points(self, points: Any) -> np.ndarray:
        return map_points(self.rectified_to_render, points)

    def original_to_rectified_points(self, points: Any) -> np.ndarray:
        return map_points(self.render_to_rectified, points)

    def rectified_to_source_points(self, points: Any) -> np.ndarray:
        return map_points(self.rectified_to_source, points)

    def source_to_rectified_points(self, points: Any) -> np.ndarray:
        return map_points(self.source_to_rectified, points)

    def round_trip_error(self, rectified_points: Any) -> float:
        """Largest pixel error of rectified -> render -> rectified and via the source frame."""
        pts = np.asarray(rectified_points, dtype=float).reshape(-1, 2)
        if not len(pts):
            return 0.0
        via_render = self.original_to_rectified_points(self.rectified_to_original_points(pts))
        via_source = self.source_to_rectified_points(self.rectified_to_source_points(pts))
        return float(max(np.linalg.norm(via_render - pts, axis=1).max(), np.linalg.norm(via_source - pts, axis=1).max()))

    def to_dict(self) -> dict[str, Any]:
        s = self.source
        return {
            "coordinate_convention": "pixel_edge_top_left",
            "source_kind": s.source_kind,
            "source_frame": s.source_frame,
            "stored_width": s.stored_width,
            "stored_height": s.stored_height,
            "exif_orientation": s.exif_orientation,
            "render_dpi": s.render_dpi,
            "render_scale": s.render_scale,
            "pdf_page_width_pt": s.pdf_page_width_pt,
            "pdf_page_height_pt": s.pdf_page_height_pt,
            "pdf_rotation": s.pdf_rotation,
            "render_width": self.render_width,
            "render_height": self.render_height,
            "rectified_width": self.rectified_width,
            "rectified_height": self.rectified_height,
            "geometry_correction": self.geometry_correction,
            "render_to_rectified": _matrix(self.render_to_rectified),
            "rectified_to_render": _matrix(self.rectified_to_render),
            "render_to_source": _matrix(s.render_to_source),
            "rectified_to_source": _matrix(self.rectified_to_source),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageTransform":
        source = SourceFrame(
            source_kind=data["source_kind"], source_frame=data["source_frame"],
            render_to_source=np.asarray(data["render_to_source"], dtype=float),
            stored_width=data["stored_width"], stored_height=data["stored_height"],
            exif_orientation=data["exif_orientation"], render_dpi=data["render_dpi"],
            render_scale=data["render_scale"], pdf_page_width_pt=data["pdf_page_width_pt"],
            pdf_page_height_pt=data["pdf_page_height_pt"], pdf_rotation=data["pdf_rotation"],
        )
        return cls(source, data["render_width"], data["render_height"], data["rectified_width"],
                   data["rectified_height"], data["geometry_correction"],
                   np.asarray(data["render_to_rectified"], dtype=float), np.asarray(data["rectified_to_render"], dtype=float))
