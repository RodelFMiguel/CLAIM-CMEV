"""M1 vehicle part segmentation module (docs/specs/module-01-vehicle-part-segmentation.md).

Exports configuration, inference engine, pure adapter entry point, and consumer handler for M1.
The adapter names load on first use, so reading the configuration does not import PyTorch.
"""
from __future__ import annotations

from typing import Any

from .config import PartsConfig, load_parts_config

_ADAPTER_EXPORTS = frozenset({
    "ModelUnavailable",
    "PartArtifact",
    "PartsSegmentRequest",
    "PartsSegmentResult",
    "PartsSegmenter",
    "PhotoFileInput",
    "PhotoSegmentationOutcome",
    "load_parts_segmenter",
    "make_parts_handler",
    "run_parts_segmentation",
    "verify_checkpoint",
})


def __getattr__(name: str) -> Any:
    if name in _ADAPTER_EXPORTS:
        from . import adapter

        return getattr(adapter, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted({"PartsConfig", "load_parts_config", *_ADAPTER_EXPORTS})
