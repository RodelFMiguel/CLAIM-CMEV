"""Train, reload and export the M6 pen-mark detector (Faster R-CNN ResNet-50 FPN).

Stage 1 of training specification 7.3 only: synthetic pages. The network is the one the
serving loader builds (``claim_cmev.documents.pen_marks.detector.build_model``), started
from torchvision's COCO weights. A run lives in ``artifacts/models/penmarks/<run_id>/``
with its configuration, per-epoch history and checkpoints; it is checkpointed every epoch
and can be resumed. ``export_for_serving`` writes a run as a registry *candidate* only
after the serving loader has loaded the staged entry and reproduced the detections of a
reference network (``reference_model``), which is built by torchvision's own factory, not
by the serving builder, whenever that factory can build the run's network.

Nothing here selects a model for serving, and no score produced here is real-page evidence.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from safetensors.torch import load_file, save_file

from claim_cmev.contracts.common import SCHEMA_VERSION
from claim_cmev.documents.pen_marks import detector as serving
from claim_cmev.documents.pen_marks.detector import DetectorSettings, load_penmark_detector, stable_order
from claim_cmev.vision.registry import sha256_file
from pipelines.documents.penmarks_data import (
    CONVERSION_VERSION, TARGET_CLASSES, AugmentConfig, PenMarkDataset, PenMarkPage, collate, load_image,
)
from pipelines.documents.penmarks_evaluation import (
    PagePrediction, class_detections, detection_report, selection_score, threshold_table,
)
from pipelines.vision.training import select_device

MODEL_ID = serving.MODEL_ID
LOSS_TERMS = ("loss_classifier", "loss_box_reg", "loss_objectness", "loss_rpn_box_reg")
SELECTION_METRIC = "mean over the two classes of the best F1 at IoU 0.5 on validation"
ANCHOR_PRESETS: dict[str, dict[str, tuple]] = {
    # torchvision's COCO anchors: one size per pyramid level, shapes 2:1, 1:1 and 1:2.
    "torchvision_default": {"anchor_sizes": ((32,), (64,), (128,), (256,), (512,)),
                            "anchor_aspect_ratios": (0.5, 1.0, 2.0)},
    # Two sizes per level, down to 16 px for a small X, and shapes up to 32 times wider than tall.
    "pen_marks_wide": {"anchor_sizes": ((16, 32), (32, 64), (64, 128), (128, 256), (256, 512)),
                       "anchor_aspect_ratios": (1 / 32, 1 / 8, 0.5, 1.0)},
}
BOX_TOLERANCE_PX = 0.01
SCORE_TOLERANCE = 1e-4
HISTORY_TOLERANCE = 1e-6
"""Largest difference allowed between a validation score recorded during training and the
same score of the reloaded checkpoint."""
FINAL_REPORT_NAME = "final_test_report.json"
MANIFEST_FIELDS = (
    "model_id", "version", "status", "created_at", "created_by", "task", "architecture", "base_checkpoint",
    "base_checkpoint_licence", "weights_sha256", "label_schema_version", "taxonomy_version", "preprocessing_version",
    "dataset_ids", "dataset_access_evidence", "split_version", "split_hashes", "conversion_version", "seed",
    "hardware", "dependency_versions", "code_revision", "training_duration_s", "metrics", "targets", "known_limits",
    "provenance", "contract_version")
COCO_WEIGHTS = "FasterRCNN_ResNet50_FPN_Weights.COCO_V1"
BASE_LICENCE = ("torchvision code: BSD-3-Clause. Weights: trained by the torchvision maintainers on COCO train2017; "
                "the weights' metadata states no licence of their own")
_REPO = Path(__file__).resolve().parents[2]


class TrainingConfig(BaseModel):
    """One training run. Every default is a proposed value of training specification 7.3."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # The detector, as it will be served.
    min_size: int = Field(default=800, ge=32)
    max_size: int = Field(default=1333, ge=32)
    anchor_sizes: tuple[tuple[int, ...], ...] = ANCHOR_PRESETS["torchvision_default"]["anchor_sizes"]
    anchor_aspect_ratios: tuple[float, ...] = ANCHOR_PRESETS["torchvision_default"]["anchor_aspect_ratios"]
    score_floor: float = Field(default=0.05, ge=0, lt=1)
    nms_iou: float = Field(default=0.5, gt=0, le=1)
    max_detections_per_page: int = Field(default=50, ge=1)
    # Starting point.
    pretrained: bool = True
    trainable_backbone_layers: int = Field(default=3, ge=0, le=5)
    # Optimisation.
    batch_size: int = Field(default=4, ge=1)
    accumulation_steps: int = Field(default=1, ge=1)
    epochs: int = Field(default=20, ge=1)
    learning_rate: float = Field(default=0.005, gt=0)
    momentum: float = Field(default=0.9, ge=0, lt=1)
    weight_decay: float = Field(default=5e-4, ge=0)
    warmup_steps: int = Field(default=100, ge=0)
    warmup_factor: float = Field(default=0.001, gt=0, le=1)
    lr_milestones: tuple[float, float] = (0.7, 0.9)
    lr_gamma: float = Field(default=0.1, gt=0, le=1)
    patience: int = Field(default=5, ge=1)
    seed: int = 20260922
    augment: AugmentConfig | None = Field(default_factory=AugmentConfig)
    # Running.
    device: str = "auto"
    eval_batch_size: int = Field(default=4, ge=1)
    artifacts_root: str = "artifacts"
    run_id: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> TrainingConfig:
        first, second = self.lr_milestones
        if not 0 < first < second <= 1:
            raise ValueError("lr_milestones are two increasing fractions of the epochs, such as (0.7, 0.9)")
        self.detector_settings()  # the detector validates sizes and anchors
        return self

    def detector_settings(self) -> DetectorSettings:
        """The settings the trained network is built with, and later served with."""
        return DetectorSettings(min_size=self.min_size, max_size=self.max_size, anchor_sizes=self.anchor_sizes,
                                anchor_aspect_ratios=self.anchor_aspect_ratios, score_floor=self.score_floor,
                                nms_iou=self.nms_iou, max_detections_per_page=self.max_detections_per_page)

    def to_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TrainingConfig:
        return cls.model_validate(dict(record))


