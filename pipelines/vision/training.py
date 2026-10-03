"""Manual, offline semantic-segmentation experiments for HITL parts and damage.

No imports of torch/transformers until a training operation needs them. The model
registry here contains *candidates*, never promoted serving models. See the two
notebooks in notebooks/vision for editable recipes and interpretation limits.
"""
from __future__ import annotations

import copy
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
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance

IGNORE_INDEX = 255
# Task -> (artifact folder under artifacts/models, dataset name). damage = HITL damage taxonomy.
TASKS = {"parts": ("parts", "HITL"), "damage": ("damage-hitl", "HITL"), "damage_cardd": ("damage-cardd", "CarDD")}


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
    # "cosine_epoch" is the original schedule (stepped once per epoch, no warmup) and
    # stays the default so saved runs reload unchanged. "poly" and "cosine" step after
    # every optimizer update and support a linear warmup.
    lr_schedule: str = "cosine_epoch"
    warmup_epochs: float = 0.0
    poly_power: float = 1.0
    horizontal_flip: float = 0.5
    brightness: float = 0.15
    contrast: float = 0.15
    saturation: float = 0.0
    # Training-only geometry. (1.0, 1.0) keeps the centred letterbox used for evaluation;
    # e.g. (0.5, 2.0) rescales relative to that fit, then takes a random image_size crop.
    scale_range: tuple = (1.0, 1.0)
    rotation_degrees: float = 0.0
    # Opt-in M2 crop mixture: full-image, random native-detail, target-centred.
    crop_mode: str = "legacy"
    full_image_probability: float = 0.25
    focused_crop_probability: float = 0.5
    crop_scale_range: tuple = (1.0, 1.5)
    crop_focus_class_ids: tuple = ()
    repeat_factor_threshold: float = 0.0  # disabled; training image prevalence only
    max_repeat_factor: float = 3.0
    plateau_patience: int = 3
    plateau_factor: float = 0.5
    plateau_min_lr_ratio: float = 0.01
    plateau_grace_epochs: int = 3
    # Opt-in damage-recall and regularisation controls; defaults keep saved runs unchanged.
    class_weighting: str = "none"  # "sqrt_inverse": CE weights from TRAINING pixel frequency
    class_weight_cap: float = 10.0  # largest weight relative to background (weight 1)
    drop_path_rate: float | None = None  # SegFormer stochastic depth; None keeps the checkpoint value
    classifier_dropout: float | None = None  # SegFormer decoder dropout; None keeps the checkpoint value
    ema_decay: float = 0.0  # 0 disables; e.g. 0.995 validates and saves averaged weights
    mean: tuple = (0.485, 0.456, 0.406)
    std: tuple = (0.229, 0.224, 0.225)
    device: str = "auto"
    workers: int = 0
    seed: int = 42
    amp: bool = True
    artifacts_root: str = "artifacts"
    run_id: str | None = None

    def __post_init__(self):
        if self.task not in TASKS:
            raise ValueError("task must be parts, damage (HITL damage taxonomy) or damage_cardd (CarDD)")
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
        if not (0 <= self.brightness <= 1 and 0 <= self.contrast <= 1 and 0 <= self.saturation <= 1):
            raise ValueError("brightness/contrast/saturation must lie between 0 and 1")
        if self.lr_schedule not in {"cosine_epoch", "cosine", "poly", "plateau"}:
            raise ValueError("lr_schedule must be cosine_epoch, cosine, poly or plateau")
        if self.warmup_epochs < 0 or (self.warmup_epochs and self.lr_schedule == "cosine_epoch"):
            raise ValueError("warmup_epochs must be nonnegative and needs a per-step schedule (cosine, poly or plateau)")
        if self.warmup_epochs >= self.epochs:
            raise ValueError("warmup_epochs must be shorter than training")
        if self.poly_power <= 0:
            raise ValueError("poly_power must be positive")
        if len(self.scale_range) != 2 or not 0 < self.scale_range[0] <= self.scale_range[1]:
            raise ValueError("scale_range must be (low, high) with 0 < low <= high")
        if not 0 <= self.rotation_degrees <= 45:
            raise ValueError("rotation_degrees must lie between 0 and 45")
        if self.crop_mode not in {"legacy", "damage_aware"}:
            raise ValueError("crop_mode must be legacy or damage_aware")
        if not (0 <= self.full_image_probability <= 1 and 0 <= self.focused_crop_probability <= 1
                and self.full_image_probability + self.focused_crop_probability <= 1):
            raise ValueError("Crop probabilities must be nonnegative and sum to at most one")
        if len(self.crop_scale_range) != 2 or not 0 < self.crop_scale_range[0] <= self.crop_scale_range[1]:
            raise ValueError("crop_scale_range must be positive and ordered")
        if any(not isinstance(i, int) or i <= 0 or i >= IGNORE_INDEX for i in self.crop_focus_class_ids):
            raise ValueError("crop_focus_class_ids must contain foreground class IDs")
        if not 0 <= self.repeat_factor_threshold <= 1 or self.max_repeat_factor < 1:
            raise ValueError("Invalid repeat-factor sampling settings")
        if (self.plateau_patience < 0 or self.plateau_grace_epochs < 1
                or not 0 < self.plateau_factor < 1 or not 0 < self.plateau_min_lr_ratio < 1):
            raise ValueError("Invalid plateau scheduler settings")
        if self.lr_schedule == "plateau" and self.patience <= self.plateau_patience + self.plateau_grace_epochs:
            raise ValueError("Early stopping patience must allow a plateau reduction and its grace epochs")
        if self.class_weighting not in {"none", "sqrt_inverse"} or self.class_weight_cap < 1:
            raise ValueError("class_weighting must be none or sqrt_inverse, with class_weight_cap >= 1")
        for name in ("drop_path_rate", "classifier_dropout"):
            value = getattr(self, name)
            if value is not None and not 0 <= value < 1:
                raise ValueError(f"{name} must be None or lie in [0, 1)")
            if value is not None and self.architecture != "segformer":
                raise ValueError(f"{name} is only applied to SegFormer checkpoints")
        if not 0 <= self.ema_decay < 1:
            raise ValueError("ema_decay must lie in [0, 1); 0 disables weight averaging")
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


