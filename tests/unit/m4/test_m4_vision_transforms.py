"""Photo-side coordinate transforms: EXIF orientation, letterbox and inverse mapping.

Covers the integration-contract "coordinate round-trip" cases for photos: a rotated
(EXIF) photo and a letterboxed photo map from the model frame back to the right region
of the original photo, within the configured tolerance.
"""
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageOps

from claim_cmev.vision.transforms import (
    ImageTransform,
    box_to_quad,
    decode_oriented,
    denormalise_box,
    map_points,
    normalise_box,
    orientation_matrix,
    plan_model_frame,
    prepare_model_frame,
    quad_to_box,
    read_exif_orientation,
    round_trip_error,
)
from m4_support import m4_config

TOLERANCE = m4_config().transforms.round_trip_tolerance_px


def _photo(width: int, height: int, orientation: int | None, red_box: tuple[int, int, int, int]) -> bytes:
    """Stored photo (before orientation) with a pure red rectangle at ``red_box`` (x0, y0, x1, y1)."""
    rng = np.random.default_rng(width * 7 + height)
    pixels = rng.integers(20, 200, (height, width, 3), dtype=np.uint8)
    pixels[..., 0] = np.minimum(pixels[..., 0], 150)
    x0, y0, x1, y1 = red_box
    pixels[y0:y1, x0:x1] = (255, 0, 0)
    buffer = BytesIO()
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif
    Image.fromarray(pixels).save(buffer, "PNG", **kwargs)
    return buffer.getvalue()


def _red_bbox(frame_rgb: np.ndarray) -> tuple[float, float, float, float]:
    r, g, b = (frame_rgb[..., i].astype(int) for i in range(3))
    ys, xs = np.nonzero((r > 200) & (g < 60) & (b < 60))
    return float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_orientation_matches_pillow_and_matrix_maps_pixels(orientation):
    data = _photo(30, 20, orientation, (3, 4, 9, 7))
    stored = np.asarray(Image.open(BytesIO(data)).convert("RGB"))
    expected = np.asarray(ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB"))
    oriented, used, (sw, sh) = decode_oriented(data)
    assert used == orientation == read_exif_orientation(data)
    assert (sw, sh) == (30, 20)
    assert np.array_equal(oriented, expected)
    matrix = orientation_matrix(orientation, sw, sh)
    for x, y in [(0, 0), (29, 0), (0, 19), (29, 19), (5, 5), (17, 11)]:
        ox, oy = map_points(matrix, [(x + 0.5, y + 0.5)])[0]
        assert np.array_equal(oriented[int(oy), int(ox)], stored[y, x])
    assert round_trip_error(matrix, np.linalg.inv(matrix), [(0, 0), (30, 20), (12.5, 3.25)]) < 1e-9


def test_invalid_or_missing_orientation_reads_as_one():
    assert read_exif_orientation(_photo(10, 10, None, (1, 1, 2, 2))) == 1
    assert read_exif_orientation(_photo(10, 10, 9, (1, 1, 2, 2))) == 1


def test_m1_worked_example_frame_plan():
    plan = plan_model_frame(4032, 3024, 6, 512)
    assert (plan.oriented_width, plan.oriented_height) == (3024, 4032)
    assert plan.scale == pytest.approx(512 / 4032)
    assert (plan.resized_width, plan.resized_height) == (384, 512)
    assert (plan.pad_x, plan.pad_y, plan.offset_x, plan.offset_y) == (128, 0, 0, 0)
    assert ImageTransform.from_dict(plan.to_dict()) == plan


def test_rotated_photo_mask_box_lands_on_original_region():
    stored_box = (250, 40, 330, 90)
    data = _photo(400, 300, 6, stored_box)
    frame, transform = prepare_model_frame(data, 128)
    assert frame.shape == (128, 128, 3)
    assert (transform.oriented_width, transform.oriented_height) == (300, 400)
    found = _red_bbox(frame)
    back = quad_to_box(map_points(transform.model_to_stored(), box_to_quad(found)))
    per_model_px = 1 / transform.scale
    assert np.allclose(back, stored_box, atol=TOLERANCE * per_model_px + 1)
    oriented = quad_to_box(map_points(transform.model_to_oriented(), box_to_quad(found)))
    expected = quad_to_box(map_points(transform.stored_to_oriented(), box_to_quad(stored_box)))
    assert np.allclose(oriented, expected, atol=TOLERANCE * per_model_px + 1)
    to_model = transform.oriented_to_model() @ transform.stored_to_oriented()
    assert round_trip_error(to_model, transform.model_to_stored(), box_to_quad(stored_box)) < TOLERANCE


@pytest.mark.parametrize("policy", ["longest_edge_pad", "longest_edge_centre_pad"])
def test_letterboxed_photo_box_lands_on_original_region(policy):
    stored_box = (300, 30, 360, 70)
    data = _photo(400, 100, None, stored_box)
    frame, transform = prepare_model_frame(data, 128, policy, pad_value=0)
    x0, y0, x1, y1 = transform.content_box()
    assert (x1 - x0, y1 - y0) == (128, 32)
    assert (y0 == 48) == (policy == "longest_edge_centre_pad")
    assert frame[:y0].sum() == 0 and frame[y1:].sum() == 0  # padding is padding
    found = _red_bbox(frame)
    back = quad_to_box(map_points(transform.model_to_stored(), box_to_quad(found)))
    assert np.allclose(back, stored_box, atol=TOLERANCE / transform.scale + 1)
    padding_point = map_points(transform.model_to_oriented(), [(64, (y1 + 128) / 2 if y1 < 128 else 130)])[0]
    assert not 0 <= padding_point[1] <= transform.oriented_height


def test_normalised_boxes_round_trip():
    box = (12.5, 40.0, 300.25, 90.0)
    assert np.allclose(denormalise_box(normalise_box(box, 640, 480), 640, 480), box)
