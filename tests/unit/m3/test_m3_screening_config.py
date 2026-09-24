"""The four screening signals on synthetic images, their thresholds and the M3 configuration."""
import cv2
import numpy as np
import pydantic
import pytest

from claim_cmev.contracts.common import PART_CODES, ContractError
from claim_cmev.vision.multiview import compute_view_signals, load_summary_config, screen_view
from m3_support import CFG, PASS

HOOD = 14


def _scene(size=128):
    """A textured hood region on rows [32, 96), columns [16, 112); background elsewhere."""
    parts = np.zeros((size, size), dtype=np.uint8)
    parts[32:96, 16:112] = HOOD
    yy, xx = np.mgrid[0:size, 0:size]
    image = np.where(((yy // 4) + (xx // 4)) % 2 == 0, 60, 190).astype(np.uint8)
    return parts, image


def test_mask_signals_without_an_image():
    parts, _ = _scene()
    signals = compute_view_signals(parts, HOOD)
    assert signals == {"part_area_fraction": round(64 * 96 / 128**2, 6), "border_touch_fraction": 0.0}
    assert compute_view_signals(parts, 3) == {"part_area_fraction": 0.0}


def test_sharpness_and_lighting_signals():
    parts, image = _scene()
    sharp = compute_view_signals(parts, HOOD, image)
    blurred = compute_view_signals(parts, HOOD, cv2.GaussianBlur(image, (0, 0), 3))
    assert sharp["blur_score"] > blurred["blur_score"] * 5
    assert sharp["mean_luma"] == pytest.approx(125.0, abs=1.0) and sharp["clipped_fraction"] == 0.0
    rgb = np.dstack([image] * 3)
    assert compute_view_signals(parts, HOOD, rgb)["mean_luma"] == pytest.approx(sharp["mean_luma"], abs=1e-6)
    white = compute_view_signals(parts, HOOD, np.full_like(image, 255))
    assert white["clipped_fraction"] == 1.0 and white["mean_luma"] == 255.0


def test_crop_signal_measures_contact_with_the_photo_border():
    parts, _ = _scene()
    parts[:, :] = 0
    parts[0:40, 30:70] = HOOD  # touches the top edge only
    touch = compute_view_signals(parts, HOOD)["border_touch_fraction"]
    assert 0.2 < touch < 0.3
    # In a letterboxed frame the photo border is the content box edge, not the padding edge.
    letterboxed = np.zeros((128, 128), dtype=np.uint8)
    letterboxed[16:40, 30:70] = HOOD
    assert compute_view_signals(letterboxed, HOOD)["border_touch_fraction"] == 0.0
    boxed = compute_view_signals(letterboxed, HOOD, content_box=(0, 16, 128, 112))["border_touch_fraction"]
    assert boxed > 0.2


def test_image_on_another_grid_is_a_geometry_mismatch():
    parts, image = _scene()
    with pytest.raises(ContractError) as err:
        compute_view_signals(parts, HOOD, image[:64])
    assert err.value.reason_code == "mask_geometry_mismatch"


@pytest.mark.parametrize(("change", "reason"), [
    ({"part_area_fraction": 0.01}, "part_too_small"),
    ({"blur_score": 12.0}, "low_sharpness"),
    ({"mean_luma": 20.0}, "too_dark"),
    ({"mean_luma": 240.0}, "too_bright"),
    ({"clipped_fraction": 0.3}, "clipped_exposure"),
    ({"border_touch_fraction": 0.46}, "cropped_at_border"),
])
def test_each_screen_names_its_failing_signal(change, reason):
    view = screen_view("ph_01", {"part_area_fraction": 0.1, **PASS, **change}, CFG)
    assert (view.screen_result, view.reasons) == ("fail", [reason])


def test_missing_signal_is_not_run_unless_another_signal_fails():
    view = screen_view("ph_01", {"part_area_fraction": 0.1, "border_touch_fraction": 0.0}, CFG)
    assert (view.screen_result, view.reasons) == ("not_run", ["lighting_not_computed", "sharpness_not_computed"])
    failing = screen_view("ph_01", {"part_area_fraction": 0.001}, CFG)
    assert failing.screen_result == "fail" and failing.reasons[0] == "part_too_small"
    assert screen_view("ph_01", {"part_area_fraction": 0.1, **PASS}, CFG).screen_result == "pass"


def test_screen_never_records_obstruction():
    view = screen_view("ph_01", {"part_area_fraction": 0.1, **PASS}, CFG)
    assert not [k for k in view.signals if "obstruct" in k]


# ---------------------------------------------------------------- configuration
def test_default_configuration_is_the_proposed_specification_values():
    cfg = load_summary_config()
    s = cfg.summary
    assert cfg.status == "proposed" and cfg.config_version == "m3-summary/0.1.0"
    assert (s.area_policy, s.min_part_area_fraction, s.min_blur_score, s.min_mean_luma, s.max_mean_luma,
            s.max_clipped_fraction, s.max_border_touch_fraction, s.require_coverage_confirmation) == (
        "max_member", 0.02, 100.0, 40, 220, 0.10, 0.25, True)


def test_supported_panel_list_comes_from_the_taxonomy():
    assert CFG.supported_parts() == PART_CODES
    subset = CFG.with_overrides({"summary": {"supported_panel_list": ("hood", "front-door")}})
    assert subset.supported_parts() == ("front-door", "hood")


@pytest.mark.parametrize("change", [
    {"require_coverage_confirmation": False},
    {"area_policy": "sum_members"},
    {"min_mean_luma": 230},
    {"supported_panel_list": ("wing",)},
    {"surprise": 1},
])
def test_invalid_configuration_is_rejected(change):
    with pytest.raises(pydantic.ValidationError):
        CFG.with_overrides({"summary": change})