def new_run_id() -> str:
    return f"{MODEL_ID}-fasterrcnn-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def decay_epochs(config: TrainingConfig) -> tuple[int, int]:
    """The 0-based epochs from which the learning rate is multiplied by ``lr_gamma``, once each."""
    return tuple(math.ceil(fraction * config.epochs) for fraction in config.lr_milestones)


def learning_rate_at(step: int, epoch: int, config: TrainingConfig) -> float:
    """Learning rate for optimiser update ``step`` (0-based, counted over the whole run) in ``epoch``.

    A linear warm-up from ``warmup_factor`` times the base rate over ``warmup_steps`` updates,
    then the base rate, cut by ``lr_gamma`` at each decay epoch.
    """
    warmup = 1.0
    if step < config.warmup_steps:
        warmup = config.warmup_factor + (1.0 - config.warmup_factor) * step / config.warmup_steps
    return config.learning_rate * warmup * config.lr_gamma ** sum(epoch >= start for start in decay_epochs(config))


# --------------------------------------------------------------------------- the network

def _coco_state() -> dict[str, torch.Tensor]:
    """torchvision's COCO weights under the current key names (the file is read from TORCH_HOME)."""
    from torchvision.models.detection import FasterRCNN_ResNet50_FPN_Weights, fasterrcnn_resnet50_fpn

    # The factory renames the keys of older checkpoints while it loads them.
    return fasterrcnn_resnet50_fpn(weights=FasterRCNN_ResNet50_FPN_Weights.COCO_V1, progress=False).state_dict()


def pretrained_info() -> dict[str, Any]:
    """What is known about the starting checkpoint, without loading it."""
    from torchvision.models.detection import FasterRCNN_ResNet50_FPN_Weights

    weights = FasterRCNN_ResNet50_FPN_Weights.COCO_V1
    cached = Path(torch.hub.get_dir()) / "checkpoints" / weights.url.rsplit("/", 1)[1]
    return {"name": f"torchvision {COCO_WEIGHTS}", "url": weights.url, "file": cached.name,
            "sha256": sha256_file(cached) if cached.is_file() else None,
            "parameters": weights.meta.get("num_params"),
            "coco_val2017_box_map": weights.meta.get("_metrics", {}).get("COCO-val2017", {}).get("box_map"),
            "recipe": weights.meta.get("recipe"), "licence": BASE_LICENCE,
            "licence_in_weights_metadata": weights.meta.get("license"),
            "torchvision": importlib.metadata.version("torchvision")}


def build_detector(config: TrainingConfig) -> tuple[Any, dict[str, Any]]:
    """The network to train and a record of what in it is pretrained.

    The architecture comes from the serving builder. With ``pretrained``, every COCO tensor
    whose name and shape fit is copied in. The class predictor never fits (91 COCO classes
    against 3); the two output layers of the region-proposal head fit only with the default
    number of anchors per location. Those start from torchvision's random initialisation.
    """
    model = serving.build_model(config.detector_settings())
    own = model.state_dict()
    info: dict[str, Any] = {"pretrained": config.pretrained, "transferred_tensors": 0, "new_tensors": sorted(own),
                            "base_checkpoint": None}
    if config.pretrained:
        state = _coco_state()
        transfer = {name: tensor for name, tensor in state.items() if name in own and own[name].shape == tensor.shape}
        model.load_state_dict(transfer, strict=False)
        info.update(transferred_tensors=len(transfer), new_tensors=sorted(set(own) - set(transfer)),
                    base_checkpoint=pretrained_info())
    # torchvision trains the last ``trainable_backbone_layers`` of conv1, layer1, ..., layer4.
    trainable = ["layer4", "layer3", "layer2", "layer1", "conv1"][:config.trainable_backbone_layers]
    for name, parameter in model.backbone.body.named_parameters():
        parameter.requires_grad_(any(name.startswith(layer) for layer in trainable))
    return model, info


def parameter_table(model: Any) -> list[dict[str, Any]]:
    """Parameter counts of the five blocks of the detector, with how many are trained."""
    blocks = [("backbone.body (ResNet-50)", model.backbone.body), ("backbone.fpn (feature pyramid)", model.backbone.fpn),
              ("rpn.head (objectness and box deltas per anchor)", model.rpn.head),
              ("roi_heads.box_head (two fully connected layers)", model.roi_heads.box_head),
              ("roi_heads.box_predictor (class scores and box refinement)", model.roi_heads.box_predictor)]
    return [{"block": name, "parameters": sum(p.numel() for p in module.parameters()),
             "trainable": sum(p.numel() for p in module.parameters() if p.requires_grad)} for name, module in blocks]


def rpn_anchors(model: Any, *, height: int, width: int) -> dict[str, Any]:
    """Every anchor the network lays over a ``height`` x ``width`` page, in its resized frame."""
    was_training = model.training
    model.eval()
    device = next(model.parameters()).device
    with torch.no_grad():
        images, _ = model.transform([torch.zeros(3, height, width, device=device)])
        features = list(model.backbone(images.tensors).values())
        anchors = model.rpn.anchor_generator(images, features)[0].cpu()
    model.train(was_training)
    resized = tuple(int(v) for v in images.image_sizes[0])
    per_location = model.rpn.anchor_generator.num_anchors_per_location()[0]
    shapes = [tuple(int(v) for v in feature.shape[-2:]) for feature in features]
    return {"anchors": anchors, "scale": resized[0] / height, "resized": resized,
            "padded": tuple(int(v) for v in images.tensors.shape[-2:]), "feature_shapes": shapes,
            "anchors_per_level": [h * w * per_location for h, w in shapes], "anchors_per_location": per_location}


# --------------------------------------------------------------------------- inference

def _tensor(pixels: np.ndarray, device: torch.device) -> torch.Tensor:
    return torch.from_numpy(np.ascontiguousarray(pixels)).to(device).permute(2, 0, 1).float().div(255.0)


def predict_pages(model: Any, pages: Sequence[PenMarkPage], *, device: str | torch.device,
                  batch_size: int = 4) -> dict[str, PagePrediction]:
    """The network's detections for each page, in the fixed order of the serving detector."""
    device = torch.device(device)
    model.eval()
    predictions: dict[str, PagePrediction] = {}
    for start in range(0, len(pages), batch_size):
        batch = pages[start:start + batch_size]
        with torch.inference_mode():
            outputs = model([_tensor(load_image(page), device) for page in batch])
        for page, output in zip(batch, outputs):
            boxes = output["boxes"].cpu().numpy().astype(np.float64).reshape(-1, 4)
            labels = output["labels"].cpu().numpy().astype(np.int64)
            scores = output["scores"].cpu().numpy().astype(np.float64)
            order = stable_order(boxes, labels, scores)
            predictions[page.image_id] = PagePrediction(boxes[order], labels[order], scores[order])
    return predictions