def random_scale_crop(image, mask, size, scale, mean=(0.485, 0.456, 0.406), rng=random):
    """Rescale relative to the letterbox fit, then cut a random size x size window.

    Larger scales crop part of the image; smaller ones place it at a random offset on a
    mean-coloured canvas. Padding is ignore 255 in the labels, as in ``letterbox``.
    """
    if image.size != mask.size:
        raise ValueError("Image and mask dimensions differ")
    width, height = image.size
    factor = min(size / width, size / height) * scale
    resized = (max(1, round(width * factor)), max(1, round(height * factor)))
    image = image.resize(resized, Image.Resampling.BILINEAR)
    mask = mask.resize(resized, Image.Resampling.NEAREST)
    # Negative offsets paste the image inside the canvas; positive ones crop it.
    left = rng.randint(0, resized[0] - size) if resized[0] > size else -rng.randint(0, size - resized[0])
    top = rng.randint(0, resized[1] - size) if resized[1] > size else -rng.randint(0, size - resized[1])
    canvas = Image.new("RGB", (size, size), tuple(round(x * 255) for x in mean))
    target = Image.new("L", (size, size), IGNORE_INDEX)
    canvas.paste(image, (-left, -top))
    target.paste(mask, (-left, -top))
    return canvas, target


def damage_aware_crop(image, mask, config, rng=random):
    """Mix full photographs with native-detail crops; supervision is training-only.

    Choose a foreground class uniformly among preferred classes present, then a
    pixel of that class. A focused crop includes this anchor before any subsequent
    rotation. No ground-truth crop selection is used during validation/inference.
    """
    if image.size != mask.size:
        raise ValueError("Image and mask dimensions differ")
    draw = rng.random()
    if draw < config.full_image_probability:
        return letterbox(image, mask, config.image_size, config.mean)
    side = max(1, round(config.image_size / rng.uniform(*config.crop_scale_range)))
    width, height = image.size
    anchor = None
    if draw < config.full_image_probability + config.focused_crop_probability:
        labels = np.asarray(mask)
        present = [int(i) for i in np.unique(labels) if i not in (0, IGNORE_INDEX)]
        preferred = [i for i in config.crop_focus_class_ids if i in present]
        if present:
            selected = rng.choice(preferred or present)
            ys, xs = np.nonzero(labels == selected)
            i = rng.randrange(len(xs))
            anchor = (int(xs[i]), int(ys[i]))
    def origin(length, coordinate):
        if length <= side:
            return -rng.randint(0, side - length)
        if coordinate is None:
            return rng.randint(0, length - side)
        return rng.randint(max(0, coordinate - side + 1), min(coordinate, length - side))
    left = origin(width, anchor[0] if anchor else None)
    top = origin(height, anchor[1] if anchor else None)
    fill = tuple(round(x * 255) for x in config.mean)
    pixels, target = Image.new("RGB", (side, side), fill), Image.new("L", (side, side), IGNORE_INDEX)
    pixels.paste(image, (-left, -top))
    target.paste(mask, (-left, -top))
    size = (config.image_size, config.image_size)
    return pixels.resize(size, Image.Resampling.BILINEAR), target.resize(size, Image.Resampling.NEAREST)


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
        low, high = self.config.scale_range
        if self.training and self.config.crop_mode == "damage_aware":
            image, mask = damage_aware_crop(image, mask, self.config)
        elif self.training and (low, high) != (1.0, 1.0):
            image, mask = random_scale_crop(image, mask, self.config.image_size,
                                            random.uniform(low, high), self.config.mean)
        else:
            image, mask = letterbox(image, mask, self.config.image_size, self.config.mean)
        if self.training:
            if self.config.rotation_degrees:
                angle = random.uniform(-self.config.rotation_degrees, self.config.rotation_degrees)
                fill = tuple(round(x * 255) for x in self.config.mean)
                image = image.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=fill)
                mask = mask.rotate(angle, resample=Image.Resampling.NEAREST, fillcolor=IGNORE_INDEX)
            if random.random() < self.config.horizontal_flip:
                image, mask = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            image = ImageEnhance.Brightness(image).enhance(random.uniform(1-self.config.brightness, 1+self.config.brightness))
            image = ImageEnhance.Contrast(image).enhance(random.uniform(1-self.config.contrast, 1+self.config.contrast))
            if self.config.saturation:
                image = ImageEnhance.Color(image).enhance(random.uniform(1-self.config.saturation, 1+self.config.saturation))
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
            if config.drop_path_rate is not None:
                model_config.drop_path_rate = config.drop_path_rate
            if config.classifier_dropout is not None:
                model_config.classifier_dropout_prob = config.classifier_dropout
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


