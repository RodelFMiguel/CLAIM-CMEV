"""Hand a notebook training run to the model registry the M1 worker serves from.

``pipelines/vision/training.py`` keeps a run as ``best.pt`` under ``artifacts/models/parts/<run_id>``.
The worker reads a registry entry: Hugging Face weights, ``manifest.json`` and ``preprocessing.json``
under ``artifacts/models/<model_version>``. ``export_parts_run`` writes that entry and proves it
loads. It creates a candidate; serving it is a change to ``configs/models/parts.yaml``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from claim_cmev.vision.palette import ID_TO_PART_CODE
from claim_cmev.vision.parts.adapter import PartsSegmenter, serving_preprocessing, verify_checkpoint
from claim_cmev.vision.parts.config import PartsConfig, load_parts_config
from pipelines.vision.train_segformer import base_checkpoint_licence, dependency_versions
from pipelines.vision.training import load_run


def export_parts_run(run_dir: str | Path, model_version: str, registry_root: str | Path | None = None,
                     parts_config: PartsConfig | None = None) -> Path:
    """Write the best checkpoint of a parts run as registry entry ``model_version``; returns its folder.

    ``model_version`` is ``parts/<name>``. The run must be a SegFormer trained on the parts
    taxonomy in the serving frame (``TrainingConfig(frame="serving")``) at the size the worker
    serves. The entry is checked with the worker's own verification and loader, and the exported
    weights must give the run's logits, before it appears in the registry. An existing entry is
    never replaced.
    """
    serving = parts_config or load_parts_config()
    name = PurePosixPath(model_version)
    if len(name.parts) != 2 or name.parts[0] != serving.model_id or name.parts[1] in {".", ".."}:
        raise ValueError(f"model_version must be {serving.model_id}/<name>, not {model_version!r}")
    serving = serving.model_copy(update={"model_version": model_version})
    entry = serving.model_dir(registry_root)
    if entry.exists():
        raise FileExistsError(f"{entry} already exists; a registry entry is not replaced, choose another version")

    model, config, record, _ = load_run(run_dir, device="cpu")
    if config.task != "parts" or config.architecture != "segformer":
        raise ValueError(f"the parts worker serves a SegFormer trained on parts; this run is {config.architecture} "
                         f"on {config.task}")
    if record["class_names"] != [ID_TO_PART_CODE[i] for i in sorted(ID_TO_PART_CODE)]:
        raise ValueError("the run's classes are not the parts taxonomy in its order; classes are never renumbered")
    if config.frame != "serving":
        raise ValueError(f'the run was trained in the {config.frame!r} frame; the worker builds another one. '
                         'Train with TrainingConfig(frame="serving") to export a run')

    import torch

    trained = record["preprocessing"]
    entry.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".export-", dir=entry.parent) as temporary:
        stage = Path(temporary) / entry.name
        model.network.save_pretrained(stage)
        (stage / "preprocessing.json").write_text(json.dumps({
            "preprocessing_version": "1.0.0", "color_space": "RGB",
            **{key: trained[key] for key in serving_preprocessing(serving)},
            "interpolation": "area_when_shrinking_else_bilinear_for_images_nearest_for_masks",
        }, indent=2), encoding="utf-8")
        (stage / "manifest.json").write_text(json.dumps({
            "model_id": serving.model_id, "version": model_version, "status": "candidate",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "pipelines/vision/registry.py export of a notebook run",
            "task": "semantic_segmentation", "architecture": config.checkpoint, "base_checkpoint": config.checkpoint,
            "base_checkpoint_revision": record.get("resolved_checkpoint_revision"),
            "base_checkpoint_licence": base_checkpoint_licence({"architecture": config.checkpoint}),
            "weights_sha256": hashlib.sha256((stage / "model.safetensors").read_bytes()).hexdigest(),
            "taxonomy_version": serving.taxonomy_version,
            "dependency_versions": dependency_versions(),
            "source_run": {key: record.get(key) for key in (
                "run_id", "checkpoint_sha256", "manifest_hash", "split_hash", "taxonomy_hash", "dataset", "best_epoch",
                "completed_epochs", "status", "config")} | {
                "trained_with": record.get("environment", {}).get("packages")},
            # The run's own validation score, on the validation split of its prepared data.
            "metrics": {"val_mIoU_foreground": record.get("best_val_miou_foreground"),
                        "best_epoch": record.get("best_epoch")},
        }, indent=2), encoding="utf-8")

        verify_checkpoint(stage, serving)
        served = PartsSegmenter(model_dir=stage, config=serving, device_str="cpu")
        pixels = torch.rand(1, 3, config.image_size, config.image_size, generator=torch.Generator().manual_seed(0))
        with torch.no_grad():
            if not torch.allclose(served.model(pixel_values=pixels).logits, model.network(pixels).logits, atol=1e-5):
                raise RuntimeError("the exported weights do not reproduce the run's logits; nothing was exported")
        shutil.move(str(stage), str(entry))
    return entry
