"""Evaluation script for vehicle part segmentation (M1).

Evaluates a trained SegFormer-B0 model on the held-out test split, computes
overall foreground mIoU, per-class IoU, precision, recall, support counts,
and checks acceptance criteria against CLAIM-CMEV targets.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
               docs/specs/evaluation_plan.md section 4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from transformers import SegformerForSemanticSegmentation

from claim_cmev.contracts.common import PART_CODES
from pipelines.vision.convert_hitl import ID_TO_PART_CODE
from pipelines.vision.dataset import HitlPartsDataset

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cmev.pipelines.eval_segmentation")

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


def compute_confusion_matrix(preds: torch.Tensor, targets: torch.Tensor, num_classes: int = 22) -> torch.Tensor:
    valid_mask = (targets >= 0) & (targets < num_classes)
    targets_valid = targets[valid_mask]
    preds_valid = preds[valid_mask]
    indices = num_classes * targets_valid + preds_valid
    cm = torch.bincount(indices, minlength=num_classes * num_classes)
    return cm.reshape(num_classes, num_classes)


def evaluate_split(
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

    log.info(f"Loading model from {model_dir} to {device}...")
    if (model_dir / "model.pt").exists() and not (model_dir / "config.json").exists():
        import torchvision.models.segmentation as seg_models
        m = seg_models.deeplabv3_resnet50(aux_loss=True)
        m.classifier[4] = torch.nn.Conv2d(256, 22, kernel_size=1)
        if m.aux_classifier is not None:
            m.aux_classifier[4] = torch.nn.Conv2d(256, 22, kernel_size=1)
        state_dict = torch.load(model_dir / "model.pt", map_location=device, weights_only=True)
        m.load_state_dict(state_dict)
        model = m.to(device)
    else:
        model = SegformerForSemanticSegmentation.from_pretrained(model_dir).to(device)
    model.eval()

    dataset = HitlPartsDataset(split_path, image_size=512, is_train=False)
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=2,
        pin_memory=(device.type == "cuda"),
    )

    num_classes = 22
    total_cm = torch.zeros((num_classes, num_classes), dtype=torch.int64, device=device)
    total_loss = 0.0
    num_batches = 0

    log.info(f"Evaluating {len(dataset)} examples on {device}...")
    with torch.no_grad():
        for batch in loader:
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            with torch.amp.autocast(device_type="cuda" if device.type == "cuda" else "cpu", enabled=(device.type == "cuda")):
                if hasattr(model, "config") and "segformer" in model.config.model_type:
                    outputs = model(pixel_values=pixel_values, labels=labels)
                    loss = outputs.loss
                    logits = outputs.logits
                else:
                    outputs = model(pixel_values)
                    logits = outputs["out"] if isinstance(outputs, dict) and "out" in outputs else outputs
                    loss = F.cross_entropy(logits, labels)

            total_loss += loss.item()
            num_batches += 1

            upsampled = F.interpolate(logits, size=labels.shape[-2:], mode="bilinear", align_corners=False)
            preds = upsampled.argmax(dim=1)

            batch_cm = compute_confusion_matrix(preds, labels, num_classes=num_classes)
            total_cm += batch_cm

    mean_loss = total_loss / max(num_batches, 1)
    cm_cpu = total_cm.cpu()
    intersection = cm_cpu.diag().float()
    ground_truth_sum = cm_cpu.sum(dim=1).float()
    prediction_sum = cm_cpu.sum(dim=0).float()
    union = ground_truth_sum + prediction_sum - intersection

    per_class_metrics: dict[str, Any] = {}
    foreground_ious: list[float] = []
    foreground_precisions: list[float] = []
    foreground_recalls: list[float] = []
    foreground_f1s: list[float] = []
    panel_ious: list[float] = []

    for class_id in range(num_classes):
        class_name = ID_TO_PART_CODE.get(class_id, f"class_{class_id}")
        gt_pixels = int(ground_truth_sum[class_id].item())
        pred_pixels = int(prediction_sum[class_id].item())
        inter_pixels = int(intersection[class_id].item())
        union_pixels = int(union[class_id].item())

        iou = inter_pixels / (union_pixels + 1e-7) if union_pixels > 0 else 0.0
        precision = inter_pixels / (pred_pixels + 1e-7) if pred_pixels > 0 else 0.0
        recall = inter_pixels / (gt_pixels + 1e-7) if gt_pixels > 0 else 0.0
        f1 = (2.0 * precision * recall) / (precision + recall + 1e-7) if (precision + recall) > 0 else 0.0

        is_panel = class_name in PANEL_CLASSES
        per_class_metrics[class_name] = {
            "class_id": class_id,
            "iou": round(iou, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "support_pixels": gt_pixels,
            "predicted_pixels": pred_pixels,
            "is_panel": is_panel,
            "panel_floor_met": (iou >= 0.40) if is_panel else None,
        }

        if class_id > 0 and gt_pixels > 0:
            foreground_ious.append(iou)
            foreground_precisions.append(precision)
            foreground_recalls.append(recall)
            foreground_f1s.append(f1)
            if is_panel:
                panel_ious.append(iou)

    miou_fg = float(np.mean(foreground_ious)) if foreground_ious else 0.0
    miou_panels = float(np.mean(panel_ious)) if panel_ious else 0.0
    macro_prec_fg = float(np.mean(foreground_precisions)) if foreground_precisions else 0.0
    macro_rec_fg = float(np.mean(foreground_recalls)) if foreground_recalls else 0.0
    macro_f1_fg = float(np.mean(foreground_f1s)) if foreground_f1s else 0.0

    total_correct = int(intersection.sum().item())
    total_pixels = int(ground_truth_sum.sum().item())
    pixel_acc = total_correct / (total_pixels + 1e-7)

    panels_below_threshold = [
        name for name, data in per_class_metrics.items()
        if data.get("is_panel") and data.get("iou", 0.0) < 0.40
    ]

    report = {
        "report_version": "1.0.0",
        "split_evaluated": split_path.name,
        "split_file": str(split_path),
        "model_dir": str(model_dir),
        "total_images": len(dataset),
        "test_loss": round(mean_loss, 4),
        "pixel_accuracy": round(pixel_acc, 4),
        "mIoU_foreground": round(miou_fg, 4),
        "mIoU_panels": round(miou_panels, 4),
        "macro_precision_foreground": round(macro_prec_fg, 4),
        "macro_recall_foreground": round(macro_rec_fg, 4),
        "macro_f1_foreground": round(macro_f1_fg, 4),
        "target_mIoU": ">=0.60",
        "target_mIoU_met": bool(miou_fg >= 0.60),
        "supported_panel_target": "no supported panel < 0.40",
        "supported_panel_target_met": (len(panels_below_threshold) == 0),
        "panels_below_threshold": panels_below_threshold,
        "per_class_metrics": per_class_metrics,
        "confusion_matrix": cm_cpu.tolist(),
    }

    # Format Markdown Table
    table_header = (
        f"\n| Class ID | Part Name | IoU | Precision | Recall | F1 Score | Pixel Support | Panel Floor (>=0.40) |\n"
        f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
    )
    table_rows = []
    for cname, m in per_class_metrics.items():
        cid = m["class_id"]
        iou_str = f"{m['iou']:.4f}"
        prec_str = f"{m['precision']:.4f}"
        rec_str = f"{m['recall']:.4f}"
        f1_str = f"{m.get('f1_score', 0.0):.4f}"
        sup_str = f"{m['support_pixels']:,}"
        if m["is_panel"]:
            floor_str = "PASS" if m["panel_floor_met"] else "**FAIL**"
        else:
            floor_str = "N/A"
        table_rows.append(f"| {cid:2d} | {cname:16s} | {iou_str} | {prec_str} | {rec_str} | {f1_str} | {sup_str:13s} | {floor_str} |")

    table_text = table_header + "\n".join(table_rows)

    summary_text = (
        f"\n==================== Evaluation Summary ====================\n"
        f"Split: {split_path.name} ({len(dataset)} images)\n"
        f"Overall Pixel Accuracy: {pixel_acc:.4f}\n"
        f"Foreground mIoU: {miou_fg:.4f} (Target: >= 0.60 -> {'MET' if miou_fg >= 0.60 else 'UNMET'})\n"
        f"Supported Panels mIoU: {miou_panels:.4f}\n"
        f"Macro Precision (fg): {macro_prec_fg:.4f} | Macro Recall (fg): {macro_rec_fg:.4f} | Macro F1 (fg): {macro_f1_fg:.4f}\n"
        f"Panels below 0.40 floor: {panels_below_threshold or 'None (All Passed)'}\n"
        f"============================================================"
    )
    print(table_text)
    print(summary_text)

    if output_report:
        output_report = Path(output_report)
        output_report.parent.mkdir(parents=True, exist_ok=True)
        output_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info(f"Saved evaluation report to: {output_report}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate SegFormer-B0 part segmentation")
    parser.add_argument("--model-dir", type=str, default="artifacts/models/parts/0.1.0", help="Model checkpoint directory")
    parser.add_argument("--split", type=str, default="data/splits/parts/0.1.0/test.jsonl", help="Split jsonl file")
    parser.add_argument("--output", type=str, default="artifacts/models/parts/0.1.0/test_evaluation_report.json", help="Output report JSON")
    parser.add_argument("--batch-size", type=int, default=8, help="Batch size")
    parser.add_argument("--device", type=str, default=None, help="Device to use (cuda/cpu)")
    args = parser.parse_args()

    evaluate_split(
        model_dir=args.model_dir,
        split_path=args.split,
        output_report=args.output,
        batch_size=args.batch_size,
        device_str=args.device,
    )