def _to_device(images: Sequence[torch.Tensor], targets: Sequence[Mapping[str, torch.Tensor]], device: torch.device):
    return ([image.to(device) for image in images],
            [{"boxes": target["boxes"].to(device), "labels": target["labels"].to(device)} for target in targets])


def validation_losses(model: Any, pages: Sequence[PenMarkPage], *, device: torch.device, batch_size: int) -> dict[str, float]:
    """The four training losses on pages the network is not trained on, averaged per page.

    torchvision returns losses only in training mode. The batch norms are frozen, so that
    mode changes no statistics; nothing is updated because no gradient is taken.
    """
    dataset = PenMarkDataset(pages)
    sums, count = dict.fromkeys(LOSS_TERMS, 0.0), 0
    model.train()
    with torch.no_grad():
        for start in range(0, len(dataset), batch_size):
            images, targets = collate([dataset[i] for i in range(start, min(start + batch_size, len(dataset)))])
            losses = model(*_to_device(images, targets, device))
            for name in LOSS_TERMS:
                sums[name] += float(losses[name]) * len(images)
            count += len(images)
    means = {name: value / max(count, 1) for name, value in sums.items()}
    return {**means, "total": sum(means.values())}


# --------------------------------------------------------------------------- training

def _git_state() -> dict[str, Any]:
    try:
        commit = subprocess.run(["git", "-C", str(_REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True, timeout=30).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(_REPO), "status", "--porcelain"], capture_output=True, text=True,
                                    check=True, timeout=60).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None, "reason": "git could not be run"}


