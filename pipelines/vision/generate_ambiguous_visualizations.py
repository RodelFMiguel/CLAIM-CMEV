"""Generate visual overlay figures for ambiguous damage-to-part cases.

Saves multi-panel figures for unresolved/ambiguous damage regions, showing:
1. Original photo
2. M1 Part segmentation overlay
3. M2 Damage segmentation overlay
4. Zoomed bounding-box crop with candidate containment percentages
"""
from __future__ import annotations

import json
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
from pipelines.vision.convert_damage import ID_TO_DAMAGE_CODE
from pipelines.vision.convert_hitl import build_palette
from pipelines.vision.damage_dataset import HitlDamageDataset
from pipelines.vision.benchmark_m1_m2_assignment import extract_damage_components

PART_CLASSES = {i + 1: code for i, code in enumerate(PART_CODES)}
DAMAGE_MAP = {i: c for i, c in ID_TO_DAMAGE_CODE.items() if i != 0}

# Palette colors for parts (distinct colors)
PART_COLORS = [
    (0, 0, 0),        # 0: background
    (233, 83, 83),    # 1: windshield
    (150, 60, 61),    # 2: back-windshield
    (144, 55, 101),   # 3: front-window
    (145, 48, 33),    # 4: back-window
    (254, 47, 192),   # 5: front-door
    (154, 135, 207),  # 6: back-door
    (64, 153, 61),    # 7: front-wheel
    (130, 6, 219),    # 8: back-wheel
    (74, 247, 120),   # 9: front-bumper
    (124, 147, 218),  # 10: back-bumper
    (50, 6, 152),     # 11: headlight
    (46, 127, 98),    # 12: tail-light
    (67, 85, 203),    # 13: hood
    (229, 248, 58),   # 14: trunk
    (144, 208, 146),  # 15: licence-plate
    (188, 87, 78),    # 16: mirror
    (135, 219, 0),    # 17: roof
    (230, 45, 48),    # 18: grille
    (193, 151, 68),   # 19: rocker-panel
    (92, 117, 41),    # 20: quarter-panel
    (213, 11, 180),   # 21: fender
]

DAMAGE_COLORS = [
    (0, 0, 0),        # 0: background
    (255, 0, 0),      # 1: missing-part (Red)
    (255, 128, 0),    # 2: broken-part (Orange)
    (255, 255, 0),    # 3: scratch (Yellow)
    (128, 0, 255),    # 4: cracked (Purple)
    (0, 255, 255),    # 5: dent (Cyan)
    (0, 255, 0),      # 6: flaking (Green)
    (255, 0, 255),    # 7: paint-chip (Magenta)
    (139, 69, 19),    # 8: corrosion (Brown)
]


def colorize_mask(mask: np.ndarray, color_list: list[tuple[int, int, int]]) -> np.ndarray:
    rgb = np.zeros((*mask.shape, 3), dtype=np.uint8)
    for cid, col in enumerate(color_list):
        if cid < len(color_list):
            rgb[mask == cid] = col
    return rgb