def segmentation_loss(logits, labels, ce_weight=1.0, dice_weight=0.5, class_weights=None):
    """CE plus foreground soft Dice, excluding ignore pixels from both terms.

    ``class_weights`` (one per class) re-weights only the cross-entropy term.
    """
    import torch
    import torch.nn.functional as F
    valid = labels != IGNORE_INDEX
    if not bool(valid.any()):
        return logits.sum() * 0
    logits = logits.float()
    ce = F.cross_entropy(logits, labels, weight=class_weights, ignore_index=IGNORE_INDEX)
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
    binary_tp = int(matrix[1:, 1:].sum())
    binary_fp, binary_fn = int(matrix[0, 1:].sum()), int(matrix[1:, 0].sum())
    binary_union = binary_tp + binary_fp + binary_fn
    return {"miou_foreground": macro("iou"), "dice_foreground": macro("dice"),
            "precision_foreground": macro("precision"), "recall_foreground": macro("recall"),
            "pixel_accuracy": ratio(tp.sum(), matrix.sum()), "valid_pixels": int(matrix.sum()),
            "macro_class_count": int(np.count_nonzero(union[1:])),
            "macro_policy": "background excluded; zero-union classes undefined/excluded; false-positive-only classes included",
            "per_class": per_class, "confusion_matrix": matrix.tolist(),
            "binary_foreground": {"iou": ratio(binary_tp, binary_union),
                "precision": ratio(binary_tp, binary_tp + binary_fp),
                "recall": ratio(binary_tp, binary_tp + binary_fn),
                "dice": ratio(2 * binary_tp, 2 * binary_tp + binary_fp + binary_fn),
                "true_positive": binary_tp, "false_positive": binary_fp, "false_negative": binary_fn,
                "iou_reason": None if binary_union else "absent_in_truth_and_prediction",
                "policy": "Collapse multiclass argmax IDs > 0; ignore 255 truth; no tuned threshold"}}


def lr_factor(step, total_steps, warmup_steps, schedule="poly", power=1.0):
    """Multiplier on the base learning rate at an optimizer step (0-based)."""
    if warmup_steps and step < warmup_steps:
        return (step + 1) / warmup_steps
    progress = min(1.0, (step - warmup_steps) / max(1, total_steps - warmup_steps))
    if schedule == "cosine":
        return 0.5 * (1 + math.cos(math.pi * progress))
    return (1 - progress) ** power


def _worker_seed(worker_id):
    import torch
    worker_seed = torch.initial_seed() % 2**32
    np.random.seed(worker_seed)
    random.seed(worker_seed)


def repeat_sampling_weights(records, num_classes, threshold, max_repeat=3.0):
    """Capped sqrt(threshold / image prevalence), max over each image's classes.

    Length of the epoch stays fixed. Repeated sampling is not independent evidence.
    Refuse non-training records so held-out support cannot influence sampling.
    """
    if not records or any(r.get("split") != "train" for r in records):
        raise ValueError("Repeat sampling requires nonempty training records only")
    presence = []
    for record in records:
        if "pixel_counts" in record:
            row = np.asarray(record["pixel_counts"])
            if len(row) != num_classes:
                raise ValueError("Pixel counts differ from taxonomy")
            presence.append(row > 0)
        else:
            with Image.open(record["mask_path"]) as mask:
                labels = np.asarray(mask)
                presence.append([np.any(labels == i) for i in range(num_classes)])
    presence = np.asarray(presence, dtype=bool)
    presence[:, 0] = False
    frequency = presence.mean(0)
    factors = np.ones(num_classes)
    supported = frequency > 0
    factors[supported] = np.clip(np.sqrt(threshold / frequency[supported]), 1, max_repeat)
    return np.where(presence, factors, 1).max(1).tolist()


