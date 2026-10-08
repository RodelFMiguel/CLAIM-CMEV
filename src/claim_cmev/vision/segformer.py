"""Loading a SegFormer checkpoint for serving. Imports the model libraries."""
from __future__ import annotations

import logging
from pathlib import Path

import torch
import transformers
from transformers import SegformerConfig, SegformerForSemanticSegmentation

from .registry import ModelUnavailable

log = logging.getLogger("cmev.vision.segformer")


def resolve_device(setting: str, override: str | None = None) -> torch.device:
    """``override`` if given; else CUDA for ``cuda`` or ``auto`` when it is available, otherwise the CPU."""
    if override:
        return torch.device(override)
    if setting == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if setting == "cuda":
        log.warning("CUDA requested in configuration but not available. Falling back to CPU.")
    return torch.device("cpu")


def load_strict(model_dir: str | Path, device: torch.device) -> SegformerForSemanticSegmentation:
    """Load a checkpoint and refuse it unless every tensor loaded.

    ``from_pretrained`` only warns when tensor names differ, for example across transformers
    versions, and leaves those layers at random values. Such a model must not be served.
    """
    model_dir = Path(model_dir)
    config = SegformerConfig.from_pretrained(model_dir)
    model, loading = SegformerForSemanticSegmentation.from_pretrained(model_dir, config=config,
                                                                      output_loading_info=True)
    unloaded = sorted([*loading.get("missing_keys", ()), *loading.get("unexpected_keys", ()),
                       *(entry[0] for entry in loading.get("mismatched_keys", ()))])
    if unloaded:
        saved_with = getattr(config, "transformers_version", None) or "an unrecorded version"
        raise ModelUnavailable(
            "model_weights_incomplete",
            f"{len(unloaded)} tensors of {model_dir} did not load (for example {unloaded[0]}). The checkpoint "
            f"was saved with transformers {saved_with}; transformers {transformers.__version__} is installed")
    return model.to(device).eval()


__all__ = ["load_strict", "resolve_device"]
