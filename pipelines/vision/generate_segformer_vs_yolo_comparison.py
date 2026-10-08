"""Generate comprehensive side-by-side visual comparison between SegFormer-B3 and YOLOv8m-seg.

Compares:
1. M2 Damage Segmentation: SegFormer-B3 vs YOLOv8m-seg vs Ground Truth
2. M1 Parts Segmentation: SegFormer-B3 vs YOLOv8m-seg
3. Downstream Part Assignment: SegFormer Pipeline vs YOLO Pipeline
"""
from __future__ import annotations

from pathlib import Path
import sys

import cv2
import matplotlib.pyplot as plt
import numpy as np
import torch
from transformers import SegformerForSemanticSegmentation
from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claim_cmev.contracts.common import PART_CODES
from claim_cmev.vision.damage.assignment import assign_component
from claim_cmev.vision.damage.config import load_assignment_config
from pipelines.vision.damage_dataset import HitlDamageDataset
from pipelines.vision.generate_ambiguous_visualizations import (
    PART_CLASSES,
    PART_COLORS,
    DAMAGE_COLORS,
    colorize_mask,
    extract_damage_components,
)

DAMAGE_NAMES = [
    "background",
    "missing-part",
    "broken-part",
    "scratch",
    "cracked",
    "dent",
    "flaking",
    "paint-chip",
    "corrosion",
]