def generate_visualizations(num_cases: int = 4) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = PROJECT_ROOT / "artifacts/benchmarks/visualizations"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading models for visualization...")
    m1_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/parts/0.8.0-b3-compound").to(device).eval()
    m1_yolo = YOLO(str(PROJECT_ROOT / "artifacts/models/parts/yolov8m-seg/weights/best.pt"))
    m2_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/damage/0.1.0-b3-compound").to(device).eval()

    cfg = load_assignment_config()
    dataset = HitlDamageDataset(PROJECT_ROOT / "data/splits/damage/0.1.0/test.jsonl", image_size=512, is_train=False)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    generated = 0
    print(f"Scanning test set for ambiguous cases...")

    for idx in range(len(dataset)):
        if generated >= num_cases:
            break

        sample = dataset[idx]
        rec = dataset.records[idx]
        raw_id = rec.get("photo_id") or rec.get("example_id", f"photo_{idx}")
        clean_id = Path(raw_id).stem.replace(" ", "_")

        # RGB Image
        pixel_val = sample["pixel_values"].permute(1, 2, 0).numpy()
        img_rgb = np.clip((pixel_val * std + mean) * 255.0, 0, 255).astype(np.uint8)
        pixel_tensor = sample["pixel_values"].unsqueeze(0).to(device)

        with torch.no_grad():
            # M1 SegFormer parts
            p_logits = m1_sf(pixel_values=pixel_tensor).logits
            p_upsampled = torch.nn.functional.interpolate(p_logits, size=(512, 512), mode="bilinear", align_corners=False)
            p_mask_sf = p_upsampled.argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

            # M2 SegFormer damage
            d_logits = m2_sf(pixel_values=pixel_tensor).logits
            d_upsampled = torch.nn.functional.interpolate(d_logits, size=(512, 512), mode="bilinear", align_corners=False)
            d_probs = torch.nn.functional.softmax(d_upsampled, dim=1).squeeze(0)
            d_mask_sf = d_probs.argmax(dim=0).cpu().numpy().astype(np.uint8)
            c_map_sf = d_probs.max(dim=0).values.cpu().numpy().astype(np.float32)

        # M1 YOLO parts for side-by-side comparison
        yolo_res = m1_yolo.predict(source=img_rgb, imgsz=512, conf=0.25, verbose=False, device=0)[0]
        p_mask_yolo = np.zeros((512, 512), dtype=np.uint8)
        if yolo_res.masks is not None and len(yolo_res.masks) > 0:
            masks_np = yolo_res.masks.data.cpu().numpy()
            classes_np = yolo_res.boxes.cls.cpu().numpy().astype(int)
            areas = [m.sum() for m in masks_np]
            for s_idx in np.argsort(areas)[::-1]:
                m_bin = masks_np[s_idx]
                c_id = classes_np[s_idx] + 1
                if m_bin.shape != (512, 512):
                    m_bin = cv2.resize(m_bin.astype(np.float32), (512, 512), interpolation=cv2.INTER_NEAREST)
                p_mask_yolo[m_bin > 0.5] = c_id

        # Extract damage components
        comps = extract_damage_components(d_mask_sf, c_map_sf, min_confidence=0.50, min_pixels=256)

        for comp_idx, comp in enumerate(comps):
            dec_sf = assign_component(comp["mask"], p_mask_sf, PART_CLASSES, set(PART_CODES), cfg.assignment)
            dec_yolo = assign_component(comp["mask"], p_mask_yolo, PART_CLASSES, set(PART_CODES), cfg.assignment)

            # Find cases where at least one model resulted in ambiguous_between_parts
            if dec_sf.part_reason == "ambiguous_between_parts" or dec_yolo.part_reason == "ambiguous_between_parts":
                fig, axes = plt.subplots(2, 3, figsize=(18, 12))
                plt.suptitle(
                    f"Ambiguous Damage Case #{generated + 1}: {clean_id} (Region {comp_idx+1})\n"
                    f"Damage Type: {comp['damage_code']} ({comp['area_pixels']} px)",
                    fontsize=16,
                    fontweight="bold",
                )

                # 1. Original Image with Bounding Box
                img_with_box = img_rgb.copy()
                x0, y0, x1, y1 = comp["bbox"]
                cv2.rectangle(img_with_box, (x0, y0), (x1, y1), (255, 0, 0), 2)
                axes[0, 0].imshow(img_with_box)
                axes[0, 0].set_title("1. Original Image (Damage Box)", fontsize=12, fontweight="bold")
                axes[0, 0].axis("off")

                # 2. M2 Damage Mask Overlay
                dmg_colored = colorize_mask(d_mask_sf, DAMAGE_COLORS)
                dmg_blend = cv2.addWeighted(img_rgb, 0.6, dmg_colored, 0.4, 0)
                cv2.rectangle(dmg_blend, (x0, y0), (x1, y1), (255, 255, 0), 2)
                axes[0, 1].imshow(dmg_blend)
                axes[0, 1].set_title(f"2. M2 SegFormer Damage Mask ({comp['damage_code']})", fontsize=12, fontweight="bold")
                axes[0, 1].axis("off")

                # 3. Zoomed Crop of the Boundary Seam
                pad = 20
                zx0, zy0 = max(0, x0 - pad), max(0, y0 - pad)
                zx1, zy1 = min(512, x1 + pad), min(512, y1 + pad)
                crop_img = img_rgb[zy0:zy1, zx0:zx1]
                axes[0, 2].imshow(crop_img)
                axes[0, 2].set_title(f"3. Zoomed Damage Detail (Panel Boundary)", fontsize=12, fontweight="bold")
                axes[0, 2].axis("off")

                # 4. M1 SegFormer Parts Overlay & Containment Decision
                p_sf_colored = colorize_mask(p_mask_sf, PART_COLORS)
                p_sf_blend = cv2.addWeighted(img_rgb, 0.6, p_sf_colored, 0.4, 0)
                p_sf_blend[comp["mask"]] = [255, 255, 255]  # highlight damage in white
                cv2.rectangle(p_sf_blend, (x0, y0), (x1, y1), (255, 0, 0), 2)
                axes[1, 0].imshow(p_sf_blend)

                cand_sf_str = "\n".join([f"• {c.part_code}: {c.containment*100:.1f}%" for c in dec_sf.candidates[:3]])
                title_sf = f"4. Pairing: M1 SegFormer x M2 SegFormer\nStatus: {dec_sf.assignment_status.upper()} ({dec_sf.part_reason or 'None'})\n{cand_sf_str}"
                axes[1, 0].set_title(title_sf, fontsize=11, fontweight="bold", color="darkred" if dec_sf.part_reason else "green")
                axes[1, 0].axis("off")

                # 5. M1 YOLOv8 Parts Overlay & Containment Decision
                p_yolo_colored = colorize_mask(p_mask_yolo, PART_COLORS)
                p_yolo_blend = cv2.addWeighted(img_rgb, 0.6, p_yolo_colored, 0.4, 0)
                p_yolo_blend[comp["mask"]] = [255, 255, 255]
                cv2.rectangle(p_yolo_blend, (x0, y0), (x1, y1), (0, 255, 0), 2)
                axes[1, 1].imshow(p_yolo_blend)

                cand_yolo_str = "\n".join([f"• {c.part_code}: {c.containment*100:.1f}%" for c in dec_yolo.candidates[:3]])
                title_yolo = f"5. Pairing: M1 YOLOv8 x M2 SegFormer\nStatus: {dec_yolo.assignment_status.upper()} ({dec_yolo.part_reason or 'None'})\n{cand_yolo_str}"
                axes[1, 1].set_title(title_yolo, fontsize=11, fontweight="bold", color="darkred" if dec_yolo.part_reason else "green")
                axes[1, 1].axis("off")

                # 6. Explanation and Decision Summary
                axes[1, 2].axis("off")
                summary_text = (
                    "=== Assignment Comparison ===\n\n"
                    f"Damage Class: {comp['damage_code']}\n"
                    f"Pixel Area: {comp['area_pixels']} px\n"
                    f"Mean Confidence: {comp['mean_confidence']:.2f}\n\n"
                    "Why Unresolved:\n"
                    "The damage region physically lies directly across\n"
                    "the gap/seam between two adjacent vehicle panels.\n\n"
                    f"M1 SegFormer Decision:\n"
                    f"  Status: {dec_sf.assignment_status}\n"
                    f"  Reason: {dec_sf.part_reason}\n\n"
                    f"M1 YOLO Decision:\n"
                    f"  Status: {dec_yolo.assignment_status}\n"
                    f"  Reason: {dec_yolo.part_reason}\n\n"
                    "Surveyor Resolution in M9:\n"
                    "Surveyor can review the photo and manually assign\n"
                    "the primary panel (e.g. via 1-click confirmation)."
                )
                axes[1, 2].text(0.05, 0.95, summary_text, fontsize=11, family="monospace", va="top")

                plt.tight_layout()
                save_path = out_dir / f"ambiguous_case_{generated+1}_{clean_id}.png"
                plt.savefig(save_path, dpi=150)
                plt.close()
                print(f"Saved visualization to: {save_path}")

                generated += 1
                break  # one figure per photo for diversity

    print(f"Generated {generated} ambiguous case visualizations in {out_dir}")


if __name__ == "__main__":
    generate_visualizations(num_cases=4)
