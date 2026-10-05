"""Training pipeline for M2 vehicle damage segmentation (SegFormer and DeepLabV3).

Trains SegFormer (e.g. nvidia/mit-b3) or DeepLabV3-ResNet50 on HITL vehicle damage masks
(8 damage classes + background) with compound loss (Weighted CE + Soft Dice).

Specification: docs/specs/module-02-damage-segmentation.md
               docs/specs/model_training_specification.md section 7.2
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from transformers import (
    SegformerConfig,
    SegformerForSemanticSegmentation,
    get_polynomial_decay_schedule_with_warmup,
)
from torchvision.models.segmentation import deeplabv3_resnet50, DeepLabV3_ResNet50_Weights
import yaml

from pipelines.vision.convert_damage import DAMAGE_CLASSES, DAMAGE_CODE_TO_ID, ID_TO_DAMAGE_CODE
from pipelines.vision.damage_dataset import HitlDamageDataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("cmev.pipelines.train_damage")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def compute_class_weights(
    train_split_path: Path,
    num_classes: int = 9,
    background_weight: float = 0.20,
) -> torch.Tensor:
    """Compute inverse square-root frequency weights from train split."""
    counts = np.zeros(num_classes, dtype=np.float64)
    records = [json.loads(line) for line in train_split_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    for rec in records:
        pix_counts = rec.get("class_pixel_counts", {})
        for code, cnt in pix_counts.items():
            if code in DAMAGE_CODE_TO_ID:
                counts[DAMAGE_CODE_TO_ID[code]] += cnt

    # Fallback if pixel counts absent
    total_fg = counts[1:].sum()
    if total_fg == 0:
        return torch.ones(num_classes, dtype=torch.float32)

    counts[0] = max(total_fg * 5.0, 1.0)
    weights = np.zeros(num_classes, dtype=np.float32)
    for c in range(num_classes):
        weights[c] = 1.0 / np.sqrt(max(counts[c], 1.0))

    # Normalize foreground to mean 1.0
    fg_mean = weights[1:].mean()
    if fg_mean > 0:
        weights = weights / fg_mean
    weights[0] = background_weight
    return torch.from_numpy(weights).float()


class SoftDiceLoss(nn.Module):
    def __init__(self, num_classes: int = 9, smooth: float = 1.0, ignore_index: int | None = None):
        super().__init__()
        self.num_classes = num_classes
        self.smooth = smooth
        self.ignore_index = ignore_index

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        probs = F.softmax(logits, dim=1)
        valid_targets = targets.clamp(0, self.num_classes - 1)
        one_hot = F.one_hot(valid_targets, num_classes=self.num_classes).permute(0, 3, 1, 2).float()

        dims = (0, 2, 3)
        intersection = torch.sum(probs * one_hot, dim=dims)
        cardinality = torch.sum(probs + one_hot, dim=dims)
        dice = (2.0 * intersection + self.smooth) / (cardinality + self.smooth)

        if self.ignore_index is not None:
            mask = torch.ones(self.num_classes, dtype=torch.bool, device=logits.device)
            mask[self.ignore_index] = False
            return (1.0 - dice[mask]).mean()
        return (1.0 - dice).mean()


class CompoundLoss(nn.Module):
    def __init__(self, ce_loss: nn.Module, dice_loss: nn.Module, dice_lambda: float = 1.0):
        super().__init__()
        self.ce = ce_loss
        self.dice = dice_loss
        self.dice_lambda = dice_lambda

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        return self.ce(logits, targets) + self.dice_lambda * self.dice(logits, targets)


def compute_confusion_matrix(preds: torch.Tensor, targets: torch.Tensor, num_classes: int = 9) -> torch.Tensor:
    valid_mask = (targets >= 0) & (targets < num_classes)
    targets_valid = targets[valid_mask]
    preds_valid = preds[valid_mask]
    indices = num_classes * targets_valid + preds_valid
    cm = torch.bincount(indices, minlength=num_classes * num_classes)
    return cm.reshape(num_classes, num_classes)


def evaluate(
    model: nn.Module,
    val_loader: DataLoader,
    device: torch.device,
    is_segformer: bool,
    num_classes: int = 9,
    criterion: nn.Module | None = None,
) -> tuple[float, float, dict[str, Any], torch.Tensor]:
    model.eval()
    total_loss = 0.0
    num_batches = 0
    total_cm = torch.zeros((num_classes, num_classes), dtype=torch.int64, device=device)

    with torch.no_grad():
        for batch in val_loader:
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            with torch.amp.autocast(device_type="cuda" if device.type == "cuda" else "cpu", enabled=(device.type == "cuda")):
                if is_segformer:
                    outputs = model(pixel_values=pixel_values)
                    logits = outputs.logits
                    upsampled_logits = F.interpolate(
                        logits,
                        size=labels.shape[-2:],
                        mode="bilinear",
                        align_corners=False,
                    )
                else:
                    outputs = model(pixel_values)
                    upsampled_logits = outputs["out"]

                if criterion is not None:
                    loss = criterion(upsampled_logits, labels)
                else:
                    loss = F.cross_entropy(upsampled_logits, labels)

            total_loss += loss.item()
            num_batches += 1

            preds = upsampled_logits.argmax(dim=1)
            batch_cm = compute_confusion_matrix(preds, labels, num_classes=num_classes)
            total_cm += batch_cm

    mean_loss = total_loss / max(num_batches, 1)
    cm_cpu = total_cm.cpu()
    intersection = cm_cpu.diag().float()
    ground_truth_sum = cm_cpu.sum(dim=1).float()
    prediction_sum = cm_cpu.sum(dim=0).float()
    union = ground_truth_sum + prediction_sum - intersection

    per_class_report: dict[str, Any] = {}
    valid_ious: list[float] = []

    for class_id in range(num_classes):
        cname = ID_TO_DAMAGE_CODE.get(class_id, f"class_{class_id}")
        gt_pixels = int(ground_truth_sum[class_id].item())
        pred_pixels = int(prediction_sum[class_id].item())
        inter_pixels = int(intersection[class_id].item())
        union_pixels = int(union[class_id].item())

        iou = inter_pixels / union_pixels if union_pixels > 0 else 0.0
        precision = inter_pixels / pred_pixels if pred_pixels > 0 else 0.0
        recall = inter_pixels / gt_pixels if gt_pixels > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        per_class_report[cname] = {
            "class_id": class_id,
            "iou": round(iou, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support_pixels": gt_pixels,
            "predicted_pixels": pred_pixels,
        }

        if class_id > 0 and gt_pixels > 0:
            valid_ious.append(iou)

    miou_foreground = float(np.mean(valid_ious)) if valid_ious else 0.0
    return mean_loss, miou_foreground, per_class_report, cm_cpu


def train_damage(
    config_path: str | Path,
    split_dir: str | Path = "data/splits/damage/0.1.0",
    output_dir: str | Path = "artifacts/models/damage/0.1.0",
) -> dict[str, Any]:
    config_path = Path(config_path)
    split_dir = Path(split_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    seed = cfg.get("seed", 20260922)
    set_seed(seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Using device: {device} ({torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU'})")

    train_split_path = split_dir / "train.jsonl"
    val_split_path = split_dir / "val.jsonl"

    image_size = cfg.get("input_size", 512)
    augment_mode = cfg.get("augment_mode", "standard")
    batch_size = cfg.get("batch_size", 4)
    num_classes = cfg.get("num_labels", 9)

    train_dataset = HitlDamageDataset(
        split_path=train_split_path,
        image_size=image_size,
        is_train=True,
        augment_mode=augment_mode,
    )
    val_dataset = HitlDamageDataset(
        split_path=val_split_path,
        image_size=image_size,
        is_train=False,
    )

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=4, pin_memory=True)

    # Loss setup
    loss_type = cfg.get("loss_type", "compound")
    dice_lambda = float(cfg.get("dice_lambda", 1.0))
    bg_weight = float(cfg.get("background_weight", 0.20))
    class_weights = compute_class_weights(train_split_path, num_classes=num_classes, background_weight=bg_weight).to(device)

    ce_loss = nn.CrossEntropyLoss(weight=class_weights)
    if loss_type == "compound":
        dice_loss = SoftDiceLoss(num_classes=num_classes, ignore_index=0)
        criterion = CompoundLoss(ce_loss, dice_loss, dice_lambda=dice_lambda)
    else:
        criterion = ce_loss

    arch = cfg.get("architecture", "nvidia/mit-b3")
    is_segformer = "mit" in arch or "segformer" in arch

    if is_segformer:
        log.info(f"Loading SegFormer architecture: {arch}...")
        seg_config = SegformerConfig.from_pretrained(arch, num_labels=num_classes)
        model = SegformerForSemanticSegmentation.from_pretrained(
            arch,
            config=seg_config,
            ignore_mismatched_sizes=True,
        ).to(device)
        encoder_params = [p for n, p in model.named_parameters() if "segformer" in n]
        head_params = [p for n, p in model.named_parameters() if "segformer" not in n]
    else:
        log.info(f"Loading DeepLabV3-ResNet50 architecture...")
        weights = DeepLabV3_ResNet50_Weights.DEFAULT
        model = deeplabv3_resnet50(weights=weights, aux_loss=True)
        model.classifier[4] = nn.Conv2d(256, num_classes, kernel_size=1)
        model.aux_classifier[4] = nn.Conv2d(256, num_classes, kernel_size=1)
        model = model.to(device)
        backbone_params = [p for n, p in model.named_parameters() if "backbone" in n]
        head_params = [p for n, p in model.named_parameters() if "backbone" not in n]
        encoder_params = backbone_params

    lr_head = float(cfg.get("learning_rate_head", 6.0e-5 if is_segformer else 1.0e-4))
    lr_encoder = float(cfg.get("learning_rate_encoder", 6.0e-6 if is_segformer else 1.0e-5))
    weight_decay = float(cfg.get("weight_decay", 0.01))

    optimizer = torch.optim.AdamW(
        [
            {"params": encoder_params, "lr": lr_encoder},
            {"params": head_params, "lr": lr_head},
        ],
        weight_decay=weight_decay,
    )

    num_epochs = int(cfg.get("num_epochs", 60))
    grad_accum_steps = int(cfg.get("gradient_accumulation_steps", 4))
    warmup_steps = int(cfg.get("warmup_steps", 300))
    total_training_steps = (len(train_loader) // grad_accum_steps) * num_epochs

    scheduler = get_polynomial_decay_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_training_steps,
        power=1.0,
    )
    scaler = torch.amp.GradScaler("cuda", enabled=(device.type == "cuda"))

    patience = int(cfg.get("early_stopping_patience", 10))
    best_miou = 0.0
    best_epoch = 0
    patience_counter = 0
    best_eval_report: dict[str, Any] = {}
    training_log_path = output_dir / "training_log.jsonl"
    start_time = time.time()

    log.info(f"Starting training for {num_epochs} epochs (grad_accum={grad_accum_steps}, patience={patience})...")

    with open(training_log_path, "w", encoding="utf-8") as log_file:
        for epoch in range(1, num_epochs + 1):
            model.train()
            total_train_loss = 0.0
            optimizer.zero_grad()

            for step, batch in enumerate(train_loader):
                pixel_values = batch["pixel_values"].to(device)
                labels = batch["labels"].to(device)

                with torch.amp.autocast("cuda", enabled=(device.type == "cuda")):
                    if is_segformer:
                        outputs = model(pixel_values=pixel_values)
                        logits = outputs.logits
                        upsampled_logits = F.interpolate(
                            logits,
                            size=labels.shape[-2:],
                            mode="bilinear",
                            align_corners=False,
                        )
                        loss = criterion(upsampled_logits, labels)
                    else:
                        outputs = model(pixel_values)
                        loss = criterion(outputs["out"], labels)
                        if "aux" in outputs:
                            loss = loss + 0.5 * criterion(outputs["aux"], labels)

                    loss = loss / grad_accum_steps

                scaler.scale(loss).backward()
                total_train_loss += loss.item() * grad_accum_steps

                if (step + 1) % grad_accum_steps == 0 or (step + 1) == len(train_loader):
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                    scaler.step(optimizer)
                    scaler.update()
                    scheduler.step()
                    optimizer.zero_grad()

            train_loss = total_train_loss / len(train_loader)
            val_loss, val_miou, per_class_report, _ = evaluate(
                model=model,
                val_loader=val_loader,
                device=device,
                is_segformer=is_segformer,
                num_classes=num_classes,
                criterion=criterion,
            )

            current_lr_head = optimizer.param_groups[1]["lr"]
            current_lr_enc = optimizer.param_groups[0]["lr"]

            log_entry = {
                "epoch": epoch,
                "train_loss": round(train_loss, 4),
                "val_loss": round(val_loss, 4),
                "val_miou_foreground": round(val_miou, 4),
                "lr_head": current_lr_head,
                "lr_encoder": current_lr_enc,
            }
            log_file.write(json.dumps(log_entry) + "\n")
            log_file.flush()

            log.info(
                f"Epoch {epoch:02d}/{num_epochs:02d} | Train Loss: {train_loss:.4f} | "
                f"Val Loss: {val_loss:.4f} | Val mIoU (fg): {val_miou:.4f} | LR: head={current_lr_head:.2e}, enc={current_lr_enc:.2e}"
            )

            if val_miou > best_miou:
                best_miou = val_miou
                best_epoch = epoch
                patience_counter = 0
                best_eval_report = per_class_report
                log.info(f"[*] New best validation mIoU: {best_miou:.4f}! Saving checkpoint...")

                if is_segformer:
                    model.save_pretrained(output_dir)
                else:
                    torch.save(model.state_dict(), output_dir / "best_model.pt")
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    log.info(f"Early stopping triggered at epoch {epoch}. Best epoch was {best_epoch} with mIoU {best_miou:.4f}")
                    break

    elapsed = time.time() - start_time
    log.info(f"Training completed in {elapsed:.1f}s. Best Epoch: {best_epoch} with Val mIoU: {best_miou:.4f}")

    # Copy config
    shutil.copy2(config_path, output_dir / "config.yaml")

    manifest = {
        "model_id": cfg.get("model_id", "damage"),
        "model_version": cfg.get("model_version", "damage/0.1.0"),
        "architecture": arch,
        "taxonomy_version": "hitl-damage-1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "best_epoch": best_epoch,
        "best_val_miou_foreground": best_miou,
        "training_time_seconds": round(elapsed, 1),
        "per_class_validation": best_eval_report,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info(f"Artifacts successfully written to: {output_dir}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Train M2 vehicle damage segmentation models")
    parser.add_argument("--config", type=str, required=True, help="Path to config YAML")
    parser.add_argument("--split-dir", type=str, default="data/splits/damage/0.1.0")
    parser.add_argument("--output-dir", type=str, required=True)
    args = parser.parse_args()

    train_damage(config_path=args.config, split_dir=args.split_dir, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
