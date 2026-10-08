"""Render architectural diagrams to PNG images for document embedding."""
from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as patches

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "artifacts/benchmarks/visualizations"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def create_diagram_1():
    """Diagram 1: M1 & M2 Deterministic Part Containment Assignment Flow."""
    fig, ax = plt.subplots(figsize=(12, 6), dpi=180)
    ax.axis("off")

    def box(x, y, w, h, text, color="#2b5b84", text_color="white", fontsize=10, bold=True):
        rect = patches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.03", ec="none", fc=color, lw=2
        )
        ax.add_patch(rect)
        weight = "bold" if bold else "normal"
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=text_color,
                fontsize=fontsize, weight=weight, multialignment="center")

    def arrow(x0, y0, x1, y1, label=""):
        ax.annotate(
            label, xy=(x1, y1), xytext=(x0, y0),
            arrowprops=dict(facecolor="#555555", edgecolor="#555555", width=1.5, headwidth=8, shrink=0.05),
            ha="center", va="bottom", fontsize=8.5, color="#333333"
        )

    # Inputs
    box(0.05, 0.70, 0.22, 0.20, "RGB Vehicle Photo\n(EXIF decoded,\nlongest_edge_pad 512x512)", "#333333")
    
    # Branches
    box(0.38, 0.75, 0.24, 0.18, "Module 1 (M1)\nVehicle Part Segmentation\n(SegFormer-B3 / YOLOv8m)", "#1f77b4")
    box(0.38, 0.45, 0.24, 0.18, "Module 2 (M2)\nDamage Segmentation\n(SegFormer-B3 Compound)", "#d62728")

    arrow(0.27, 0.80, 0.38, 0.84)
    arrow(0.27, 0.75, 0.38, 0.54)

    # M1 Mask & M2 Components
    box(0.68, 0.75, 0.26, 0.18, "M1 Part Mask [512, 512]\n21 Vehicle Body Panels\n(side = unknown)", "#2ca02c")
    box(0.68, 0.45, 0.26, 0.18, "M2 Damage Components\n8 Connected Regions\n(min_pixels, confidence floor)", "#ff7f0e")

    arrow(0.62, 0.84, 0.68, 0.84)
    arrow(0.62, 0.54, 0.68, 0.54)

    # Core Deterministic Assignment Engine
    box(0.35, 0.08, 0.35, 0.25, "Deterministic Containment Engine\n(assignment.py)\n\ncontainment(part) = |Damage ∩ Part| / |Damage|\n• Primary ≥ 60%  • Margin ≥ 20%\n• Background ≤ 50%  • No Resampling", "#4a148c")

    arrow(0.81, 0.75, 0.60, 0.33)
    arrow(0.81, 0.45, 0.55, 0.33)

    # Output decisions
    box(0.76, 0.18, 0.20, 0.12, "ASSIGNED (78.2%)\nLinked Part Code", "#2e7d32")
    box(0.76, 0.04, 0.20, 0.12, "UNRESOLVED (21.8%)\nCandidates + Reason", "#c62828")

    arrow(0.70, 0.22, 0.76, 0.24)
    arrow(0.70, 0.13, 0.76, 0.10)

    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.0)
    plt.title("CLAIM-CMEV: M1 & M2 Multi-Model Part Containment Workflow", fontsize=14, weight="bold", pad=15)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "diagram_1_m2_assignment_pipeline.png", bbox_inches="tight")
    plt.close()
    print("Saved diagram 1")


