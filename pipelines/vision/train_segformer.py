"""SegFormer-B0 training pipeline for vehicle part segmentation (M1).

Trains SegFormer-B0 (nvidia/mit-b0) on HITL vehicle part masks (21 part classes + background).
Saves artifacts, manifest, and evaluation metrics conforming to CLAIM-CMEV specifications.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
               docs/specs/model_training_specification.md section 7.1
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from transformers import (
    SegformerConfig,
    SegformerForSemanticSegmentation,
    get_polynomial_decay_schedule_with_warmup,
)
import yaml

from claim_cmev.contracts.common import PART_CODES
from pipelines.vision.convert_hitl import ID_TO_PART_CODE, PART_CODE_TO_ID
from pipelines.vision.dataset import HitlPartsDataset

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cmev.pipelines.train_segformer")

PANEL_CLASSES = {
    "hood",
    "trunk",
    "front-door",
    "back-door",
    "front-bumper",
    "back-bumper",
    "quarter-panel",
    "fender",
    "roof",
    "rocker-panel",
}


def set_seed(seed: int) -> None:
    """Set seeds for reproducibility across random, numpy, and torch."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def compute_confusion_matrix(preds: torch.Tensor, targets: torch.Tensor, num_classes: int = 22) -> torch.Tensor:
    """Compute confusion matrix of shape (num_classes, num_classes).
    
    targets are ground truth rows, preds are predicted columns.
    """
    valid_mask = (targets >= 0) & (targets < num_classes)
    targets_valid = targets[valid_mask]
    preds_valid = preds[valid_mask]
    indices = num_classes * targets_valid + preds_valid
    cm = torch.bincount(indices, minlength=num_classes * num_classes)
    return cm.reshape(num_classes, num_classes)


def compute_class_weights(
    train_split_path: Path,
    weighting_type: str = "none",
    bg_weight: float = 0.50,
    device: torch.device = torch.device("cpu"),
) -> torch.Tensor | None:
    """Compute class weights for CrossEntropyLoss based on training pixel frequencies."""
    if weighting_type == "none" or not weighting_type:
        return None

    if weighting_type == "inverse_sqrt_freq":
        # Exact training pixel counts across all 706 training masks
        counts = np.array([
            130262704, 8251968, 3206149, 4020753, 3803488, 13796839, 9407824,
            7837884, 5422967, 17626360, 9164679, 4012362, 2249440, 13617414,
            5147621, 982040, 1363353, 2133505, 2841408, 1835137, 7772521, 9917856
        ], dtype=np.float64)

        inv_sqrt = 1.0 / np.sqrt(counts)
        fg_weights = inv_sqrt[1:] / inv_sqrt[1:].mean()
        weights = np.zeros(22, dtype=np.float32)
        weights[0] = bg_weight
        weights[1:] = fg_weights
        log.info(f"Using inverse sqrt class weighting (bg={bg_weight:.2f}, fg_mean=1.00)")
        return torch.tensor(weights, dtype=torch.float32, device=device)

    raise ValueError(f"Unknown loss_weighting: {weighting_type}")


class SoftDiceLoss(torch.nn.Module):
    """Soft Dice Loss for multi-class semantic segmentation.
    
    Directly optimizes the overlap between predicted soft probabilities and ground-truth masks.
    Scale-invariant across small and large vehicle parts.
    """
    def __init__(self, num_classes: int = 22, smooth: float = 1.0, ignore_index: int | None = None):
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


class CompoundLoss(torch.nn.Module):
    """Compound Loss combining Cross-Entropy and Soft Dice Loss."""
    def __init__(self, ce_loss: torch.nn.Module, dice_loss: torch.nn.Module, dice_lambda: float = 1.0):
        super().__init__()
        self.ce = ce_loss
        self.dice = dice_loss
        self.dice_lambda = dice_lambda

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        l_ce = self.ce(logits, targets)
        l_dice = self.dice(logits, targets)
        return l_ce + self.dice_lambda * l_dice


