"""Unit tests for the HITL polygon to indexed mask converter (M1)."""
from __future__ import annotations

import numpy as np
import pytest

from claim_cmev.contracts.common import PART_CODES
from pipelines.vision.convert_hitl import (
    ID_TO_PART_CODE,
    PART_CODE_TO_ID,
    build_palette,
    rasterize_annotation,
)


def test_part_code_mapping():
    assert len(PART_CODES) == 21
    assert PART_CODE_TO_ID["windshield"] == 1
    assert ID_TO_PART_CODE[1] == "windshield"
    assert ID_TO_PART_CODE[0] == "background"


def test_build_palette():
    palette = build_palette()
    assert len(palette) == 768
    # Background is black
    assert palette[0] == 0
    assert palette[1] == 0
    assert palette[2] == 0


def test_rasterize_descending_area_and_holes():
    # Construct a 100x100 mock image with:
    # 1. Large front-door (60x60 = area ~3600) with a 20x20 interior hole
    # 2. Smaller mirror (10x10 = area ~100) placed inside the door area
    door_id = PART_CODE_TO_ID["front-door"]
    mirror_id = PART_CODE_TO_ID["mirror"]

    ann_data = {
        "size": {"height": 100, "width": 100},
        "objects": [
            # Put smaller mirror first in the input list to verify sorting works
            {
                "classTitle": "Mirror",
                "points": {
                    "exterior": [[10, 10], [20, 10], [20, 20], [10, 20]],
                    "interior": [],
                },
            },
            # Larger door defined second with a hole at [30, 30] to [50, 50]
            {
                "classTitle": "Front-door",
                "points": {
                    "exterior": [[0, 0], [60, 0], [60, 60], [0, 60]],
                    "interior": [[[30, 30], [50, 30], [50, 50], [30, 50]]],
                },
            },
        ],
    }

    title_to_id = {"Front-door": door_id, "Mirror": mirror_id}
    mask, pixel_counts, obj_counts = rasterize_annotation(ann_data, title_to_id)

    assert mask.shape == (100, 100)
    # The mirror area [10:20, 10:20] must be preserved on top of the door
    assert mask[15, 15] == mirror_id
    # Outside the mirror, inside the door must be front-door
    assert mask[5, 5] == door_id
    # Inside the hole must be background (0)
    assert mask[40, 40] == 0
    # Outside the door must be background (0)
    assert mask[80, 80] == 0

    assert obj_counts["front-door"] == 1
    assert obj_counts["mirror"] == 1
    assert "front-door" in pixel_counts
    assert "mirror" in pixel_counts


def test_rasterize_unrecognized_title_raises():
    ann_data = {
        "size": {"height": 50, "width": 50},
        "objects": [{"classTitle": "Spaceship", "points": {"exterior": [[0, 0], [10, 0], [10, 10]], "interior": []}}],
    }
    with pytest.raises(ValueError, match="Unrecognized class title"):
        rasterize_annotation(ann_data, {"Front-door": 1})
