"""The model frame every vision worker builds for a photograph.

M1, M2 and M3 must work on one pixel grid: the damage mask is overlaid on the part mask, and
the screening signals are measured inside the part mask. They therefore build the frame with
this one function. It needs no model library, so the summary worker can use it too.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ..contracts.imaging import ImageTransform
from .transforms import ImageTransform as FramePlan
from .transforms import decode_oriented, letterbox, plan_model_frame

PIXEL_MEAN = (0.485, 0.456, 0.406)
PIXEL_STD = (0.229, 0.224, 0.225)
PAD_VALUE = 0
_SCALE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ModelFrame:
    pixels: NDArray[np.uint8]
    """The photograph in the model frame: H x W x 3, RGB, padding at ``PAD_VALUE``."""
    plan: FramePlan
    transform: ImageTransform
    """The record of this frame that M1 stores and passes on."""

    @property
    def padding(self) -> NDArray[np.bool_]:
        """True where the frame holds no photograph."""
        x0, y0, x1, y1 = self.plan.content_box()
        outside = np.ones(self.pixels.shape[:2], dtype=bool)
        outside[y0:y1, x0:x1] = False
        return outside

    def normalised(self) -> NDArray[np.float32]:
        """Channels-first float tensor data: RGB / 255, then (value - mean) / std."""
        scaled = self.pixels.astype(np.float32) / 255.0
        normalised = (scaled - np.array(PIXEL_MEAN, dtype=np.float32)) / np.array(PIXEL_STD, dtype=np.float32)
        return np.ascontiguousarray(normalised.transpose(2, 0, 1))


def frame_record(plan: FramePlan) -> ImageTransform:
    """The contract record of a planned frame. ``pad_left``/``pad_top`` are the photo's offset, not the total padding."""
    return ImageTransform(
        stored_width=plan.stored_width, stored_height=plan.stored_height, exif_orientation=plan.exif_orientation,
        model_width=plan.frame_width, model_height=plan.frame_height, scale=plan.scale,
        pad_left=float(plan.offset_x), pad_top=float(plan.offset_y), mask_frame="model")


def build_model_frame(photo: bytes, input_size: int | tuple[int, int], resize_policy: str) -> ModelFrame:
    """Decode, apply the EXIF orientation, resize the longest edge to the frame and pad."""
    oriented, orientation, (stored_width, stored_height) = decode_oriented(photo)
    plan = plan_model_frame(stored_width=stored_width, stored_height=stored_height, exif_orientation=orientation,
                            frame_size=input_size, policy=resize_policy)
    return ModelFrame(pixels=letterbox(oriented, plan, pad_value=PAD_VALUE), plan=plan, transform=frame_record(plan))


def same_frame(first: ImageTransform, second: ImageTransform) -> bool:
    """Whether two frame records describe one pixel grid for one photograph."""
    a, b = first.model_dump(), second.model_dump()
    return (abs(a.pop("scale") - b.pop("scale")) <= _SCALE_TOLERANCE and a == b)


__all__ = ["ModelFrame", "PAD_VALUE", "PIXEL_MEAN", "PIXEL_STD", "build_model_frame", "frame_record", "same_frame"]