def class_loss_weights(records, num_classes, mode="sqrt_inverse", cap=10.0):
    """Cross-entropy weights from TRAINING pixel frequency: sqrt(f_background / f_class).

    Background gets 1; rarer classes get more, capped at ``cap``. Classes absent from
    training keep weight 1. Refuse non-training records so held-out labels never
    influence the loss.
    """
    if mode == "none":
        return None
    if mode != "sqrt_inverse":
        raise ValueError("Unknown class weighting mode")
    if not records or any(r.get("split") != "train" for r in records):
        raise ValueError("Class weights require nonempty training records only")
    totals = np.zeros(num_classes, dtype=np.float64)
    for record in records:
        if "pixel_counts" in record:
            row = np.asarray(record["pixel_counts"], dtype=np.float64)
            if len(row) != num_classes:
                raise ValueError("Pixel counts differ from taxonomy")
        else:
            with Image.open(record["mask_path"]) as mask:
                labels = np.asarray(mask)
            row = np.bincount(labels[labels != IGNORE_INDEX].ravel(), minlength=num_classes)[:num_classes]
        totals += row
    if totals[0] <= 0:
        raise ValueError("Training records contain no background pixels")
    weights = np.ones(num_classes)
    present = totals > 0
    weights[present] = np.clip(np.sqrt(totals[0] / totals[present]), 1, cap)
    weights[0] = 1.0
    return weights.tolist()


def ema_decay_at(update, decay):
    """Ramp the decay up from 0.1 so early averages are not dominated by the initial weights."""
    return min(decay, (1 + update) / (10 + update))


def _update_ema(averaged, model, decay):
    """Exponential moving average of parameters; buffers (e.g. BatchNorm statistics) are copied."""
    import torch
    with torch.no_grad():
        for target, source in zip(averaged.parameters(), model.parameters()):
            target.lerp_(source.detach(), 1 - decay)
        for target, source in zip(averaged.buffers(), model.buffers()):
            target.copy_(source)


def _report_prefix(split, image_size=None, background_offset=0.0):
    prefix = split if image_size is None else f"{split}_size{image_size}"
    return prefix if not background_offset else f"{prefix}_bg{background_offset:g}"


def _predict(logits, background_offset=0.0):
    """Argmax after lowering the background logit by ``background_offset`` (ties keep background)."""
    import torch
    if not background_offset:
        return logits.argmax(1)
    background = logits[:, 0] - background_offset
    best, index = logits[:, 1:].max(1)
    return torch.where(background >= best, torch.zeros_like(index), index + 1)


def _loader(records, config, device, classes, training=False):
    import torch
    sampler = None
    if training and config.repeat_factor_threshold:
        weights = repeat_sampling_weights(records, len(classes), config.repeat_factor_threshold, config.max_repeat_factor)
        sampler = torch.utils.data.WeightedRandomSampler(weights, len(records), replacement=True,
                                                        generator=torch.Generator().manual_seed(config.seed))
    return torch.utils.data.DataLoader(
        SegmentationDataset(records, config, training, len(classes)), batch_size=config.batch_size,
        shuffle=training and sampler is None, sampler=sampler,
        num_workers=0 if device.type == "mps" else config.workers,
        pin_memory=device.type == "cuda", worker_init_fn=_worker_seed,
        generator=torch.Generator().manual_seed(config.seed), drop_last=False)


