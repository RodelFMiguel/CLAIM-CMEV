"""Indexed semantic mask palette and class ID mappings for M1 part segmentation.

Shared palette definitions between offline training/converter and online serving adapter.
Class ID 0 is background; class IDs 1..21 match canonical PART_CODES in contracts.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

from typing import Any

from claim_cmev.contracts.common import PART_CODES
from claim_cmev.taxonomy import load_parts

# Canonical part codes order: index 0 is background, indices 1..21 match PART_CODES
PART_CODE_TO_ID: dict[str, int] = {code: i + 1 for i, code in enumerate(PART_CODES)}
ID_TO_PART_CODE: dict[int, str] = {i + 1: code for i, code in enumerate(PART_CODES)}
ID_TO_PART_CODE[0] = "background"

DEFAULT_PALETTE_COLORS: list[tuple[int, int, int]] = [
    (0, 0, 0),        # 0: background
    (233, 83, 83),    # 1: windshield
    (150, 60, 61),    # 2: back-windshield
    (144, 55, 101),   # 3: front-window
    (145, 48, 33),    # 4: back-window
    (254, 47, 192),   # 5: front-door
    (154, 135, 207),  # 6: back-door
    (64, 153, 61),    # 7: front-wheel
    (130, 6, 219),    # 8: back-wheel
    (74, 247, 120),   # 9: front-bumper
    (124, 147, 218),  # 10: back-bumper
    (50, 6, 152),     # 11: headlight
    (46, 127, 98),    # 12: tail-light
    (67, 85, 203),    # 13: hood
    (229, 248, 58),   # 14: trunk
    (144, 208, 146),  # 15: licence-plate
    (188, 87, 78),    # 16: mirror
    (135, 219, 0),    # 17: roof
    (230, 45, 48),    # 18: grille
    (193, 151, 68),   # 19: rocker-panel
    (92, 117, 41),    # 20: quarter-panel
    (213, 11, 180),   # 21: fender
]


def build_palette(meta_classes: list[dict[str, Any]] | None = None) -> list[int]:
    """Generate a 256-color RGB palette (768 ints) for PIL 'P' mode masks.

    Index 0 is background (0, 0, 0). Indices 1..21 use the colors from meta.json if available.
    """
    palette = [0] * 768

    color_map: dict[str, tuple[int, int, int]] = {}
    if meta_classes:
        for c in meta_classes:
            hex_str = c.get("color", "").lstrip("#")
            if len(hex_str) == 6:
                r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
                color_map[c["title"]] = (r, g, b)

    for idx, (r, g, b) in enumerate(DEFAULT_PALETTE_COLORS):
        palette[idx * 3] = r
        palette[idx * 3 + 1] = g
        palette[idx * 3 + 2] = b

    if color_map:
        parts_tax = load_parts()
        title_to_code = {p["hitl_title"]: p["code"] for p in parts_tax.meta["parts"]}
        for hitl_title, code in title_to_code.items():
            class_id = PART_CODE_TO_ID.get(code)
            if class_id and hitl_title in color_map:
                r, g, b = color_map[hitl_title]
                palette[class_id * 3] = r
                palette[class_id * 3 + 1] = g
                palette[class_id * 3 + 2] = b

    return palette