def generate_comparisons():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = PROJECT_ROOT / "artifacts/benchmarks/visualizations"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading models...")
    m1_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/parts/0.8.0-b3-compound").to(device).eval()
    m1_yolo = YOLO(str(PROJECT_ROOT / "artifacts/models/parts/yolov8m-seg/weights/best.pt"))
    m2_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/damage/0.1.0-b3-compound").to(device).eval()
    m2_yolo = YOLO(str(PROJECT_ROOT / "artifacts/models/damage/yolov8m-seg/weights/best.pt"))

    cfg = load_assignment_config()
    dataset = HitlDamageDataset(PROJECT_ROOT / "data/splits/damage/0.1.0/test.jsonl", image_size=512, is_train=False)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    # Key cases representing distinct scenarios:
    # 1. Car damages 1062.png: Subtle Scratch & Surface Corrosion (SegFormer detected, YOLO missed completely)
    # 2. Car damages 1091.jpg: Panel Boundary Seam Dent (Front Door vs Fender)
    # 3. Car damages 101.png: Complex Collision (Broken Bumper, Missing Plate, Dent)
    selected_cases = [
        ("Car damages 1062.png", "comparison_1_scratch_corrosion_recall"),
        ("Car damages 1091.jpg", "comparison_2_panel_seam_containment"),
        ("Car damages 101.png", "comparison_3_complex_collision_benchmark"),
    ]

    for raw_id, prefix in selected_cases:
        # Find index
        idx = next((i for i, r in enumerate(dataset.records) if (r.get("photo_id") or r.get("example_id")) == raw_id), None)
        if idx is None:
            continue

        sample = dataset[idx]
        clean_id = Path(raw_id).stem.replace(" ", "_")
        gt_dmg = sample["labels"].numpy()

        pixel_val = sample["pixel_values"].permute(1, 2, 0).numpy()
        img_rgb = np.clip((pixel_val * std + mean) * 255.0, 0, 255).astype(np.uint8)
        pixel_tensor = sample["pixel_values"].unsqueeze(0).to(device)

        with torch.no_grad():
            # M1 SegFormer parts
            p_logits = m1_sf(pixel_values=pixel_tensor).logits
            p_mask_sf = torch.nn.functional.interpolate(p_logits, size=(512, 512), mode="bilinear", align_corners=False).argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

            # M2 SegFormer damage
            d_logits = m2_sf(pixel_values=pixel_tensor).logits
            d_upsampled = torch.nn.functional.interpolate(d_logits, size=(512, 512), mode="bilinear", align_corners=False)
            d_probs = torch.nn.functional.softmax(d_upsampled, dim=1).squeeze(0)
            d_mask_sf = d_probs.argmax(dim=0).cpu().numpy().astype(np.uint8)
            c_map_sf = d_probs.max(dim=0).values.cpu().numpy().astype(np.float32)

        # M1 YOLO parts
        yolo_parts_res = m1_yolo.predict(source=img_rgb, imgsz=512, conf=0.25, verbose=False, device=0)[0]
        p_mask_yolo = np.zeros((512, 512), dtype=np.uint8)
        if yolo_parts_res.masks is not None and len(yolo_parts_res.masks) > 0:
            masks_np = yolo_parts_res.masks.data.cpu().numpy()
            classes_np = yolo_parts_res.boxes.cls.cpu().numpy().astype(int)
            areas = [m.sum() for m in masks_np]
            for s_idx in np.argsort(areas)[::-1]:
                m_bin = masks_np[s_idx]
                c_id = classes_np[s_idx] + 1
                if m_bin.shape != (512, 512):
                    m_bin = cv2.resize(m_bin.astype(np.float32), (512, 512), interpolation=cv2.INTER_NEAREST)
                p_mask_yolo[m_bin > 0.5] = c_id

        # M2 YOLO damage
        yolo_dmg_res = m2_yolo.predict(source=img_rgb, imgsz=512, conf=0.25, verbose=False, device=0)[0]
        d_mask_yolo = np.zeros((512, 512), dtype=np.uint8)
        c_map_yolo = np.zeros((512, 512), dtype=np.float32)
        if yolo_dmg_res.masks is not None and len(yolo_dmg_res.masks) > 0:
            masks_np = yolo_dmg_res.masks.data.cpu().numpy()
            classes_np = yolo_dmg_res.boxes.cls.cpu().numpy().astype(int)
            confs_np = yolo_dmg_res.boxes.conf.cpu().numpy()
            areas = [m.sum() for m in masks_np]
            for s_idx in np.argsort(areas)[::-1]:
                m_bin = masks_np[s_idx]
                c_id = classes_np[s_idx] + 1
                if m_bin.shape != (512, 512):
                    m_bin = cv2.resize(m_bin.astype(np.float32), (512, 512), interpolation=cv2.INTER_NEAREST)
                mask_bool = m_bin > 0.5
                d_mask_yolo[mask_bool] = c_id
                c_map_yolo[mask_bool] = confs_np[s_idx]

        # Extract components and decisions
        comps_sf = extract_damage_components(d_mask_sf, c_map_sf, min_confidence=0.50, min_pixels=256)
        comps_yolo = extract_damage_components(d_mask_yolo, c_map_yolo, min_confidence=0.50, min_pixels=256)

        decisions_sf = [assign_component(c["mask"], p_mask_sf, PART_CLASSES, set(PART_CODES), cfg.assignment) for c in comps_sf]
        decisions_yolo = [assign_component(c["mask"], p_mask_yolo, PART_CLASSES, set(PART_CODES), cfg.assignment) for c in comps_yolo]

        # Plot 2x3 grid
        fig, axes = plt.subplots(2, 3, figsize=(19, 12))
        plt.suptitle(
            f"Cross-Architecture Comparison: SegFormer-B3 vs YOLOv8m-seg\n"
            f"Test Case: {clean_id}",
            fontsize=16,
            fontweight="bold",
        )

        # Row 1: Damage Segmentation (GT vs SegFormer vs YOLO)
        # Panel 1: Original + Ground Truth Damage
        gt_colored = colorize_mask(gt_dmg, DAMAGE_COLORS)
        gt_blend = cv2.addWeighted(img_rgb, 0.6, gt_colored, 0.4, 0)
        axes[0, 0].imshow(gt_blend)
        gt_present = [DAMAGE_NAMES[c] for c in np.unique(gt_dmg) if c != 0]
        axes[0, 0].set_title(f"1. Ground Truth Damage\nClasses: {', '.join(gt_present) or 'None'}", fontsize=12, fontweight="bold")
        axes[0, 0].axis("off")

        # Panel 2: M2 SegFormer Damage
        sf_dmg_colored = colorize_mask(d_mask_sf, DAMAGE_COLORS)
        sf_dmg_blend = cv2.addWeighted(img_rgb, 0.6, sf_dmg_colored, 0.4, 0)
        axes[0, 1].imshow(sf_dmg_blend)
        sf_present = [DAMAGE_NAMES[c] for c in np.unique(d_mask_sf) if c != 0]
        sf_pixels = int(np.count_nonzero(d_mask_sf > 0))
        axes[0, 1].set_title(f"2. M2 SegFormer-B3 Damage\nDetected: {', '.join(sf_present) or 'None'} ({sf_pixels} px)", fontsize=12, fontweight="bold", color="darkgreen")
        axes[0, 1].axis("off")

        # Panel 3: M2 YOLOv8 Damage
        yolo_dmg_colored = colorize_mask(d_mask_yolo, DAMAGE_COLORS)
        yolo_dmg_blend = cv2.addWeighted(img_rgb, 0.6, yolo_dmg_colored, 0.4, 0)
        axes[0, 2].imshow(yolo_dmg_blend)
        yolo_present = [DAMAGE_NAMES[c] for c in np.unique(d_mask_yolo) if c != 0]
        yolo_pixels = int(np.count_nonzero(d_mask_yolo > 0))
        axes[0, 2].set_title(f"3. M2 YOLOv8m-seg Damage\nDetected: {', '.join(yolo_present) or 'None'} ({yolo_pixels} px)", fontsize=12, fontweight="bold", color="darkblue")
        axes[0, 2].axis("off")

        # Row 2: Parts Segmentation & Assignment Decision
        # Panel 4: M1 SegFormer Parts
        sf_parts_colored = colorize_mask(p_mask_sf, PART_COLORS)
        sf_parts_blend = cv2.addWeighted(img_rgb, 0.6, sf_parts_colored, 0.4, 0)
        axes[1, 0].imshow(sf_parts_blend)
        axes[1, 0].set_title("4. M1 SegFormer-B3 Parts Overlay\n(Dense semantic panel segmentation)", fontsize=12, fontweight="bold")
        axes[1, 0].axis("off")

        # Panel 5: M1 YOLO Parts
        yolo_parts_colored = colorize_mask(p_mask_yolo, PART_COLORS)
        yolo_parts_blend = cv2.addWeighted(img_rgb, 0.6, yolo_parts_colored, 0.4, 0)
        axes[1, 1].imshow(yolo_parts_blend)
        axes[1, 1].set_title("5. M1 YOLOv8m-seg Parts Overlay\n(Instance polygon panel segmentation)", fontsize=12, fontweight="bold")
        axes[1, 1].axis("off")

        # Panel 6: Quantitative Comparison & Assignment Results Card
        axes[1, 2].axis("off")
        sf_dec_lines = []
        for i, (c, d) in enumerate(zip(comps_sf[:2], decisions_sf[:2]), 1):
            stat = f"ASSIGNED -> {d.part_code}" if d.assignment_status == "assigned" else f"UNRESOLVED ({d.part_reason})"
            top_cand = f"{d.candidates[0].part_code}: {d.primary_containment*100:.1f}%" if d.candidates else "none"
            sf_dec_lines.append(f"  R{i} [{c['damage_code']}]: {stat} ({top_cand})")

        yolo_dec_lines = []
        for i, (c, d) in enumerate(zip(comps_yolo[:2], decisions_yolo[:2]), 1):
            stat = f"ASSIGNED -> {d.part_code}" if d.assignment_status == "assigned" else f"UNRESOLVED ({d.part_reason})"
            top_cand = f"{d.candidates[0].part_code}: {d.primary_containment*100:.1f}%" if d.candidates else "none"
            yolo_dec_lines.append(f"  R{i} [{c['damage_code']}]: {stat} ({top_cand})")

        card_text = (
            "=== PIPELINE DECISION SUMMARY ===\n\n"
            "Pipeline A: Full SegFormer (M1+M2)\n"
            f"  Damage Regions Detected: {len(comps_sf)}\n"
            + ("\n".join(sf_dec_lines) if sf_dec_lines else "  No qualifying damage regions\n")
            + "\n\n"
            "Pipeline B: Full YOLOv8 (M1+M2)\n"
            f"  Damage Regions Detected: {len(comps_yolo)}\n"
            + ("\n".join(yolo_dec_lines) if yolo_dec_lines else "  No qualifying damage regions\n")
            + "\n\n"
            "Architectural Insight:\n"
            "• SegFormer captures fine-grained textural damage\n"
            "  (scratches, chips, flaking) missed by YOLO.\n"
            "• YOLO enforces crisp polygon boundaries on large\n"
            "  panels, but suffers false negatives on subtle damage."
        )
        axes[1, 2].text(0.02, 0.98, card_text, fontsize=10.5, family="monospace", va="top",
                        bbox=dict(boxstyle="round,pad=0.5", facecolor="#f5f5f5", edgecolor="#cccccc"))

        plt.tight_layout()
        save_path = out_dir / f"{prefix}_{clean_id}.png"
        plt.savefig(save_path, dpi=150)
        plt.close()
        print(f"Generated comparison figure: {save_path}")

    print("All comparison figures generated successfully!")


if __name__ == "__main__":
    generate_comparisons()