def create_diagram_2():
    """Diagram 2: Downstream Cascading & Handling of Unresolved Evidence."""
    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=180)
    ax.axis("off")

    def box(x, y, w, h, text, color="#2b5b84", text_color="white", fontsize=9.5, bold=True):
        rect = patches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.03", ec="none", fc=color, lw=2
        )
        ax.add_patch(rect)
        weight = "bold" if bold else "normal"
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=text_color,
                fontsize=fontsize, weight=weight, multialignment="center")

    def arrow(x0, y0, x1, y1, label=""):
        ax.annotate(
            label, xy=(x1, y1), xytext=(x0, y0),
            arrowprops=dict(facecolor="#555555", edgecolor="#555555", width=1.5, headwidth=8, shrink=0.05),
            ha="center", va="center", fontsize=8.5, color="#333333"
        )

    box(0.04, 0.45, 0.22, 0.28, "M2 Observation Produced\n\nStatus: UNRESOLVED\nReasons:\n• ambiguous_between_parts\n• below_containment_threshold\n• mostly_background", "#b71c1c")

    box(0.33, 0.70, 0.28, 0.22, "Module 3 (Multiview Summary)\n\n• Grouped by observation_id\n• Retains ranked candidates list\n• Does NOT count as panel coverage", "#0d47a1")
    box(0.33, 0.25, 0.28, 0.22, "Module 8 (Consolidation Checks)\n\n• Rule R7/R8 Evaluation:\n  photographic_check = insufficient_evidence\n• NEVER marked as 'unsupported'!", "#e65100")

    arrow(0.26, 0.65, 0.33, 0.78)
    arrow(0.26, 0.50, 0.33, 0.36)

    arrow(0.47, 0.70, 0.47, 0.47)

    box(0.68, 0.45, 0.28, 0.32, "Module 9 (Surveyor Workbench)\n\n• Photo overlay highlights damage\n• Displays ranked candidates chips\n• Surveyor Action:\n  1-Click 'Confirm Part'\n  Triggers Revision 2 recompute!\n• Does NOT block finalization", "#1b5e20")

    arrow(0.61, 0.36, 0.68, 0.55)

    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.0)
    plt.title("Downstream Safe Cascade: Handling Unresolved Evidence Across M3, M8, and M9", fontsize=14, weight="bold", pad=15)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "diagram_2_downstream_cascade.png", bbox_inches="tight")
    plt.close()
    print("Saved diagram 2")


def create_diagram_3():
    """Diagram 3: SegFormer Dense Attention vs YOLO Bounding Box Proposal."""
    fig, ax = plt.subplots(figsize=(12, 6), dpi=180)
    ax.axis("off")

    def box(x, y, w, h, text, color="#2b5b84", text_color="white", fontsize=9.5, bold=True):
        rect = patches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.03", ec="none", fc=color, lw=2
        )
        ax.add_patch(rect)
        weight = "bold" if bold else "normal"
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=text_color,
                fontsize=fontsize, weight=weight, multialignment="center")

    def arrow(x0, y0, x1, y1):
        ax.annotate(
            "", xy=(x1, y1), xytext=(x0, y0),
            arrowprops=dict(facecolor="#555555", edgecolor="#555555", width=1.5, headwidth=8, shrink=0.05),
        )

    # SegFormer Branch (Left)
    box(0.04, 0.85, 0.42, 0.10, "SegFormer-B3: Dense Semantic Transformer", "#1b5e20", fontsize=11)
    box(0.04, 0.65, 0.42, 0.15, "Hierarchical Mix-Transformer (MiT-B3)\nMulti-scale overlapping patch token attention\nDense per-pixel feature extraction across 4 stages", "#2e7d32")
    box(0.04, 0.40, 0.42, 0.18, "All-MLP Pixel Decoder\nDense per-pixel classification\nNo bounding-box proposal bottleneck", "#388e3c")
    box(0.04, 0.10, 0.42, 0.22, "OUTCOME ON M2 DAMAGE:\n• Scratches: 0.1986 IoU (+101% vs YOLO)\n• Dents: 0.3323 IoU (+36% vs YOLO)\n• Corrosion & Chips detected reliably\n• 469 damage regions extracted", "#1b5e20")

    arrow(0.25, 0.65, 0.25, 0.58)
    arrow(0.25, 0.40, 0.25, 0.32)

    # YOLO Branch (Right)
    box(0.54, 0.85, 0.42, 0.10, "YOLOv8m-seg: Instance Proposal CNN", "#0d47a1", fontsize=11)
    box(0.54, 0.65, 0.42, 0.15, "CSPDarknet Backbone + C2f Blocks\nMulti-scale anchor-free detection head\nObject bounding-box candidate proposals", "#1565c0")
    box(0.54, 0.40, 0.42, 0.18, "Prototype Mask Head\nRequires distinct rectangular object boundary\nSuppresses non-object textures & thin lines", "#1976d2")
    box(0.54, 0.10, 0.42, 0.22, "OUTCOME ON M2 DAMAGE:\n• Scratches: 0.0987 IoU (misses thin lines)\n• Corrosion: 0.0000 IoU (100% false negative)\n• Merges complex wrecks into 1 blob\n• Only 202 damage regions (misses 57%)", "#b71c1c")

    arrow(0.75, 0.65, 0.75, 0.58)
    arrow(0.75, 0.40, 0.75, 0.32)

    ax.set_xlim(0, 1.0)
    ax.set_ylim(0, 1.0)
    plt.title("Architectural Comparison: Why SegFormer Decisively Outperforms YOLO on Damage", fontsize=14, weight="bold", pad=15)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "diagram_3_segformer_vs_yolo_architecture.png", bbox_inches="tight")
    plt.close()
    print("Saved diagram 3")


if __name__ == "__main__":
    create_diagram_1()
    create_diagram_2()
    create_diagram_3()
