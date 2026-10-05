"""M1 vehicle part segmentation module (docs/specs/module-01-vehicle-part-segmentation.md).

Exports configuration, inference engine, pure adapter entry point, and consumer handler for M1.
"""
from __future__ import annotations

from .adapter import (
    PartArtifact,
    PartsSegmentRequest,
    PartsSegmentResult,
    PartsSegmenter,
    PhotoFileInput,
    PhotoSegmentationOutcome,
    make_parts_handler,
    run_parts_segmentation,
)
from .config import PartsConfig, load_parts_config

__all__ = [
    "PartArtifact",
    "PartsConfig",
    "PartsSegmentRequest",
    "PartsSegmentResult",
    "PartsSegmenter",
    "PhotoFileInput",
    "PhotoSegmentationOutcome",
    "load_parts_config",
    "make_parts_handler",
    "run_parts_segmentation",
]