def environment(device: torch.device) -> dict[str, Any]:
    """Library versions and the machine a run used; a metric without them is not comparable."""
    packages = {}
    for name in ("torch", "torchvision", "numpy", "pillow", "safetensors", "pydantic"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    try:
        memory_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
    except (ValueError, OSError, AttributeError):
        memory_gb = None
    accelerator = torch.cuda.get_device_name(0) if device.type == "cuda" else (
        "Apple Metal (MPS)" if device.type == "mps" else None)
    return {**packages, "python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "processor": platform.processor() or None, "memory_gb": memory_gb,
            "device": device.type, "accelerator": accelerator, "where": "team machine"}


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _comparable(config: TrainingConfig) -> dict[str, Any]:
    """What must not change when a run is resumed."""
    record = config.to_record()
    for key in ("epochs", "patience", "device", "run_id", "artifacts_root", "eval_batch_size"):
        record.pop(key)
    return record


def train(config: TrainingConfig, train_pages: Sequence[PenMarkPage], val_pages: Sequence[PenMarkPage], *,
          data_record: Mapping[str, Any], resume_from: str | Path | None = None,
          log: Callable[[str], None] = print) -> dict[str, Any]:
    """Train on ``train_pages``, select the best epoch on ``val_pages`` and return the run summary.

    After every epoch the run writes ``history.jsonl``, ``last.pt`` (network, optimiser and
    counters) and, when the selection score improved, ``best.safetensors``. ``resume_from``
    continues such a run from its last finished epoch; its configuration must be the same
    apart from the number of epochs. Training stops early after ``patience`` evaluations
    without improvement. An interrupt keeps the best checkpoint and marks the run.
    """
    if not train_pages or not val_pages:
        raise ValueError("training needs at least one training page and one validation page")
    if {p.split for p in (*train_pages, *val_pages)} & {"test"}:
        raise ValueError("the held-out test split takes no part in training or model selection")
    device = select_device(config.device)
    run_id = config.run_id or (Path(resume_from).name if resume_from else new_run_id())
    root = Path(config.artifacts_root)
    run_dir, evaluation_dir = root / "models" / MODEL_ID / run_id, root / "evaluation" / run_id
    if resume_from is not None and Path(resume_from).resolve() != run_dir.resolve():
        raise ValueError(f"resume_from is {resume_from}, but the configuration places the run at {run_dir}")
    if resume_from is None and run_dir.exists():
        raise FileExistsError(f"{run_dir} already exists; a run is never overwritten")
    run_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)

    random.seed(config.seed)
    np.random.seed(config.seed % 2 ** 32)
    torch.manual_seed(config.seed)
    model, starting_point = build_detector(config)
    model.to(device)
    optimiser = torch.optim.SGD([p for p in model.parameters() if p.requires_grad], lr=config.learning_rate,
                                momentum=config.momentum, weight_decay=config.weight_decay)
    state = {"epoch": 0, "step": 0, "best_score": None, "best_epoch": None, "stale": 0, "seconds": 0.0}
    if resume_from is not None:
        stored = TrainingConfig.from_record(json.loads((run_dir / "config.json").read_text(encoding="utf-8")))
        if _comparable(stored) != _comparable(config):
            changed = sorted(k for k, v in _comparable(config).items() if _comparable(stored)[k] != v)
            raise ValueError(f"the configuration differs from the run being resumed in {changed}")
        checkpoint = torch.load(run_dir / "last.pt", map_location="cpu", weights_only=False)
        model.load_state_dict(checkpoint["model"])
        optimiser.load_state_dict(checkpoint["optimiser"])
        state = checkpoint["state"]
        log(f"resuming {run_id} after epoch {state['epoch']}")
    _write_json(run_dir / "config.json", config.to_record())

    record = {
        "run_id": run_id, "model_id": MODEL_ID, "architecture": serving.ARCHITECTURE, "status": "running",
        "seed": config.seed, "device": device.type, "selection_metric": SELECTION_METRIC,
        "pages": {"train": len(train_pages), "validation": len(val_pages)},
        "page_ids": {"train": [p.image_id for p in train_pages], "validation": [p.image_id for p in val_pages]},
        "marks": {split: {c: sum(m.mark_class == c for p in pages for m in p.marks) for c in TARGET_CLASSES}
                  for split, pages in (("train", train_pages), ("validation", val_pages))},
        "data": dict(data_record), "starting_point": starting_point, "environment": environment(device),
        "code_revision": _git_state(), "started_at": datetime.now(timezone.utc).isoformat(),
        "determinism": "seeded; a repeat run on a GPU differs slightly",
    }

    def save_record(**changes: Any) -> None:
        record.update(completed_epochs=state["epoch"], best_epoch=state["best_epoch"], best_score=state["best_score"],
                      training_duration_s=round(state["seconds"], 1), **changes)
        _write_json(run_dir / "run.json", record)

    save_record()
    dataset = PenMarkDataset(train_pages, augment=config.augment, seed=config.seed)
    shuffle = torch.Generator()
    status = "completed"
    try:
        for epoch in range(state["epoch"], config.epochs):
            started = time.perf_counter()
            dataset.set_epoch(epoch)
            shuffle.manual_seed(config.seed + epoch)  # a resumed run shuffles like an uninterrupted one
            order = torch.randperm(len(dataset), generator=shuffle).tolist()
            batches = [order[i:i + config.batch_size] for i in range(0, len(order), config.batch_size)]
            model.train()
            sums, seen, updates = dict.fromkeys(LOSS_TERMS, 0.0), 0, 0
            optimiser.zero_grad()
            for index, batch in enumerate(batches):
                rate = learning_rate_at(state["step"], epoch, config)
                for group in optimiser.param_groups:
                    group["lr"] = rate
                images, targets = collate([dataset[i] for i in batch])
                losses = model(*_to_device(images, targets, device))
                total = sum(losses[name] for name in LOSS_TERMS)
                if not math.isfinite(float(total.detach())):
                    raise FloatingPointError(f"non-finite loss in epoch {epoch + 1}, batch {index + 1}: "
                                             f"{ {name: float(losses[name].detach()) for name in LOSS_TERMS} }")
                (total / config.accumulation_steps).backward()
                for name in LOSS_TERMS:
                    sums[name] += float(losses[name].detach()) * len(batch)
                seen += len(batch)
                if (index + 1) % config.accumulation_steps == 0 or index + 1 == len(batches):
                    optimiser.step()
                    optimiser.zero_grad()
                    state["step"] += 1
                    updates += 1
            train_loss = {name: value / seen for name, value in sums.items()}
            train_loss["total"] = sum(train_loss.values())
            val_loss = validation_losses(model, val_pages, device=device, batch_size=config.eval_batch_size)
            predictions = predict_pages(model, val_pages, device=device, batch_size=config.eval_batch_size)
            score = float(selection_score(val_pages, predictions))
            report = detection_report(val_pages, predictions)
            best_f1 = {}
            for mark_class in TARGET_CLASSES:
                found = class_detections(val_pages, predictions, mark_class)
                best_f1[mark_class] = (float(threshold_table(found["scores"], found["hits"], found["marks"])["f1"].max())
                                       if found["marks"] else None)
            state["epoch"] = epoch + 1
            state["seconds"] += time.perf_counter() - started
            improved = state["best_score"] is None or score > state["best_score"] + 1e-9
            if improved:
                state.update(best_score=score, best_epoch=epoch + 1, stale=0)
                save_file({k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()},
                          str(run_dir / "best.safetensors"))
            else:
                state["stale"] += 1
            row = {"epoch": epoch + 1, "steps": updates, "learning_rate": rate, "train_loss": train_loss,
                   "val_loss": val_loss, "val_selection_score": score, "val_best_f1": best_f1,
                   "val_ap50": {c: report[c]["ap50"] for c in TARGET_CLASSES},
                   "val_ap50_95": {c: report[c]["ap50_95"] for c in TARGET_CLASSES},
                   "val_detections": {c: report[c]["detections"] for c in TARGET_CLASSES}, "improved": improved,
                   "seconds": round(time.perf_counter() - started, 1)}
            with (run_dir / "history.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")
            torch.save({"model": model.state_dict(), "optimiser": optimiser.state_dict(), "state": state},
                       run_dir / "last.pt")
            save_record()
            log(f"epoch {epoch + 1}/{config.epochs}  lr {rate:.5f}  train loss {train_loss['total']:.4f}  "
                f"val loss {val_loss['total']:.4f}  val best-F1 mean {score:.4f}"
                + "".join(f"  {c} F1 {best_f1[c]:.3f} AP50 {report[c]['ap50']:.3f}" for c in TARGET_CLASSES
                          if best_f1[c] is not None)
                + f"  {row['seconds']:.0f}s" + ("  *best*" if improved else ""))
            if device.type == "mps":
                torch.mps.empty_cache()
            if state["stale"] >= config.patience and epoch + 1 < config.epochs:
                status = "stopped_early"
                log(f"stopping: no improvement in {config.patience} evaluations (best epoch {state['best_epoch']})")
                break
    except KeyboardInterrupt:
        status = "interrupted"
        log(f"interrupted after epoch {state['epoch']}; best.safetensors keeps epoch {state['best_epoch']}")
    except BaseException:
        save_record(status="failed", finished_at=datetime.now(timezone.utc).isoformat())
        raise
    save_record(status=status, finished_at=datetime.now(timezone.utc).isoformat())
    return {**record, "run_dir": str(run_dir), "evaluation_dir": str(evaluation_dir)}


