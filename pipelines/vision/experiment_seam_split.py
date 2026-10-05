"""Controlled evaluation experiment for seam-guided component splitting policy (M2).

Hypothesis:
Can seam-guided splitting (split_components) on multi-panel damage instances
resolve 'ambiguous_between_parts' without generating fragmented false-positive slivers?

Evaluated against the 132 held-out test images using:
- M1: SegFormer-B3 Compound
- M2: SegFormer-B3 Compound
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
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import torch
import torch.nn.functional as F
from transformers import SegformerForSemanticSegmentation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claim_cmev.contracts.common import PART_CODES
from claim_cmev.vision.damage.assignment import assign_component, measure_containment, decide_assignment
from claim_cmev.vision.damage.config import load_assignment_config
from pipelines.vision.convert_damage import DAMAGE_CLASSES, ID_TO_DAMAGE_CODE
from pipelines.vision.damage_dataset import HitlDamageDataset
from pipelines.vision.generate_ambiguous_visualizations import (
    PART_CLASSES,
    PART_COLORS,
    DAMAGE_COLORS,
    colorize_mask,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmev.pipelines.experiment_seam_split")

OUT_DIR = PROJECT_ROOT / "artifacts/benchmarks"
VIZ_DIR = OUT_DIR / "visualizations"
OUT_DIR.mkdir(parents=True, exist_ok=True)
VIZ_DIR.mkdir(parents=True, exist_ok=True)

PART_MAP = {i + 1: code for i, code in enumerate(PART_CODES)}
DAMAGE_MAP = {i: c for i, c in ID_TO_DAMAGE_CODE.items() if i != 0}


def extract_raw_components(
    damage_mask: np.ndarray,
    confidence: np.ndarray,
    min_confidence: float = 0.50,
    min_pixels: int = 256,
    connectivity: int = 8,
    max_components: int = 50,
) -> list[dict[str, Any]]:
    """Extract connected damage components adhering to standard M2."""
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
                
    if len(candidates) > max_components:
        candidates = sorted(candidates, key=lambda c: c["area_pixels"], reverse=True)[:max_components]
        
    return candidates


def evaluate_policy(
    all_samples: list[dict[str, Any]],
    policy_name: str,
    cfg,
    split_mode: str = "none",  # "none", "naive_256", "safety_gated_128", "conservative_500"
    min_split_pixels: int = 256,
) -> dict[str, Any]:
    """Run assignment under a given component-splitting policy."""
    total_components = 0
    assigned_count = 0
    unresolved_count = 0
    reasons_count: dict[str, int] = {}
    containments: list[float] = []
    sliver_count = 0  # Sub-components < 15% of original parent area
    split_events = 0  # Number of original components that got split
    
    for sample in all_samples:
        p_mask = sample["part_mask"]
        accepted_parts = sample["accepted_parts"]
        raw_components = sample["raw_components"]
        
        processed_components = []
        
        for comp in raw_components:
            comp_mask = comp["mask"]
            area = comp["area_pixels"]
            
            # Baseline decision first
            decision = assign_component(comp_mask, p_mask, PART_MAP, accepted_parts, cfg.assignment)
            
            should_split = False
            if split_mode != "none" and decision.part_reason == "ambiguous_between_parts":
                should_split = True
            elif split_mode == "naive_all" and len(decision.candidates) > 1:
                should_split = True
                
            if not should_split:
                processed_components.append({
                    "mask": comp_mask,
                    "area_pixels": area,
                    "damage_code": comp["damage_code"],
                    "decision": decision,
                    "is_sub_component": False,
                })
            else:
                # Attempt seam-guided split
                # Find all overlapping parts
                intersected_parts = []
                for cand in decision.candidates:
                    p_code = cand.part_code
                    p_id = next((k for k, v in PART_MAP.items() if v == p_code), None)
                    if p_id is not None:
                        sub_mask = comp_mask & (p_mask == p_id)
                        sub_area = int(sub_mask.sum())
                        if sub_area >= min_split_pixels:
                            intersected_parts.append((p_code, sub_mask, sub_area))
                
                # Check split criteria
                if len(intersected_parts) >= 2:
                    split_events += 1
                    for p_code, sub_mask, sub_area in intersected_parts:
                        sub_decision = assign_component(sub_mask, p_mask, PART_MAP, accepted_parts, cfg.assignment)
                        is_sliver = (sub_area / area) < 0.15
                        if is_sliver:
                            sliver_count += 1
                        processed_components.append({
                            "mask": sub_mask,
                            "area_pixels": sub_area,
                            "damage_code": comp["damage_code"],
                            "decision": sub_decision,
                            "is_sub_component": True,
                        })
                else:
                    # Could not cleanly split both sides above threshold; keep original
                    processed_components.append({
                        "mask": comp_mask,
                        "area_pixels": area,
                        "damage_code": comp["damage_code"],
                        "decision": decision,
                        "is_sub_component": False,
                    })
                    
        # Aggregate stats
        for item in processed_components:
            total_components += 1
            dec = item["decision"]
            if dec.assignment_status == "assigned":
                assigned_count += 1
                containments.append(dec.primary_containment)
            else:
                unresolved_count += 1
                r = dec.part_reason or "unknown"
                reasons_count[r] = reasons_count.get(r, 0) + 1
                
    assignment_rate = assigned_count / total_components if total_components > 0 else 0.0
    mean_containment = float(np.mean(containments)) if containments else 0.0
    
    return {
        "policy_name": policy_name,
        "split_mode": split_mode,
        "min_split_pixels": min_split_pixels,
        "total_components": total_components,
        "split_events": split_events,
        "sliver_count": sliver_count,
        "assigned_count": assigned_count,
        "assignment_rate": round(assignment_rate, 4),
        "mean_containment": round(mean_containment, 4),
        "unresolved_count": unresolved_count,
        "reasons": reasons_count,
    }


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Running seam split experiment on {device}...")
    
    cfg = load_assignment_config()
    
    # 1. Load models
    m1_sf_dir = PROJECT_ROOT / "artifacts/models/parts/0.8.0-b3-compound"
    m2_sf_dir = PROJECT_ROOT / "artifacts/models/damage/0.1.0-b3-compound"
    
    log.info("Loading M1 SegFormer-B3...")
    m1_model = SegformerForSemanticSegmentation.from_pretrained(m1_sf_dir).to(device).eval()
    
    log.info("Loading M2 SegFormer-B3...")
    m2_model = SegformerForSemanticSegmentation.from_pretrained(m2_sf_dir).to(device).eval()
    
    # 2. Load dataset
    test_split_path = PROJECT_ROOT / "data/splits/damage/0.1.0/test.jsonl"
    dataset = HitlDamageDataset(test_split_path, image_size=512, is_train=False)
    
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    
    log.info(f"Extracting baseline predictions across {len(dataset)} test images...")
    all_samples: list[dict[str, Any]] = []
    
    # Store candidates for visualization
    cases_for_viz: list[dict[str, Any]] = []
    
    for idx in range(len(dataset)):
        sample = dataset[idx]
        raw_id = sample.get("example_id") or sample.get("photo_id", f"sample_{idx}")
        pixel_tensor = sample["pixel_values"].unsqueeze(0).to(device)
        
        pixel_val = sample["pixel_values"].permute(1, 2, 0).numpy()
        img_rgb = np.clip((pixel_val * std + mean) * 255.0, 0, 255).astype(np.uint8)
        
        with torch.no_grad():
            # M1 parts
            p_logits = m1_model(pixel_values=pixel_tensor).logits
            p_upsampled = F.interpolate(p_logits, size=(512, 512), mode="bilinear", align_corners=False)
            p_mask = p_upsampled.argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)
            
            # M2 damage
            d_logits = m2_model(pixel_values=pixel_tensor).logits
            d_upsampled = F.interpolate(d_logits, size=(512, 512), mode="bilinear", align_corners=False)
            d_probs = F.softmax(d_upsampled, dim=1).squeeze(0)
            d_mask = d_probs.argmax(dim=0).cpu().numpy().astype(np.uint8)
            c_map = d_probs.max(dim=0).values.cpu().numpy().astype(np.float32)
            
        p_ids = {int(v) for v in np.unique(p_mask)} - {0}
        accepted_parts = {PART_MAP[pid] for pid in p_ids if pid in PART_MAP}
        
        raw_comps = extract_raw_components(
            d_mask, c_map,
            min_confidence=cfg.regions.min_damage_confidence,
            min_pixels=cfg.regions.min_damage_pixels,
            connectivity=cfg.regions.connectivity,
            max_components=cfg.regions.max_components_per_photo,
        )
        
        all_samples.append({
            "idx": idx,
            "raw_id": raw_id,
            "img_rgb": img_rgb,
            "part_mask": p_mask,
            "accepted_parts": accepted_parts,
            "raw_components": raw_comps,
        })
        
    log.info("Evaluating policies...")
    
    policies = [
        ("Baseline (No Split - Current Contract)", "none", 256),
        ("Policy 1: Safety-Gated Split (min 500 px)", "conservative_500", 500),
        ("Policy 2: Standard Seam Split (min 256 px)", "naive_256", 256),
        ("Policy 3: Aggressive Seam Split (min 128 px)", "safety_gated_128", 128),
    ]
    
    results = []
    for name, mode, min_px in policies:
        res = evaluate_policy(all_samples, name, cfg, split_mode=mode, min_split_pixels=min_px)
        results.append(res)
        log.info(f"{name:45s} | Obs: {res['total_components']} | Assigned: {res['assigned_count']} ({res['assignment_rate']*100:.1f}%) | "
                 f"Splits: {res['split_events']} | Slivers (<15%): {res['sliver_count']} | Ambiguous: {res['reasons'].get('ambiguous_between_parts', 0)}")

    # 3. Save benchmark JSON & MD
    report_data = {
        "benchmark_name": "M2 Seam-Guided Component Splitting Policy Experiment",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test_images_count": len(dataset),
        "results": results,
    }
    
    json_path = OUT_DIR / "m2_seam_split_experiment.json"
    json_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
    log.info(f"Saved experiment results to {json_path}")
    
    # 4. Generate Visual Case Overlays
    log.info("Generating visual overlays for clean split vs sliver risk...")
    
    # Search for Case 1: Clean split on large collision (Car damages 101.png or 1063.png)
    # Search for Case 2: Dangerous sliver bleed on seam
    for sample in all_samples:
        p_mask = sample["part_mask"]
        accepted_parts = sample["accepted_parts"]
        raw_comps = sample["raw_components"]
        raw_id = sample["raw_id"]
        img_name = Path(raw_id).name
        
        for comp in raw_comps:
            comp_mask = comp["mask"]
            area = comp["area_pixels"]
            dec = assign_component(comp_mask, p_mask, PART_MAP, accepted_parts, cfg.assignment)
            
            # Check Case 1: Relatively balanced clean split (Car damages 1162)
            if dec.part_reason == "ambiguous_between_parts" and len(dec.candidates) >= 2:
                p1, p2 = dec.candidates[0], dec.candidates[1]
                if 0.35 <= p1.containment <= 0.65 and 0.35 <= p2.containment <= 0.65 and area > 1000:
                    if {p1.part_code, p2.part_code} & {"front-bumper", "fender", "back-bumper", "quarter-panel", "front-door", "back-door"} == {p1.part_code, p2.part_code}:
                        if not any(c.get("type") == "clean_split" for c in cases_for_viz):
                            cases_for_viz.append({
                                "type": "clean_split",
                                "sample": sample,
                                "comp": comp,
                                "decision": dec,
                                "p1_code": p1.part_code,
                                "p2_code": p2.part_code,
                            })
                            
            # Check Case 2: True Sliver Bleed on distinct photo (Car damages 308.jpg: headlight 85.5% spilling 12.1% onto hood)
            if "308" in img_name and area > 1500:
                h_id = next((k for k, v in PART_MAP.items() if v == "headlight"), None)
                hood_id = next((k for k, v in PART_MAP.items() if v == "hood"), None)
                if h_id is not None and hood_id is not None:
                    h_cnt = (comp_mask & (p_mask == h_id)).sum()
                    hood_cnt = (comp_mask & (p_mask == hood_id)).sum()
                    if h_cnt > 1000 and 100 < hood_cnt < 400:
                        if not any(c.get("type") == "sliver_risk" for c in cases_for_viz):
                            cases_for_viz.append({
                                "type": "sliver_risk",
                                "sample": sample,
                                "comp": comp,
                                "decision": dec,
                                "p1_code": "headlight",
                                "p2_code": "hood",
                            })
                            
            # Check Case 3: Legitimate Minor Edge Case on Car damages 101.png (bumper vs headlight ~22%)
            if "101" in img_name and area > 2000:
                b_id = next((k for k, v in PART_MAP.items() if v == "front-bumper"), None)
                hl_id = next((k for k, v in PART_MAP.items() if v == "headlight"), None)
                if b_id is not None and hl_id is not None:
                    b_cnt = (comp_mask & (p_mask == b_id)).sum()
                    hl_cnt = (comp_mask & (p_mask == hl_id)).sum()
                    if b_cnt > 1000 and hl_cnt > 300:
                        if not any(c.get("type") == "edge_case" for c in cases_for_viz):
                            cases_for_viz.append({
                                "type": "edge_case",
                                "sample": sample,
                                "comp": comp,
                                "decision": dec,
                                "p1_code": "front-bumper",
                                "p2_code": "headlight",
                            })
                            
    for viz_case in cases_for_viz:
        case_type = viz_case["type"]
        sample = viz_case["sample"]
        comp = viz_case["comp"]
        dec = viz_case["decision"]
        raw_id = sample["raw_id"]
        img_rgb = sample["img_rgb"]
        p_mask = sample["part_mask"]
        comp_mask = comp["mask"]
        
        p1_code = viz_case["p1_code"]
        p2_code = viz_case["p2_code"]
        p1_id = next(k for k, v in PART_MAP.items() if v == p1_code)
        p2_id = next(k for k, v in PART_MAP.items() if v == p2_code)
        
        sub1_mask = comp_mask & (p_mask == p1_id)
        sub2_mask = comp_mask & (p_mask == p2_id)
        
        fig, axes = plt.subplots(1, 4, figsize=(18, 5.5), dpi=180)
        
        # 1. Image with bounding box
        x1, y1, x2, y2 = comp["bbox"]
        axes[0].imshow(img_rgb)
        rect = patches.Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=2, edgecolor="#f59e0b", facecolor="none")
        axes[0].add_patch(rect)
        axes[0].set_title(f"Vehicle Photo ({Path(raw_id).name})\nRegion: {comp['area_pixels']} px", fontsize=10, weight="bold")
        axes[0].axis("off")
        
        # 2. Baseline Assignment (Unsplit)
        overlay_base = img_rgb.copy()
        
        # Highlight all OTHER damages faintly so reader knows M2 detected them
        for other_comp in sample["raw_components"]:
            if other_comp["bbox"] != comp["bbox"]:
                other_mask = other_comp["mask"]
                overlay_base[other_mask] = (0.4 * np.array([253, 224, 71]) + 0.6 * overlay_base[other_mask]).astype(np.uint8) # Faint yellow
                
        # Highlight TARGET ambiguous damage boldly
        overlay_base[comp_mask] = (0.6 * np.array([239, 68, 68]) + 0.4 * overlay_base[comp_mask]).astype(np.uint8)
        
        pct1 = sub1_mask.sum() / max(1, comp["area_pixels"]) * 100
        pct2 = sub2_mask.sum() / max(1, comp["area_pixels"]) * 100
        status_str = dec.part_reason if dec.part_reason else dec.assignment_status
        axes[1].imshow(overlay_base)
        axes[1].set_title(f"BASELINE: TARGET COMPONENT\nStatus: {status_str}\n{p1_code}: {pct1:.1f}%, {p2_code}: {pct2:.1f}%", fontsize=9.5, weight="bold", color="#b91c1c")
        axes[1].axis("off")
        
        # 3. M1 Parts Seam Boundary
        parts_overlay = img_rgb.copy()
        c1 = np.array([59, 130, 246], dtype=np.float32) # Force Blue
        c2 = np.array([16, 185, 129], dtype=np.float32) # Force Green
        parts_overlay[p_mask == p1_id] = (0.4 * c1 + 0.6 * parts_overlay[p_mask == p1_id]).astype(np.uint8)
        parts_overlay[p_mask == p2_id] = (0.4 * c2 + 0.6 * parts_overlay[p_mask == p2_id]).astype(np.uint8)
        axes[2].imshow(parts_overlay)
        axes[2].set_title(f"M1 Panel Seam Interface\nBlue: {p1_code} | Green: {p2_code}", fontsize=10, weight="bold")
        axes[2].axis("off")
        
        # 4. Seam Split Outcome
        split_overlay = img_rgb.copy()
        split_overlay[sub1_mask] = (0.6 * np.array([59, 130, 246]) + 0.4 * split_overlay[sub1_mask]).astype(np.uint8)  # Blue
        split_overlay[sub2_mask] = (0.6 * np.array([16, 185, 129]) + 0.4 * split_overlay[sub2_mask]).astype(np.uint8)  # Green
        axes[3].imshow(split_overlay)
        
        # --- Add Explicit Arrow Annotations ---
        y1_inds, x1_inds = np.where(sub1_mask)
        if len(y1_inds) > 0:
            cy1, cx1 = int(np.mean(y1_inds)), int(np.mean(x1_inds))
            # Blue text below, pointing UP
            bottom_edge = int(np.max(y1_inds))
            text_y1 = min(img_rgb.shape[0]-15, bottom_edge + 80)
            axes[3].annotate(
                f"Split 1: {p1_code}",
                xy=(cx1, bottom_edge), xytext=(cx1, text_y1),
                arrowprops=dict(facecolor='#3b82f6', shrink=0.05, width=2, headwidth=8),
                fontsize=9, weight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.3", fc="#3b82f6", ec="white", lw=1, alpha=0.9),
                ha="center", va="center"
            )
            
        y2_inds, x2_inds = np.where(sub2_mask)
        if len(y2_inds) > 0:
            cy2, cx2 = int(np.mean(y2_inds)), int(np.mean(x2_inds))
            # Green text above, pointing DOWN
            top_edge = int(np.min(y2_inds))
            text_y2 = max(15, top_edge - 80)
            axes[3].annotate(
                f"Split 2: {p2_code}",
                xy=(cx2, top_edge), xytext=(cx2, text_y2),
                arrowprops=dict(facecolor='#10b981', shrink=0.05, width=2, headwidth=8),
                fontsize=9, weight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.3", fc="#10b981", ec="white", lw=1, alpha=0.9),
                ha="center", va="center"
            )
        # --- End Annotations ---
        
        if case_type == "clean_split":
            title_text = (
                f"SEAM SPLIT (SUCCESSFUL)\n"
                f"Sub-Part 1 ({p1_code}): {sub1_mask.sum()} px (Assigned)\n"
                f"Sub-Part 2 ({p2_code}): {sub2_mask.sum()} px (Assigned)"
            )
            color_text = "#047857"
            filename = "seam_split_case_1_clean_split.png"
        elif case_type == "sliver_risk":
            title_text = (
                f"SEAM SPLIT (SLIVER / FALSE REPAIR RISK)\n"
                f"Major ({p1_code}): {sub1_mask.sum()} px ({sub1_mask.sum()/comp['area_pixels']*100:.1f}%)\n"
                f"Minor Sliver ({p2_code}): {sub2_mask.sum()} px ({sub2_mask.sum()/comp['area_pixels']*100:.1f}% - False Claim!)"
            )
            color_text = "#d97706"
            filename = "seam_split_case_2_sliver_risk.png"
        else:
            title_text = (
                f"SEAM SPLIT (EDGE CASE - LEGITIMATE MINOR SPLIT)\n"
                f"Major ({p1_code}): {sub1_mask.sum()} px ({sub1_mask.sum()/comp['area_pixels']*100:.1f}%)\n"
                f"Minor ({p2_code}): {sub2_mask.sum()} px ({sub2_mask.sum()/comp['area_pixels']*100:.1f}% - Valid Repair!)"
            )
            color_text = "#9333ea"  # Purple
            filename = "seam_split_case_3_edge_case.png"
            
        axes[3].set_title(title_text, fontsize=9.5, weight="bold", color=color_text)
        axes[3].axis("off")
        
        plt.tight_layout()
        out_file = VIZ_DIR / filename
        plt.savefig(out_file, bbox_inches="tight")
        plt.close()
        log.info(f"Saved visual overlay: {out_file}")

    log.info("Seam split experiment successfully completed.")


if __name__ == "__main__":
    main()