def _evaluate(model, loader, device, classes, config, class_weights=None, background_offset=0.0):
    import torch
    model.eval()
    matrix = np.zeros((len(classes), len(classes)), dtype=np.int64)
    loss_sum, image_count = 0.0, 0
    started = time.perf_counter()
    with torch.inference_mode():
        for batch in loader:
            pixels, labels = batch["pixel_values"].to(device), batch["labels"].to(device)
            logits = model(pixels)
            loss = segmentation_loss(logits, labels, config.ce_weight, config.dice_weight, class_weights)
            loss_sum += loss.item() * len(labels)
            image_count += len(labels)
            prediction = _predict(logits, background_offset)
            matrix += confusion_counts(labels.cpu().numpy(), prediction.cpu().numpy(), len(classes))
    if not image_count:
        raise ValueError("Evaluation split is empty")
    metrics = metrics_from_confusion(matrix, classes)
    elapsed = time.perf_counter() - started
    metrics.update(loss=loss_sum / image_count, image_count=image_count, elapsed_seconds=elapsed,
                   background_offset=background_offset,
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
    run_dir = root / "models" / TASKS[config.task][0] / run_id
    evaluation_dir = root / "evaluation" / run_id
    if run_dir.exists() or evaluation_dir.exists():
        raise FileExistsError("Run ID already exists; use a new run_id to preserve prior artifacts")
    run_dir.mkdir(parents=True)
    evaluation_dir.mkdir(parents=True)
    record = {"run_id": run_id, "run_dir": str(run_dir), "evaluation_dir": str(evaluation_dir),
              "status": "running", "registry_status": "candidate", "task": config.task,
              "dataset": TASKS[config.task][1], "class_names": classes, "taxonomy_hash": _hash(classes),
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
        use_amp = config.amp and device.type == "cuda"
        scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
        training_loader = _loader(train_records, config, device, classes, training=True)
        weight_list = class_loss_weights(train_records, len(classes), config.class_weighting, config.class_weight_cap)
        class_weights = None if weight_list is None else torch.tensor(weight_list, dtype=torch.float32, device=device)
        if weight_list is not None:
            record["class_weights"] = dict(zip(classes, weight_list))
        averaged = None
        if config.ema_decay:
            averaged = copy.deepcopy(model).eval()
            for parameter in averaged.parameters():
                parameter.requires_grad_(False)
            record["ema"] = {"decay": config.ema_decay, "warmup": "min(decay, (1 + update) / (10 + update))",
                             "policy": "validation, best.pt and last.pt 'model' hold averaged weights; 'raw_model' holds the trained weights"}
        per_step = config.lr_schedule in {"poly", "cosine"}
        updates_per_epoch = math.ceil(len(training_loader) / config.accumulation_steps)
        warmup_updates = round(config.warmup_epochs * updates_per_epoch)
        if per_step:
            total_updates = updates_per_epoch * config.epochs
            scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: lr_factor(
                step, total_updates, warmup_updates, config.lr_schedule, config.poly_power))
            record["schedule"] = {"updates_per_epoch": updates_per_epoch, "total_updates": total_updates,
                                  "warmup_updates": warmup_updates}
        elif config.lr_schedule == "plateau":
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
                optimizer, mode="max", factor=config.plateau_factor, patience=config.plateau_patience,
                threshold=config.min_delta, threshold_mode="abs",
                min_lr=[config.encoder_lr * config.plateau_min_lr_ratio,
                        config.head_lr * config.plateau_min_lr_ratio])
            record["schedule"] = {"monitor": "validation foreground mIoU", "warmup_updates": warmup_updates,
                                  "early_stop_grace_epochs": config.plateau_grace_epochs}
        else:
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=config.epochs)
        validation_loader = _loader(val_records, config, device, classes)
        best, stale = -math.inf, 0
        optimizer_updates, last_lr_drop = 0, -config.plateau_grace_epochs
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
                    loss = segmentation_loss(logits, labels, config.ce_weight, config.dice_weight, class_weights)
                if not bool(torch.isfinite(loss)):
                    raise FloatingPointError("Non-finite training loss")
                scaler.scale(loss / group_size).backward()
                if (step + 1) % config.accumulation_steps == 0 or step + 1 == total_batches:
                    if config.lr_schedule == "plateau" and optimizer_updates < warmup_updates:
                        fraction = (optimizer_updates + 1) / warmup_updates
                        for group, base_lr in zip(optimizer.param_groups, (config.encoder_lr, config.head_lr)):
                            group["lr"] = base_lr * fraction
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer.zero_grad(set_to_none=True)
                    optimizer_updates += 1
                    if averaged is not None:
                        _update_ema(averaged, model, ema_decay_at(optimizer_updates - 1, config.ema_decay))
                    if per_step:
                        scheduler.step()
                train_loss += loss.item() * len(labels)
                seen += len(labels)
                matrix += confusion_counts(labels.cpu().numpy(), logits.detach().argmax(1).cpu().numpy(), len(classes))
            val = _evaluate(averaged if averaged is not None else model, validation_loader, device, classes,
                            config, class_weights)
            score = val["miou_foreground"]
            if score is None:
                raise ValueError("Validation has no foreground union; cannot select a model")
            epoch_record = {"epoch": epoch+1, "train_loss": train_loss / seen,
                            "train_miou_foreground": metrics_from_confusion(matrix, classes)["miou_foreground"],
                            "val_loss": val["loss"], "val_miou_foreground": score,
                            "val_dice_foreground": val["dice_foreground"],
                            "val_binary_iou": val["binary_foreground"]["iou"],
                            "val_binary_recall": val["binary_foreground"]["recall"],
                            "val_binary_precision": val["binary_foreground"]["precision"],
                            "encoder_lr": optimizer.param_groups[0]["lr"], "head_lr": optimizer.param_groups[1]["lr"],
                            "elapsed_seconds": time.perf_counter()-started}
            history.append(epoch_record)
            improved = score > best + config.min_delta
            if improved:
                best, stale = score, 0
                record.update(best_epoch=epoch+1, best_val_miou_foreground=score)
                _json(evaluation_dir / "val_metrics.json", {**val, "split": "val", "epoch": epoch+1})
            else:
                stale += 1
            if config.lr_schedule == "plateau" and optimizer_updates >= warmup_updates:
                before = [group["lr"] for group in optimizer.param_groups]
                scheduler.step(score)
                if any(group["lr"] < old for group, old in zip(optimizer.param_groups, before)):
                    last_lr_drop = epoch
            elif config.lr_schedule == "cosine_epoch":
                scheduler.step()
            epoch_record["next_encoder_lr"] = optimizer.param_groups[0]["lr"]
            epoch_record["next_head_lr"] = optimizer.param_groups[1]["lr"]
            _json(run_dir / "history.json", history)
            with (run_dir / "training_log.jsonl").open("a") as log:
                log.write(json.dumps(epoch_record) + "\n")
            checkpoint = {"model": (averaged if averaged is not None else model).state_dict(),
                          "optimizer": optimizer.state_dict(),
                          "scheduler": scheduler.state_dict(), "scaler": scaler.state_dict(),
                          "epoch": epoch+1, "best_val_miou_foreground": best,
                          "manifest_hash": manifest["manifest_hash"], "split_hash": manifest["split_hash"],
                          "class_names": classes, "config": asdict(config), "torch_rng_state": torch.get_rng_state()}
            if averaged is not None:
                checkpoint["raw_model"] = model.state_dict()
            _save_checkpoint(run_dir / "last.pt", checkpoint)
            if improved:
                _save_checkpoint(run_dir / "best.pt", checkpoint)
            _json(run_dir / "manifest.json", record)
            binary = val["binary_foreground"]
            print(f"epoch {epoch+1}/{config.epochs}: train loss {train_loss/seen:.4f}, val loss {val['loss']:.4f}, "
                  f"val foreground mIoU {score:.4f}, any-damage IoU {binary['iou'] or 0:.4f} "
                  f"(recall {binary['recall'] or 0:.3f}, precision {binary['precision'] or 0:.3f})")
            grace_complete = config.lr_schedule != "plateau" or epoch - last_lr_drop >= config.plateau_grace_epochs
            if stale >= config.patience and grace_complete:
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


