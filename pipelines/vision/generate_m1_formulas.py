"""Render mathematical formulas and quantitative benchmark charts for M1 Report."""
from __future__ import annotations

import json
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "artifacts/benchmarks/visualizations/m1_parts"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def render_formula_1():
    """Formula 1: Weighted Cross Entropy with Inverse Sqrt Frequency."""
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=200)
    ax.axis("off")

    # Background card
    rect = patches.FancyBboxPatch(
        (0.02, 0.05), 0.96, 0.90, boxstyle="round,pad=0.03", ec="#1f497d", fc="#f8fafc", lw=2
    )
    ax.add_patch(rect)

    # Header banner
    banner = patches.FancyBboxPatch(
        (0.02, 0.76), 0.96, 0.19, boxstyle="round,pad=0.02", ec="none", fc="#1f497d"
    )
    ax.add_patch(banner)
    ax.text(0.5, 0.855, "MATHEMATICAL FORMULATION 1: WEIGHTED CROSS-ENTROPY LOSS",
            ha="center", va="center", color="white", fontsize=11.5, weight="bold")

    # Formula 1a: Loss equation
    eq1 = r"$\mathcal{L}_{\mathrm{WCE}} = - \frac{1}{N} \sum_{i=1}^{N} \sum_{c=0}^{C-1} w_c \cdot y_{i,c} \log(\hat{p}_{i,c})$"
    ax.text(0.5, 0.62, eq1, ha="center", va="center", fontsize=14, color="#0f172a")

    # Formula 1b: Weight equation
    eq2 = r"$w_c = \mathrm{clamp}\left( \sqrt{ \frac{\sum_{j=0}^{C-1} N_j + \epsilon}{N_c + \epsilon} }, \; w_{\mathrm{min}} = 0.10, \; w_{\mathrm{max}} = 10.0 \right)$"
    ax.text(0.5, 0.38, eq2, ha="center", va="center", fontsize=12.5, color="#1e3a8a")

    # Explanation notes
    notes = (
        "• Background class (c=0) comprises >70% of total pixels: down-weighted to w_0 = 0.10\n"
        "• Thin/rare panels (roof, rocker-panel, licence-plate) boosted up to 10.0x to eliminate gradient starvation\n"
        "• Inverse square-root dampens extreme inverse-frequency ratios, stabilizing AdamW optimizer training"
    )
    ax.text(0.06, 0.18, notes, ha="left", va="center", fontsize=8.5, color="#334155", linespacing=1.4)

    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.0)
    plt.tight_layout()
    out_path = OUT_DIR / "m1_formula_1_cross_entropy_weights.png"
    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def render_formula_2():
    """Formula 2: Multiclass Soft Dice Loss."""
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=200)
    ax.axis("off")

    rect = patches.FancyBboxPatch(
        (0.02, 0.05), 0.96, 0.90, boxstyle="round,pad=0.03", ec="#1e5f74", fc="#f8fafc", lw=2
    )
    ax.add_patch(rect)

    banner = patches.FancyBboxPatch(
        (0.02, 0.76), 0.96, 0.19, boxstyle="round,pad=0.02", ec="none", fc="#1e5f74"
    )
    ax.add_patch(banner)
    ax.text(0.5, 0.855, "MATHEMATICAL FORMULATION 2: MULTICLASS SOFT DICE LOSS",
            ha="center", va="center", color="white", fontsize=11.5, weight="bold")

    eq = r"$\mathcal{L}_{\mathrm{Dice}} = 1 - \frac{1}{|C_{\mathrm{fg}}|} \sum_{c \in C_{\mathrm{fg}}} \frac{2 \sum_{i=1}^{N} \hat{p}_{i,c} \cdot y_{i,c} + \epsilon}{\sum_{i=1}^{N} \hat{p}_{i,c}^2 + \sum_{i=1}^{N} y_{i,c}^2 + \epsilon}$"
    ax.text(0.5, 0.52, eq, ha="center", va="center", fontsize=14, color="#0f172a")

    notes = (
        r"• $C_{\mathrm{fg}} = \{1, 2, \dots, 21\}$: Excludes background ($c=0$) to prevent massive background overlap dominance" + "\n"
        r"• Soft formulation allows continuous gradients: $\frac{\partial \mathcal{L}_{\mathrm{Dice}}}{\partial \hat{p}_{i,c}}$ backpropagates directly into token representations" + "\n"
        r"• Invariant to panel pixel size: small panels (rocker-panel) contribute equally to loss gradient as large panels (hood)"
    )
    ax.text(0.06, 0.20, notes, ha="left", va="center", fontsize=8.5, color="#334155", linespacing=1.4)

    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.0)
    plt.tight_layout()
    out_path = OUT_DIR / "m1_formula_2_soft_dice_loss.png"
    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def render_formula_3():
    """Formula 3: Compound Loss Function."""
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=200)
    ax.axis("off")

    rect = patches.FancyBboxPatch(
        (0.02, 0.05), 0.96, 0.90, boxstyle="round,pad=0.03", ec="#4a148c", fc="#f8fafc", lw=2
    )
    ax.add_patch(rect)

    banner = patches.FancyBboxPatch(
        (0.02, 0.76), 0.96, 0.19, boxstyle="round,pad=0.02", ec="none", fc="#4a148c"
    )
    ax.add_patch(banner)
    ax.text(0.5, 0.855, "MATHEMATICAL FORMULATION 3: COMPOUND LOSS OBJECTIVE",
            ha="center", va="center", color="white", fontsize=11.5, weight="bold")

    eq = r"$\mathcal{L}_{\mathrm{total}} = \mathcal{L}_{\mathrm{WCE}} + \lambda_{\mathrm{Dice}} \cdot \mathcal{L}_{\mathrm{Dice}}, \quad \text{with } \lambda_{\mathrm{Dice}} = 1.0$"
    ax.text(0.5, 0.53, eq, ha="center", va="center", fontsize=15, color="#2e1065")

    notes = (
        "• Dual Optimization Mechanism:\n"
        "   1. Weighted Cross-Entropy provides smooth pixel-level probability convergence and steep gradients early in training.\n"
        "   2. Soft Dice Loss directly maximizes geometric region overlap and penalizes boundary bleeding across seam lines.\n"
        "• Empirical Result: Lifted SegFormer-B0 from 0.4664 mIoU (3 floor violations) to 0.6034 mIoU (0 floor violations)."
    )
    ax.text(0.06, 0.20, notes, ha="left", va="center", fontsize=8.5, color="#334155", linespacing=1.3)

    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.0)
    plt.tight_layout()
    out_path = OUT_DIR / "m1_formula_3_compound_loss.png"
    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def render_formula_4():
    """Formula 4: Evaluation Metrics & Acceptance Thresholds."""
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=200)
    ax.axis("off")

    rect = patches.FancyBboxPatch(
        (0.02, 0.04), 0.96, 0.92, boxstyle="round,pad=0.03", ec="#1b5e20", fc="#f8fafc", lw=2
    )
    ax.add_patch(rect)

    banner = patches.FancyBboxPatch(
        (0.02, 0.79), 0.96, 0.17, boxstyle="round,pad=0.02", ec="none", fc="#1b5e20"
    )
    ax.add_patch(banner)
    ax.text(0.5, 0.875, "MATHEMATICAL FORMULATION 4: EVALUATION METRICS & INVARIANTS",
            ha="center", va="center", color="white", fontsize=11.5, weight="bold")

    eq1 = r"$\mathrm{IoU}_c = \frac{\sum_i \hat{y}_{i,c} \cdot y_{i,c}}{\sum_i \hat{y}_{i,c} + \sum_i y_{i,c} - \sum_i \hat{y}_{i,c} \cdot y_{i,c}} = \frac{\mathrm{TP}_c}{\mathrm{TP}_c + \mathrm{FP}_c + \mathrm{FN}_c}$"
    ax.text(0.5, 0.67, eq1, ha="center", va="center", fontsize=13, color="#0f172a")

    eq2 = r"$\mathrm{mIoU}_{\mathrm{fg}} = \frac{1}{21} \sum_{c=1}^{21} \mathrm{IoU}_c \geq 0.60, \qquad \mathrm{mIoU}_{\mathrm{panels}} = \frac{1}{10} \sum_{p \in \mathcal{P}} \mathrm{IoU}_p, \quad \forall p \in \mathcal{P}: \mathrm{IoU}_p \geq 0.40$"
    ax.text(0.5, 0.45, eq2, ha="center", va="center", fontsize=12, color="#047857")

    eq3 = r"$\mathrm{Precision}_c = \frac{\mathrm{TP}_c}{\mathrm{TP}_c + \mathrm{FP}_c}, \quad \mathrm{Recall}_c = \frac{\mathrm{TP}_c}{\mathrm{TP}_c + \mathrm{FN}_c}, \quad F_{1,c} = \frac{2 \cdot \mathrm{Precision}_c \cdot \mathrm{Recall}_c}{\mathrm{Precision}_c + \mathrm{Recall}_c}$"
    ax.text(0.5, 0.26, eq3, ha="center", va="center", fontsize=11.5, color="#1e3a8a")

    notes = (
        r"• $\mathcal{P} = \{\text{hood, trunk, front-door, back-door, front-bumper, back-bumper, quarter-panel, fender, roof, rocker-panel}\}$" + "\n"
        "• Contract Invariant: side = 'unknown' (reason: 'hitl_labels_unsided'). Left/right orientation is never inferred by M1."
    )
    ax.text(0.06, 0.10, notes, ha="left", va="center", fontsize=8.0, color="#334155", linespacing=1.3)

    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.0)
    plt.tight_layout()
    out_path = OUT_DIR / "m1_formula_4_evaluation_metrics.png"
    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def render_chart_1_progression():
    """Chart 1: Foreground mIoU and Panels mIoU progression across all 10 M1 models."""
    models_labels = [
        "0.1.0 B0\nBaseline CE",
        "0.2.0 B0\nWeighted CE",
        "0.3.0 B0\nCompound",
        "0.4.0 B0\nAug+Compound",
        "0.7.0 B2\nVanilla CE",
        "0.5.0 B2\nCompound",
        "0.6.0 B2\nAug+Compound",
        "0.8.0 B3\nCompound",
        "DeepLabV3\nResNet50",
        "YOLOv8m-seg\n(Anchor-Free)",
    ]

    fg_miou = [0.4664, 0.5897, 0.6034, 0.5986, 0.6914, 0.7075, 0.7062, 0.7303, 0.7284, 0.7940]
    panels_miou = [0.4710, 0.5831, 0.5945, 0.5907, 0.6789, 0.6914, 0.6916, 0.7254, 0.7357, 0.8086]

    x = np.arange(len(models_labels))
    width = 0.38

    fig, ax = plt.subplots(figsize=(14, 6.5), dpi=200)

    rects1 = ax.bar(x - width/2, fg_miou, width, label="Foreground mIoU (21 Classes)", color="#2b5b84", edgecolor="#1a365d")
    rects2 = ax.bar(x + width/2, panels_miou, width, label="Supported Panels mIoU (10 Panels)", color="#38a169", edgecolor="#22543d")

    # Add threshold lines
    ax.axhline(0.60, color="#e53e3e", linestyle="--", linewidth=1.5, label="Target Overall Floor (≥ 0.60 mIoU)")
    ax.axhline(0.40, color="#dd6b20", linestyle=":", linewidth=1.5, label="Per-Panel Floor Contract (≥ 0.40 mIoU)")

    ax.set_ylabel("Mean IoU Score", fontsize=12, weight="bold")
    ax.set_title("M1 Vehicle Part Segmentation: Ablation & Architecture Benchmark Progression", fontsize=14, weight="bold", pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(models_labels, fontsize=9.5, rotation=0)
    ax.set_ylim(0, 0.90)
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    ax.legend(loc="upper left", fontsize=10, framealpha=0.95)

    # Attach labels on bars
    for bar in rects1:
        yval = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2.0, yval + 0.008, f"{yval:.4f}", ha='center', va='bottom', fontsize=7.5, rotation=90)
    for bar in rects2:
        yval = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2.0, yval + 0.008, f"{yval:.4f}", ha='center', va='bottom', fontsize=7.5, rotation=90, color="#1c4532", weight="bold")

    # Add phase bracket notes
    ax.annotate("Loss Ablations (B0)", xy=(1.5, 0.05), xytext=(1.5, 0.02),
                ha="center", fontsize=10, weight="bold", color="#4a5568")
    ax.annotate("Capacity Scaling (B2)", xy=(5.0, 0.05), xytext=(5.0, 0.02),
                ha="center", fontsize=10, weight="bold", color="#4a5568")
    ax.annotate("Advanced Models (B3, ResNet, YOLO)", xy=(8.0, 0.05), xytext=(8.0, 0.02),
                ha="center", fontsize=10, weight="bold", color="#4a5568")

    plt.tight_layout()
    out_path = OUT_DIR / "m1_chart_1_ablation_progression.png"
    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def render_chart_2_panel_comparison():
    """Chart 2: Grouped Bar Chart comparing per-panel IoU across key models."""
    panels = ["hood", "trunk", "front-door", "back-door", "front-bumper", "back-bumper", "quarter-panel", "fender", "roof", "rocker-panel"]
    
    # Data from models
    b0_base = [0.7544, 0.5143, 0.5191, 0.2708, 0.6958, 0.6846, 0.4682, 0.5484, 0.1316, 0.1224]
    b0_comp = [0.7852, 0.6267, 0.5505, 0.4071, 0.7399, 0.7650, 0.5438, 0.5904, 0.4643, 0.4727]
    b3_comp = [0.8656, 0.7712, 0.6695, 0.6103, 0.8234, 0.8568, 0.7242, 0.7471, 0.5914, 0.5941]
    resnet50 = [0.8830, 0.7274, 0.7271, 0.6850, 0.8054, 0.8433, 0.7177, 0.7882, 0.6087, 0.5712]
    yolo_seg = [0.9127, 0.8390, 0.7947, 0.7918, 0.8423, 0.8897, 0.8492, 0.8719, 0.6694, 0.6254]

    x = np.arange(len(panels))
    width = 0.16

    fig, ax = plt.subplots(figsize=(15, 7), dpi=200)

    ax.bar(x - 2*width, b0_base, width, label="SegFormer-B0 Baseline (Vanilla CE)", color="#cbd5e1", edgecolor="#94a3b8")
    ax.bar(x - 1*width, b0_comp, width, label="SegFormer-B0 Compound Loss", color="#93c5fd", edgecolor="#3b82f6")
    ax.bar(x, b3_comp, width, label="SegFormer-B3 Compound Loss", color="#1d4ed8", edgecolor="#1e3a8a")
    ax.bar(x + 1*width, resnet50, width, label="DeepLabV3-ResNet50 ASPP", color="#8b5cf6", edgecolor="#6d28d9")
    ax.bar(x + 2*width, yolo_seg, width, label="YOLOv8m-seg (Best Overall)", color="#10b981", edgecolor="#047857")

    # Contract floor line
    ax.axhline(0.40, color="#ef4444", linestyle="--", linewidth=1.8, label="Contract Floor Target (IoU ≥ 0.40)")

    ax.set_ylabel("Class IoU", fontsize=12, weight="bold")
    ax.set_title("Per-Panel IoU Comparison Across 10 Contract-Supported Vehicle Body Panels", fontsize=14, weight="bold", pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(panels, fontsize=10.5, weight="bold", rotation=20)
    ax.set_ylim(0, 1.0)
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    ax.legend(loc="upper left", fontsize=10, framealpha=0.95)

    # Annotate the failing panels in B0
    ax.annotate("FAILED (0.2708)", xy=(3 - 2*width, 0.2708), xytext=(3 - 2*width - 0.2, 0.35),
                arrowprops=dict(facecolor="#ef4444", shrink=0.05, width=1, headwidth=5),
                fontsize=8, weight="bold", color="#b91c1c")
    ax.annotate("FAILED (0.1316)", xy=(8 - 2*width, 0.1316), xytext=(8 - 2*width - 0.2, 0.25),
                arrowprops=dict(facecolor="#ef4444", shrink=0.05, width=1, headwidth=5),
                fontsize=8, weight="bold", color="#b91c1c")
    ax.annotate("FAILED (0.1224)", xy=(9 - 2*width, 0.1224), xytext=(9 - 2*width - 0.2, 0.25),
                arrowprops=dict(facecolor="#ef4444", shrink=0.05, width=1, headwidth=5),
                fontsize=8, weight="bold", color="#b91c1c")

    plt.tight_layout()
    out_path = OUT_DIR / "m1_chart_2_panel_iou_comparison.png"
    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    render_formula_1()
    render_formula_2()
    render_formula_3()
    render_formula_4()
    render_chart_1_progression()
    render_chart_2_panel_comparison()
