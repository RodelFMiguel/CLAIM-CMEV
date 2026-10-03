"""Unit tests for M1 vehicle part segmentation adapter and configuration.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from claim_cmev.vision.parts.config import PartsConfig, load_parts_config
from pipelines.vision.convert_hitl import build_palette, ID_TO_PART_CODE, PART_CODE_TO_ID


def test_parts_config_defaults():
    cfg = PartsConfig()
    assert cfg.model_id == "parts"
    assert cfg.model_version == "parts/0.1.0"
    assert cfg.input_size == 512
    assert cfg.resize_policy == "longest_edge_pad"
    assert cfg.min_part_pixels == 512
    assert cfg.min_part_confidence == 0.50


def test_parts_config_load_yaml(tmp_path: Path):
    yaml_file = tmp_path / "test_parts.yaml"
    yaml_file.write_text(
        """
model_id: "parts"
model_version: "parts/0.2.0"
input_size: 256
min_part_pixels: 100
min_part_confidence: 0.65
""",
        encoding="utf-8",
    )
    cfg = load_parts_config(yaml_file)
    assert cfg.model_version == "parts/0.2.0"
    assert cfg.input_size == 256
    assert cfg.min_part_pixels == 100
    assert cfg.min_part_confidence == 0.65


def test_palette_structure():
    palette = build_palette()
    assert len(palette) == 768
    # Class 0 is background: black (0, 0, 0)
    assert palette[0] == 0
    assert palette[1] == 0
    assert palette[2] == 0


def test_part_codes_mapping():
    assert ID_TO_PART_CODE[0] == "background"
    assert len(ID_TO_PART_CODE) == 22
    assert len(PART_CODE_TO_ID) == 21  # background not in PART_CODE_TO_ID
    for i in range(1, 22):
        code = ID_TO_PART_CODE[i]
        assert PART_CODE_TO_ID[code] == i
