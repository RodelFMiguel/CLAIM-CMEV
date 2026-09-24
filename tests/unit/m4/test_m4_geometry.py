"""correct_page_geometry: reliability gate, rotation fallback and the stored homography pair."""
import math

import cv2
import numpy as np
import pytest

from claim_cmev.documents.text_layout import correct_page_geometry
from claim_cmev.documents.text_layout.geometry import page_aspect
from claim_cmev.documents.text_layout.synthetic import (
    camera_corners,
    photograph,
    render_estimate_page,
    rotate_within_frame,
)
from claim_cmev.vision.transforms import map_points
from m4_support import m4_config

CFG = m4_config()
PAGE = render_estimate_page(1240)
SIZE = (1200, 1600)
PAGE_POINTS = np.array([[x, y] for x in (60, 620, 1180) for y in (60, 877, 1690)], dtype=float)


def _page_error(geometry, page_to_image) -> float:
    """Largest distance between where page points land and a uniformly scaled page."""
    mapped = map_points(geometry.homography @ page_to_image, PAGE_POINTS)
    return float(np.abs(mapped - PAGE_POINTS * geometry.width / PAGE.width).max())


def _assert_pair(geometry):
    assert np.allclose(geometry.inverse @ geometry.homography, np.eye(3), atol=1e-9)


def test_flat_scan_is_left_uncorrected_with_a_reason():
    geometry = correct_page_geometry(PAGE.image, CFG)
    assert (geometry.kind, geometry.reason) == ("none", "skew_below_threshold")
    assert not geometry.correction_applied and not geometry.boundary.reliable
    assert np.array_equal(geometry.homography, np.eye(3)) and geometry.image is PAGE.image
    _assert_pair(geometry)


def test_a_ruled_frame_inside_a_flat_scan_is_not_taken_for_the_page():
    framed = PAGE.image.copy()
    cv2.rectangle(framed, (60, 60), (1180, 1690), (0, 0, 0), 3)
    geometry = correct_page_geometry(framed, CFG)
    assert geometry.boundary.found and geometry.boundary.area_fraction > CFG.page.min_page_area_fraction
    assert geometry.boundary.reason == "boundary_low_contrast"
    assert geometry.kind == "none"


def test_perspective_photo_is_rectified_to_the_page():
    corners = camera_corners(PAGE, SIZE, pitch_deg=25, yaw_deg=-15, roll_deg=3)
    photo, page_to_photo = photograph(PAGE, corners, SIZE, seed=1)
    geometry = correct_page_geometry(photo, CFG)
    assert (geometry.kind, geometry.reason) == ("perspective", "reliable_page_boundary")
    assert geometry.correction_applied and geometry.aspect_method == "focal_estimate"
    assert geometry.width == CFG.page.rectified_width_px
    assert geometry.height == pytest.approx(PAGE.height * geometry.width / PAGE.width, abs=10)
    assert _page_error(geometry, page_to_photo) < 0.01 * geometry.width
    _assert_pair(geometry)


def test_rotated_photo_with_a_visible_boundary_is_rectified():
    photo, page_to_photo = photograph(PAGE, camera_corners(PAGE, SIZE, roll_deg=9, pitch_deg=5, fill=0.62),
                                      SIZE, seed=2, blur_sigma=1.0)
    geometry = correct_page_geometry(photo, CFG)
    assert geometry.kind == "perspective"
    assert _page_error(geometry, page_to_photo) < 0.01 * geometry.width
    _assert_pair(geometry)


def test_rotated_scan_without_a_boundary_is_deskewed_without_losing_content():
    image, page_to_image = rotate_within_frame(PAGE, 4.0)
    geometry = correct_page_geometry(image, CFG)
    assert (geometry.kind, geometry.reason) == ("rotation", "boundary_unreliable_deskewed")
    assert not geometry.correction_applied
    assert geometry.rotation_deg == pytest.approx(-4.0, abs=0.2)
    combined = geometry.homography @ page_to_image
    direction = map_points(combined, [(0, 0), (1000, 0)])
    angle = math.degrees(math.atan2(*(direction[1] - direction[0])[::-1]))
    assert abs(angle) < 0.2  # text lines are horizontal again
    corners = map_points(geometry.homography, [(0, 0), (1240, 0), (1240, PAGE.height), (0, PAGE.height)])
    assert corners.min() >= -1e-6 and corners[:, 0].max() <= geometry.width and corners[:, 1].max() <= geometry.height
    _assert_pair(geometry)


def test_weak_boundary_falls_back_instead_of_warping():
    photo, _ = photograph(PAGE, camera_corners(PAGE, SIZE, fill=0.3, roll_deg=3), SIZE, seed=3)
    geometry = correct_page_geometry(photo, CFG)
    assert geometry.boundary.found and geometry.boundary.reason == "boundary_area_below_minimum"
    assert geometry.kind != "perspective" and not geometry.correction_applied


def test_clearly_skewed_page_without_boundary_is_not_rotated_and_is_marked():
    image, _ = rotate_within_frame(PAGE, 25.0)
    geometry = correct_page_geometry(image, CFG)
    assert (geometry.kind, geometry.reason) == ("none", "skew_exceeds_limit")
    assert geometry.skew_uncorrected and geometry.skew.angle_deg == pytest.approx(25.0, abs=0.3)


@pytest.mark.parametrize("image, reason", [
    (np.full((1754, 1240, 3), 230, np.uint8), "skew_no_text_foreground"),
    (rotate_within_frame(PAGE, 0.3)[0], "skew_below_threshold"),
])
def test_uncorrected_outcomes_carry_their_reason(image, reason):
    geometry = correct_page_geometry(image, CFG)
    assert (geometry.kind, geometry.reason) == ("none", reason)


def test_page_aspect_falls_back_to_side_lengths_for_a_flat_view():
    quad = np.array([[100, 100], [1100, 100], [1100, 1514], [100, 1514]], dtype=float)
    aspect, method = page_aspect(quad, 1200, 1600)
    assert method == "side_lengths" and aspect == pytest.approx(1.414)