def evaluate_run(run_dir, manifest, split="val", device="auto", image_size=None, background_offset=0.0):
    """Explicit held-out evaluation; call test only after all choices are frozen.

    ``background_offset`` must come from ``tune_background_offset`` on validation;
    reports with an offset are written under a separate ``_bg<offset>`` prefix.
    """
    if split not in {"val", "test"}:
        raise ValueError("Evaluation supports val or test; reserved assignment data is separate")
    model, config, record, selected_device = load_run(run_dir, device)
    if image_size is not None:
        config = replace(config, image_size=image_size)
    current = task_manifest(manifest, config.task)
    if any(current[key] != record[key] for key in ("class_names", "manifest_hash", "split_hash")):
        raise ValueError("Evaluation data/taxonomy differs from the frozen run")
    records = [r for r in current["records"] if r["split"] == split]
    _verify_records(records)
    loader = _loader(records, config, selected_device, current["class_names"])
    weights = record.get("class_weights")
    if weights is not None:
        import torch
        weights = torch.tensor([weights[name] for name in current["class_names"]], dtype=torch.float32,
                               device=selected_device)
    metrics = _evaluate(model, loader, selected_device, current["class_names"], config, weights, background_offset)
    metrics.update(split=split, epoch=record["best_epoch"], run_id=record["run_id"],
                   evaluation_image_size=config.image_size, manifest_hash=record["manifest_hash"],
                   split_hash=record["split_hash"],
                   checkpoint_sha256=_file_hash(Path(run_dir) / "best.pt"))
    output = Path(record["evaluation_dir"])
    prefix = _report_prefix(split, image_size, background_offset)
    _json(output / f"{prefix}_metrics.json", metrics)
    import pandas as pd
    pd.DataFrame(metrics["per_class"]).to_csv(output / f"{prefix}_per_class.csv", index=False)
    np.save(output / f"{prefix}_confusion.npy", np.asarray(metrics["confusion_matrix"]))
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
                             ("val_binary_iou", axes[1], "validation any-damage IoU"),
                             ("encoder_lr", axes[2], "encoder"), ("head_lr", axes[2], "head")):
        if all(key in h for h in history):  # older runs lack the any-damage series
            axis.plot(epochs, [h[key] for h in history], label=label)
    for axis, title in zip(axes, ("CE + Dice loss", "Foreground mIoU", "Learning rate")):
        axis.set(title=title, xlabel="Epoch")
        axis.axvline(record["best_epoch"], linestyle="--", color="grey", alpha=.5)
        axis.legend()
        axis.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(Path(record["evaluation_dir"]) / "training_history.png", dpi=160)
    return fig


def plot_confusion(run_dir, split="val", image_size=None, background_offset=0.0):
    import matplotlib.pyplot as plt
    record = _read_run(run_dir)
    prefix = _report_prefix(split, image_size, background_offset)
    metrics = json.loads((Path(record["evaluation_dir"]) / f"{prefix}_metrics.json").read_text())
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
    fig.savefig(Path(record["evaluation_dir"]) / f"{prefix}_confusion.png", dpi=160)
    # Separate per-class plot makes rare-class failure visible alongside the matrix.
    bars, axis = plt.subplots(figsize=(12, 5))
    foreground = metrics["per_class"][1:]
    axis.bar([r["class_name"] for r in foreground], [r["iou"] if r["iou"] is not None else np.nan for r in foreground])
    axis.set(ylabel="IoU", ylim=(0, 1), title=f"{split}: foreground per-class IoU (undefined classes omitted)")
    plt.setp(axis.get_xticklabels(), rotation=60, ha="right")
    bars.tight_layout()
    bars.savefig(Path(record["evaluation_dir"]) / f"{prefix}_per_class_iou.png", dpi=160)
    return fig, bars


