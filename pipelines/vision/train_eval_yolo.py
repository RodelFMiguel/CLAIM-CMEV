"""Train and evaluate YOLOv8m-seg on vehicle part segmentation (M1).

Trains YOLOv8m-seg on polygon annotations and evaluates semantic masks against
the CLAIM-CMEV test split, computing mIoU, confusion matrix, accuracy, precision,
recall, and F1 score for direct comparison with SegFormer and ResNet-50.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np
import torch
from torch.utils.data import DataLoader
from ultralytics import YOLO

from claim_cmev.contracts.common import PART_CODES
from pipelines.vision.convert_hitl import ID_TO_PART_CODE
from pipelines.vision.dataset import HitlPartsDataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmev.pipelines.train_eval_yolo")

PANEL_CLASSES = {
    "hood", "trunk", "front-door", "back-door", "front-bumper",
    "back-bumper", "quarter-panel", "fender", "roof", "rocker-panel",
}


def compute_confusion_matrix(preds: torch.Tensor, targets: torch.Tensor, num_classes: int = 22) -> torch.Tensor:
    valid_mask = (targets >= 0) & (targets < num_classes)
    targets_valid = targets[valid_mask]
    preds_valid = preds[valid_mask]
    indices = num_classes * targets_valid + preds_valid
    cm = torch.bincount(indices, minlength=num_classes * num_classes)
    return cm.reshape(num_classes, num_classes)


def train_yolo(
    data_yaml: Path,
    output_dir: Path,
    epochs: int = 60,
    batch_size: int = 8,
    imgsz: int = 512,
    patience: int = 10,
    seed: int = 20260922,
) -> Path:
    log.info(f"Loading pretrained yolov8m-seg.pt...")
    model = YOLO("yolov8m-seg.pt")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    project_dir = output_dir.parent
    exp_name = output_dir.name

    log.info(f"Starting YOLOv8m-seg training for {epochs} epochs...")
    model.train(
        data=str(data_yaml.resolve()),
        epochs=epochs,
        batch=batch_size,
        imgsz=imgsz,
        project=str(project_dir.resolve()),
        name=exp_name,
        exist_ok=True,
        patience=patience,
        seed=seed,
        device=0,
        verbose=True,
        save=True,
    )
    best_weights = output_dir / "weights" / "best.pt"
    if not best_weights.exists():
        best_weights = output_dir / "weights" / "last.pt"
    log.info(f"YOLO training finished. Best checkpoint at: {best_weights}")
    return best_weights


def evaluate_yolo_semantic(
    weights_path: Path,
    test_split_path: Path,
    output_report_path: Path,
    imgsz: int = 512,
    conf_thresh: float = 0.25,
) -> dict[str, Any]:
    log.info(f"Evaluating YOLOv8 semantic segmentation from {weights_path}...")
    model = YOLO(str(weights_path))

    dataset = HitlPartsDataset(test_split_path, image_size=imgsz, is_train=False)
    num_classes = 22
    total_cm = torch.zeros((num_classes, num_classes), dtype=torch.int64)

    log.info(f"Evaluating {len(dataset)} test images...")
    for idx in range(len(dataset)):
        sample = dataset[idx]
        gt_mask = sample["labels"].numpy()  # (512, 512), 0..21

        # Reconstruct uint8 RGB image from dataset pixel values for pixel-perfect mask alignment
        pixel_val = sample["pixel_values"].permute(1, 2, 0).numpy()
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        img_rgb = np.clip((pixel_val * std + mean) * 255.0, 0, 255).astype(np.uint8)

        # Run inference on exact aligned image
        results = model.predict(source=img_rgb, imgsz=imgsz, conf=conf_thresh, verbose=False, device=0)
        res = results[0]

        pred_mask = np.zeros((imgsz, imgsz), dtype=np.uint8)

        if res.masks is not None and len(res.masks) > 0:
            # Sort masks by area descending so smaller parts remain on top
            masks_np = res.masks.data.cpu().numpy()  # (N, H, W) binary
            classes_np = res.boxes.cls.cpu().numpy().astype(int)  # 0..20
            confs_np = res.boxes.conf.cpu().numpy()

            areas = [m.sum() for m in masks_np]
            sort_indices = np.argsort(areas)[::-1]

            for s_idx in sort_indices:
                m_bin = masks_np[s_idx]
                c_id = classes_np[s_idx] + 1  # 0..20 in YOLO -> 1..21 in taxonomy
                
                # Resize mask to (512, 512) if needed
                if m_bin.shape != (imgsz, imgsz):
                    m_bin = cv2.resize(m_bin.astype(np.float32), (imgsz, imgsz), interpolation=cv2.INTER_NEAREST)
                
                pred_mask[m_bin > 0.5] = c_id

        gt_tensor = torch.from_numpy(gt_mask).long()
        pred_tensor = torch.from_numpy(pred_mask).long()
        cm = compute_confusion_matrix(pred_tensor, gt_tensor, num_classes=num_classes)
        total_cm += cm

    # Compute metrics
    intersection = total_cm.diag().float()
    ground_truth_sum = total_cm.sum(dim=1).float()
    prediction_sum = total_cm.sum(dim=0).float()
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

    panels_below = [
        name for name, data in per_class_metrics.items()
        if data.get("is_panel") and data.get("iou", 0.0) < 0.40
    ]

    report = {
        "report_version": "1.0.0",
        "model_name": "YOLOv8m-seg",
        "split_evaluated": test_split_path.name,
        "total_images": len(dataset),
        "pixel_accuracy": round(pixel_acc, 4),
        "mIoU_foreground": round(miou_fg, 4),
        "mIoU_panels": round(miou_panels, 4),
        "macro_precision_foreground": round(macro_prec_fg, 4),
        "macro_recall_foreground": round(macro_rec_fg, 4),
        "macro_f1_foreground": round(macro_f1_fg, 4),
        "target_mIoU": ">=0.60",
        "target_mIoU_met": bool(miou_fg >= 0.60),
        "supported_panel_target": "no supported panel < 0.40",
        "supported_panel_target_met": (len(panels_below) == 0),
        "panels_below_threshold": panels_below,
        "per_class_metrics": per_class_metrics,
        "confusion_matrix": total_cm.tolist(),
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
        f"\n==================== YOLOv8m-seg Evaluation Summary ====================\n"
        f"Split: {test_split_path.name} ({len(dataset)} images)\n"
        f"Overall Pixel Accuracy: {pixel_acc:.4f}\n"
        f"Foreground mIoU: {miou_fg:.4f} (Target: >= 0.60 -> {'MET' if miou_fg >= 0.60 else 'UNMET'})\n"
        f"Supported Panels mIoU: {miou_panels:.4f}\n"
        f"Macro Precision (fg): {macro_prec_fg:.4f} | Macro Recall (fg): {macro_rec_fg:.4f} | Macro F1 (fg): {macro_f1_fg:.4f}\n"
        f"Panels below 0.40 floor: {panels_below or 'None (All Passed)'}\n"
        f"========================================================================="
    )
    print(table_text)
    print(summary_text)

    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info(f"Saved YOLO evaluation report to: {output_report_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate YOLOv8m-seg on vehicle parts")
    parser.add_argument("--data", type=str, default="data/interim/yolo_parts/dataset.yaml")
    parser.add_argument("--output-dir", type=str, default="artifacts/models/parts/yolov8m-seg")
    parser.add_argument("--test-split", type=str, default="data/splits/parts/0.1.0/test.jsonl")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--skip-train", action="store_true", help="Skip training and only evaluate")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    data_yaml = Path(args.data)
    test_split = Path(args.test_split)
    report_path = output_dir / "test_evaluation_report.json"

    if not args.skip_train:
        best_weights = train_yolo(data_yaml=data_yaml, output_dir=output_dir, epochs=args.epochs, batch_size=args.batch_size)
    else:
        best_weights = output_dir / "weights" / "best.pt"

    evaluate_yolo_semantic(weights_path=best_weights, test_split_path=test_split, output_report_path=report_path)


if __name__ == "__main__":
    main()