def read_history(run_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / "history.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_run(run_dir: str | Path, device: str | torch.device = "cpu") -> tuple[Any, TrainingConfig, dict[str, Any]]:
    """The best checkpoint of a saved run in evaluation mode, with its configuration and record.

    The network is rebuilt from the saved configuration (nothing is downloaded) and the
    weights are loaded strictly. This is a reload for evaluation, not an optimiser resume.
    """
    run_dir = Path(run_dir)
    for name in ("config.json", "run.json", "best.safetensors"):
        if not (run_dir / name).is_file():
            raise FileNotFoundError(f"{run_dir} is not a finished pen-mark run: {name} is missing")
    config = TrainingConfig.from_record(json.loads((run_dir / "config.json").read_text(encoding="utf-8")))
    model = serving.build_model(config.detector_settings())
    model.load_state_dict(load_file(str(run_dir / "best.safetensors")), strict=True)
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return model.to(torch.device(device)).eval(), config, record


def pages_used(record: Mapping[str, Any], partition_pages: Mapping[str, Sequence[PenMarkPage]]) -> dict[str, list[str]]:
    """The image ids a run trained and validated on, per partition.

    A run records them as ``page_ids``. A run saved before that record existed can be
    described only when it used every page of ``partition_pages``; otherwise the pages
    cannot be named and this raises instead of guessing.
    """
    recorded = record.get("page_ids")
    if recorded:
        return {split: list(ids) for split, ids in recorded.items()}
    used = {}
    for split, count in record["pages"].items():
        ids = [page.image_id for page in partition_pages.get(split, ())]
        if len(ids) != count:
            raise ValueError(f"the run used {count} {split} pages and did not record which ones; {len(ids)} "
                             "were given here, so the pages it used cannot be named")
        used[split] = ids
    return used


def evaluation_dirs(artifacts_root: str | Path, run_id: str, *, reload: bool = False,
                    overwrite_run_outputs: bool = False, stamp: str | None = None) -> tuple[Path, Path]:
    """Where a run's own reports live, and where this execution writes its figures and reports.

    A training run writes into ``<artifacts_root>/evaluation/<run_id>/``. Evaluating a saved
    run again (a reload) writes into a ``reload-<UTC time>`` folder below it, so the run's
    own figures and ``validation_report.json`` are not replaced, unless
    ``overwrite_run_outputs`` asks for exactly that.
    """
    run_eval = Path(artifacts_root) / "evaluation" / run_id
    if not reload or overwrite_run_outputs:
        return run_eval, run_eval
    return run_eval, run_eval / f"reload-{stamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def final_evaluation_path(run_eval_dir: str | Path, *, quick_run: bool) -> Path:
    """Where the one final held-out report of a run is written; refuses when it must not be.

    Refused for a quick run (it would spend the held-out split on a plumbing check) and
    when the run already has a final report: a second look at the test split after any
    change turns it into a second validation split.
    """
    if quick_run:
        raise RuntimeError("FINAL_EVAL with QUICK_RUN would spend the held-out split on a plumbing check. "
                           "Turn one of them off.")
    path = Path(run_eval_dir) / FINAL_REPORT_NAME
    if path.exists():
        raise FileExistsError(f"{path} already exists: the held-out split has already been evaluated for this run. "
                              "A final evaluation is done once; it is not repeated or replaced")
    return path


def reference_kind(config: TrainingConfig, record: Mapping[str, Any]) -> str:
    """Which network the serving check compares the served detector with.

    ``torchvision_factory`` when torchvision's own COCO factory can build the run's network:
    the run started from the COCO weights and kept torchvision's anchors. Otherwise
    ``serving_builder``, the weaker check: the reference is then built by the code under test.
    """
    default = ANCHOR_PRESETS["torchvision_default"]
    started_from_coco = (record.get("starting_point") or {}).get("base_checkpoint") is not None
    default_anchors = (config.anchor_sizes == default["anchor_sizes"]
                       and config.anchor_aspect_ratios == default["anchor_aspect_ratios"])
    return "torchvision_factory" if started_from_coco and default_anchors else "serving_builder"


def _torchvision_factory_model(config: TrainingConfig, weights_path: Path) -> Any:
    """The run's network as torchvision itself builds it, holding the run's weights.

    ``fasterrcnn_resnet50_fpn(weights=COCO_V1)`` constructs the backbone, its frozen batch
    norms (and their ``eps``), the anchors and both heads without ``serving.build_model``.
    Only the values a run can change are passed in: the working resolution, the score
    floor, the NMS IoU and the per-page cap. The COCO file is read from TORCH_HOME.
    """
    from torchvision.models.detection import FasterRCNN_ResNet50_FPN_Weights, fasterrcnn_resnet50_fpn
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    settings = config.detector_settings()
    model = fasterrcnn_resnet50_fpn(
        weights=FasterRCNN_ResNet50_FPN_Weights.COCO_V1, progress=False, min_size=settings.min_size,
        max_size=settings.max_size, box_score_thresh=settings.score_floor, box_nms_thresh=settings.nms_iou,
        box_detections_per_img=settings.max_detections_per_page)
    model.roi_heads.box_predictor = FastRCNNPredictor(model.roi_heads.box_predictor.cls_score.in_features,
                                                      len(serving.LABELS))
    model.load_state_dict(load_file(str(weights_path)), strict=True)
    return model


def reference_model(run_dir: str | Path, device: str | torch.device = "cpu") -> tuple[Any, dict[str, str]]:
    """The best checkpoint of a run in a network to compare the served detector with, and how it was built."""
    run_dir = Path(run_dir)
    config = TrainingConfig.from_record(json.loads((run_dir / "config.json").read_text(encoding="utf-8")))
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    kind, weights = reference_kind(config, record), run_dir / "best.safetensors"
    if kind == "torchvision_factory":
        model = _torchvision_factory_model(config, weights)
        note = ("independent of the serving builder: torchvision's own fasterrcnn_resnet50_fpn(weights=COCO_V1) with "
                "a new 3-class predictor and a strict load; only the run's resolution, score floor, NMS IoU and "
                "per-page cap are passed in")
    else:
        model = serving.build_model(config.detector_settings())
        model.load_state_dict(load_file(str(weights)), strict=True)
        note = ("weaker: the reference is built by the serving builder itself, because torchvision's COCO factory "
                "cannot build this network (other anchors, or no COCO starting point). A fault inside the builder "
                "would be in both networks and would not show")
    return model.to(torch.device(device)).eval(), {"reference": kind, "reference_note": note}


def history_check(run_dir: str | Path, pages: Sequence[PenMarkPage],
                  predictions: Mapping[str, PagePrediction]) -> dict[str, Any]:
    """Do reloaded weights score what the live network scored when they were saved?

    ``history.jsonl`` holds the validation scores the network had *during training*, at the
    epoch whose weights became ``best.safetensors``. ``predictions`` are what a reloaded
    network returns for the run's validation ``pages``. Equal scores tie the file on disk
    to the network that was trained, by a route that shares no model-building code with
    training's in-memory network. Not run (``passed`` is ``None``) when ``pages`` are not
    the pages the run validated on.
    """
    run_dir = Path(run_dir)
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    expected = (record.get("pages") or {}).get("validation")
    recorded_ids = (record.get("page_ids") or {}).get("validation")
    given = [page.image_id for page in pages]
    if expected != len(given) or (recorded_ids is not None and sorted(recorded_ids) != sorted(given)):
        return {"ran": False, "passed": None,
                "reason": f"the run validated on {expected} validation pages; {len(given)} "
                          f"{'other ' if expected == len(given) else ''}pages were given, so its recorded scores "
                          "cannot be compared"}
    row = next(h for h in read_history(run_dir) if h["epoch"] == record["best_epoch"])
    report = detection_report(pages, predictions)
    pairs: dict[str, tuple[Any, Any]] = {"val_selection_score": (row["val_selection_score"],
                                                                 float(selection_score(pages, predictions)))}
    for mark_class in TARGET_CLASSES:
        pairs[f"val_ap50.{mark_class}"] = (row["val_ap50"][mark_class], report[mark_class]["ap50"])
        pairs[f"val_ap50_95.{mark_class}"] = (row["val_ap50_95"][mark_class], report[mark_class]["ap50_95"])
        pairs[f"val_detections.{mark_class}"] = (row["val_detections"][mark_class], report[mark_class]["detections"])
    differences = [0.0 if a is None and b is None else math.inf if a is None or b is None else abs(float(a) - float(b))
                   for a, b in pairs.values()]
    largest = max(differences)
    return {"ran": True, "passed": bool(largest <= HISTORY_TOLERANCE), "epoch": record["best_epoch"],
            "what": "validation scores of the reloaded best checkpoint against the scores the live network "
                    "recorded in history.jsonl at that epoch",
            "compared": {name: {"recorded_in_training": a, "reloaded": b} for name, (a, b) in pairs.items()},
            "max_difference": largest if math.isfinite(largest) else None, "tolerance": HISTORY_TOLERANCE}


# --------------------------------------------------------------------------- export

def _proposals(model: Any, pixels: np.ndarray, device: torch.device) -> np.ndarray:
    """The region proposals for one page: present even when no detection clears the score floor."""
    with torch.inference_mode():
        images, _ = model.transform([_tensor(pixels, device)])
        proposals, _ = model.rpn(images, model.backbone(images.tensors))
    return proposals[0].cpu().numpy()


def _serving_check(run_dir: Path, entry: Path, pages: Sequence[PenMarkPage], device: torch.device) -> dict[str, Any]:
    """Load the staged entry with the serving loader and compare what it detects with a reference network.

    The reference holds the run's best weights (``reference_model``). The record says how
    it was built, because the two ways are not equally strong, and adds ``history_check``.
    """
    detector = load_penmark_detector(entry, device=str(device))
    reference, built = reference_model(run_dir, device=device)
    config = TrainingConfig.from_record(json.loads((run_dir / "config.json").read_text(encoding="utf-8")))
    expected = predict_pages(reference, pages, device=device, batch_size=1)
    box_difference = score_difference = 0.0
    detections, identical = 0, True
    for page in pages:
        boxes, labels, scores = detector.predict(load_image(page))
        wanted = expected[page.image_id]
        if len(scores) != len(wanted.scores) or not np.array_equal(labels, wanted.labels):
            identical = False
            continue
        detections += len(scores)
        if len(scores):
            box_difference = max(box_difference, float(np.abs(boxes - wanted.boxes).max()))
            score_difference = max(score_difference, float(np.abs(scores - wanted.scores).max()))
    pixels = load_image(pages[0])
    served, wanted_proposals = _proposals(detector.model, pixels, device), _proposals(reference, pixels, device)
    # Another number of proposals is a difference in itself; it has no size in pixels.
    proposal_difference = (float(np.abs(served - wanted_proposals).max()) if served.size else 0.0) \
        if served.shape == wanted_proposals.shape else None
    # Scored with the batch size training validated with, so the comparison repeats that computation.
    history = history_check(run_dir, pages, predict_pages(reference, pages, device=device,
                                                          batch_size=config.eval_batch_size))
    return {"pages": len(pages), "detections": detections, "identical_labels": identical, "device": device.type,
            **built, "max_box_difference_px": box_difference, "max_score_difference": score_difference,
            "max_proposal_difference_px": proposal_difference, "box_tolerance_px": BOX_TOLERANCE_PX,
            "score_tolerance": SCORE_TOLERANCE, "history_check": history,
            "passed": bool(identical and box_difference <= BOX_TOLERANCE_PX and score_difference <= SCORE_TOLERANCE
                           and proposal_difference is not None and proposal_difference <= BOX_TOLERANCE_PX
                           and history["passed"] is not False)}


def _notice(record: Mapping[str, Any], quick_note: str | None = None) -> str:
    base = record["starting_point"]["base_checkpoint"]
    start = ("The network started from RANDOM weights; there is no base checkpoint." if base is None else
             f"Base checkpoint: {base['name']} (`{base['file']}`, SHA-256 `{base['sha256']}`), from {base['url']}.\n"
             f"Licence: {BASE_LICENCE}. COCO images and annotations have their own terms "
             "(see cocodataset.org); they were not verified for this entry.")
    data = record["data"]
    return "\n".join([
        "# NOTICE", "", "M6 pen-mark detector, a registry **candidate**. Model-release status, not claim approval.", "",
        *([f"**{quick_note}**", ""] if quick_note else []),
        "## Base checkpoint", "", start, "",
        "The network architecture is torchvision's Faster R-CNN ResNet-50 FPN (torchvision code: BSD-3-Clause).", "",
        "## Training data", "",
        f"Dataset: `{data.get('dataset', {}).get('name')}`, fully synthetic pages generated in this repository "
        "(`scripts/prepare_marked_motor_claims.py`): one page layout, surveyor ink rendered with handwriting "
        "fonts. No access consent applies. The pages and their fonts stay local and are not distributed "
        "with this entry.", "",
        "## Restrictions and limits", "",
        "- Every reported metric is synthetic validation evidence. It is not real-page performance.",
        "- No real photograph, no real handwriting and no unseen layout took part in training or evaluation.",
        "- Proposals only: every mark the detector yields is `pending` until a surveyor decides.", ""])


def export_for_serving(run_dir: str | Path, name: str, *, registry_root: str | Path, evaluation_report: Mapping[str, Any],
                       thresholds: Mapping[str, float], check_pages: Sequence[PenMarkPage], created_by: str,
                       device: str | torch.device = "cpu", threshold_rule: str | None = None,
                       threshold_alternatives: Mapping[str, Any] | None = None, quick_run: bool = False,
                       pages_used: Mapping[str, Sequence[str]] | None = None) -> Path:
    """Write the best checkpoint of ``run_dir`` as registry candidate ``penmarks/<name>``; returns its folder.

    The entry is staged in a temporary folder beside its destination. The serving loader
    must accept the staged entry and reproduce the detections of the run's reference
    network on ``check_pages`` before the folder is renamed into place (``_serving_check``).
    An existing entry is never replaced, and a failed check leaves nothing behind.

    ``thresholds`` are the per-class score thresholds the report was computed with, selected
    on validation; ``threshold_alternatives`` are the other candidates shown to the team.
    Both are recorded as proposed and written to no configuration. ``pages_used`` names the
    pages the run trained and validated on when the run did not record them. ``quick_run``
    marks a plumbing-check export; an export of a run that used fewer pages than its
    partitions hold is marked the same way whatever the caller says.
    """
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"the entry name is one folder name, not {name!r}")
    if set(thresholds) != set(TARGET_CLASSES):
        raise ValueError(f"thresholds must give one score threshold for each of {TARGET_CLASSES}")
    if not check_pages:
        raise ValueError("check_pages must hold at least one page to compare detections on")
    run_dir, device = Path(run_dir), torch.device(device)
    entry = Path(registry_root) / MODEL_ID / name
    if entry.exists():
        raise FileExistsError(f"{entry} already exists; a registry entry is not replaced, choose another name")
    _, config, record = load_run(run_dir, device="cpu")
    settings, version = config.detector_settings(), f"{MODEL_ID}/{name}"
    base, data = record["starting_point"]["base_checkpoint"], record["data"]
    history = read_history(run_dir)
    used = {split: list(ids) for split, ids in (pages_used or record.get("page_ids") or {}).items()}
    partitions = data.get("partitions") or {}
    totals = {split: (partitions.get(split) or {}).get("pages") for split in partitions}
    fewer = [f"{len(ids)} of {totals[split]} {split} pages" for split, ids in used.items()
             if totals.get(split) and len(ids) < totals[split]]
    quick = bool(quick_run or fewer)
    quick_note = None if not quick else (
        "QUICK RUN: a plumbing check, not a model result. Do not serve this entry."
        + (f" The run used {', '.join(fewer)}." if fewer else " Its exporter marked it as a quick run."))
    every = sum(count for count in totals.values() if count)
    config_record = config.to_record()
    root = Path(config.artifacts_root)
    if root.is_absolute() and root.is_relative_to(_REPO):  # a path inside the checkout is recorded portably
        config_record["artifacts_root"] = str(root.relative_to(_REPO))
    linking = evaluation_report.get("linking") or {}

    def row_level(rows: str, detections: str = "detector") -> Any:
        return ((linking.get(rows) or {}).get(detections) or {}).get("row_level")
    entry.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".export-", dir=entry.parent) as temporary:
        stage = Path(temporary) / name
        stage.mkdir()
        shutil.copy2(run_dir / "best.safetensors", stage / serving.WEIGHTS_FILE)
        _write_json(stage / "label_schema.json", serving.label_schema_record())
        _write_json(stage / "preprocessing.json", settings.to_record())
        _write_json(stage / "split_manifest.json", {
            "split_version": data.get("split_version"), "grouping_key": data.get("grouping_key"),
            "ratios": {split: round(count / every, 3) for split, count in totals.items() if count} if every else None,
            "ratios_basis": "pages of each partition divided by the pages of all partitions; the split is the "
                            "dataset's own and was not drawn by this run",
            "split_files": data.get("split_files"), "partitions": data.get("partitions"),
            "pages_used": used or None, "pages_used_counts": {split: len(ids) for split, ids in used.items()},
            "pages_used_note": "the pages this run trained on and validated on" if used
                               else "the run did not record the pages it used",
            "held_out": "the test partition was not read for training, model selection or threshold selection"})
        _write_json(stage / "evaluation_report.json", {
            "partition": "validation", "provenance": "synthetic", "run_id": record["run_id"],
            "best_epoch": record["best_epoch"], "selection_metric": SELECTION_METRIC,
            "thresholds": {c: float(thresholds[c]) for c in TARGET_CLASSES}, "validation": evaluation_report,
            "note": "Synthetic validation evidence only. The specification's targets apply to held-out real "
                    "team-marked pages, which do not exist yet."})
        (stage / "training_log.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in history) + json.dumps({"effective_config": config_record})
            + "\n", encoding="utf-8")
        (stage / "config.yaml").write_text(yaml.safe_dump(config_record, sort_keys=False), encoding="utf-8")
        (stage / "NOTICE.md").write_text(_notice(record, quick_note), encoding="utf-8")
        environment_record = record["environment"]
        targets = [{"metric": metric, "target": target, "evaluation_data": "held-out team-marked physical pages",
                    "status": "not_evaluated", "reason": "no real team-marked page exists yet"}
                   for metric, target in (
                       ("exclusion per-row detection and linking precision", ">= 0.90"),
                       ("exclusion per-row detection and linking recall", ">= 0.90"),
                       ("price-change detection precision", ">= 0.85"),
                       ("price-change detection recall", ">= 0.85"))]
        manifest = {
            "model_id": MODEL_ID, "version": version, "status": "candidate",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "created_by": created_by, "task": "object_detection", "architecture": settings.architecture,
            "base_checkpoint": None if base is None else f"{base['name']} ({base['file']}, sha256 {base['sha256']})",
            "base_checkpoint_licence": None if base is None else BASE_LICENCE,
            "weights_sha256": sha256_file(stage / serving.WEIGHTS_FILE),
            "label_schema_version": serving.LABEL_SCHEMA_VERSION, "taxonomy_version": serving.MARK_TAXONOMY_VERSION,
            "preprocessing_version": settings.preprocessing_version,
            "preprocessing_sha256": sha256_file(stage / "preprocessing.json"),
            "dataset_ids": [data.get("dataset", {}).get("name")],
            "dataset_ids_note": "generated locally; not listed in data/manifests/dataset_sources.json",
            "dataset_access_evidence": None,
            "dataset_access_evidence_reason": "the dataset is generated in this repository; no access consent applies",
            "split_version": data.get("split_version"), "split_hashes": data.get("split_files"),
            "conversion_version": CONVERSION_VERSION, "seed": config.seed,
            "hardware": {key: environment_record.get(key) for key in (
                "machine", "processor", "platform", "memory_gb", "device", "accelerator", "where")},
            "dependency_versions": {key: environment_record.get(key) for key in (
                "python", "torch", "torchvision", "numpy", "pillow", "safetensors", "pydantic")},
            "code_revision": record["code_revision"], "training_duration_s": record["training_duration_s"],
            "metrics": {"partition": "validation", "provenance": "synthetic", "best_epoch": record["best_epoch"],
                        "selection_score": record["best_score"], "selection_metric": SELECTION_METRIC,
                        "pages": evaluation_report.get("pages"), "marks": evaluation_report.get("marks"),
                        "detection": {c: {k: v for k, v in (evaluation_report.get("detection", {}).get(c) or {}).items()
                                          if k != "curve"} for c in TARGET_CLASSES},
                        "operating_point": evaluation_report.get("operating_point", {}).get("classes"),
                        "false_positive_kinds": evaluation_report.get("operating_point", {}).get(
                            "false_positive_summary"),
                        # Per-row detection and linking, named after the rows the linker was given.
                        "row_level_with_oracle_cell_union_rows": row_level("oracle_cell_union_rows"),
                        "row_level_with_oracle_cell_union_rows_and_oracle_detections": row_level(
                            "oracle_cell_union_rows", "oracle_detections"),
                        "row_level_with_oracle_table_band_rows": row_level("oracle_table_band_rows"),
                        "row_level_with_oracle_table_band_rows_note": "contrast only: full table bands are not the "
                                                                      "row boxes M5 supplies",
                        "row_level_with_m5_rows": row_level("m5_rows"),
                        **({"row_level_with_m5_rows_source": linking["m5_rows"].get("source")} if linking.get("m5_rows")
                           else {"row_level_with_m5_rows_reason": "no M5 rows file was given to this evaluation"})},
            "targets": targets,
            "known_limits": ((quick_note + " ") if quick_note else "") +
                            "Stage 1 only: trained and validated on synthetic pages of one layout with font-rendered "
                            "ink. No real photograph, handwriting or unseen writer. Strike-only and remark-only "
                            "exclusions extend the specification's X examples. Validation chose the epoch, the score "
                            "thresholds and the reported numbers, so the operating point is optimistic. The labels "
                            "box a cross and its remark as one mark; the detector also proposes the parts, which "
                            "count as false positives. Row linking depends strongly on the row boxes the linker is "
                            "given: the primary figures use oracle rows built as the union of each row's printed "
                            "cells (module-06 step 4); figures with rows from one trained M5 candidate are recorded "
                            "separately when present; the full-table-band rows are a contrast only. The detector "
                            "does not read handwritten amounts.",
            "quick_run": quick, **({"quick_run_note": quick_note} if quick else {}),
            "provenance": "synthetic", "provenance_detail": data.get("dataset", {}).get("provenance"),
            "contract_version": SCHEMA_VERSION,
            "thresholds": {"score_threshold": {c: float(thresholds[c]) for c in TARGET_CLASSES}, "status": "proposed",
                           "selected_on": "validation", "rule": threshold_rule,
                           "score_threshold_role": "the thresholds this entry's metrics were computed with",
                           "alternatives": None if threshold_alternatives is None else dict(threshold_alternatives),
                           "note": "Proposed, and not a value to copy as it stands. Validation chose the epoch, "
                                   "these thresholds and the reported numbers, so the operating point is optimistic. "
                                   "A lower threshold keeps more weak detections: more false proposals for the "
                                   "surveyor, less risk of losing a mark. The team chooses.",
                           "applied_by": "link_detections, from the linking configuration"},
            "m4_cache": data.get("m4_cache"),
            "source_run": {"run_id": record["run_id"], "status": record["status"],
                           "completed_epochs": record["completed_epochs"], "best_epoch": record["best_epoch"],
                           "config_sha256": hashlib.sha256((run_dir / "config.json").read_bytes()).hexdigest(),
                           "new_tensors": record["starting_point"]["new_tensors"] if base is not None else "all"},
        }
        if base is None:
            manifest["base_checkpoint_reason"] = "the network was trained from random weights"
            manifest["base_checkpoint_licence_reason"] = "there is no base checkpoint"
        for field in MANIFEST_FIELDS:  # a null never travels without its reason
            if manifest[field] is None:
                manifest.setdefault(f"{field}_reason", "the run's data record does not provide it")
        _write_json(stage / "manifest.json", manifest)

        check = _serving_check(run_dir, stage, check_pages, device)
        if not check["passed"]:
            raise RuntimeError(f"the staged entry does not reproduce the run's detections ({check}); "
                               "nothing was exported")
        _write_json(stage / "serving_check.json", check)
        os.replace(stage, entry)
    return entry


