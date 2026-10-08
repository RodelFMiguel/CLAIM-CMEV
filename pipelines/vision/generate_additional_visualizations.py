"""Generate visual figures for below_containment_threshold and mostly_background cases."""
from __future__ import annotations

from pathlib import Path
import sys

import cv2
import matplotlib.pyplot as plt
import numpy as np
import torch
from transformers import SegformerForSemanticSegmentation

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


def run():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = PROJECT_ROOT / "artifacts/benchmarks/visualizations"
    out_dir.mkdir(parents=True, exist_ok=True)

    m1_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/parts/0.8.0-b3-compound").to(device).eval()
    m2_sf = SegformerForSemanticSegmentation.from_pretrained(PROJECT_ROOT / "artifacts/models/damage/0.1.0-b3-compound").to(device).eval()

    cfg = load_assignment_config()
    dataset = HitlDamageDataset(PROJECT_ROOT / "data/splits/damage/0.1.0/test.jsonl", image_size=512, is_train=False)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    # Targets to plot
    targets = {
        "Car damages 1162.png": "below_thresh_1162",
        "Car damages 1184.png": "below_thresh_1184",
        "Car damages 394.png": "mostly_bg_394",
        "Car damages 602.png": "mostly_bg_602",
    }

    for idx in range(len(dataset)):
        rec = dataset.records[idx]
        raw_id = rec.get("photo_id") or rec.get("example_id", f"photo_{idx}")
        if raw_id not in targets:
            continue

        label_prefix = targets[raw_id]
        clean_id = Path(raw_id).stem.replace(" ", "_")
        sample = dataset[idx]

        pixel_val = sample["pixel_values"].permute(1, 2, 0).numpy()
        img_rgb = np.clip((pixel_val * std + mean) * 255.0, 0, 255).astype(np.uint8)
        pixel_tensor = sample["pixel_values"].unsqueeze(0).to(device)

        with torch.no_grad():
            p_logits = m1_sf(pixel_values=pixel_tensor).logits
            p_mask = torch.nn.functional.interpolate(p_logits, size=(512, 512), mode="bilinear", align_corners=False).argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)

            d_logits = m2_sf(pixel_values=pixel_tensor).logits
            d_upsampled = torch.nn.functional.interpolate(d_logits, size=(512, 512), mode="bilinear", align_corners=False)
            d_probs = torch.nn.functional.softmax(d_upsampled, dim=1).squeeze(0)
            d_mask = d_probs.argmax(dim=0).cpu().numpy().astype(np.uint8)
            c_map = d_probs.max(dim=0).values.cpu().numpy().astype(np.float32)

        comps = extract_damage_components(d_mask, c_map, min_confidence=0.50, min_pixels=256)

        for comp_idx, comp in enumerate(comps):
            dec = assign_component(comp["mask"], p_mask, PART_CLASSES, set(PART_CODES), cfg.assignment)
            target_reason = "below_containment_threshold" if "below_thresh" in label_prefix else "mostly_background"

            if dec.part_reason == target_reason:
                fig, axes = plt.subplots(2, 2, figsize=(14, 12))
                plt.suptitle(
                    f"Failure Analysis: {dec.part_reason.upper()}\n"
                    f"Photo: {clean_id} | Damage: {comp['damage_code']} ({comp['area_pixels']} px)",
                    fontsize=15,
                    fontweight="bold",
                )

                # 1. Original Image with Bounding Box
                img_box = img_rgb.copy()
                x0, y0, x1, y1 = comp["bbox"]
                cv2.rectangle(img_box, (x0, y0), (x1, y1), (255, 0, 0), 2)
                axes[0, 0].imshow(img_box)
                axes[0, 0].set_title(f"1. Original Photo (Detected Box)", fontsize=12, fontweight="bold")
                axes[0, 0].axis("off")

                # 2. Damage Mask
                dmg_col = colorize_mask(d_mask, DAMAGE_COLORS)
                dmg_blend = cv2.addWeighted(img_rgb, 0.6, dmg_col, 0.4, 0)
                cv2.rectangle(dmg_blend, (x0, y0), (x1, y1), (255, 255, 0), 2)
                axes[0, 1].imshow(dmg_blend)
                axes[0, 1].set_title(f"2. M2 Damage Mask: {comp['damage_code']}", fontsize=12, fontweight="bold")
                axes[0, 1].axis("off")

                # 3. Parts Mask with Highlighted Component
                parts_col = colorize_mask(p_mask, PART_COLORS)
                parts_blend = cv2.addWeighted(img_rgb, 0.6, parts_col, 0.4, 0)
                parts_blend[comp["mask"]] = [255, 255, 255]
                cv2.rectangle(parts_blend, (x0, y0), (x1, y1), (0, 0, 255), 2)
                axes[1, 0].imshow(parts_blend)
                axes[1, 0].set_title(f"3. M1 Parts Overlay (White: Damage Region)", fontsize=12, fontweight="bold")
                axes[1, 0].axis("off")

                # 4. Containment Breakdown Text & Why it Failed
                axes[1, 1].axis("off")
                cand_lines = "\n".join([f"    • {c.part_code}: {c.containment*100:.1f}%" for c in dec.candidates[:4]])
                
                if target_reason == "below_containment_threshold":
                    explanation = (
                        "Rule Evaluated:\n"
                        "  assign_min_containment = 0.60 (60.0%)\n\n"
                        f"Why It Failed:\n"
                        f"  Primary part is '{dec.candidates[0].part_code}' at {dec.primary_containment*100:.1f}%,\n"
                        f"  which is below the required 60.0% containment floor.\n"
                        f"  Because this severe collision area touches 3+ panels,\n"
                        f"  no single panel reaches majority (60%) coverage."
                    )
                else:
                    explanation = (
                        "Rule Evaluated:\n"
                        "  assign_background_max = 0.50 (50.0%)\n\n"
                        f"Why It Failed:\n"
                        f"  Background containment is {dec.background_containment*100:.1f}% (> 50.0%).\n"
                        f"  More than half of the detected region lies outside\n"
                        f"  the vehicle panel boundaries (e.g., ground/glare/open cavity)."
                    )

                text = (
                    f"=== Region Containment Metrics ===\n\n"
                    f"Status: {dec.assignment_status.upper()}\n"
                    f"Reason Code: {dec.part_reason}\n\n"
                    f"Containment Breakdown:\n"
                    f"{cand_lines}\n"
                    f"    • background: {dec.background_containment*100:.1f}%\n\n"
                    f"Primary Containment: {dec.primary_containment*100:.1f}%\n"
                    f"Runner-Up Containment: {dec.runner_up_containment*100 if dec.runner_up_containment else 0:.1f}%\n"
                    f"Background Area: {dec.background_containment*100:.1f}%\n\n"
                    f"{explanation}"
                )
                axes[1, 1].text(0.05, 0.95, text, fontsize=11, family="monospace", va="top")

                plt.tight_layout()
                save_path = out_dir / f"{label_prefix}_{clean_id}.png"
                plt.savefig(save_path, dpi=150)
                plt.close()
                print(f"Saved: {save_path}")
                break


if __name__ == "__main__":
    run()
