"""Generate side-by-side visual evidence showing where SegFormer significantly outperforms YOLO."""
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


def run():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = PROJECT_ROOT / "artifacts/benchmarks/visualizations"
    out_dir.mkdir(parents=True, exist_ok=True)

    m1_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/parts/0.8.0-b3-compound").to(device).eval()
    m2_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/damage/0.1.0-b3-compound").to(device).eval()
    m2_yolo = YOLO(str(PROJECT_ROOT / "artifacts/models/damage/yolov8m-seg/weights/best.pt"))

    cfg = load_assignment_config()
    dataset = HitlDamageDataset(PROJECT_ROOT / "data/splits/damage/0.1.0/test.jsonl", image_size=512, is_train=False)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    cases = [
        ("Car damages 116.png", "segformer_superior_1_dent_scratch", "Door Dent + Hairline Scratch"),
        ("Car damages 1070.png", "segformer_superior_2_high_iou_dent", "Large Deep Panel Dent"),
        ("Car damages 1259.png", "segformer_superior_3_crease_dent", "Panel Crease Dent"),
    ]

    for raw_id, prefix, desc in cases:
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
            p_logits = m1_sf(pixel_values=pixel_tensor).logits
            p_mask_sf = torch.nn.functional.interpolate(p_logits, size=(512, 512), mode="bilinear", align_corners=False).argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

            d_logits = m2_sf(pixel_values=pixel_tensor).logits
            d_upsampled = torch.nn.functional.interpolate(d_logits, size=(512, 512), mode="bilinear", align_corners=False)
            d_probs = torch.nn.functional.softmax(d_upsampled, dim=1).squeeze(0)
            d_mask_sf = d_probs.argmax(dim=0).cpu().numpy().astype(np.uint8)
            c_map_sf = d_probs.max(dim=0).values.cpu().numpy().astype(np.float32)

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

        # Calculate per-class IoUs for both models against Ground Truth
        ious_sf = {}
        ious_yolo = {}
        present_gt = [c for c in np.unique(gt_dmg) if c != 0]

        for c in present_gt:
            cname = DAMAGE_NAMES[c]
            gt_c = (gt_dmg == c)
            sf_c = (d_mask_sf == c)
            y_c = (d_mask_yolo == c)

            inter_sf = np.count_nonzero(gt_c & sf_c)
            union_sf = np.count_nonzero(gt_c | sf_c)
            ious_sf[cname] = inter_sf / union_sf if union_sf > 0 else 0.0

            inter_y = np.count_nonzero(gt_c & y_c)
            union_y = np.count_nonzero(gt_c | y_c)
            ious_yolo[cname] = inter_y / union_y if union_y > 0 else 0.0

        # Plot 2x2 multi-panel layout
        fig, axes = plt.subplots(2, 2, figsize=(15, 12))
        plt.suptitle(
            f"Why SegFormer Outperforms YOLO: {desc}\n"
            f"Test Photo: {clean_id}",
            fontsize=16,
            fontweight="bold",
        )

        # 1. Original Image
        axes[0, 0].imshow(img_rgb)
        axes[0, 0].set_title("1. Original Test Photo", fontsize=13, fontweight="bold")
        axes[0, 0].axis("off")

        # 2. Ground Truth Mask Overlay
        gt_col = colorize_mask(gt_dmg, DAMAGE_COLORS)
        gt_blend = cv2.addWeighted(img_rgb, 0.6, gt_col, 0.4, 0)
        axes[0, 1].imshow(gt_blend)
        gt_str = ", ".join([f"{cname} ({int(np.count_nonzero(gt_dmg == c))} px)" for c, cname in [(c, DAMAGE_NAMES[c]) for c in present_gt]])
        axes[0, 1].set_title(f"2. Ground Truth Damage\n{gt_str}", fontsize=12, fontweight="bold")
        axes[0, 1].axis("off")

        # 3. SegFormer Prediction
        sf_col = colorize_mask(d_mask_sf, DAMAGE_COLORS)
        sf_blend = cv2.addWeighted(img_rgb, 0.6, sf_col, 0.4, 0)
        axes[1, 0].imshow(sf_blend)
        sf_stats = " | ".join([f"{k} IoU: {v*100:.1f}%" for k, v in ious_sf.items()])
        axes[1, 0].set_title(
            f"3. SegFormer-B3 Prediction (WINNER)\n{sf_stats}",
            fontsize=12,
            fontweight="bold",
            color="darkgreen",
        )
        axes[1, 0].axis("off")

        # 4. YOLOv8 Prediction
        yolo_col = colorize_mask(d_mask_yolo, DAMAGE_COLORS)
        yolo_blend = cv2.addWeighted(img_rgb, 0.6, yolo_col, 0.4, 0)
        axes[1, 1].imshow(yolo_blend)
        yolo_stats = " | ".join([f"{k} IoU: {v*100:.1f}%" for k, v in ious_yolo.items()])
        axes[1, 1].set_title(
            f"4. YOLOv8m-seg Prediction\n{yolo_stats}",
            fontsize=12,
            fontweight="bold",
            color="darkred" if any(v < 0.25 for v in ious_yolo.values()) else "navy",
        )
        axes[1, 1].axis("off")

        plt.tight_layout()
        save_path = out_dir / f"{prefix}_{clean_id}.png"
        plt.savefig(save_path, dpi=150)
        plt.close()
        print(f"Saved: {save_path}")

    print("Done!")


if __name__ == "__main__":
    run()