def serving_selection(entry: str | Path) -> dict[str, Any]:
    """The values a person copies into configuration to serve ``entry``; this writes nothing."""
    entry = Path(entry)
    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    settings = DetectorSettings.from_record(json.loads((entry / "preprocessing.json").read_text(encoding="utf-8")))
    return {
        "model": {"model_id": manifest["model_id"], "model_version": manifest["version"],
                  "architecture": manifest["architecture"], "input_min_size": settings.min_size,
                  "input_max_size": settings.max_size, "nms_iou": settings.nms_iou,
                  "max_detections_per_page": settings.max_detections_per_page, "score_floor": settings.score_floor,
                  "status": manifest["status"]},
        "linking": {"detection": {"score_threshold": dict(manifest["thresholds"]["score_threshold"])}},
        "linking_alternatives": manifest["thresholds"].get("alternatives"),
    }


__all__ = ["ANCHOR_PRESETS", "BOX_TOLERANCE_PX", "FINAL_REPORT_NAME", "HISTORY_TOLERANCE", "LOSS_TERMS",
           "MANIFEST_FIELDS", "MODEL_ID", "SCORE_TOLERANCE", "SELECTION_METRIC", "TrainingConfig", "build_detector",
           "decay_epochs", "environment", "evaluation_dirs", "export_for_serving", "final_evaluation_path",
           "history_check", "learning_rate_at", "load_run", "new_run_id", "pages_used", "parameter_table",
           "predict_pages", "pretrained_info", "read_history", "reference_kind", "reference_model", "rpn_anchors",
           "serving_selection", "train", "validation_losses"]
