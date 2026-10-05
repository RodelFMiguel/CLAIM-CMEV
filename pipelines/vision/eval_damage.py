"""Evaluation harness for M2 vehicle damage segmentation models.

Evaluates trained damage segmentation checkpoints (SegFormer or DeepLabV3)
on held-out test splits, generating comprehensive reports with confusion matrix,
pixel accuracy, precision, recall, F1 (Dice), and mIoU.

Specification: docs/specs/module-02-damage-segmentation.md
               docs/specs/evaluation_plan.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision.models.segmentation import deeplabv3_resnet50
from transformers import SegformerForSemanticSegmentation

from pipelines.vision.convert_damage import DAMAGE_CLASSES, ID_TO_DAMAGE_CODE
from pipelines.vision.damage_dataset import HitlDamageDataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmev.pipelines.eval_damage")


def compute_confusion_matrix(preds: torch.Tensor, targets: torch.Tensor, num_classes: int = 9) -> torch.Tensor:
    valid_mask = (targets >= 0) & (targets < num_classes)
    targets_valid = targets[valid_mask]
    preds_valid = preds[valid_mask]
    indices = num_classes * targets_valid + preds_valid
    cm = torch.bincount(indices, minlength=num_classes * num_classes)
    return cm.reshape(num_classes, num_classes)


def evaluate_damage_split(
    model_dir: str | Path,
    split_path: str | Path,
    output_report: str | Path | None = None,
    batch_size: int = 8,
    device_str: str | None = None,
) -> dict[str, Any]:
    model_dir = Path(model_dir)
    split_path = Path(split_path)

    if device_str is None:
        device_str = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device_str)

    log.info(f"Loading damage model from {model_dir} to {device}...")
    num_classes = 9  # 0: background, 1..8: damage classes

    if (model_dir / "best_model.pt").exists() and not (model_dir / "config.json").exists():
        is_segformer = False
        m = deeplabv3_resnet50(aux_loss=True)
        m.classifier[4] = torch.nn.Conv2d(256, num_classes, kernel_size=1)
        if m.aux_classifier is not None:
            m.aux_classifier[4] = torch.nn.Conv2d(256, num_classes, kernel_size=1)
        state_dict = torch.load(model_dir / "best_model.pt", map_location=device, weights_only=True)
        m.load_state_dict(state_dict)
        model = m.to(device)
    else:
        is_segformer = True
        model = SegformerForSemanticSegmentation.from_pretrained(model_dir).to(device)
    model.eval()

    dataset = HitlDamageDataset(split_path, image_size=512, is_train=False)
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=2,
        pin_memory=(device.type == "cuda"),
    )

    total_cm = torch.zeros((num_classes, num_classes), dtype=torch.int64, device=device)
    total_loss = 0.0
    num_batches = 0

    log.info(f"Evaluating {len(dataset)} examples on {device}...")
    with torch.no_grad():
        for batch in loader:
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            with torch.amp.autocast(device_type="cuda" if device.type == "cuda" else "cpu", enabled=(device.type == "cuda")):
                if is_segformer:
                    outputs = model(pixel_values=pixel_values)
                    logits = outputs.logits
                    upsampled = F.interpolate(
                        logits,
                        size=labels.shape[-2:],
                        mode="bilinear",
                        align_corners=False,
                    )
                else:
                    outputs = model(pixel_values)
                    upsampled = outputs["out"]

                loss = F.cross_entropy(upsampled, labels)

            total_loss += loss.item()
            num_batches += 1

            preds = upsampled.argmax(dim=1)
            cm = compute_confusion_matrix(preds, labels, num_classes=num_classes)
            total_cm += cm

    mean_loss = total_loss / max(num_batches, 1)
    cm_cpu = total_cm.cpu()
    intersection = cm_cpu.diag().float()
    ground_truth_sum = cm_cpu.sum(dim=1).float()
    prediction_sum = cm_cpu.sum(dim=0).float()
    union = ground_truth_sum + prediction_sum - intersection

    per_class_metrics: dict[str, Any] = {}
    valid_ious: list[float] = []
    valid_precisions: list[float] = []
    valid_recalls: list[float] = []
    valid_f1s: list[float] = []

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

        per_class_metrics[cname] = {
            "class_id": class_id,
            "iou": round(iou, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "support_pixels": gt_pixels,
            "predicted_pixels": pred_pixels,
        }

        if class_id > 0:
            valid_ious.append(iou)
            valid_precisions.append(precision)
            valid_recalls.append(recall)
            valid_f1s.append(f1)

    total_pixels = int(cm_cpu.sum().item())
    correct_pixels = int(intersection.sum().item())
    pixel_acc = correct_pixels / total_pixels if total_pixels > 0 else 0.0

    miou_fg = float(np.mean(valid_ious)) if valid_ious else 0.0
    macro_prec_fg = float(np.mean(valid_precisions)) if valid_precisions else 0.0
    macro_rec_fg = float(np.mean(valid_recalls)) if valid_recalls else 0.0
    macro_f1_fg = float(np.mean(valid_f1s)) if valid_f1s else 0.0

    report = {
        "report_version": "1.0.0",
        "split_evaluated": split_path.name,
        "split_file": str(split_path),
        "model_dir": str(model_dir),
        "total_images": len(dataset),
        "total_pixels_evaluated": total_pixels,
        "test_loss": round(mean_loss, 4),
        "pixel_accuracy": round(pixel_acc, 4),
        "mIoU_foreground": round(miou_fg, 4),
        "macro_precision_foreground": round(macro_prec_fg, 4),
        "macro_recall_foreground": round(macro_rec_fg, 4),
        "macro_f1_foreground": round(macro_f1_fg, 4),
        "per_class_metrics": per_class_metrics,
        "confusion_matrix": cm_cpu.tolist(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Print summary
    table_header = (
        f"\n| {'Class ID':8s} | {'Damage Class':16s} | {'IoU':6s} | {'Precision':9s} | {'Recall':6s} | {'F1 Score':8s} | {'Pixel Support':13s} |\n"
        f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
    )
    table_rows = []
    for cid in range(num_classes):
        cname = ID_TO_DAMAGE_CODE.get(cid, f"class_{cid}")
        m = per_class_metrics[cname]
        table_rows.append(
            f"| {cid:2d} | {cname:16s} | {m['iou']:.4f} | {m['precision']:.4f}    | {m['recall']:.4f} | {m['f1_score']:.4f}   | {m['support_pixels']:,} |"
        )
    table_text = table_header + "\n".join(table_rows)

    summary_text = (
        f"\n==================== Evaluation Summary ====================\n"
        f"Split: {split_path.name} ({len(dataset)} images)\n"
        f"Overall Pixel Accuracy: {pixel_acc:.4f}\n"
        f"Foreground mIoU: {miou_fg:.4f}\n"
        f"Macro Precision (fg): {macro_prec_fg:.4f} | Macro Recall (fg): {macro_rec_fg:.4f} | Macro F1 (fg): {macro_f1_fg:.4f}\n"
        f"============================================================"
    )
    print(table_text)
    print(summary_text)

    if output_report:
        out_p = Path(output_report)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info(f"Saved evaluation report to: {out_p}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate M2 vehicle damage segmentation checkpoint")
    parser.add_argument("--model-dir", type=str, required=True)
    parser.add_argument("--split", type=str, default="data/splits/damage/0.1.0/test.jsonl")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", type=str, default=None)
    args = parser.parse_args()

    evaluate_damage_split(
        model_dir=args.model_dir,
        split_path=args.split,
        output_report=args.output,
        batch_size=args.batch_size,
        device_str=args.device,
    )


if __name__ == "__main__":
    main()