def show_predictions(run_dir, manifest, split="val", count=4, device="auto", image_size=None, background_offset=0.0):
    import torch
    import matplotlib.pyplot as plt
    if split not in {"val", "test"}:
        raise ValueError("Prediction preview must use val/test")
    model, config, record, selected_device = load_run(run_dir, device)
    if image_size is not None:
        config = replace(config, image_size=image_size)
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
            prediction = _predict(model(example["pixel_values"][None].to(selected_device)), background_offset)[0].cpu().numpy()
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
    prefix = _report_prefix(split, image_size, background_offset)
    fig.savefig(Path(record["evaluation_dir"]) / f"{prefix}_overlays.png", dpi=150)
    return fig


def tune_background_offset(run_dir, manifest, offsets=None, device="auto", image_size=None,
                           objective="miou_foreground"):
    """Choose a background logit offset on VALIDATION only, in one inference pass.

    Lowering the background score by ``offset`` makes the model call damage more
    readily (higher recall, lower precision); a negative offset does the reverse.
    Returns ``(selected_offset, table)`` and saves both. Apply the selected value to
    the test split unchanged; never tune it on test.
    """
    import torch
    import pandas as pd
    if objective not in {"miou_foreground", "binary_iou"}:
        raise ValueError("objective must be miou_foreground or binary_iou")
    offsets = [round(float(x), 4) for x in (np.arange(-1.0, 3.01, 0.25) if offsets is None else offsets)]
    if 0.0 not in offsets or len(set(offsets)) != len(offsets):
        raise ValueError("Offsets must be distinct and include 0 (the untuned model)")
    model, config, record, selected_device = load_run(run_dir, device)
    if image_size is not None:
        config = replace(config, image_size=image_size)
    current = task_manifest(manifest, config.task)
    if any(current[key] != record[key] for key in ("class_names", "manifest_hash", "split_hash")):
        raise ValueError("Tuning data/taxonomy differs from the frozen run")
    names = current["class_names"]
    records = [r for r in current["records"] if r["split"] == "val"]
    if not records:
        raise ValueError("No validation records")
    _verify_records(records)
    matrices = {offset: np.zeros((len(names), len(names)), dtype=np.int64) for offset in offsets}
    with torch.inference_mode():
        for batch in _loader(records, config, selected_device, names):
            logits = model(batch["pixel_values"].to(selected_device))
            labels = batch["labels"].numpy()
            for offset in offsets:
                matrices[offset] += confusion_counts(labels, _predict(logits, offset).cpu().numpy(), len(names))
    rows = []
    for offset in offsets:
        metrics = metrics_from_confusion(matrices[offset], names)
        binary = metrics["binary_foreground"]
        rows.append({"background_offset": offset, "miou_foreground": metrics["miou_foreground"],
                     "binary_iou": binary["iou"], "binary_recall": binary["recall"],
                     "binary_precision": binary["precision"],
                     **{f"iou_{r['class_name']}": r["iou"] for r in metrics["per_class"][1:]}})
    table = pd.DataFrame(rows)
    ranked = table.assign(distance=table["background_offset"].abs()).sort_values(
        [objective, "distance"], ascending=[False, True])
    selected = float(ranked.iloc[0]["background_offset"])
    _json(Path(record["evaluation_dir"]) / f"{_report_prefix('val', image_size)}_background_offset.json",
          {"run_id": record["run_id"], "checkpoint_sha256": _file_hash(Path(run_dir) / "best.pt"),
           "split": "val", "evaluation_image_size": config.image_size, "objective": objective,
           "selected_offset": selected, "rows": rows,
           "policy": "Selected on validation only; apply unchanged to test. Ties prefer the smallest change."})
    return selected, table


def model_summary(model, depth=2):
    """Parameter counts per module down to ``depth`` levels, marking encoder layers.

    ``model`` is the adapter returned by ``build_model``; its wrapped network is
    summarised, so row names match the printed module tree.
    """
    import pandas as pd
    encoder_ids = {id(p) for p in model.encoder_parameters()}
    rows = []
    for name, module in model.network.named_modules():
        level = name.count(".") + 1 if name else 0
        if not name or level > depth:
            continue
        parameters = list(module.parameters())
        count = sum(p.numel() for p in parameters)
        if not count:
            continue
        rows.append({"module": name, "type": type(module).__name__, "level": level, "parameters": count,
                     "part": "encoder" if all(id(p) in encoder_ids for p in parameters) else "head/decoder"})
    return pd.DataFrame(rows)


