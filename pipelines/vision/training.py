"""Manual, offline semantic-segmentation experiments for HITL parts and damage.

No imports of torch/transformers until a training operation needs them. The model
registry here contains *candidates*, never promoted serving models. See the two
notebooks in notebooks/vision for editable recipes and interpretation limits.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance

IGNORE_INDEX = 255


@dataclass
class TrainingConfig:
    task: str = "parts"
    architecture: str = "segformer"
    checkpoint: str = "nvidia/mit-b2"
    revision: str | None = None
    pretrained: bool = True
    image_size: int = 512
    batch_size: int = 2
    epochs: int = 30
    encoder_lr: float = 6e-5
    head_lr: float = 6e-4
    weight_decay: float = 0.01
    patience: int = 8
    min_delta: float = 0.0
    freeze_encoder_epochs: int = 0
    freeze_batchnorm: bool = True
    accumulation_steps: int = 1
    ce_weight: float = 1.0
    dice_weight: float = 0.5
    max_grad_norm: float = 1.0
    horizontal_flip: float = 0.5
    brightness: float = 0.15
    contrast: float = 0.15
    mean: tuple = (0.485, 0.456, 0.406)
    std: tuple = (0.229, 0.224, 0.225)
    device: str = "auto"
    workers: int = 0
    seed: int = 42
    amp: bool = True
    artifacts_root: str = "artifacts"
    run_id: str | None = None

    def __post_init__(self):
        if self.task not in {"parts", "damage"}:
            raise ValueError("task must be parts or damage (HITL damage taxonomy)")
        if self.architecture not in {"segformer", "resnet50", "resnet101", "hf_semantic"}:
            raise ValueError("Unknown architecture; add a semantic logits adapter in build_model")
        for name in ("image_size", "batch_size", "epochs", "accumulation_steps", "patience"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.image_size < 32 or self.image_size % 32:
            raise ValueError("image_size must be a multiple of 32, at least 32")
        if self.workers < 0 or self.freeze_encoder_epochs < 0:
            raise ValueError("workers and freeze_encoder_epochs must be nonnegative")
        if not 0 <= self.horizontal_flip <= 1:
            raise ValueError("horizontal_flip must be a probability")
        if not (0 <= self.brightness <= 1 and 0 <= self.contrast <= 1):
            raise ValueError("brightness/contrast must lie between 0 and 1")
        if len(self.mean) != 3 or len(self.std) != 3 or min(self.std) <= 0:
            raise ValueError("Provide three normalization means and positive standard deviations")
        if min(self.encoder_lr, self.head_lr, self.max_grad_norm) <= 0:
            raise ValueError("Learning rates and max_grad_norm must be positive")
        if min(self.ce_weight, self.dice_weight, self.weight_decay, self.min_delta) < 0:
            raise ValueError("Loss weights, decay and min_delta must be nonnegative")
        if self.ce_weight + self.dice_weight == 0:
            raise ValueError("At least one loss weight must be positive")
        if self.run_id and (Path(self.run_id).name != self.run_id or self.run_id in {".", ".."}):
            raise ValueError("run_id must be a single directory name")


def select_device(preference="auto"):
    """CUDA first, then Apple Metal, then CPU; explicit unavailable devices fail."""
    import torch
    if preference == "auto":
        preference = "cuda" if torch.cuda.is_available() else (
            "mps" if torch.backends.mps.is_available() else "cpu")
    device = torch.device(preference)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable in this torch installation")
    if device.type == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS requested but unavailable; select CPU explicitly")
    if device.type not in {"cuda", "mps", "cpu"}:
        raise ValueError("Supported devices: auto, cuda[:index], mps, cpu")
    return device


def _json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n")
    temp.replace(path)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def task_manifest(manifest, task):
    """Accept prepare_hitl/load_prepared dictionaries or a manifest JSON filename."""
    if isinstance(manifest, (str, Path)):
        from pipelines.vision.hitl_data import load_prepared
        manifest = load_prepared(manifest)
    result = dict(manifest)
    result["records"] = [r for r in manifest["records"] if r.get("task", task) == task]
    names = manifest["class_names"]
    result["class_names"] = names[task] if isinstance(names, dict) else names
    if len(result["class_names"]) < 2 or result["class_names"][0] != "background":
        raise ValueError("Taxonomy must start with background and contain foreground labels")
    result["task"] = task
    result["manifest_hash"] = manifest.get("manifest_hash") or _hash(manifest)
    # Include file content hashes and actual memberships, not just a caller's manifest label.
    result["split_hash"] = _hash(sorted([
        {k: r.get(k) for k in ("sample_id", "group_id", "split", "image_sha256", "mask_sha256")}
        for r in result["records"]], key=lambda r: (str(r["sample_id"]), str(r["split"]))))
    groups = {}
    for record in manifest["records"]:
        if record["split"] not in {"train", "val", "test", "reserved"}:
            raise ValueError("Unknown split")
        for key in ("group_id", "image_sha256"):
            value = record.get(key)
            if value:
                group_key = (key, value)
                previous = groups.setdefault(group_key, record["split"])
                if previous != record["split"]:
                    raise ValueError(f"Leakage: {key} occurs in multiple splits")
    return result


def _verify_records(records):
    for record in records:
        for kind in ("image", "mask"):
            expected = record.get(f"{kind}_sha256")
            if expected and _file_hash(record[f"{kind}_path"]) != expected:
                raise ValueError(f"Prepared {kind} hash mismatch: {record['sample_id']}")


def letterbox(image, mask, size, mean=(0.485, 0.456, 0.406)):
    """Keep aspect ratio; resize masks nearest; pad labels with ignore 255."""
    if image.size != mask.size:
        raise ValueError("Image and mask dimensions differ")
    width, height = image.size
    scale = min(size / width, size / height)
    resized = (max(1, round(width * scale)), max(1, round(height * scale)))
    left, top = (size - resized[0]) // 2, (size - resized[1]) // 2
    canvas = Image.new("RGB", (size, size), tuple(round(x * 255) for x in mean))
    target = Image.new("L", (size, size), IGNORE_INDEX)
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    target.paste(mask.resize(resized, Image.Resampling.NEAREST), (left, top))
    return canvas, target


class SegmentationDataset:
    """Map-style torch dataset; augmentation never modifies source evidence."""
    def __init__(self, records, config, training=False, num_classes=None):
        self.records, self.config, self.training = records, config, training
        self.num_classes = num_classes

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        import torch
        record = self.records[index]
        with Image.open(record["image_path"]) as source:
            image = source.convert("RGB")
        with Image.open(record["mask_path"]) as source:
            # P-mode indexed masks must retain ids rather than convert their palette to luminance.
            labels = np.asarray(source)
            if labels.ndim != 2:
                raise ValueError("Expected a single-channel class-index mask")
            if self.num_classes is not None and np.any((labels != IGNORE_INDEX) & (labels >= self.num_classes)):
                raise ValueError("Mask has labels outside the frozen taxonomy")
            mask = Image.fromarray(labels.astype(np.uint8))
        image, mask = letterbox(image, mask, self.config.image_size, self.config.mean)
        if self.training:
            if random.random() < self.config.horizontal_flip:
                image, mask = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            image = ImageEnhance.Brightness(image).enhance(random.uniform(1-self.config.brightness, 1+self.config.brightness))
            image = ImageEnhance.Contrast(image).enhance(random.uniform(1-self.config.contrast, 1+self.config.contrast))
        pixels = np.asarray(image, dtype=np.float32) / 255.0
        pixels = (pixels - np.asarray(self.config.mean, dtype=np.float32)) / np.asarray(self.config.std, dtype=np.float32)
        return {"pixel_values": torch.from_numpy(pixels.transpose(2, 0, 1).copy()),
                "labels": torch.from_numpy(np.asarray(mask, dtype=np.int64).copy()),
                "sample_id": str(record["sample_id"])}


def build_model(config, class_names, *, saved_config=None, load_pretrained=None):
    """Return a logits adapter with encoder_parameters() and serializable model config.

    hf_semantic accepts Hugging Face dense semantic architectures (e.g. UperNet
    with a Swin backbone), not classifier-only ViT checkpoints or query masks.
    Normalization is explicitly controlled by TrainingConfig for all backbones.
    """
    os.environ.setdefault("HF_HOME", str(Path(config.artifacts_root).resolve() / "models" / ".cache" / "huggingface"))
    os.environ.setdefault("TORCH_HOME", str(Path(config.artifacts_root).resolve() / "models" / ".cache" / "torch"))
    import torch
    from torch import nn
    import torch.nn.functional as F
    pretrained = config.pretrained if load_pretrained is None else load_pretrained
    count = len(class_names)
    if config.architecture in {"segformer", "hf_semantic"}:
        from transformers import AutoConfig, AutoModelForSemanticSegmentation, SegformerConfig, SegformerForSemanticSegmentation
        if saved_config:
            values = dict(saved_config)
            model_type = values.pop("model_type")
            model_config = AutoConfig.for_model(model_type, **values)
        else:
            model_config = AutoConfig.from_pretrained(config.checkpoint, revision=config.revision)
        model_config.num_labels = count
        model_config.id2label = dict(enumerate(class_names))
        model_config.label2id = {name: i for i, name in enumerate(class_names)}
        model_config.semantic_loss_ignore_index = IGNORE_INDEX
        if config.architecture == "segformer":
            if not isinstance(model_config, SegformerConfig):
                raise ValueError("segformer requires a SegFormer/MiT checkpoint")
            model_cls = SegformerForSemanticSegmentation
            base = model_cls.from_pretrained(config.checkpoint, revision=config.revision, config=model_config,
                                            ignore_mismatched_sizes=True) if pretrained else model_cls(model_config)
            encoder = base.segformer
        else:
            base = AutoModelForSemanticSegmentation.from_pretrained(
                config.checkpoint, revision=config.revision, config=model_config,
                ignore_mismatched_sizes=True) if pretrained else AutoModelForSemanticSegmentation.from_config(model_config)
            encoder = getattr(base, "backbone", None)
            if encoder is None:
                encoder = base.base_model
            if encoder is base:
                raise ValueError("Cannot identify encoder; add an explicit encoder adapter for this architecture")
        architecture_config = base.config.to_dict()
    else:
        from torchvision.models import ResNet50_Weights, ResNet101_Weights
        from torchvision.models.segmentation import deeplabv3_resnet50, deeplabv3_resnet101
        factory, weights = (deeplabv3_resnet50, ResNet50_Weights.DEFAULT) if config.architecture == "resnet50" else (deeplabv3_resnet101, ResNet101_Weights.DEFAULT)
        base = factory(weights=None, weights_backbone=weights if pretrained else None,
                       num_classes=count, aux_loss=False)
        encoder = base.backbone
        architecture_config = {"architecture": config.architecture, "num_labels": count,
                               "encoder_weights": str(weights) if pretrained else None}

    class LogitsAdapter(nn.Module):
        def __init__(self):
            super().__init__()
            self.network = base
            self.architecture_config = architecture_config
            # Avoid registering the same encoder twice in state_dict.
            self._encoder_ids = {id(p) for p in encoder.parameters()}

        def encoder_parameters(self):
            return [p for p in self.parameters() if id(p) in self._encoder_ids]

        def head_parameters(self):
            return [p for p in self.parameters() if id(p) not in self._encoder_ids]

        def forward(self, pixels):
            result = self.network(pixels)
            logits = result["out"] if isinstance(result, dict) and "out" in result else result.logits
            if logits.ndim != 4 or logits.shape[1] != count:
                raise ValueError("Adapter requires dense N,C,H,W class logits")
            return F.interpolate(logits, size=pixels.shape[-2:], mode="bilinear", align_corners=False)

    return LogitsAdapter()


def segmentation_loss(logits, labels, ce_weight=1.0, dice_weight=0.5):
    """CE plus foreground soft Dice, excluding ignore pixels from both terms."""
    import torch
    import torch.nn.functional as F
    valid = labels != IGNORE_INDEX
    if not bool(valid.any()):
        return logits.sum() * 0
    logits = logits.float()
    ce = F.cross_entropy(logits, labels, ignore_index=IGNORE_INDEX)
    targets = F.one_hot(labels.masked_fill(~valid, 0), logits.shape[1]).permute(0, 3, 1, 2).float()
    targets = targets * valid[:, None]
    probabilities = logits.softmax(dim=1) * valid[:, None]
    intersection = (probabilities * targets).sum((0, 2, 3))[1:]
    denominator = (probabilities + targets).sum((0, 2, 3))[1:]
    dice = 1 - ((2 * intersection + 1e-6) / (denominator + 1e-6)).mean()
    return ce_weight * ce + dice_weight * dice


def confusion_counts(target, prediction, num_classes):
    """Rows are truth, columns predictions. Ignore padding/conflicted truth."""
    target, prediction = np.asarray(target).ravel(), np.asarray(prediction).ravel()
    if target.shape != prediction.shape:
        raise ValueError("Target/prediction shapes differ")
    valid = target != IGNORE_INDEX
    target, prediction = target[valid], prediction[valid]
    if np.any((target < 0) | (target >= num_classes) | (prediction < 0) | (prediction >= num_classes)):
        raise ValueError("Class index outside taxonomy")
    return np.bincount(num_classes * target.astype(np.int64) + prediction.astype(np.int64),
                       minlength=num_classes ** 2).reshape(num_classes, num_classes)


def metrics_from_confusion(matrix, class_names):
    """Absent-in-truth but predicted classes score zero IoU and stay in macro.

    Classes with neither truth nor predictions are undefined (null), reported
    with a reason, and excluded from macro denominator. Background is excluded.
    """
    matrix = np.asarray(matrix, dtype=np.int64)
    if matrix.shape != (len(class_names), len(class_names)) or np.any(matrix < 0):
        raise ValueError("Invalid confusion matrix")
    true, predicted, tp = matrix.sum(1), matrix.sum(0), matrix.diagonal()
    union = true + predicted - tp
    def ratio(a, b):
        return float(a / b) if b else None
    per_class = [{"class_id": i, "class_name": name, "iou": ratio(tp[i], union[i]),
                  "dice": ratio(2 * tp[i], true[i] + predicted[i]),
                  "precision": ratio(tp[i], predicted[i]), "recall": ratio(tp[i], true[i]),
                  "support_pixels": int(true[i]), "predicted_pixels": int(predicted[i]),
                  "iou_reason": None if union[i] else "absent_in_truth_and_prediction"}
                 for i, name in enumerate(class_names)]
    def macro(key):
        values = [r[key] for r in per_class[1:] if r[key] is not None]
        return float(np.mean(values)) if values else None
    return {"miou_foreground": macro("iou"), "dice_foreground": macro("dice"),
            "precision_foreground": macro("precision"), "recall_foreground": macro("recall"),
            "pixel_accuracy": ratio(tp.sum(), matrix.sum()), "valid_pixels": int(matrix.sum()),
            "macro_class_count": int(np.count_nonzero(union[1:])),
            "macro_policy": "background excluded; zero-union classes undefined/excluded; false-positive-only classes included",
            "per_class": per_class, "confusion_matrix": matrix.tolist()}


def _worker_seed(worker_id):
    import torch
    worker_seed = torch.initial_seed() % 2**32
    np.random.seed(worker_seed)
    random.seed(worker_seed)


def _loader(records, config, device, classes, training=False):
    import torch
    return torch.utils.data.DataLoader(
        SegmentationDataset(records, config, training, len(classes)), batch_size=config.batch_size,
        shuffle=training, num_workers=0 if device.type == "mps" else config.workers,
        pin_memory=device.type == "cuda", worker_init_fn=_worker_seed,
        generator=torch.Generator().manual_seed(config.seed), drop_last=False)


def _evaluate(model, loader, device, classes, config):
    import torch
    model.eval()
    matrix = np.zeros((len(classes), len(classes)), dtype=np.int64)
    loss_sum, image_count = 0.0, 0
    started = time.perf_counter()
    with torch.inference_mode():
        for batch in loader:
            pixels, labels = batch["pixel_values"].to(device), batch["labels"].to(device)
            logits = model(pixels)
            loss = segmentation_loss(logits, labels, config.ce_weight, config.dice_weight)
            loss_sum += loss.item() * len(labels)
            image_count += len(labels)
            matrix += confusion_counts(labels.cpu().numpy(), logits.argmax(1).cpu().numpy(), len(classes))
    if not image_count:
        raise ValueError("Evaluation split is empty")
    metrics = metrics_from_confusion(matrix, classes)
    elapsed = time.perf_counter() - started
    metrics.update(loss=loss_sum / image_count, image_count=image_count, elapsed_seconds=elapsed,
                   images_per_second=image_count / elapsed, timing_scope="full evaluation including loading, loss and CPU confusion")
    return metrics


def _environment(device):
    def git(*args):
        try:
            result = subprocess.run(["git", *args], capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else None
        except FileNotFoundError:
            return None
    import torch
    versions = {}
    for package in ("torch", "torchvision", "transformers", "numpy", "Pillow", "matplotlib", "pandas"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": versions,
            "git_revision": git("rev-parse", "HEAD"), "git_status": git("status", "--porcelain"),
            "device": str(device), "cuda_version": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else platform.processor(),
            "cuda_available": torch.cuda.is_available(), "mps_available": torch.backends.mps.is_available()}


def _save_checkpoint(path, state):
    import torch
    temporary = Path(str(path) + ".tmp")
    def cpu(value):
        if isinstance(value, torch.Tensor):
            return value.detach().cpu()
        if isinstance(value, dict):
            return {key: cpu(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cpu(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cpu(item) for item in value)
        return value
    torch.save(cpu(state), temporary)
    temporary.replace(path)


def train(manifest, config):
    """Create a new candidate run, select best on validation, never touch test."""
    import torch
    from torch import nn
    manifest = task_manifest(manifest, config.task)
    classes = manifest["class_names"]
    device = select_device(config.device)
    records = manifest["records"]
    train_records = [r for r in records if r["split"] == "train"]
    val_records = [r for r in records if r["split"] == "val"]
    if not train_records or not val_records:
        raise ValueError("Both training and validation records are required")
    _verify_records(train_records + val_records)
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config.seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    run_id = config.run_id or f"{config.task}-{config.architecture}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    root = Path(config.artifacts_root).resolve()
    run_dir = root / "models" / ("parts" if config.task == "parts" else "damage-hitl") / run_id
    evaluation_dir = root / "evaluation" / run_id
    if run_dir.exists() or evaluation_dir.exists():
        raise FileExistsError("Run ID already exists; use a new run_id to preserve prior artifacts")
    run_dir.mkdir(parents=True)
    evaluation_dir.mkdir(parents=True)
    record = {"run_id": run_id, "run_dir": str(run_dir), "evaluation_dir": str(evaluation_dir),
              "status": "running", "registry_status": "candidate", "task": config.task,
              "dataset": "HITL", "class_names": classes, "taxonomy_hash": _hash(classes),
              "manifest_hash": manifest["manifest_hash"], "split_hash": manifest["split_hash"],
              "config": asdict(config), "environment": _environment(device),
              "preprocessing": {"resize": "aspect-preserving centered letterbox", "mask_resampling": "nearest",
                                "ignore_index": IGNORE_INDEX, "background": 0, "reduce_labels": False,
                                "evaluation_frame": "resized letterboxed model frame; padding excluded"},
              "effective_workers": 0 if device.type == "mps" else config.workers,
              "precision": "cuda_float16_autocast" if config.amp and device.type == "cuda" else "float32",
              "weight_source": (config.checkpoint if config.architecture in {"segformer", "hf_semantic"}
                                else ("ResNet50_Weights.IMAGENET1K_V2" if config.architecture == "resnet50"
                                      else "ResNet101_Weights.IMAGENET1K_V2")) if config.pretrained else "random_initialization",
              "weight_source_url": (f"https://huggingface.co/{config.checkpoint}" if config.architecture in {"segformer", "hf_semantic"}
                                    else "https://docs.pytorch.org/vision/stable/models.html"),
              "license_reference": (f"https://huggingface.co/{config.checkpoint}/blob/main/README.md"
                                    if config.architecture in {"segformer", "hf_semantic"}
                                    else "https://github.com/pytorch/vision/blob/main/LICENSE"),
              "license_note": "Review checkpoint/dataset terms separately before deployment; no serving promotion by this harness",
              "created_utc": datetime.now(timezone.utc).isoformat()}
    _json(run_dir / "manifest.json", record)
    _json(run_dir / "dataset_manifest.json", manifest)
    _json(run_dir / "config.json", asdict(config))
    history = []
    try:
        model = build_model(config, classes).float().to(device)
        _json(run_dir / "model_config.json", model.architecture_config)
        record["resolved_checkpoint_revision"] = getattr(getattr(model.network, "config", None), "_commit_hash", None)
        record["parameters"] = sum(p.numel() for p in model.parameters())
        encoder_parameters = model.encoder_parameters()
        optimizer = torch.optim.AdamW([{"params": encoder_parameters, "lr": config.encoder_lr},
                                       {"params": model.head_parameters(), "lr": config.head_lr}], weight_decay=config.weight_decay)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=config.epochs)
        use_amp = config.amp and device.type == "cuda"
        scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
        training_loader = _loader(train_records, config, device, classes, training=True)
        validation_loader = _loader(val_records, config, device, classes)
        best, stale = -math.inf, 0
        for epoch in range(config.epochs):
            started = time.perf_counter()
            model.train()
            for parameter in encoder_parameters:
                parameter.requires_grad_(epoch >= config.freeze_encoder_epochs)
            if config.freeze_batchnorm:
                for module in model.modules():
                    if isinstance(module, nn.modules.batchnorm._BatchNorm):
                        module.eval()
            optimizer.zero_grad(set_to_none=True)
            train_loss, seen = 0.0, 0
            matrix = np.zeros((len(classes), len(classes)), dtype=np.int64)
            total_batches = len(training_loader)
            for step, batch in enumerate(training_loader):
                pixels, labels = batch["pixel_values"].to(device), batch["labels"].to(device)
                group_start = (step // config.accumulation_steps) * config.accumulation_steps
                group_size = min(config.accumulation_steps, total_batches - group_start)
                with torch.autocast(device_type=device.type, enabled=use_amp):
                    logits = model(pixels)
                    loss = segmentation_loss(logits, labels, config.ce_weight, config.dice_weight)
                if not bool(torch.isfinite(loss)):
                    raise FloatingPointError("Non-finite training loss")
                scaler.scale(loss / group_size).backward()
                if (step + 1) % config.accumulation_steps == 0 or step + 1 == total_batches:
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer.zero_grad(set_to_none=True)
                train_loss += loss.item() * len(labels)
                seen += len(labels)
                matrix += confusion_counts(labels.cpu().numpy(), logits.detach().argmax(1).cpu().numpy(), len(classes))
            val = _evaluate(model, validation_loader, device, classes, config)
            score = val["miou_foreground"]
            if score is None:
                raise ValueError("Validation has no foreground union; cannot select a model")
            epoch_record = {"epoch": epoch+1, "train_loss": train_loss / seen,
                            "train_miou_foreground": metrics_from_confusion(matrix, classes)["miou_foreground"],
                            "val_loss": val["loss"], "val_miou_foreground": score,
                            "val_dice_foreground": val["dice_foreground"],
                            "encoder_lr": optimizer.param_groups[0]["lr"], "head_lr": optimizer.param_groups[1]["lr"],
                            "elapsed_seconds": time.perf_counter()-started}
            history.append(epoch_record)
            _json(run_dir / "history.json", history)
            with (run_dir / "training_log.jsonl").open("a") as log:
                log.write(json.dumps(epoch_record) + "\n")
            improved = score > best + config.min_delta
            if improved:
                best, stale = score, 0
                record.update(best_epoch=epoch+1, best_val_miou_foreground=score)
                _json(evaluation_dir / "val_metrics.json", {**val, "split": "val", "epoch": epoch+1})
            else:
                stale += 1
            scheduler.step()
            checkpoint = {"model": model.state_dict(), "optimizer": optimizer.state_dict(),
                          "scheduler": scheduler.state_dict(), "scaler": scaler.state_dict(),
                          "epoch": epoch+1, "best_val_miou_foreground": best,
                          "manifest_hash": manifest["manifest_hash"], "split_hash": manifest["split_hash"],
                          "class_names": classes, "config": asdict(config), "torch_rng_state": torch.get_rng_state()}
            _save_checkpoint(run_dir / "last.pt", checkpoint)
            if improved:
                _save_checkpoint(run_dir / "best.pt", checkpoint)
            _json(run_dir / "manifest.json", record)
            print(f"epoch {epoch+1}/{config.epochs}: train loss {train_loss/seen:.4f}, val loss {val['loss']:.4f}, val foreground mIoU {score:.4f}")
            if stale >= config.patience:
                break
        record.update(status="completed", completed_epochs=len(history), checkpoint_sha256=_file_hash(run_dir / "best.pt"))
    except BaseException as error:
        record.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      failure=f"{type(error).__name__}: {error}", completed_epochs=len(history))
        raise
    finally:
        record["updated_utc"] = datetime.now(timezone.utc).isoformat()
        _json(run_dir / "manifest.json", record)
        _json(evaluation_dir / "manifest.json", record)
    return record


def _read_run(run_dir):
    """Locate moved artifacts by their standard artifacts/models/task/run layout."""
    run_dir = Path(run_dir).resolve()
    record = json.loads((run_dir / "manifest.json").read_text())
    record["run_dir"] = str(run_dir)
    record["evaluation_dir"] = str(run_dir.parents[2] / "evaluation" / run_dir.name)
    Path(record["evaluation_dir"]).mkdir(parents=True, exist_ok=True)
    return record


def load_run(run_dir, device="auto"):
    """Reload a local best checkpoint without downloading the base model again."""
    import torch
    run_dir = Path(run_dir)
    record = _read_run(run_dir)
    config = TrainingConfig(**record["config"])
    if record.get("checkpoint_sha256") and _file_hash(run_dir / "best.pt") != record["checkpoint_sha256"]:
        raise ValueError("Best checkpoint hash no longer matches recorded artifact")
    selected_device = select_device(device)
    model = build_model(config, record["class_names"], saved_config=json.loads((run_dir / "model_config.json").read_text()), load_pretrained=False)
    state = torch.load(run_dir / "best.pt", map_location="cpu", weights_only=True)
    if state["class_names"] != record["class_names"] or state["split_hash"] != record["split_hash"]:
        raise ValueError("Checkpoint metadata differs from run manifest")
    model.load_state_dict(state["model"])
    return model.float().to(selected_device).eval(), config, record, selected_device


def evaluate_run(run_dir, manifest, split="val", device="auto"):
    """Explicit held-out evaluation; call test only after all choices are frozen."""
    if split not in {"val", "test"}:
        raise ValueError("Evaluation supports val or test; reserved assignment data is separate")
    model, config, record, selected_device = load_run(run_dir, device)
    current = task_manifest(manifest, config.task)
    if any(current[key] != record[key] for key in ("class_names", "manifest_hash", "split_hash")):
        raise ValueError("Evaluation data/taxonomy differs from the frozen run")
    records = [r for r in current["records"] if r["split"] == split]
    _verify_records(records)
    loader = _loader(records, config, selected_device, current["class_names"])
    metrics = _evaluate(model, loader, selected_device, current["class_names"], config)
    metrics.update(split=split, epoch=record["best_epoch"], run_id=record["run_id"],
                   checkpoint_sha256=_file_hash(Path(run_dir) / "best.pt"))
    output = Path(record["evaluation_dir"])
    _json(output / f"{split}_metrics.json", metrics)
    import pandas as pd
    pd.DataFrame(metrics["per_class"]).to_csv(output / f"{split}_per_class.csv", index=False)
    np.save(output / f"{split}_confusion.npy", np.asarray(metrics["confusion_matrix"]))
    return metrics


def plot_history(run_dir):
    import matplotlib.pyplot as plt
    run_dir = Path(run_dir)
    history = json.loads((run_dir / "history.json").read_text())
    record = _read_run(run_dir)
    fig, axes = plt.subplots(1, 3, figsize=(16, 4))
    epochs = [h["epoch"] for h in history]
    for key, axis, label in (("train_loss", axes[0], "train"), ("val_loss", axes[0], "validation"),
                             ("train_miou_foreground", axes[1], "train"), ("val_miou_foreground", axes[1], "validation"),
                             ("encoder_lr", axes[2], "encoder"), ("head_lr", axes[2], "head")):
        axis.plot(epochs, [h[key] for h in history], label=label)
    for axis, title in zip(axes, ("CE + Dice loss", "Foreground mIoU", "Learning rate")):
        axis.set(title=title, xlabel="Epoch")
        axis.axvline(record["best_epoch"], linestyle="--", color="grey", alpha=.5)
        axis.legend()
        axis.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(Path(record["evaluation_dir"]) / "training_history.png", dpi=160)
    return fig


def plot_confusion(run_dir, split="val"):
    import matplotlib.pyplot as plt
    record = _read_run(run_dir)
    metrics = json.loads((Path(record["evaluation_dir"]) / f"{split}_metrics.json").read_text())
    counts = np.asarray(metrics["confusion_matrix"])
    normalized = np.divide(counts, counts.sum(1, keepdims=True), out=np.zeros_like(counts, dtype=float), where=counts.sum(1, keepdims=True)!=0)
    fig, axes = plt.subplots(1, 2, figsize=(18, 8))
    for axis, values, title in zip(axes, (counts, normalized), ("Pixel counts", "Normalized by true class")):
        display = axis.imshow(values, cmap="Blues")
        axis.set(xticks=range(len(record["class_names"])), yticks=range(len(record["class_names"])),
                 xticklabels=record["class_names"], yticklabels=record["class_names"], xlabel="Predicted", ylabel="True", title=title)
        plt.setp(axis.get_xticklabels(), rotation=90)
        fig.colorbar(display, ax=axis, fraction=.046)
    fig.tight_layout()
    fig.savefig(Path(record["evaluation_dir"]) / f"{split}_confusion.png", dpi=160)
    # Separate per-class plot makes rare-class failure visible alongside the matrix.
    bars, axis = plt.subplots(figsize=(12, 5))
    foreground = metrics["per_class"][1:]
    axis.bar([r["class_name"] for r in foreground], [r["iou"] if r["iou"] is not None else np.nan for r in foreground])
    axis.set(ylabel="IoU", ylim=(0, 1), title=f"{split}: foreground per-class IoU (undefined classes omitted)")
    plt.setp(axis.get_xticklabels(), rotation=60, ha="right")
    bars.tight_layout()
    bars.savefig(Path(record["evaluation_dir"]) / f"{split}_per_class_iou.png", dpi=160)
    return fig, bars


def show_predictions(run_dir, manifest, split="val", count=4, device="auto"):
    import torch
    import matplotlib.pyplot as plt
    if split not in {"val", "test"}:
        raise ValueError("Prediction preview must use val/test")
    model, config, record, selected_device = load_run(run_dir, device)
    current = task_manifest(manifest, config.task)
    if any(current[key] != record[key] for key in ("class_names", "manifest_hash", "split_hash")):
        raise ValueError("Preview manifest differs from the frozen run")
    records = [r for r in current["records"] if r["split"] == split][:count]
    if not records:
        raise ValueError("No examples for this preview")
    _verify_records(records)
    dataset = SegmentationDataset(records, config, num_classes=len(current["class_names"]))
    palette = plt.get_cmap("turbo", len(current["class_names"]))
    fig, axes = plt.subplots(len(records), 4, figsize=(16, 4*len(records)), squeeze=False)
    for i in range(len(records)):
        example = dataset[i]
        with torch.inference_mode():
            prediction = model(example["pixel_values"][None].to(selected_device)).argmax(1)[0].cpu().numpy()
        truth = example["labels"].numpy()
        pixels = example["pixel_values"].numpy().transpose(1, 2, 0) * np.asarray(config.std) + np.asarray(config.mean)
        truth_masked = np.ma.masked_where(truth == IGNORE_INDEX, truth)
        prediction_masked = np.ma.masked_where(truth == IGNORE_INDEX, prediction)
        for axis in axes[i]:
            axis.imshow(pixels.clip(0, 1))
            axis.axis("off")
        axes[i, 1].imshow(truth_masked, cmap=palette, vmin=0, vmax=len(current["class_names"])-1, alpha=.55)
        axes[i, 2].imshow(prediction_masked, cmap=palette, vmin=0, vmax=len(current["class_names"])-1, alpha=.55)
        error = np.ma.masked_where((truth == prediction) | (truth == IGNORE_INDEX), np.ones_like(truth))
        axes[i, 3].imshow(error, cmap="autumn", vmin=0, vmax=1, alpha=.8)
        for axis, title in zip(axes[i], (example["sample_id"], "Truth overlay", "Prediction overlay", "Error pixels")):
            axis.set_title(title)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=palette(i), label=name) for i, name in enumerate(current["class_names"])],
               loc="lower center", ncol=min(6, len(current["class_names"])), fontsize=8)
    fig.tight_layout(rect=(0, .10, 1, 1))
    fig.savefig(Path(record["evaluation_dir"]) / f"{split}_overlays.png", dpi=150)
    return fig


def compare_runs(run_dirs):
    """Validation-only comparison; reject differing cohorts, taxonomy or image size."""
    import pandas as pd
    rows, expected = [], None
    for directory in run_dirs:
        record = _read_run(directory)
        if record["status"] != "completed":
            raise ValueError("Only completed runs can be compared")
        signature = (record["task"], record["manifest_hash"], record["split_hash"], record["taxonomy_hash"], record["config"]["image_size"])
        if expected is not None and signature != expected:
            raise ValueError("Comparison needs identical task, split, taxonomy and evaluation resolution")
        expected = signature
        metrics = json.loads((Path(record["evaluation_dir"]) / "val_metrics.json").read_text())
        rows.append({"run_id": record["run_id"], "architecture": record["config"]["architecture"],
                     "checkpoint": record.get("weight_source", record["config"]["checkpoint"]), "seed": record["config"]["seed"],
                     "best_epoch": record["best_epoch"], "val_miou": metrics["miou_foreground"],
                     "val_dice": metrics["dice_foreground"], "parameters": record.get("parameters"),
                     "device": record["environment"]["device"]})
    return pd.DataFrame(rows).sort_values("val_miou", ascending=False) if rows else pd.DataFrame()
