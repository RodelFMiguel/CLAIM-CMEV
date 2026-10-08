"""Hand a trained model to the model registry the vision workers serve from.

A worker reads a registry entry: Hugging Face weights, ``manifest.json`` and ``preprocessing.json``
under ``artifacts/models/<model_version>``, and verifies it before serving.

- ``export_parts_run`` writes that entry from a notebook run (``pipelines/vision/training.py`` keeps
  a run as ``best.pt``) and proves it loads. Serving it is a change to ``configs/models/parts.yaml``.
- ``adopt_damage_run`` completes the output folder of ``pipelines/vision/train_damage.py``, which
  lacks the weight hash, the class names and the frame record, into an entry the M2 worker loads.
- ``export_damage_run`` does for a notebook damage run (CarDD or HITL) what ``export_parts_run``
  does for parts. Serving its entry is a change to ``configs/models/damage.yaml``.

Both create candidates; neither changes which model is served.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any

import yaml

from claim_cmev.contracts.common import DAMAGE_VOCABULARIES
from claim_cmev.taxonomy import load_damage_cardd, load_damage_hitl
from claim_cmev.vision.damage.adapter import DamageSegmenter, verify_damage_checkpoint
from claim_cmev.vision.damage.model_config import DamageModelConfig, load_damage_model_config
from claim_cmev.vision.palette import ID_TO_PART_CODE
from claim_cmev.vision.parts.adapter import PartsSegmenter, serving_preprocessing, verify_checkpoint
from claim_cmev.vision.parts.config import PartsConfig, load_parts_config
from claim_cmev.vision.registry import WEIGHT_FILES, sha256_file
from claim_cmev.vision.registry import serving_preprocessing as frame_settings
from pipelines.vision.train_segformer import base_checkpoint_licence, dependency_versions
from pipelines.vision.training import load_run


def _entry_name(model_version: str, model_id: str) -> None:
    name = PurePosixPath(model_version)
    if len(name.parts) != 2 or name.parts[0] != model_id or name.parts[1] in {".", ".."}:
        raise ValueError(f"model_version must be {model_id}/<name>, not {model_version!r}")


def _require_serving_frame(config: Any) -> None:
    if config.architecture != "segformer":
        raise ValueError(f"the workers serve SegFormer checkpoints; this run is {config.architecture}")
    if config.frame != "serving":
        raise ValueError(f'the run was trained in the {config.frame!r} frame; the worker builds another one. '
                         'Train with TrainingConfig(frame="serving") to export a run')


def _export(model: Any, config: Any, record: dict[str, Any], entry: Path, *, model_id: str, model_version: str,
            taxonomy_version: str, frame_keys: Any, load_served: Any) -> Path:
    """Write the run as registry entry ``entry``, after the worker's own checks pass on a staged copy.

    ``load_served(stage)`` verifies the staged folder and returns the model as the worker loads
    it. The exported weights must give the run's logits before the entry appears.
    """
    import torch

    trained = record["preprocessing"]
    entry.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".export-", dir=entry.parent) as temporary:
        stage = Path(temporary) / entry.name
        model.network.save_pretrained(stage)
        (stage / "preprocessing.json").write_text(json.dumps({
            "preprocessing_version": "1.0.0", "color_space": "RGB", **{key: trained[key] for key in frame_keys},
            "interpolation": "area_when_shrinking_else_bilinear_for_images_nearest_for_masks",
        }, indent=2), encoding="utf-8")
        (stage / "manifest.json").write_text(json.dumps({
            "model_id": model_id, "version": model_version, "status": "candidate",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "pipelines/vision/registry.py export of a notebook run",
            "task": "semantic_segmentation", "architecture": config.checkpoint, "base_checkpoint": config.checkpoint,
            "base_checkpoint_revision": record.get("resolved_checkpoint_revision"),
            "base_checkpoint_licence": base_checkpoint_licence({"architecture": config.checkpoint}),
            "weights_sha256": hashlib.sha256((stage / "model.safetensors").read_bytes()).hexdigest(),
            "taxonomy_version": taxonomy_version,
            "dependency_versions": dependency_versions(),
            "source_run": {key: record.get(key) for key in (
                "run_id", "checkpoint_sha256", "manifest_hash", "split_hash", "taxonomy_hash", "dataset", "best_epoch",
                "completed_epochs", "status", "config")} | {
                "trained_with": record.get("environment", {}).get("packages")},
            # The run's own validation score, on the validation split of its prepared data.
            "metrics": {"val_mIoU_foreground": record.get("best_val_miou_foreground"),
                        "best_epoch": record.get("best_epoch")},
        }, indent=2), encoding="utf-8")

        served = load_served(stage)
        pixels = torch.rand(1, 3, config.image_size, config.image_size, generator=torch.Generator().manual_seed(0))
        with torch.no_grad():
            if not torch.allclose(served(pixel_values=pixels).logits, model.network(pixels).logits, atol=1e-5):
                raise RuntimeError("the exported weights do not reproduce the run's logits; nothing was exported")
        shutil.move(str(stage), str(entry))
    return entry


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
    _entry_name(model_version, serving.model_id)
    serving = serving.model_copy(update={"model_version": model_version})
    entry = serving.model_dir(registry_root)
    if entry.exists():
        raise FileExistsError(f"{entry} already exists; a registry entry is not replaced, choose another version")

    model, config, record, _ = load_run(run_dir, device="cpu")
    if config.task != "parts":
        raise ValueError(f"the parts worker serves a model trained on parts; this run is on {config.task}")
    if record["class_names"] != [ID_TO_PART_CODE[i] for i in sorted(ID_TO_PART_CODE)]:
        raise ValueError("the run's classes are not the parts taxonomy in its order; classes are never renumbered")
    _require_serving_frame(config)

    def load_served(stage: Path) -> Any:
        verify_checkpoint(stage, serving)
        return PartsSegmenter(model_dir=stage, config=serving, device_str="cpu").model

    return _export(model, config, record, entry, model_id=serving.model_id, model_version=model_version,
                   taxonomy_version=serving.taxonomy_version, frame_keys=serving_preprocessing(serving),
                   load_served=load_served)


def export_damage_run(run_dir: str | Path, model_version: str, registry_root: str | Path | None = None,
                      damage_config: DamageModelConfig | None = None) -> Path:
    """Write the best checkpoint of a notebook damage run as registry entry ``model_version``.

    The run's classes decide the vocabulary: background plus exactly the CarDD codes, or exactly
    the HITL codes, in the run's own order. ``model_version`` is then ``damage-cardd/<name>`` or
    ``damage-hitl/<name>``, so the folder says which vocabulary its observations will carry.
    The run must be a SegFormer trained in the serving frame at the size the damage worker
    serves (``damage_config``, by default ``configs/models/damage.yaml``), because its mask is
    overlaid on the M1 mask of the same photograph. Checked and written like a parts export.

    This creates a candidate. To serve it, name it in ``configs/models/damage.yaml`` with its
    ``model_id`` and ``taxonomy_version``.
    """
    frame = damage_config or load_damage_model_config()
    model, config, record, _ = load_run(run_dir, device="cpu")
    if not config.task.startswith("damage"):
        raise ValueError(f"the damage worker serves a model trained on damage; this run is on {config.task}")
    classes = record["class_names"]
    vocabulary = next((prefix for prefix, codes in DAMAGE_VOCABULARIES.items()
                       if classes[:1] == ["background"] and sorted(classes[1:]) == sorted(codes)), None)
    if vocabulary is None:
        raise ValueError("the run's classes are not background plus the codes of one known damage vocabulary")
    model_id = vocabulary.rstrip("-")
    _entry_name(model_version, model_id)
    taxonomy = (load_damage_cardd() if model_id == "damage-cardd" else load_damage_hitl()).version
    serving = DamageModelConfig(model_id=model_id, model_version=model_version, taxonomy_version=taxonomy,
                                config_version=frame.config_version, input_size=frame.input_size,
                                resize_policy=frame.resize_policy, device="cpu")
    entry = serving.model_dir(registry_root)
    if entry.exists():
        raise FileExistsError(f"{entry} already exists; a registry entry is not replaced, choose another version")
    _require_serving_frame(config)

    def load_served(stage: Path) -> Any:
        verify_damage_checkpoint(stage, serving)
        return DamageSegmenter(stage, serving, device_str="cpu").model

    return _export(model, config, record, entry, model_id=model_id, model_version=model_version,
                   taxonomy_version=taxonomy, frame_keys=frame_settings(serving.input_size, serving.resize_policy),
                   load_served=load_served)


def _damage_records(run_dir: Path, config: DamageModelConfig) -> dict[str, dict[str, Any]]:
    """The three records a damage registry entry needs, derived from what the trainer left in ``run_dir``."""
    trainer_path = run_dir / "manifest.trainer.json"
    trainer = json.loads((trainer_path if trainer_path.is_file() else run_dir / "manifest.json").read_text("utf-8"))
    weights = next((run_dir / name for name in WEIGHT_FILES if (run_dir / name).is_file()), None)
    if weights is None:
        raise FileNotFoundError(f"no weight file in {run_dir}")
    # The trainer leaves the checkpoint's id2label at LABEL_<n>; its manifest holds the numbering it trained with.
    numbering = {str(entry["class_id"]): name for name, entry in (trainer.get("per_class_validation") or {}).items()}
    recipe = yaml.safe_load((run_dir / "config.yaml").read_text(encoding="utf-8")) or {}
    # pipelines/vision/damage_dataset.py builds the frame with the application's own functions:
    # ImageNet normalisation, black padding, and the size and policy of the run's configuration.
    preprocessing = {"preprocessing_version": "1.0.0", "color_space": "RGB",
                     **frame_settings(recipe.get("input_size"), recipe.get("resize_policy")),
                     "interpolation": "area_when_shrinking_else_bilinear_for_images_nearest_for_masks"}
    manifest = {**trainer, "model_id": config.model_id, "version": config.model_version, "status": "candidate",
                "taxonomy_version": config.taxonomy_version, "weights_sha256": sha256_file(weights),
                "trainer": {"model_version": trainer.get("model_version"),
                            "taxonomy_version": trainer.get("taxonomy_version")},
                "adopted_by": "pipelines/vision/registry.py adopt_damage_run"}
    manifest.pop("model_version", None)  # the registry's key is ``version``; the trainer's name is under ``trainer``
    return {"manifest.json": manifest, "preprocessing.json": preprocessing,
            "label_schema.json": {"schema_version": "1.0.0", "taxonomy_version": config.taxonomy_version,
                                  "background_class_id": 0, "background_class_code": "background",
                                  "num_classes": len(numbering), "id_to_code": numbering,
                                  "side_resolution": "unresolved"}}


def _write_records(folder: Path, records: dict[str, dict[str, Any]]) -> None:
    for name, content in records.items():
        (folder / name).write_text(json.dumps(content, indent=2), encoding="utf-8")


def adopt_damage_run(run_dir: str | Path, registry_root: str | Path | None = None,
                     damage_config: DamageModelConfig | None = None) -> Path:
    """Complete a damage trainer's output folder into the registry entry of the configured ``model_version``.

    The trainer writes the weights with default class names, its own version and taxonomy
    name, no weight hash and no frame record, so the M2 worker cannot verify its folder. This
    derives the missing records from the run's own files, checks the result with the worker's
    verification and loader, and only then writes: in place when ``run_dir`` already is the
    entry, otherwise into a copy. The trainer's manifest is kept as ``manifest.trainer.json``.
    An existing entry is never replaced, and a refused run is left untouched.
    """
    config = damage_config or load_damage_model_config()
    run_dir, entry = Path(run_dir).resolve(), config.model_dir(registry_root).resolve()
    if entry != run_dir and entry.exists():
        raise FileExistsError(f"{entry} already exists; a registry entry is not replaced, choose another version")
    records = _damage_records(run_dir, config)

    with tempfile.TemporaryDirectory(prefix="cmev-adopt-") as temporary:
        stage = Path(temporary) / "entry"
        stage.mkdir()
        for name in ("config.json", *(n for n in WEIGHT_FILES if (run_dir / n).is_file())):
            os.symlink(run_dir / name, stage / name)
        _write_records(stage, records)
        verify_damage_checkpoint(stage, config)
        DamageSegmenter(stage, config, device_str="cpu")  # every tensor loads, and the class count fits

    if entry != run_dir:
        entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(run_dir, entry)
    if not (entry / "manifest.trainer.json").exists():
        shutil.copy2(entry / "manifest.json", entry / "manifest.trainer.json")
    _write_records(entry, records)
    return entry
