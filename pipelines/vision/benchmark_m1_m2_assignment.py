"""M1-to-M2 Cross-Architecture Part Assignment Benchmark.

Fuses predicted M1 vehicle part masks (SegFormer-B3, YOLOv8m-seg) with predicted
M2 vehicle damage masks (SegFormer-B3, DeepLabV3-ResNet50, YOLOv8m-seg) via deterministic
mask containment (src/claim_cmev/vision/damage/assignment.py).

Specification: docs/specs/module-02-damage-segmentation.md
               docs/specs/integration_contracts.md section 3
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

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from torchvision.models.segmentation import deeplabv3_resnet50
from transformers import SegformerForSemanticSegmentation
from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claim_cmev.contracts.common import PART_CODES
from claim_cmev.vision.damage.assignment import assign_component
from claim_cmev.vision.damage.config import AssignmentConfig, load_assignment_config
from pipelines.vision.convert_damage import DAMAGE_CLASSES, ID_TO_DAMAGE_CODE
from pipelines.vision.damage_dataset import HitlDamageDataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmev.pipelines.benchmark_assignment")

PART_CLASSES = {i + 1: code for i, code in enumerate(PART_CODES)}
DAMAGE_MAP = {i: c for i, c in ID_TO_DAMAGE_CODE.items() if i != 0}


class ModelPredictor:
    def __init__(self, device: torch.device):
        self.device = device
        self.m1_segformer = None
        self.m1_yolo = None
        self.m2_segformer = None
        self.m2_deeplabv3 = None
        self.m2_yolo = None

    def load_all(
        self,
        m1_sf_dir: Path,
        m1_yolo_path: Path,
        m2_sf_dir: Path,
        m2_dlv3_dir: Path,
        m2_yolo_path: Path,
    ) -> None:
        log.info(f"Loading M1 SegFormer from {m1_sf_dir}...")
        self.m1_segformer = SegformerForSemanticSegmentation.from_pretrained(m1_sf_dir).to(self.device).eval()

        log.info(f"Loading M1 YOLO from {m1_yolo_path}...")
        self.m1_yolo = YOLO(str(m1_yolo_path))

        log.info(f"Loading M2 SegFormer from {m2_sf_dir}...")
        self.m2_segformer = SegformerForSemanticSegmentation.from_pretrained(m2_sf_dir).to(self.device).eval()

        log.info(f"Loading M2 DeepLabV3 from {m2_dlv3_dir}...")
        m = deeplabv3_resnet50(aux_loss=True)
        m.classifier[4] = torch.nn.Conv2d(256, 9, kernel_size=1)
        if m.aux_classifier is not None:
            m.aux_classifier[4] = torch.nn.Conv2d(256, 9, kernel_size=1)
        st = torch.load(m2_dlv3_dir / "best_model.pt", map_location=self.device, weights_only=True)
        m.load_state_dict(st)
        self.m2_deeplabv3 = m.to(self.device).eval()

        log.info(f"Loading M2 YOLO from {m2_yolo_path}...")
        self.m2_yolo = YOLO(str(m2_yolo_path))

    @torch.no_grad()
    def predict_m1_segformer(self, pixel_tensor: torch.Tensor) -> np.ndarray:
        # pixel_tensor: (1, 3, 512, 512)
        outputs = self.m1_segformer(pixel_values=pixel_tensor.to(self.device))
        logits = outputs.logits
        upsampled = F.interpolate(logits, size=(512, 512), mode="bilinear", align_corners=False)
        return upsampled.argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

    def predict_m1_yolo(self, img_rgb: np.ndarray, imgsz: int = 512, conf_thresh: float = 0.25) -> np.ndarray:
        results = self.m1_yolo.predict(source=img_rgb, imgsz=imgsz, conf=conf_thresh, verbose=False, device=0)
        res = results[0]
        pred_mask = np.zeros((imgsz, imgsz), dtype=np.uint8)
        if res.masks is not None and len(res.masks) > 0:
            masks_np = res.masks.data.cpu().numpy()
            classes_np = res.boxes.cls.cpu().numpy().astype(int)
            areas = [m.sum() for m in masks_np]
            for s_idx in np.argsort(areas)[::-1]:
                m_bin = masks_np[s_idx]
                c_id = classes_np[s_idx] + 1
                if m_bin.shape != (imgsz, imgsz):
                    m_bin = cv2.resize(m_bin.astype(np.float32), (imgsz, imgsz), interpolation=cv2.INTER_NEAREST)
                pred_mask[m_bin > 0.5] = c_id
        return pred_mask

    @torch.no_grad()
    def predict_m2_segformer(self, pixel_tensor: torch.Tensor) -> tuple[np.ndarray, np.ndarray]:
        outputs = self.m2_segformer(pixel_values=pixel_tensor.to(self.device))
        logits = outputs.logits
        upsampled = F.interpolate(logits, size=(512, 512), mode="bilinear", align_corners=False)
        probs = F.softmax(upsampled, dim=1).squeeze(0)  # (9, 512, 512)
        cls_mask = probs.argmax(dim=0).cpu().numpy().astype(np.uint8)
        conf_map = probs.max(dim=0).values.cpu().numpy().astype(np.float32)
        return cls_mask, conf_map

    @torch.no_grad()
    def predict_m2_deeplabv3(self, pixel_tensor: torch.Tensor) -> tuple[np.ndarray, np.ndarray]:
        outputs = self.m2_deeplabv3(pixel_tensor.to(self.device))
        logits = outputs["out"]
        probs = F.softmax(logits, dim=1).squeeze(0)  # (9, 512, 512)
        cls_mask = probs.argmax(dim=0).cpu().numpy().astype(np.uint8)
        conf_map = probs.max(dim=0).values.cpu().numpy().astype(np.float32)
        return cls_mask, conf_map

    def predict_m2_yolo(self, img_rgb: np.ndarray, imgsz: int = 512, conf_thresh: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
        results = self.m2_yolo.predict(source=img_rgb, imgsz=imgsz, conf=conf_thresh, verbose=False, device=0)
        res = results[0]
        pred_mask = np.zeros((imgsz, imgsz), dtype=np.uint8)
        conf_map = np.zeros((imgsz, imgsz), dtype=np.float32)
        if res.masks is not None and len(res.masks) > 0:
            masks_np = res.masks.data.cpu().numpy()
            classes_np = res.boxes.cls.cpu().numpy().astype(int)
            confs_np = res.boxes.conf.cpu().numpy()
            areas = [m.sum() for m in masks_np]
            for s_idx in np.argsort(areas)[::-1]:
                m_bin = masks_np[s_idx]
                c_id = classes_np[s_idx] + 1
                c_conf = confs_np[s_idx]
                if m_bin.shape != (imgsz, imgsz):
                    m_bin = cv2.resize(m_bin.astype(np.float32), (imgsz, imgsz), interpolation=cv2.INTER_NEAREST)
                mask_bool = m_bin > 0.5
                pred_mask[mask_bool] = c_id
                conf_map[mask_bool] = c_conf
        return pred_mask, conf_map


def extract_damage_components(
    damage_mask: np.ndarray,
    confidence: np.ndarray,
    min_confidence: float = 0.50,
    min_pixels: int = 256,
    connectivity: int = 8,
    max_components: int = 50,
) -> list[dict[str, Any]]:
    """Extract connected damage components adhering to M2 specification."""
    confident = confidence >= min_confidence
    present = {int(v) for v in np.unique(damage_mask)} - {0}
    
    candidates: list[dict[str, Any]] = []
    for class_id in sorted(present):
        binary = ((damage_mask == class_id) & confident).astype(np.uint8)
        if not binary.any():
            continue
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=connectivity)
        for label in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[label])
            if area >= min_pixels:
                comp_mask = (labels == label)
                mean_conf = float(confidence[comp_mask].mean())
                candidates.append({
                    "class_id": class_id,
                    "damage_code": DAMAGE_MAP.get(class_id, f"damage_{class_id}"),
                    "mask": comp_mask,
                    "area_pixels": area,
                    "mean_confidence": mean_conf,
                    "bbox": (x, y, x + w, y + h),
                })
                
    # Cap excess components by descending area
    if len(candidates) > max_components:
        candidates = sorted(candidates, key=lambda c: c["area_pixels"], reverse=True)[:max_components]
        
    return candidates


def run_benchmark() -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Running assignment benchmark on device: {device}")

    # Paths
    m1_sf_dir = PROJECT_ROOT / "artifacts/models/parts/0.8.0-b3-compound"
    m1_yolo_path = PROJECT_ROOT / "artifacts/models/parts/yolov8m-seg/weights/best.pt"
    m2_sf_dir = PROJECT_ROOT / "artifacts/models/damage/0.1.0-b3-compound"
    m2_dlv3_dir = PROJECT_ROOT / "artifacts/models/damage/resnet50"
    m2_yolo_path = PROJECT_ROOT / "artifacts/models/damage/yolov8m-seg/weights/best.pt"

    test_split_path = PROJECT_ROOT / "data/splits/damage/0.1.0/test.jsonl"
    dataset = HitlDamageDataset(test_split_path, image_size=512, is_train=False)
    log.info(f"Loaded test split with {len(dataset)} images.")

    predictor = ModelPredictor(device)
    predictor.load_all(m1_sf_dir, m1_yolo_path, m2_sf_dir, m2_dlv3_dir, m2_yolo_path)

    base_cfg = load_assignment_config()
    # Also evaluate fine-grained mode (min_damage_pixels: 64 to capture hairline scratches)
    fine_cfg = base_cfg.with_overrides({
        "config_version": "m2-assignment/0.1.0-fine",
        "regions": {"min_damage_pixels": 64, "min_damage_confidence": 0.40},
    })

    pairings = [
        ("M1_SegFormer_B3", "M2_SegFormer_B3"),
        ("M1_YOLOv8m_seg", "M2_SegFormer_B3"),
        ("M1_SegFormer_B3", "M2_DeepLabV3_ResNet50"),
        ("M1_YOLOv8m_seg", "M2_DeepLabV3_ResNet50"),
        ("M1_YOLOv8m_seg", "M2_YOLOv8m_seg"),
        ("M1_SegFormer_B3", "M2_YOLOv8m_seg"),
    ]

    # Pre-compute all masks for all 132 test images
    log.info("Running inference on all test images across all models...")
    cache: list[dict[str, Any]] = []

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    for idx in range(len(dataset)):
        sample = dataset[idx]
        rec = dataset.records[idx]
        raw_id = rec.get("photo_id") or rec.get("example_id", f"photo_{idx}")
        photo_id = Path(raw_id).stem.replace(" ", "_")
        pixel_tensor = sample["pixel_values"].unsqueeze(0)  # (1, 3, 512, 512)

        # Reconstruct RGB for YOLO
        pixel_val = sample["pixel_values"].permute(1, 2, 0).numpy()
        img_rgb = np.clip((pixel_val * std + mean) * 255.0, 0, 255).astype(np.uint8)

        # M1 Part Masks
        p_sf = predictor.predict_m1_segformer(pixel_tensor)
        p_yolo = predictor.predict_m1_yolo(img_rgb)

        # M2 Damage Masks
        d_sf, c_sf = predictor.predict_m2_segformer(pixel_tensor)
        d_dlv3, c_dlv3 = predictor.predict_m2_deeplabv3(pixel_tensor)
        d_yolo, c_yolo = predictor.predict_m2_yolo(img_rgb)

        cache.append({
            "photo_id": photo_id,
            "m1": {
                "M1_SegFormer_B3": p_sf,
                "M1_YOLOv8m_seg": p_yolo,
            },
            "m2": {
                "M2_SegFormer_B3": (d_sf, c_sf),
                "M2_DeepLabV3_ResNet50": (d_dlv3, c_dlv3),
                "M2_YOLOv8m_seg": (d_yolo, c_yolo),
            },
        })

    log.info("Inference complete. Evaluating assignment pairs under standard and fine configurations...")

    benchmark_results: dict[str, Any] = {
        "benchmark_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_test_images": len(dataset),
        "configurations": {
            "standard": base_cfg.model_dump(),
            "fine": fine_cfg.model_dump(),
        },
        "pairings_summary": {},
    }

    for cfg_mode, current_cfg in [("standard", base_cfg), ("fine", fine_cfg)]:
        min_p = current_cfg.regions.min_damage_pixels
        min_c = current_cfg.regions.min_damage_confidence
        conn = current_cfg.regions.connectivity
        max_c = current_cfg.regions.max_components_per_photo

        for m1_name, m2_name in pairings:
            pair_key = f"{m1_name}__x__{m2_name}__{cfg_mode}"
            log.info(f"Benchmarking pair: {pair_key}...")

            total_regions = 0
            assigned_count = 0
            unresolved_count = 0
            reason_counts: dict[str, int] = {}
            damage_breakdown: dict[str, dict[str, int]] = {}
            part_match_counts: dict[str, int] = {}
            primary_containments: list[float] = []
            bg_containments: list[float] = []
            images_with_damage = 0

            for entry in cache:
                photo_id = entry["photo_id"]
                p_mask = entry["m1"][m1_name]
                d_mask, c_map = entry["m2"][m2_name]

                components = extract_damage_components(
                    damage_mask=d_mask,
                    confidence=c_map,
                    min_confidence=min_c,
                    min_pixels=min_p,
                    connectivity=conn,
                    max_components=max_c,
                )

                if components:
                    images_with_damage += 1

                for comp in components:
                    total_regions += 1
                    dmg_code = comp["damage_code"]
                    if dmg_code not in damage_breakdown:
                        damage_breakdown[dmg_code] = {"total": 0, "assigned": 0, "unresolved": 0}
                    damage_breakdown[dmg_code]["total"] += 1

                    # Pure deterministic assignment decision
                    decision = assign_component(
                        component_mask=comp["mask"],
                        part_mask=p_mask,
                        part_classes=PART_CLASSES,
                        accepted_parts=set(PART_CODES),
                        rule=current_cfg.assignment,
                    )

                    primary_containments.append(decision.primary_containment)
                    bg_containments.append(decision.background_containment)

                    if decision.assignment_status == "assigned":
                        assigned_count += 1
                        damage_breakdown[dmg_code]["assigned"] += 1
                        part = decision.part_code or "unknown"
                        part_match_counts[part] = part_match_counts.get(part, 0) + 1
                    else:
                        unresolved_count += 1
                        damage_breakdown[dmg_code]["unresolved"] += 1
                        reason = decision.part_reason or "unknown"
                        reason_counts[reason] = reason_counts.get(reason, 0) + 1

            assign_rate = round(assigned_count / total_regions, 4) if total_regions > 0 else 0.0
            mean_prim_cont = round(float(np.mean(primary_containments)), 4) if primary_containments else 0.0
            mean_bg_cont = round(float(np.mean(bg_containments)), 4) if bg_containments else 0.0

            benchmark_results["pairings_summary"][pair_key] = {
                "m1_model": m1_name,
                "m2_model": m2_name,
                "config_mode": cfg_mode,
                "images_with_damage": images_with_damage,
                "total_damage_regions": total_regions,
                "assigned_regions": assigned_count,
                "unresolved_regions": unresolved_count,
                "assignment_rate": assign_rate,
                "mean_primary_containment": mean_prim_cont,
                "mean_background_containment": mean_bg_cont,
                "unresolved_reasons": reason_counts,
                "damage_type_breakdown": damage_breakdown,
                "top_assigned_parts": dict(sorted(part_match_counts.items(), key=lambda x: x[1], reverse=True)[:8]),
            }

    out_dir = PROJECT_ROOT / "artifacts/benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "m1_m2_assignment_benchmark.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2)

    # Generate Markdown Report
    out_md = out_dir / "m1_m2_assignment_benchmark.md"
    lines = [
        "# M1-to-M2 Damage-to-Part Assignment Benchmark Report",
        "",
        f"**Date:** {benchmark_results['benchmark_timestamp']}  ",
        f"**Test Images Evaluated:** {benchmark_results['total_test_images']} images  ",
        "",
        "## 1. Cross-Architecture Benchmark Summary (Standard Config: min_damage_pixels=256)",
        "",
        "| Pair ID | M1 Parts Model | M2 Damage Model | Detected Regions | Assigned Regions | Assignment Rate | Mean Containment | Mean BG Containment |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for m1_name, m2_name in pairings:
        k = f"{m1_name}__x__{m2_name}__standard"
        s = benchmark_results["pairings_summary"][k]
        lines.append(
            f"| {m1_name[:8]} x {m2_name[:8]} | {m1_name} | {m2_name} | {s['total_damage_regions']} | {s['assigned_regions']} | **{s['assignment_rate']*100:.1f}%** | {s['mean_primary_containment']:.3f} | {s['mean_background_containment']:.3f} |"
        )

    lines.extend([
        "",
        "## 2. Sensitivity Benchmark (Fine Config: min_damage_pixels=64)",
        "",
        "| Pair ID | M1 Parts Model | M2 Damage Model | Detected Regions | Assigned Regions | Assignment Rate | Mean Containment | Mean BG Containment |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    for m1_name, m2_name in pairings:
        k = f"{m1_name}__x__{m2_name}__fine"
        s = benchmark_results["pairings_summary"][k]
        lines.append(
            f"| {m1_name[:8]} x {m2_name[:8]} | {m1_name} | {m2_name} | {s['total_damage_regions']} | {s['assigned_regions']} | **{s['assignment_rate']*100:.1f}%** | {s['mean_primary_containment']:.3f} | {s['mean_background_containment']:.3f} |"
        )

    lines.extend([
        "",
        "## 3. Unresolved Failure Analysis (Standard Config)",
        "",
        "| Pairing | Ambiguous Between Parts | Mostly Background | Below Containment Threshold | No Part Overlap |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ])

    for m1_name, m2_name in pairings:
        k = f"{m1_name}__x__{m2_name}__standard"
        s = benchmark_results["pairings_summary"][k]
        reasons = s["unresolved_reasons"]
        lines.append(
            f"| {m1_name} x {m2_name} | {reasons.get('ambiguous_between_parts', 0)} | {reasons.get('mostly_background', 0)} | {reasons.get('below_containment_threshold', 0)} | {reasons.get('no_part_overlap', 0)} |"
        )

    out_md.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Assignment benchmark complete! Saved reports to {out_json} and {out_md}")


if __name__ == "__main__":
    run_benchmark()
