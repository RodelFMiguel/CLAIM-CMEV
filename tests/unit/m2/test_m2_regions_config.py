"""Region extraction, box transforms, the side fuzz test and the versioned configuration."""
import io
from pathlib import Path
import tokenize

import numpy as np
import pydantic
import pytest
import yaml

from claim_cmev.contracts.common import PART_CODES, ContractError
from claim_cmev.contracts.imaging import ImageTransform
from claim_cmev.vision.damage import assignment as assignment_module
from claim_cmev.vision.damage import config as config_module
from claim_cmev.vision.damage import extract_regions, load_assignment_config, regions as regions_module
from claim_cmev.vision.damage.config import DEFAULT_CONFIG_PATH
from claim_cmev.vision.transforms import plan_model_frame
from m2_support import DAMAGE_CLASSES, DAMAGE_ID, GRID, PART_ID, blank, config, paint, run, validate_event

REGIONS = config().regions


# ---------------------------------------------------------------- regions
def test_low_confidence_pixels_become_background_and_are_counted():
    damage = paint(blank(), DAMAGE_ID["dent"], 0, 0, 20, 40)
    confidence = np.full(damage.shape, 0.9)
    confidence[20:40, :] = 0.3
    result = extract_regions(damage, confidence, DAMAGE_CLASSES, REGIONS)
    [region] = result.regions
    assert region.pixel_count == 400 and region.bbox_model == (0, 0, 20, 20)
    assert result.dropped_low_confidence_pixels == 400 and "below_min_damage_confidence" in result.reasons
    assert region.mean_confidence == pytest.approx(0.9)


def test_connectivity_is_configured_not_hard_coded():
    damage = blank()
    paint(damage, DAMAGE_ID["dent"], 0, 0, 20, 20)
    paint(damage, DAMAGE_ID["dent"], 20, 20, 40, 40)  # touches only at a corner
    conf = np.full(damage.shape, 0.8)
    assert len(extract_regions(damage, conf, DAMAGE_CLASSES, REGIONS).regions) == 1
    four = REGIONS.model_copy(update={"connectivity": 4})
    assert len(extract_regions(damage, conf, DAMAGE_CLASSES, four).regions) == 2


def test_adjacent_regions_of_different_classes_are_never_merged():
    damage = blank()
    paint(damage, DAMAGE_ID["dent"], 0, 0, 20, 20)
    paint(damage, DAMAGE_ID["crack"], 20, 0, 40, 20)
    result = extract_regions(damage, np.full(damage.shape, 0.8), DAMAGE_CLASSES, REGIONS)
    assert [r.damage_code for r in result.regions] == ["dent", "crack"]


def test_excess_components_drop_the_smallest_and_keep_order():
    damage = blank()
    paint(damage, DAMAGE_ID["dent"], 0, 0, 20, 20)       # 400
    paint(damage, DAMAGE_ID["dent"], 40, 0, 70, 20)      # 600
    paint(damage, DAMAGE_ID["dent"], 80, 0, 100, 15)     # 300, dropped
    capped = REGIONS.model_copy(update={"max_components_per_photo": 2})
    result = extract_regions(damage, np.full(damage.shape, 0.8), DAMAGE_CLASSES, capped)
    assert [(r.index, r.pixel_count) for r in result.regions] == [(1, 400), (2, 600)]
    assert result.max_components_exceeded_count == 1 and result.dropped_region_count == 1
    assert "max_components_exceeded" in result.reasons


def test_damage_class_map_must_be_cardd_without_background():
    damage = paint(blank(), 1, 0, 0, 20, 20)
    conf = np.full(damage.shape, 0.8)
    with pytest.raises(ContractError) as err:
        extract_regions(damage, conf, {0: "dent", 1: "scratch"}, REGIONS)
    assert err.value.reason_code == "mask_encoding_mismatch"
    with pytest.raises(ContractError) as err:
        extract_regions(damage, conf, {1: "corrosion"}, REGIONS)  # HITL-only label
    assert err.value.reason_code == "taxonomy_version_mismatch"


# ---------------------------------------------------------------- boxes on the original photo
def _letterbox_damage():
    # Stored 1024 x 768 photo in a 128 frame: scale 0.125, 16 px padding top and bottom.
    damage = paint(blank(), DAMAGE_ID["dent"], 32, 32, 64, 48)
    parts = paint(blank(), PART_ID["hood"], 0, 16, 128, 112)  # hood over the whole content area
    return damage, parts


def test_bbox_is_normalised_on_the_original_photo_with_the_contract_transform():
    damage, parts = _letterbox_damage()
    transform = ImageTransform(stored_width=1024, stored_height=768, model_width=GRID, model_height=GRID,
                               scale=0.125, pad_left=0.0, pad_top=16.0)
    [obs] = run(damage, parts, transform=transform).observations
    assert obs.bbox_norm == pytest.approx((0.25, 0.166667, 0.5, 0.333333), abs=1e-6)