def show_targets(manifest, task, split="train", count=6, rare_classes=3, seed=0, focus_classes=None):
    """Audit prepared targets: photograph, label mask and overlay per sample.

    Draws some images containing the rarest classes of the split, then random ones.
    Ignored pixels (255) are magenta, so unlabelled overlap is never mistaken for a
    white car. Use train/val while developing; the test split stays unseen.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    if split not in {"train", "val"}:
        raise ValueError("Audit train or val targets; keep test unseen during development")
    current = task_manifest(manifest, task)
    names = current["class_names"]
    records = [r for r in current["records"] if r["split"] == split]
    if not records:
        raise ValueError(f"No {split} records for {task}")
    rng = random.Random(seed)
    totals = np.sum([r["pixel_counts"] for r in records], axis=0)
    chosen = []
    if focus_classes is not None:
        if any(name not in names[1:] for name in focus_classes):
            raise ValueError("Focus classes must be known foreground labels")
        priority = [names.index(name) for name in focus_classes]
    else:
        priority = [i for i in np.argsort(totals) if i and totals[i]][:rare_classes]
    for class_id in priority:
        candidates = [r for r in records if r["pixel_counts"][class_id] and r not in chosen]
        if candidates and len(chosen) < count:
            chosen.append(rng.choice(candidates))
    remaining = [r for r in records if r not in chosen]
    chosen += rng.sample(remaining, min(count - len(chosen), len(remaining)))
    palette = plt.get_cmap("turbo", len(names))
    colours = ListedColormap([palette(i) for i in range(len(names))] + [(1.0, 0.0, 1.0, 1.0)])
    fig, axes = plt.subplots(len(chosen), 3, figsize=(13, 3.5 * len(chosen)), squeeze=False)
    present = set()
    for row, record in enumerate(chosen):
        with Image.open(record["image_path"]) as image:
            rgb = np.asarray(image.convert("RGB"))
        with Image.open(record["mask_path"]) as image:
            labels = np.asarray(image)
        present.update(int(v) for v in np.unique(labels) if v != IGNORE_INDEX)
        shown = np.where(labels == IGNORE_INDEX, len(names), labels)
        axes[row, 0].imshow(rgb)
        axes[row, 1].imshow(shown, cmap=colours, vmin=0, vmax=len(names), interpolation="nearest")
        axes[row, 2].imshow(rgb)
        axes[row, 2].imshow(np.ma.masked_where(labels == 0, shown), cmap=colours, vmin=0, vmax=len(names),
                            alpha=0.5, interpolation="nearest")
        ignored = float(np.mean(labels == IGNORE_INDEX))
        for column, heading in enumerate(("Photograph", f"Target · {ignored:.1%} ignored", "Target overlay")):
            axes[row, column].set_title(f"{heading} · {str(record['sample_id'])[-6:]}")
            axes[row, column].axis("off")
    handles = [Patch(color=palette(i), label=names[i]) for i in sorted(present)]
    handles.append(Patch(color=(1.0, 0.0, 1.0), label="ignored (255)"))
    fig.legend(handles=handles, loc="lower center", ncol=min(6, len(handles)), fontsize=8)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    return fig


def compare_runs(run_dirs, evaluation_image_size=None):
    """Compare matching validation cohorts/taxonomies on a shared evaluation grid."""
    import pandas as pd
    rows, expected = [], None
    for directory in run_dirs:
        record = _read_run(directory)
        if record["status"] != "completed":
            raise ValueError("Only completed runs can be compared")
        size = evaluation_image_size if evaluation_image_size is not None else record["config"]["image_size"]
        signature = (record["task"], record["manifest_hash"], record["split_hash"], record["taxonomy_hash"], size)
        if expected is not None and signature != expected:
            raise ValueError("Comparison needs identical task, split, taxonomy and evaluation resolution")
        expected = signature
        filename = "val_metrics.json" if evaluation_image_size is None else f"val_size{size}_metrics.json"
        metrics = json.loads((Path(record["evaluation_dir"]) / filename).read_text())
        if evaluation_image_size is not None:
            if any(metrics.get(key) != record[key] for key in ("manifest_hash", "split_hash", "run_id")):
                raise ValueError("Common-resolution evaluation has incompatible provenance")
            if metrics.get("evaluation_image_size") != size or metrics.get("checkpoint_sha256") != record["checkpoint_sha256"]:
                raise ValueError("Common-resolution evaluation has incompatible resolution or checkpoint")
        rows.append({"run_id": record["run_id"], "architecture": record["config"]["architecture"],
                     "checkpoint": record.get("weight_source", record["config"]["checkpoint"]), "seed": record["config"]["seed"],
                     "best_epoch": record["best_epoch"], "val_miou": metrics["miou_foreground"],
                     "val_dice": metrics["dice_foreground"],
                     "binary_iou": metrics.get("binary_foreground", {}).get("iou"),
                     "training_image_size": record["config"]["image_size"], "evaluation_image_size": size,
                     "parameters": record.get("parameters"),
                     "device": record["environment"]["device"]})
    return pd.DataFrame(rows).sort_values("val_miou", ascending=False) if rows else pd.DataFrame()
