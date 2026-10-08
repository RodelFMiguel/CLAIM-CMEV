"""Generate high-resolution visual comparison overlays for M1 report."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import cv2
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import torch
from transformers import SegformerForSemanticSegmentation
from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.vision.dataset import HitlPartsDataset

OUT_DIR = PROJECT_ROOT / "artifacts/benchmarks/visualizations/m1_parts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 21 Canonical part labels
PART_NAMES = [
    "background", "windshield", "back-windshield", "front-window", "back-window",
    "front-door", "back-door", "front-wheel", "back-wheel", "front-bumper",
    "back-bumper", "headlight", "tail-light", "hood", "trunk",
    "licence-plate", "mirror", "roof", "grille", "rocker-panel",
    "quarter-panel", "fender"
]

# Color map for 22 classes
PART_COLORS = np.array([
    [0, 0, 0],         # 0: background (black / transparent)
    [0, 200, 255],     # 1: windshield (cyan)
    [0, 150, 200],     # 2: back-windshield
    [0, 100, 255],     # 3: front-window
    [0, 50, 200],      # 4: back-window
    [255, 120, 0],     # 5: front-door (orange)
    [220, 80, 0],      # 6: back-door (dark orange)
    [100, 100, 100],   # 7: front-wheel (gray)
    [80, 80, 80],      # 8: back-wheel (dark gray)
    [0, 220, 100],     # 9: front-bumper (green)
    [0, 160, 70],      # 10: back-bumper (dark green)
    [255, 255, 0],     # 11: headlight (yellow)
    [200, 0, 0],       # 12: tail-light (red)
    [180, 0, 220],     # 13: hood (purple)
    [130, 0, 180],     # 14: trunk (dark purple)
    [255, 255, 255],   # 15: licence-plate (white)
    [255, 200, 100],   # 16: mirror
    [50, 150, 255],    # 17: roof (sky blue)
    [255, 0, 120],     # 18: grille (magenta)
    [255, 215, 0],     # 19: rocker-panel (gold)
    [0, 180, 220],     # 20: quarter-panel (teal)
    [30, 144, 255],    # 21: fender (dodger blue)
], dtype=np.uint8)


def colorize_part_mask(mask: np.ndarray) -> np.ndarray:
    return PART_COLORS[np.clip(mask, 0, 21)]


def blend_overlay(img_rgb: np.ndarray, mask: np.ndarray, alpha: float = 0.55) -> np.ndarray:
    color_mask = colorize_part_mask(mask)
    fg = mask > 0
    blended = img_rgb.copy()
    blended[fg] = (alpha * color_mask[fg] + (1 - alpha) * img_rgb[fg]).astype(np.uint8)
    return blended


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Load dataset
    split_path = PROJECT_ROOT / "data/splits/parts/0.1.0/test.jsonl"
    dataset = HitlPartsDataset(split_path, image_size=512, is_train=False)

    # Load models
    print("Loading models...")
    print("1. YOLOv8m-seg...")
    yolo_model = YOLO(str(PROJECT_ROOT / "artifacts/models/parts/yolov8m-seg/weights/best.pt"))

    print("2. SegFormer-B3...")
    b3_model = SegformerForSemanticSegmentation.from_pretrained(
        PROJECT_ROOT / "artifacts/models/parts/0.8.0-b3-compound"
    ).to(device).eval()

    print("3. SegFormer-B2...")
    b2_model = SegformerForSemanticSegmentation.from_pretrained(
        PROJECT_ROOT / "artifacts/models/parts/0.5.0-b2"
    ).to(device).eval()

    print("4. SegFormer-B0 Baseline...")
    b0_model = SegformerForSemanticSegmentation.from_pretrained(
        PROJECT_ROOT / "artifacts/models/parts/0.1.0"
    ).to(device).eval()

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    cases = [
        {
            "id": "Car damages 1048.png",
            "filename": "m1_visual_1_door_seams_and_rocker_panel.png",
            "title": "Case 1: Door Seam Boundary & Rocker Panel Resolution (Car damages 1048.png)",
            "focus": [5, 6, 17, 19, 21, 9],  # front-door, back-door, roof, rocker-panel, fender, front-bumper
            "notes": (
                "Key Finding:\n"
                "• SegFormer-B0 Baseline fails on rocker-panel (missing completely) and blurs front-door / back-door seam.\n"
                "• SegFormer-B3 and YOLOv8m-seg cleanly separate front-door and back-door, and accurately trace the thin rocker-panel.\n"
                "• YOLOv8m-seg demonstrates the sharpest edge adherence along the vehicle sill."
            )
        },
        {
            "id": "Car damages 1052.png",
            "filename": "m1_visual_2_rear_quarter_and_trunk.png",
            "title": "Case 2: Rear Body Assembly, Quarter Panel & Trunk (Car damages 1052.png)",
            "focus": [14, 10, 20, 6, 19, 2],  # trunk, back-bumper, quarter-panel, back-door, rocker-panel, back-windshield
            "notes": (
                "Key Finding:\n"
                "• Complex rear 3/4 perspective with overlapping curvature between quarter-panel and trunk.\n"
                "• SegFormer-B0 confuses quarter-panel with door boundary and under-segments back-bumper.\n"
                "• SegFormer-B3 and YOLO correctly segment trunk, bumper, and distinct quarter-panel boundary."
            )
        },
        {
            "id": "Car damages 101.png",
            "filename": "m1_visual_3_front_hood_and_bumper.png",
            "title": "Case 3: Front Collision Profile: Hood, Grille & Front Bumper (Car damages 101.png)",
            "focus": [13, 9, 18, 11, 21, 1],  # hood, front-bumper, grille, headlight, fender, windshield
            "notes": (
                "Key Finding:\n"
                "• Front impact zone with deformed bumper and grille.\n"
                "• SegFormer-B0 over-segments hood into windshield and loses narrow rocker panel.\n"
                "• YOLOv8m-seg and SegFormer-B3 accurately demarcate hood, headlight, grille, and bumper."
            )
        }
    ]

    for case in cases:
        raw_id = case["id"]
        idx = next((i for i, r in enumerate(dataset.records) if r.get("example_id") == raw_id), None)
        if idx is None:
            print(f"Skipping {raw_id}: not found in records")
            continue

        sample = dataset[idx]
        gt_mask = sample["labels"].numpy()

        # Image recovery
        pixel_val = sample["pixel_values"].permute(1, 2, 0).numpy()
        img_rgb = np.clip((pixel_val * std + mean) * 255.0, 0, 255).astype(np.uint8)
        pixel_tensor = sample["pixel_values"].unsqueeze(0).to(device)

        with torch.no_grad():
            # SegFormer-B3
            logits_b3 = b3_model(pixel_values=pixel_tensor).logits
            mask_b3 = torch.nn.functional.interpolate(
                logits_b3, size=(512, 512), mode="bilinear", align_corners=False
            ).argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

            # SegFormer-B2
            logits_b2 = b2_model(pixel_values=pixel_tensor).logits
            mask_b2 = torch.nn.functional.interpolate(
                logits_b2, size=(512, 512), mode="bilinear", align_corners=False
            ).argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

            # SegFormer-B0
            logits_b0 = b0_model(pixel_values=pixel_tensor).logits
            mask_b0 = torch.nn.functional.interpolate(
                logits_b0, size=(512, 512), mode="bilinear", align_corners=False
            ).argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

        # YOLO inference
        # Convert RGB to BGR for ultralytics
        img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        results = yolo_model.predict(img_bgr, imgsz=512, conf=0.25, verbose=False)
        mask_yolo = np.zeros((512, 512), dtype=np.uint8)
        if results and results[0].masks is not None:
            boxes = results[0].boxes
            clses = boxes.cls.cpu().numpy().astype(int)
            confs = boxes.conf.cpu().numpy()
            masks = results[0].masks.data.cpu().numpy()

            # Sort by area descending so smaller panels paint over larger ones
            areas = [m.sum() for m in masks]
            sort_order = np.argsort(areas)[::-1]
            for m_idx in sort_order:
                yolo_cls = clses[m_idx]
                part_id = yolo_cls + 1  # 1-indexed (0 is background)
                m = cv2.resize(masks[m_idx], (512, 512), interpolation=cv2.INTER_NEAREST)
                mask_yolo[m > 0.5] = part_id

        # Plot 2x3 Grid
        fig, axes = plt.subplots(2, 3, figsize=(16, 10), dpi=200)

        # 1. Original Image
        axes[0, 0].imshow(img_rgb)
        axes[0, 0].set_title("Input Vehicle Photo (512x512)", fontsize=11, weight="bold")
        axes[0, 0].axis("off")

        # 2. Ground Truth
        axes[0, 1].imshow(blend_overlay(img_rgb, gt_mask))
        axes[0, 1].set_title("Ground Truth Human Annotation", fontsize=11, weight="bold")
        axes[0, 1].axis("off")

        # 3. YOLOv8m-seg
        axes[0, 2].imshow(blend_overlay(img_rgb, mask_yolo))
        axes[0, 2].set_title("YOLOv8m-seg (Best Overall: 0.7940 mIoU)", fontsize=11, weight="bold", color="#047857")
        axes[0, 2].axis("off")

        # 4. SegFormer-B3
        axes[1, 0].imshow(blend_overlay(img_rgb, mask_b3))
        axes[1, 0].set_title("SegFormer-B3 Compound (0.7303 mIoU)", fontsize=11, weight="bold", color="#1e3a8a")
        axes[1, 0].axis("off")

        # 5. SegFormer-B2
        axes[1, 1].imshow(blend_overlay(img_rgb, mask_b2))
        axes[1, 1].set_title("SegFormer-B2 Compound (0.7075 mIoU)", fontsize=11, weight="bold", color="#2563eb")
        axes[1, 1].axis("off")

        # 6. SegFormer-B0 Baseline
        axes[1, 2].imshow(blend_overlay(img_rgb, mask_b0))
        axes[1, 2].set_title("SegFormer-B0 Baseline (0.4664 mIoU - Failed Floor)", fontsize=11, weight="bold", color="#b91c1c")
        axes[1, 2].axis("off")

        # Legend handles for panels in this case
        legend_handles = []
        unique_classes = set(np.unique(gt_mask)).union(np.unique(mask_yolo)).union(np.unique(mask_b3))
        for c in sorted(unique_classes):
            if c == 0 or c >= 22:
                continue
            color = PART_COLORS[c] / 255.0
            patch = patches.Patch(facecolor=color, edgecolor="#333333", label=PART_NAMES[c])
            legend_handles.append(patch)

        fig.legend(handles=legend_handles, loc="lower center", ncol=min(len(legend_handles), 8),
                   fontsize=9, bbox_to_anchor=(0.5, 0.01), framealpha=0.95)

        fig.suptitle(case["title"], fontsize=13, weight="bold", y=0.98)
        plt.subplots_adjust(left=0.04, right=0.96, top=0.92, bottom=0.08, wspace=0.06, hspace=0.15)

        out_path = OUT_DIR / case["filename"]
        plt.savefig(out_path, bbox_inches="tight")
        plt.close()
        print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
