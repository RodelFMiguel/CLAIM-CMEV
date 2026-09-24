"""Server-rendered evidence overlays with Pillow (module 09 "Evidence panel", UI spec 10.6).

The browser draws nothing: these functions return PNG bytes. Boxes arrive normalised
on the frame the contracts define: line-item rows and pen marks on the corrected page
render, damage observations on the original photograph after EXIF orientation. A page
overlay can also be drawn on the uploaded page frame by mapping each box through the
stored ``PageTransform``. Masks are expected as binary images (nonzero is the region),
either already in the photo frame or in the model frame of the given ``ImageTransform``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from io import BytesIO
import math
from typing import Any, Literal

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from ..contracts.common import ContractError
from ..contracts.documents import PageTransform
from ..contracts.imaging import ImageTransform
from .config import ReviewConfig, default_review_config

ImageInput = bytes | Image.Image
Box = tuple[float, float, float, float]


def _load(image: ImageInput, *, exif: bool) -> Image.Image:
    img = Image.open(BytesIO(image)) if isinstance(image, (bytes, bytearray)) else image.copy()
    if exif:
        img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _png(img: Image.Image) -> bytes:
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def box_to_pixels(box: Box, width: int, height: int) -> tuple[int, int, int, int]:
    """Inclusive pixel rectangle covered by a normalised pixel-edge box."""
    x0, y0, x1, y1 = box
    left = min(width - 1, max(0, math.floor(x0 * width)))
    top = min(height - 1, max(0, math.floor(y0 * height)))
    right = max(left, min(width - 1, math.ceil(x1 * width) - 1))
    bottom = max(top, min(height - 1, math.ceil(y1 * height) - 1))
    return left, top, right, bottom


class _Canvas:
    """An RGBA layer composited once over the base image, so fills blend predictably."""

    def __init__(self, base: Image.Image):
        self.base = base
        self.layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.layer)

    def shape(self, points: Any, color: tuple[int, int, int], width: int, fill_alpha: int = 0) -> None:
        fill = (*color, fill_alpha) if fill_alpha else None
        if isinstance(points, tuple) and len(points) == 4 and not isinstance(points[0], tuple):
            self.draw.rectangle(points, fill=fill, outline=(*color, 255), width=width)
        else:
            self.draw.polygon(list(points), fill=fill, outline=(*color, 255), width=width)

    def mask_outline(self, mask: Image.Image, color: tuple[int, int, int]) -> None:
        edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
        self.layer.paste((*color, 255), mask=edge)

    def png(self) -> bytes:
        return _png(Image.alpha_composite(self.base.convert("RGBA"), self.layer).convert("RGB"))


def _page_geometry(img: Image.Image, transform: PageTransform | None,
                   frame: Literal["corrected", "source"]):
    width, height = img.size
    if frame == "corrected":
        if transform is not None and (width, height) != (transform.corrected_width, transform.corrected_height):
            raise ContractError("overlay_frame_mismatch", "the image is not the corrected render of this transform")
        return lambda box: box_to_pixels(box, width, height)
    if transform is None:
        raise ContractError("transform_required", "drawing on the uploaded page needs its PageTransform")
    if (width, height) != (transform.source_width, transform.source_height):
        raise ContractError("overlay_frame_mismatch", "the image is not the source frame of this transform")
    return lambda box: transform.norm_box_to_source_quad(box)


def render_page_highlight(
    page_image: ImageInput,
    *,
    page_id: str,
    line_items: Iterable[Any] = (),
    marks: Iterable[Any] = (),
    selected_entry_id: str | None = None,
    selected_mark_id: str | None = None,
    show_rows: bool = True,
    show_marks: bool = True,
    transform: PageTransform | None = None,
    frame: Literal["corrected", "source"] = "corrected",
    config: ReviewConfig | None = None,
) -> bytes:
    """PNG of an estimate page with its row boxes and pen-mark boxes drawn.

    ``line_items`` are ``LineItem`` records and ``marks`` are ``PenMark`` records or
    ``MarkView`` values; only those on ``page_id`` are drawn. The selected row and
    mark are filled and drawn thicker, and marks linked to the selected row are drawn
    thicker too. ``frame="source"`` draws on the uploaded page (or PDF render) through
    ``transform``.
    """
    style = (config or default_review_config()).overlay
    img = _load(page_image, exif=False)
    geometry = _page_geometry(img, transform, frame)
    canvas = _Canvas(img)
    rows = [i for i in line_items if i.page_id == page_id] if show_rows else []
    page_marks = [m for m in marks if m.page_id == page_id] if show_marks else []
    for item in sorted(rows, key=lambda i: i.entry_id == selected_entry_id):
        selected = item.entry_id == selected_entry_id
        canvas.shape(geometry(item.row_box_norm), style.colors.row_selected if selected else style.colors.row,
                     style.selected_line_width_px if selected else style.line_width_px,
                     style.selected_fill_alpha if selected else 0)
    for mark in sorted(page_marks, key=lambda m: (m.mark_id == selected_mark_id, m.entry_id == selected_entry_id)):
        selected = mark.mark_id == selected_mark_id
        linked = selected_entry_id is not None and mark.entry_id == selected_entry_id
        color = (style.colors.mark_selected if selected else
                 style.colors.mark_exclusion if mark.mark_type == "exclusion" else style.colors.mark_price_change)
        canvas.shape(geometry(mark.box_norm), color,
                     style.selected_line_width_px if selected or linked else style.line_width_px,
                     style.selected_fill_alpha if selected else 0)
    return canvas.png()


def render_mark_crop(page_image: ImageInput, box_norm: Box, *, zoom: float = 3.2, margin: float = 0.5,
                     config: ReviewConfig | None = None) -> bytes:
    """PNG crop around one mark box on the corrected render, enlarged for reading handwriting."""
    if zoom <= 0 or margin < 0:
        raise ContractError("crop_invalid", "zoom is positive and margin is nonnegative")
    style = (config or default_review_config()).overlay
    img = _load(page_image, exif=False)
    width, height = img.size
    left, top, right, bottom = box_to_pixels(box_norm, width, height)
    pad_x, pad_y = (right - left + 1) * margin, (bottom - top + 1) * margin
    crop_box = (max(0, int(left - pad_x)), max(0, int(top - pad_y)),
                min(width, int(right + 1 + pad_x)), min(height, int(bottom + 1 + pad_y)))
    canvas = _Canvas(img)
    canvas.shape((left, top, right, bottom), style.colors.mark_selected, style.line_width_px)
    composed = Image.open(BytesIO(canvas.png())).crop(crop_box)
    size = (max(1, round(composed.width * zoom)), max(1, round(composed.height * zoom)))
    return _png(composed.resize(size, Image.Resampling.LANCZOS))


def _mask_in_photo_frame(mask: ImageInput, size: tuple[int, int], transform: ImageTransform | None) -> Image.Image:
    binary = _load(mask, exif=False).convert("L").point(lambda v: 255 if v else 0)
    if binary.size == size:
        return binary
    if transform is None or binary.size != (transform.model_width, transform.model_height):
        raise ContractError("mask_geometry_mismatch", "the mask is neither in the photo frame nor the model frame")
    if transform.original_size != size:
        raise ContractError("overlay_frame_mismatch", "the photo is not the original frame of this transform")
    width, height = size
    box = (transform.pad_left, transform.pad_top,
           transform.pad_left + width * transform.scale, transform.pad_top + height * transform.scale)
    return binary.resize(size, Image.Resampling.NEAREST, box=box)


def render_photo_overlay(
    photo_image: ImageInput,
    *,
    photo_id: str,
    observations: Iterable[Any] = (),
    selected_observation_id: str | None = None,
    damage_masks: Mapping[str, ImageInput] | None = None,
    part_masks: Mapping[str, ImageInput] | None = None,
    transform: ImageTransform | None = None,
    apply_exif: bool = True,
    show_damage: bool = True,
    show_parts: bool = True,
    config: ReviewConfig | None = None,
) -> bytes:
    """PNG of a photograph with damage-observation boxes and optional mask outlines.

    ``observations`` are ``ImageDamageObservation`` records; only those on ``photo_id``
    are drawn, with ``bbox_norm`` on the original (EXIF-oriented) frame. ``damage_masks``
    maps an observation id to its binary mask; ``part_masks`` maps any label to a part
    mask. The original photograph is never modified; this returns a new rendering.
    """
    style = (config or default_review_config()).overlay
    img = _load(photo_image, exif=apply_exif)
    if transform is not None and transform.original_size != img.size:
        raise ContractError("overlay_frame_mismatch", "the photo is not the original frame of this transform")
    canvas = _Canvas(img)
    if show_parts:
        for mask in (part_masks or {}).values():
            canvas.mask_outline(_mask_in_photo_frame(mask, img.size, transform), style.colors.row)
    if show_damage:
        drawn = [o for o in observations if o.photo_id == photo_id]
        for obs in sorted(drawn, key=lambda o: o.observation_id == selected_observation_id):
            selected = obs.observation_id == selected_observation_id
            canvas.shape(box_to_pixels(obs.bbox_norm, *img.size),
                         style.colors.damage_selected if selected else style.colors.damage,
                         style.selected_line_width_px if selected else style.line_width_px,
                         style.selected_fill_alpha if selected else 0)
        for observation_id, mask in (damage_masks or {}).items():
            if any(o.observation_id == observation_id for o in drawn):
                canvas.mask_outline(_mask_in_photo_frame(mask, img.size, transform), style.colors.mask_outline)
    return canvas.png()
