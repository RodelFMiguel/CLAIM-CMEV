"""M1 vehicle part segmentation module (docs/specs/module-01-*.md).

Exports configuration, inference engine, and consumer handler for the M1 parts worker.
"""
from __future__ import annotations

from .adapter import PartsSegmenter, make_parts_handler
from .config import PartsConfig, load_parts_config

__all__ = [
    "PartsConfig",
    "PartsSegmenter",
    "load_parts_config",
    "make_parts_handler",
]