def test_bbox_inverts_exif_orientation_and_letterbox_with_the_vision_transform():
    damage, parts = _letterbox_damage()
    frame = plan_model_frame(768, 1024, 6, GRID, "longest_edge_centre_pad")  # stored portrait, displayed landscape
    assert (frame.oriented_width, frame.oriented_height, frame.offset_y) == (1024, 768, 16)
    [obs] = run(damage, parts, transform=frame).observations
    assert obs.bbox_norm == pytest.approx((0.25, 0.166667, 0.5, 0.333333), abs=1e-6)


def test_transform_for_another_frame_is_a_geometry_mismatch():
    damage, parts = _letterbox_damage()
    transform = ImageTransform(stored_width=4032, stored_height=3024, model_width=512, model_height=512,
                               scale=512 / 4032, pad_top=64.0)
    with pytest.raises(ContractError) as err:
        run(damage, parts, transform=transform)
    assert err.value.reason_code == "mask_geometry_mismatch"


# ---------------------------------------------------------------- side is never invented
def test_fuzz_side_is_always_unknown():
    """Integration contracts section 11: fuzz M2 outputs and assert side == 'unknown' in every one."""
    rng = np.random.default_rng(20260924)
    base = config()
    seen_reasons, seen = set(), 0
    for trial in range(120):
        parts = blank()
        for _ in range(int(rng.integers(0, 6))):
            x0, y0 = (int(v) for v in rng.integers(0, GRID - 8, 2))
            x1, y1 = x0 + int(rng.integers(8, 80)), y0 + int(rng.integers(8, 80))
            paint(parts, int(rng.integers(0, len(PART_CODES) + 1)), x0, y0, x1, y1)
        damage = blank()
        for _ in range(int(rng.integers(0, 5))):
            x0, y0 = (int(v) for v in rng.integers(0, GRID - 16, 2))
            paint(damage, int(rng.integers(1, 7)), x0, y0, x0 + int(rng.integers(16, 48)), y0 + int(rng.integers(16, 48)))
        cfg = base.with_overrides({"assignment": {
            "assign_min_containment": float(rng.choice([0.3, 0.6, 0.9])),
            "assign_ambiguity_margin": float(rng.choice([0.0, 0.2, 0.5])),
            "assign_background_max": float(rng.choice([0.2, 0.5, 1.0]))}})
        accepted = {c for c in PART_CODES if rng.random() < 0.7}
        result = run(damage, None if trial % 7 == 0 else parts, cfg=cfg, accepted=accepted,
                     confidence=rng.uniform(0.3, 1.0, damage.shape), photo_id=f"ph_fuzz_{trial}")
        for obs in result.observations:
            seen += 1
            assert obs.side == "unknown" and obs.side_reason == "hitl_labels_unsided"
            assert (obs.part_code is None) == (obs.part_reason is not None)
            seen_reasons.add(obs.part_reason)
        if trial % 10 == 0:
            payload = validate_event(result, cfg)["payload"]
            assert all(o["side"] == "unknown" for o in payload["observations"])
    assert seen > 100 and {None, "part_masks_missing"} <= seen_reasons and len(seen_reasons) >= 4


# ---------------------------------------------------------------- configuration
def test_default_configuration_is_the_proposed_specification_values():
    cfg = load_assignment_config()
    assert cfg.status == "proposed" and cfg.config_version == "m2-assignment/0.1.0"
    assert (cfg.regions.min_damage_confidence, cfg.regions.min_damage_pixels, cfg.regions.connectivity,
            cfg.regions.max_components_per_photo) == (0.5, 256, 8, 50)
    rule = cfg.assignment
    assert (rule.assign_min_containment, rule.assign_ambiguity_margin, rule.assign_background_max,
            rule.split_components) == (0.6, 0.2, 0.5, False)


@pytest.mark.parametrize("change", [
    {"assignment": {"split_components": True}},
    {"assignment": {"surprise": 1}},
    {"regions": {"connectivity": 6}},
    {"assignment": {"assign_min_containment": 0}},
])
def test_invalid_configuration_is_rejected(change):
    with pytest.raises(pydantic.ValidationError):
        load_assignment_config().with_overrides(change)


def test_missing_key_is_rejected(tmp_path: Path):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    del data["assignment"]["assign_background_max"]
    path = tmp_path / "m2.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(pydantic.ValidationError):
        load_assignment_config(path)


def test_no_threshold_literal_in_the_assignment_code():
    """Integration contracts 3.5: thresholds live in versioned configuration, never in Python."""
    for module in (assignment_module, regions_module, config_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        floats = [tok.string for tok in tokenize.generate_tokens(io.StringIO(source).readline)
                  if tok.type == tokenize.NUMBER and "." in tok.string]
        assert set(floats) <= {"0.0", "1.0"}, (module.__name__, floats)