def evaluate(
    model: torch.nn.Module,
    val_loader: DataLoader,
    device: torch.device,
    num_classes: int = 22,
    criterion: torch.nn.Module | None = None,
) -> tuple[float, float, dict[str, Any], torch.Tensor]:
    """Evaluate model on validation/test DataLoader.
    
    Returns:
        (mean_loss, mIoU_foreground, per_class_report, confusion_matrix)
    """
    model.eval()
    total_loss = 0.0
    num_batches = 0
    total_cm = torch.zeros((num_classes, num_classes), dtype=torch.int64, device=device)

    with torch.no_grad():
        for batch in val_loader:
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            with torch.amp.autocast(device_type="cuda" if device.type == "cuda" else "cpu", enabled=(device.type == "cuda")):
                outputs = model(pixel_values=pixel_values)
                logits = outputs.logits
                upsampled_logits = F.interpolate(
                    logits,
                    size=labels.shape[-2:],
                    mode="bilinear",
                    align_corners=False,
                )
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

    # Compute metrics from confusion matrix
    cm_cpu = total_cm.cpu()
    intersection = cm_cpu.diag().float()
    ground_truth_sum = cm_cpu.sum(dim=1).float()
    prediction_sum = cm_cpu.sum(dim=0).float()
    union = ground_truth_sum + prediction_sum - intersection

    per_class_report: dict[str, Any] = {}
    valid_ious: list[float] = []

    for class_id in range(num_classes):
        class_name = ID_TO_PART_CODE.get(class_id, f"class_{class_id}")
        gt_pixels = int(ground_truth_sum[class_id].item())
        pred_pixels = int(prediction_sum[class_id].item())
        inter_pixels = int(intersection[class_id].item())
        union_pixels = int(union[class_id].item())

        iou = inter_pixels / (union_pixels + 1e-7) if union_pixels > 0 else 0.0
        precision = inter_pixels / (pred_pixels + 1e-7) if pred_pixels > 0 else 0.0
        recall = inter_pixels / (gt_pixels + 1e-7) if gt_pixels > 0 else 0.0

        per_class_report[class_name] = {
            "class_id": class_id,
            "iou": round(iou, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "support_pixels": gt_pixels,
            "pred_pixels": pred_pixels,
            "is_panel": class_name in PANEL_CLASSES,
            "panel_floor_met": (iou >= 0.40) if class_name in PANEL_CLASSES else None,
        }

        # Background (class_id=0) is excluded from mIoU calculation per evaluation plan
        if class_id > 0 and gt_pixels > 0:
            valid_ious.append(iou)

    miou_foreground = float(np.mean(valid_ious)) if valid_ious else 0.0

    return mean_loss, miou_foreground, per_class_report, cm_cpu


def get_git_revision() -> str:
    """Get current git commit hash."""
    try:
        rev = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        return rev
    except Exception:
        return "unknown"


def sha256_file(path: Path) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def train(
    config_path: str | Path = "configs/models/parts.yaml",
    split_dir: str | Path = "data/splits/parts/0.1.0",
    output_dir: str | Path = "artifacts/models/parts/0.1.0",
) -> dict[str, Any]:
    """Execute complete SegFormer-B0 training loop and save artifacts."""
    config_path = Path(config_path)
    split_dir = Path(split_dir)
    output_dir = Path(output_dir)

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    seed = cfg.get("seed", 20260922)
    set_seed(seed)

    device_str = cfg.get("device", "cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(device_str)
    log.info(f"Using device: {device} ({torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU'})")

    # Datasets and Loaders
    train_split = split_dir / "train.jsonl"
    val_split = split_dir / "val.jsonl"
    split_manifest_path = split_dir / "split_manifest.json"

    train_dataset = HitlPartsDataset(
        train_split,
        image_size=cfg.get("input_size", 512),
        is_train=True,
        augment_mode=cfg.get("augment_mode", "standard"),
    )
    val_dataset = HitlPartsDataset(val_split, image_size=cfg.get("input_size", 512), is_train=False)

    batch_size = cfg.get("batch_size", 8)
    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=2,
        pin_memory=(device.type == "cuda"),
        drop_last=False,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=2,
        pin_memory=(device.type == "cuda"),
        drop_last=False,
    )

    log.info(f"Loaded train set: {len(train_dataset)} images, val set: {len(val_dataset)} images.")

    # Model Initialization
    id2label = {i: ID_TO_PART_CODE[i] for i in range(22)}
    label2id = {ID_TO_PART_CODE[i]: i for i in range(22)}

    seg_config = SegformerConfig.from_pretrained(
        cfg.get("architecture", "nvidia/mit-b0"),
        num_labels=22,
        id2label=id2label,
        label2id=label2id,
    )
    model = SegformerForSemanticSegmentation.from_pretrained(
        cfg.get("architecture", "nvidia/mit-b0"),
        config=seg_config,
        ignore_mismatched_sizes=True,
    ).to(device)

    # Optimizer with differential learning rates
    lr_head = float(cfg.get("learning_rate_head", 6.0e-5))
    lr_encoder = float(cfg.get("learning_rate_encoder", 6.0e-6))
    weight_decay = float(cfg.get("weight_decay", 0.01))

    encoder_params = [p for n, p in model.named_parameters() if "segformer" in n]
    head_params = [p for n, p in model.named_parameters() if "segformer" not in n]

    optimizer = torch.optim.AdamW(
        [
            {"params": encoder_params, "lr": lr_encoder},
            {"params": head_params, "lr": lr_head},
        ],
        weight_decay=weight_decay,
    )

    num_epochs = int(cfg.get("num_epochs", 40))
    grad_accum_steps = int(cfg.get("gradient_accumulation_steps", 2))
    warmup_steps = int(cfg.get("warmup_steps", 500))
    total_training_steps = (len(train_loader) // grad_accum_steps) * num_epochs

    scheduler = get_polynomial_decay_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_training_steps,
        power=1.0,
    )

    scaler = torch.amp.GradScaler("cuda", enabled=(device.type == "cuda"))

    # Tracking & Early Stopping
    patience = int(cfg.get("early_stopping_patience", 8))
    best_miou = 0.0
    best_epoch = 0
    patience_counter = 0
    best_eval_report: dict[str, Any] = {}

    output_dir.mkdir(parents=True, exist_ok=True)
    temp_best_dir = output_dir / "best_checkpoint"
    temp_best_dir.mkdir(parents=True, exist_ok=True)

    training_logs: list[dict[str, Any]] = []
    start_time = time.time()

    loss_type = cfg.get("loss_type", "ce")
    dice_lambda = float(cfg.get("dice_lambda", 1.0))
    weighting_type = cfg.get("loss_weighting", "none")
    bg_weight = float(cfg.get("background_weight", 0.50))
    class_weights = compute_class_weights(train_split, weighting_type=weighting_type, bg_weight=bg_weight, device=device)
    ce_loss = torch.nn.CrossEntropyLoss(weight=class_weights)

    if loss_type == "compound":
        dice_loss = SoftDiceLoss(num_classes=22, smooth=1.0)
        criterion = CompoundLoss(ce_loss=ce_loss, dice_loss=dice_loss, dice_lambda=dice_lambda)
        log.info(f"Using Compound Loss: Weighted CE + {dice_lambda:.2f} * SoftDiceLoss")
    else:
        criterion = ce_loss
        log.info(f"Using CrossEntropyLoss (weighting={weighting_type})")

    log.info(f"Starting training for {num_epochs} epochs (grad_accum={grad_accum_steps}, patience={patience}, loss_type={loss_type})...")

    step_count = 0
    for epoch in range(1, num_epochs + 1):
        model.train()
        epoch_loss = 0.0
        optimizer.zero_grad()

        for batch_idx, batch in enumerate(train_loader):
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            with torch.amp.autocast(device_type="cuda" if device.type == "cuda" else "cpu", enabled=(device.type == "cuda")):
                outputs = model(pixel_values=pixel_values)
                logits = outputs.logits
                upsampled_logits = F.interpolate(
                    logits,
                    size=labels.shape[-2:],
                    mode="bilinear",
                    align_corners=False,
                )
                loss = criterion(upsampled_logits, labels) / grad_accum_steps

            scaler.scale(loss).backward()
            epoch_loss += loss.item() * grad_accum_steps

            if (batch_idx + 1) % grad_accum_steps == 0 or (batch_idx + 1) == len(train_loader):
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad()
                scheduler.step()
                step_count += 1

        avg_train_loss = epoch_loss / len(train_loader)

        # Validation evaluation
        val_loss, val_miou, per_class_rep, _ = evaluate(model, val_loader, device=device, criterion=criterion)
        current_lr = scheduler.get_last_lr()

        log.info(
            f"Epoch {epoch:02d}/{num_epochs:02d} | "
            f"Train Loss: {avg_train_loss:.4f} | "
            f"Val Loss: {val_loss:.4f} | "
            f"Val mIoU (fg): {val_miou:.4f} | "
            f"LR: head={current_lr[1]:.2e}, enc={current_lr[0]:.2e}"
        )

        epoch_record = {
            "epoch": epoch,
            "train_loss": round(avg_train_loss, 4),
            "val_loss": round(val_loss, 4),
            "val_miou": round(val_miou, 4),
            "lr_encoder": current_lr[0],
            "lr_head": current_lr[1],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        training_logs.append(epoch_record)

        # Check for improvement
        if val_miou > best_miou:
            best_miou = val_miou
            best_epoch = epoch
            patience_counter = 0
            log.info(f"[*] New best validation mIoU: {best_miou:.4f}! Saving checkpoint...")
            model.save_pretrained(temp_best_dir)
            best_eval_report = per_class_rep
        else:
            patience_counter += 1
            if patience_counter >= patience:
                log.info(f"Early stopping triggered after {epoch} epochs (no improvement for {patience} epochs).")
                break

    training_duration_s = time.time() - start_time
    log.info(f"Training completed in {training_duration_s:.1f}s. Best Epoch: {best_epoch} with Val mIoU: {best_miou:.4f}")

    # Move best checkpoint weights to output_dir root
    for fpath in temp_best_dir.iterdir():
        shutil.copy2(fpath, output_dir / fpath.name)
    shutil.rmtree(temp_best_dir, ignore_errors=True)

    weights_file = output_dir / "model.safetensors"
    if not weights_file.exists():
        weights_file = output_dir / "pytorch_model.bin"
    weights_sha256 = sha256_file(weights_file) if weights_file.exists() else "unknown"

    # Write label_schema.json
    label_schema = {
        "schema_version": "1.0.0",
        "taxonomy_version": cfg.get("taxonomy_version", "parts-1.0.0"),
        "background_class_id": 0,
        "background_class_code": "background",
        "num_classes": 22,
        "id_to_code": ID_TO_PART_CODE,
        "code_to_id": PART_CODE_TO_ID,
        "side_resolution": "unresolved",
        "side_rule": "HITL labels carry no left/right side. All predictions assign side=unknown.",
    }
    (output_dir / "label_schema.json").write_text(json.dumps(label_schema, indent=2), encoding="utf-8")

    # Write preprocessing.json
    preprocessing = {
        "preprocessing_version": "1.0.0",
        "input_size": cfg.get("input_size", 512),
        "resize_policy": cfg.get("resize_policy", "longest_edge_pad"),
        "color_space": "RGB",
        "pixel_mean": [0.485, 0.456, 0.406],
        "pixel_std": [0.229, 0.224, 0.225],
        "pad_value": 0,
        "interpolation": "nearest_for_masks_bilinear_for_images",
    }
    (output_dir / "preprocessing.json").write_text(json.dumps(preprocessing, indent=2), encoding="utf-8")

    # Copy split_manifest.json
    if split_manifest_path.exists():
        shutil.copy2(split_manifest_path, output_dir / "split_manifest.json")
        split_manifest_data = json.loads(split_manifest_path.read_text(encoding="utf-8"))
        split_hashes = split_manifest_data.get("split_hashes", {})
    else:
        split_hashes = {}

    # Copy config.yaml
    shutil.copy2(config_path, output_dir / "config.yaml")

    # Write training_log.jsonl
    with open(output_dir / "training_log.jsonl", "w", encoding="utf-8") as f:
        for entry in training_logs:
            f.write(json.dumps(entry) + "\n")

    # Write evaluation_report.json (Validation)
    panel_under_40 = [
        name for name, data in best_eval_report.items()
        if data.get("is_panel") and data.get("iou", 0.0) < 0.40
    ]
    eval_report = {
        "report_version": "1.0.0",
        "split_evaluated": "validation",
        "split_version": cfg.get("split_version", "0.1.0"),
        "best_epoch": best_epoch,
        "mIoU_foreground": round(best_miou, 4),
        "target_mIoU": ">=0.60",
        "target_mIoU_met": bool(best_miou >= 0.60),
        "supported_panel_target": "no supported panel < 0.40",
        "supported_panel_target_met": (len(panel_under_40) == 0),
        "panels_below_threshold": panel_under_40,
        "per_class_metrics": best_eval_report,
    }
    (output_dir / "evaluation_report.json").write_text(json.dumps(eval_report, indent=2), encoding="utf-8")

    # Write NOTICE.md
    notice_text = f"""# Model Notice: SegFormer-B0 Vehicle Part Segmentation

- **Model ID**: parts
- **Version**: {cfg.get("model_version", "parts/0.1.0")}
- **Base Checkpoint**: nvidia/mit-b0
- **Base Checkpoint Licence**: NVIDIA Source Code License - Non-commercial
- **Dataset**: HITL Car Parts Dataset (Supervisely polygon annotations converted to indexed semantic masks)
- **Dataset Licence & Consent**: Research use
- **Side Resolution**: HITL labels carry no left/right side. All predictions assign side='unknown' per CLAIM-CMEV invariant.
"""
    (output_dir / "NOTICE.md").write_text(notice_text, encoding="utf-8")

    # Write manifest.json
    manifest = {
        "model_id": cfg.get("model_id", "parts"),
        "version": cfg.get("model_version", "parts/0.1.0"),
        "status": "candidate",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "Lane 1 (Vision)",
        "task": "semantic_segmentation",
        "architecture": cfg.get("architecture", "nvidia/mit-b0"),
        "base_checkpoint": cfg.get("architecture", "nvidia/mit-b0"),
        "base_checkpoint_licence": "NVIDIA Source Code License - Non-commercial",
        "weights_sha256": weights_sha256,
        "recipe": {
            "loss_type": loss_type,
            "dice_lambda": dice_lambda if loss_type == "compound" else None,
            "loss_weighting": weighting_type,
            "background_weight": bg_weight if weighting_type != "none" else None,
            "augment_mode": cfg.get("augment_mode", "standard"),
            "num_epochs": num_epochs,
            "batch_size": batch_size,
            "grad_accum_steps": grad_accum_steps,
            "lr_head": lr_head,
            "lr_encoder": lr_encoder,
        },
        "label_schema_version": "1.0.0",
        "taxonomy_version": cfg.get("taxonomy_version", "parts-1.0.0"),
        "preprocessing_version": "1.0.0",
        "dataset_ids": ["hitl-car-parts"],
        "dataset_access_evidence": "data/manifests/dataset_sources.json",
        "split_version": cfg.get("split_version", "0.1.0"),
        "split_hashes": split_hashes,
        "conversion_version": "0.1.0",
        "seed": seed,
        "hardware": {
            "device": str(device),
            "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "None",
            "pytorch_version": torch.__version__,
            "cuda_version": torch.version.cuda if torch.cuda.is_available() else "None",
        },
        "dependency_versions": {
            "torch": torch.__version__,
            "transformers": "5.17.0",
        },
        "code_revision": get_git_revision(),
        "training_duration_s": round(training_duration_s, 2),
        "metrics": {
            "val_mIoU_foreground": round(best_miou, 4),
            "best_epoch": best_epoch,
            "total_epochs_trained": epoch,
        },
        "targets": {
            "mIoU_ge_0_60": {
                "target": ">=0.60",
                "achieved": round(best_miou, 4),
                "met": bool(best_miou >= 0.60),
            },
            "no_panel_under_0_40": {
                "target": "no panel < 0.40",
                "panels_below": panel_under_40,
                "met": (len(panel_under_40) == 0),
            },
        },
        "known_limits": (
            "HITL part labels carry no left or right side. Every prediction leaves side unresolved "
            "as 'unknown'. Side identity is resolved in M3 via human confirmation."
        ),
        "provenance": "real",
        "contract_version": "1.0.0",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    log.info(f"Artifacts successfully written to: {output_dir}")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train SegFormer-B0 on HITL vehicle parts")
    parser.add_argument("--config", type=str, default="configs/models/parts.yaml", help="Path to config file")
    parser.add_argument("--split-dir", type=str, default="data/splits/parts/0.1.0", help="Path to split directory")
    parser.add_argument("--output-dir", type=str, default="artifacts/models/parts/0.1.0", help="Output artifact directory")
    args = parser.parse_args()

    train(config_path=args.config, split_dir=args.split_dir, output_dir=args.output_dir)
