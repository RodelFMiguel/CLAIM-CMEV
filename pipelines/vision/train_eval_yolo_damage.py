"""YOLOv8m-seg training and semantic evaluation pipeline for M2 vehicle damage segmentation.

Fine-tunes YOLOv8m-seg on HITL damage polygon labels, converts predictions to semantic masks,
and computes confusion matrices, pixel accuracy, precision, recall, F1 score, and mIoU.

Specification: docs/specs/module-02-damage-segmentation.md
               docs/specs/model_training_specification.md section 7.2
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any

import cv2
import numpy as np
import torch
from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.vision.convert_damage import DAMAGE_CLASSES, DAMAGE_CODE_TO_ID, ID_TO_DAMAGE_CODE
from pipelines.vision.damage_dataset import HitlDamageDataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmev.pipelines.train_eval_yolo_damage")


def compute_confusion_matrix(pred: torch.Tensor, gt: torch.Tensor, num_classes: int = 9) -> torch.Tensor:
    """Compute confusion matrix: rows = ground truth, cols = predictions."""
    mask = (gt >= 0) & (gt < num_classes) & (pred >= 0) & (pred < num_classes)
    indices = num_classes * gt[mask] + pred[mask]
    cm = torch.bincount(indices, minlength=num_classes ** 2)
    return cm.reshape(num_classes, num_classes)


def train_yolo_damage(
    data_yaml: Path,
    output_dir: Path,
    epochs: int = 60,
    batch_size: int = 8,
    imgsz: int = 512,
    patience: int = 10,
    seed: int = 20260922,
) -> Path:
    log.info("Loading pretrained yolov8m-seg.pt for M2 damage...")
    model = YOLO("yolov8m-seg.pt")

    output_dir.mkdir(parents=True, exist_ok=True)
    project_dir = output_dir.parent
    exp_name = output_dir.name

    log.info(f"Starting YOLOv8m-seg damage training for {epochs} epochs...")
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
    log.info(f"YOLO damage training finished. Best checkpoint at: {best_weights}")
    return best_weights


def evaluate_yolo_damage_semantic(
    weights_path: Path,
    test_split_path: Path,
    output_report_path: Path,
    imgsz: int = 512,
    conf_thresh: float = 0.25,
) -> dict[str, Any]:
    log.info(f"Evaluating YOLOv8 damage semantic segmentation from {weights_path}...")
    model = YOLO(str(weights_path))

    dataset = HitlDamageDataset(test_split_path, image_size=imgsz, is_train=False)
    num_classes = 9  # 0: background, 1..8: damage classes
    total_cm = torch.zeros((num_classes, num_classes), dtype=torch.int64)

    log.info(f"Evaluating {len(dataset)} test images...")
    for idx in range(len(dataset)):
        sample = dataset[idx]
        gt_mask = sample["labels"].numpy()  # (512, 512), 0..8

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
            masks_np = res.masks.data.cpu().numpy()  # (N, H, W) binary
            classes_np = res.boxes.cls.cpu().numpy().astype(int)  # 0..7
            confs_np = res.boxes.conf.cpu().numpy()

            areas = [m.sum() for m in masks_np]
            # Smaller damages (cracks, scratches) on top of larger ones (dents)
            sort_indices = np.argsort(areas)[::-1]

            for s_idx in sort_indices:
                m_bin = masks_np[s_idx]
                c_id = classes_np[s_idx] + 1  # 0..7 in YOLO -> 1..8 in damage taxonomy

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

    for cid in range(num_classes):
        cname = ID_TO_DAMAGE_CODE.get(cid, f"class_{cid}")
        gt_pix = int(ground_truth_sum[cid].item())
        pred_pix = int(prediction_sum[cid].item())
        inter_pix = int(intersection[cid].item())
        union_pix = int(union[cid].item())

        iou = inter_pix / union_pix if union_pix > 0 else 0.0
        prec = inter_pix / pred_pix if pred_pix > 0 else 0.0
        rec = inter_pix / gt_pix if gt_pix > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0

        per_class_metrics[cname] = {
            "class_id": cid,
            "iou": round(iou, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1_score": round(f1, 4),
            "gt_pixel_support": gt_pix,
            "pred_pixel_count": pred_pix,
        }

        if cid > 0:  # foreground damage classes
            foreground_ious.append(iou)
            foreground_precisions.append(prec)
            foreground_recalls.append(rec)
            foreground_f1s.append(f1)

    total_pixels = int(total_cm.sum().item())
    correct_pixels = int(intersection.sum().item())
    pixel_acc = correct_pixels / total_pixels if total_pixels > 0 else 0.0

    miou_fg = float(np.mean(foreground_ious)) if foreground_ious else 0.0
    macro_prec_fg = float(np.mean(foreground_precisions)) if foreground_precisions else 0.0
    macro_rec_fg = float(np.mean(foreground_recalls)) if foreground_recalls else 0.0
    macro_f1_fg = float(np.mean(foreground_f1s)) if foreground_f1s else 0.0

    report = {
        "report_version": "1.0.0",
        "model_name": "YOLOv8m-seg (M2 Damage)",
        "task": "vehicle_damage_segmentation",
        "split_evaluated": test_split_path.name,
        "total_images": len(dataset),
        "total_pixels_evaluated": total_pixels,
        "pixel_accuracy": round(pixel_acc, 4),
        "mIoU_foreground": round(miou_fg, 4),
        "macro_precision_foreground": round(macro_prec_fg, 4),
        "macro_recall_foreground": round(macro_rec_fg, 4),
        "macro_f1_foreground": round(macro_f1_fg, 4),
        "per_class_metrics": per_class_metrics,
        "confusion_matrix": total_cm.cpu().tolist(),
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
            f"| {cid:2d} | {cname:16s} | {m['iou']:.4f} | {m['precision']:.4f}    | {m['recall']:.4f} | {m['f1_score']:.4f}   | {m['gt_pixel_support']:,} |"
        )
    table_text = table_header + "\n".join(table_rows)

    summary_text = (
        f"\n==================== YOLOv8m-seg M2 Damage Evaluation Summary ====================\n"
        f"Split: {test_split_path.name} ({len(dataset)} images)\n"
        f"Overall Pixel Accuracy: {pixel_acc:.4f}\n"
        f"Foreground mIoU: {miou_fg:.4f}\n"
        f"Macro Precision (fg): {macro_prec_fg:.4f} | Macro Recall (fg): {macro_rec_fg:.4f} | Macro F1 (fg): {macro_f1_fg:.4f}\n"
        f"==================================================================================="
    )
    print(table_text)
    print(summary_text)

    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info(f"Saved YOLO damage evaluation report to: {output_report_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate YOLOv8m-seg on vehicle damages")
    parser.add_argument("--data", type=str, default="data/interim/yolo_damage/dataset.yaml")
    parser.add_argument("--output-dir", type=str, default="artifacts/models/damage/yolov8m-seg")
    parser.add_argument("--test-split", type=str, default="data/splits/damage/0.1.0/test.jsonl")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--skip-train", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    data_yaml = Path(args.data)
    test_split = Path(args.test_split)
    report_path = output_dir / "test_evaluation_report.json"

    if not args.skip_train:
        best_weights = train_yolo_damage(data_yaml=data_yaml, output_dir=output_dir, epochs=args.epochs, batch_size=args.batch_size)
    else:
        best_weights = output_dir / "weights" / "best.pt"

    evaluate_yolo_damage_semantic(weights_path=best_weights, test_split_path=test_split, output_report_path=report_path)


if __name__ == "__main__":
    main()
