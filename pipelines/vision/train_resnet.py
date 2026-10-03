"""DeepLabV3-ResNet50 training pipeline for vehicle part segmentation (M1).

Trains DeepLabV3 with a ResNet-50 backbone on HITL vehicle part masks (21 part classes + background).
Conforms to CLAIM-CMEV specifications and artifact manifest contracts.

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
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
import torchvision.models.segmentation as seg_models
from transformers import get_polynomial_decay_schedule_with_warmup
import yaml

from claim_cmev.contracts.common import PART_CODES
from pipelines.vision.convert_hitl import ID_TO_PART_CODE, PART_CODE_TO_ID
from pipelines.vision.dataset import HitlPartsDataset

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cmev.pipelines.train_resnet")

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
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def compute_confusion_matrix(preds: torch.Tensor, targets: torch.Tensor, num_classes: int = 22) -> torch.Tensor:
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
    num_classes: int = 22,
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
                outputs = model(pixel_values)
                logits = outputs["out"]
                loss = F.cross_entropy(logits, labels)

            total_loss += loss.item()
            num_batches += 1

            preds = logits.argmax(dim=1)
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

        if class_id > 0 and gt_pixels > 0:
            valid_ious.append(iou)

    miou_foreground = float(np.mean(valid_ious)) if valid_ious else 0.0
    return mean_loss, miou_foreground, per_class_report, cm_cpu


def get_git_revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def train(
    config_path: str | Path = "configs/models/parts_resnet50.yaml",
    split_dir: str | Path = "data/splits/parts/0.1.0",
    output_dir: str | Path = "artifacts/models/parts/resnet50",
) -> dict[str, Any]:
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

    batch_size = cfg.get("batch_size", 4)
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

    # Instantiate DeepLabV3-ResNet50
    model = seg_models.deeplabv3_resnet50(weights=seg_models.DeepLabV3_ResNet50_Weights.DEFAULT)
    model.classifier[4] = nn.Conv2d(256, 22, kernel_size=1)
    if model.aux_classifier is not None:
        model.aux_classifier[4] = nn.Conv2d(256, 22, kernel_size=1)
    model = model.to(device)

    lr_head = float(cfg.get("learning_rate_head", 1.0e-4))
    lr_encoder = float(cfg.get("learning_rate_encoder", 1.0e-5))
    weight_decay = float(cfg.get("weight_decay", 0.01))

    backbone_params = [p for n, p in model.named_parameters() if "backbone" in n]
    classifier_params = [p for n, p in model.named_parameters() if "backbone" not in n]

    optimizer = torch.optim.AdamW(
        [
            {"params": backbone_params, "lr": lr_encoder},
            {"params": classifier_params, "lr": lr_head},
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
    criterion = nn.CrossEntropyLoss()

    patience = int(cfg.get("early_stopping_patience", 10))
    best_miou = 0.0
    best_epoch = 0
    patience_counter = 0
    best_eval_report: dict[str, Any] = {}

    output_dir.mkdir(parents=True, exist_ok=True)
    temp_best_model_path = output_dir / "best_model.pt"

    training_logs: list[dict[str, Any]] = []
    start_time = time.time()

    log.info(f"Starting DeepLabV3-ResNet50 training for {num_epochs} epochs (grad_accum={grad_accum_steps}, patience={patience})...")

    step_count = 0
    for epoch in range(1, num_epochs + 1):
        model.train()
        epoch_loss = 0.0
        optimizer.zero_grad()

        for batch_idx, batch in enumerate(train_loader):
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            with torch.amp.autocast(device_type="cuda" if device.type == "cuda" else "cpu", enabled=(device.type == "cuda")):
                outputs = model(pixel_values)
                logits = outputs["out"]
                loss = criterion(logits, labels)
                if "aux" in outputs and outputs["aux"] is not None:
                    loss_aux = criterion(outputs["aux"], labels)
                    loss = loss + 0.5 * loss_aux
                loss = loss / grad_accum_steps

            scaler.scale(loss).backward()
            epoch_loss += loss.item() * grad_accum_steps

            if (batch_idx + 1) % grad_accum_steps == 0 or (batch_idx + 1) == len(train_loader):
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad()
                scheduler.step()
                step_count += 1

        avg_train_loss = epoch_loss / len(train_loader)
        val_loss, val_miou, per_class_rep, _ = evaluate(model, val_loader, device=device)
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

        if val_miou > best_miou:
            best_miou = val_miou
            best_epoch = epoch
            patience_counter = 0
            log.info(f"[*] New best validation mIoU: {best_miou:.4f}! Saving checkpoint...")
            torch.save(model.state_dict(), temp_best_model_path)
            torch.save(model, output_dir / "full_model.pt")
            best_eval_report = per_class_rep
        else:
            patience_counter += 1
            if patience_counter >= patience:
                log.info(f"Early stopping triggered after {epoch} epochs (no improvement for {patience} epochs).")
                break

    training_duration_s = time.time() - start_time
    log.info(f"Training completed in {training_duration_s:.1f}s. Best Epoch: {best_epoch} with Val mIoU: {best_miou:.4f}")

    if temp_best_model_path.exists():
        shutil.move(temp_best_model_path, output_dir / "model.pt")

    weights_file = output_dir / "model.pt"
    weights_sha256 = sha256_file(weights_file) if weights_file.exists() else "unknown"

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

    if split_manifest_path.exists():
        shutil.copy2(split_manifest_path, output_dir / "split_manifest.json")
        split_manifest_data = json.loads(split_manifest_path.read_text(encoding="utf-8"))
        split_hashes = split_manifest_data.get("split_hashes", {})
    else:
        split_hashes = {}

    shutil.copy2(config_path, output_dir / "config.yaml")

    with open(output_dir / "training_log.jsonl", "w", encoding="utf-8") as f:
        for entry in training_logs:
            f.write(json.dumps(entry) + "\n")

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

    manifest = {
        "model_id": cfg.get("model_id", "parts"),
        "version": cfg.get("model_version", "parts/resnet50"),
        "status": "candidate",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "Lane 1 (Vision)",
        "task": "semantic_segmentation",
        "architecture": "deeplabv3_resnet50",
        "base_checkpoint": "deeplabv3_resnet50_coco",
        "weights_sha256": weights_sha256,
        "recipe": {
            "loss_type": cfg.get("loss_type", "ce"),
            "loss_weighting": cfg.get("loss_weighting", "none"),
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
            "torchvision": "0.29.0",
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
    parser = argparse.ArgumentParser(description="Train DeepLabV3-ResNet50 on HITL vehicle parts")
    parser.add_argument("--config", type=str, default="configs/models/parts_resnet50.yaml", help="Path to config file")
    parser.add_argument("--split-dir", type=str, default="data/splits/parts/0.1.0", help="Path to split directory")
    parser.add_argument("--output-dir", type=str, default="artifacts/models/parts/resnet50", help="Output artifact directory")
    args = parser.parse_args()

    train(config_path=args.config, split_dir=args.split_dir, output_dir=args.output_dir)
