# CLAIM-CMEV: Module 1 & Module 2 Team Handshake Guide

Welcome! This document provides a complete onboarding and handoff guide for taking over, extending, or consuming **Module 1 (Vehicle Part Segmentation)** and **Module 2 (Damage Segmentation & Part Matching)** in the CLAIM-CMEV system.

---

## 1. Executive Summary: What Has Been Built

1. **Module 1 (Vehicle Parts Segmentation)**:
   - Evaluated 3 architectures across held-out test splits.
   - **Champion Model**: **YOLOv8m-seg** achieved **0.7940 mIoU** (car body panels are discrete, rigid geometric objects).
   - **Alternative Transformer**: SegFormer-B3 (`0.8.0-b3-compound`) achieved **0.7303 mIoU**.
   - **Serving & Worker**: Implemented hardened batch adapter (`run_parts_segmentation`), contract-compliant schema validation (`part_mask_ref`), CPU fallback, and registered worker service in Docker Compose (`infra/Dockerfile.parts`).

2. **Module 2 (Damage Segmentation & Part Matching)**:
   - Evaluated 3 architectures across 132 test images.
   - **Champion Model**: **SegFormer-B3 (Compound Loss)** achieved **0.1576 foreground mIoU** (+42.5% over YOLO) and detected **469 damage regions** (vs YOLO's 202). Damage consists of diffuse surface textures (scratches, corrosion, stone chips) best captured by multi-scale attention.
   - **Deterministic Part Assignment**: Assigns damage components to parts via pixel containment with strict margin thresholds.
   - **Dual-Gated Seam Splitting & Sliver Pruning Policy**: Resolved multi-panel ambiguities while pruning minor slivers (<20% parent area) to prevent false downstream repair recommendations.

---

## 2. Git Branch & Environment Setup

### A. Recommended Git Branch
Both branches are pushed to GitHub:
- `origin/M1-Implementation`: Serving path hardening, CPU/GPU fallbacks, dependency fixes, Docker worker.
- `origin/M2-Implementation`: Damage assignment engine, dual-gated seam splitting, configs, benchmarks.

To have **both** M1 and M2 in your local environment, fetch and merge:
```bash
git fetch origin
git checkout M2-Implementation
git merge origin/M1-Implementation
```
*(There are zero merge conflicts between these branches).*

### B. Python Environment
Install project dependencies in editable mode:
```bash
# Activate your Python 3.12 virtual environment
source .venv/bin/activate

# Install package with core vision and contract dependencies
pip install -e .
```

---

## 3. Model Checkpoints (Google Drive Handoff)

Model weights are intentionally untracked in Git to prevent repository bloat and GitHub size limit violations. Download the three archives from Google Drive and unpack them into these exact project paths:

```
CLAIM-CMEV/
└── artifacts/
    └── models/
        ├── parts/
        │   ├── yolov8m-seg/
        │   │   └── weights/
        │   │       └── best.pt               # <-- [M1 YOLO Champion, 53 MB]
        │   └── 0.8.0-b3-compound/
        │       ├── model.safetensors         # <-- [M1 SegFormer-B3, 181 MB]
        │       ├── config.json
        │       └── manifest.json
        └── damage/
            └── 0.1.0-b3-compound/
                ├── model.safetensors         # <-- [M2 SegFormer Champion, 181 MB]
                ├── config.json
                └── manifest.json
```

> **Note:** `.gitignore` is configured to ignore `*.pt`, `*.safetensors`, and `/artifacts/models/**`, so you will not accidentally commit these large weights.

---

## 4. Key Code Locations & Entry Points

### Module 1: Parts Segmentation
- **Serving Adapter**: [`src/claim_cmev/vision/parts/adapter.py`](src/claim_cmev/vision/parts/adapter.py)
  - `run_parts_segmentation()`: Accepts photos, runs model inference, normalizes geometries, and generates visualization overlays.
- **Color Palette & Overlay Utilities**: [`src/claim_cmev/vision/palette.py`](src/claim_cmev/vision/palette.py)
- **Worker & Service Registration**: [`src/claim_cmev/worker.py`](src/claim_cmev/worker.py) (`role="parts"`), [`infra/Dockerfile.parts`](infra/Dockerfile.parts).
- **Specification**: [`docs/specs/module-01-parts-segmentation.md`](docs/specs/module-01-parts-segmentation.md)

### Module 2: Damage & Part Matching
- **Matching & Splitting Engine**: [`src/claim_cmev/vision/damage/assignment.py`](src/claim_cmev/vision/damage/assignment.py)
  - `assign_component()`: Evaluates damage masks against M1 part masks, applies containment margins, and executes dual-gated seam splitting.
- **Configuration & Thresholds**: [`configs/pipeline/m2_assignment.yaml`](configs/pipeline/m2_assignment.yaml)
  - Controls minimum pixel thresholds, containment boundaries (60%), ambiguity margin (20%), and dual-gating parameters (`split_min_pixels: 400`, `split_min_proportion: 0.20`).
- **Specification**: [`docs/specs/module-02-damage-segmentation.md`](docs/specs/module-02-damage-segmentation.md)

---

## 5. Dual-Gated Seam Splitting & Sliver Pruning Policy

In collision photos, damage often straddles panel seams (e.g. 52% bumper / 48% fender). 

```
                                  [Damage Component]
                                          |
                      -----------------------------------------
                     |                                         |
          [Lead Margin >= 20%]                       [Lead Margin < 20%]
                     |                                         |
           Assigned to Single Panel                   Boundary Ambiguity
         (Minor bleed absorbed)                                |
                                                  ----------------------------
                                                 |                            |
                                      split_components=false        split_components=true
                                                 |                            |
                                        Preserved Whole as           Dual-Gated Screening
                                        "unresolved"                 (>=400 px AND >=20% area)
                                        (Safe deferral to M9)                 |
                                                                    ---------------------
                                                                   |                     |
                                                               < 2 panels pass      >= 2 panels pass
                                                                   |                     |
                                                            Keep Whole &          Clean Partition
                                                            Unresolved           (Prune minor slivers)
```

- **Default Setting**: `split_components: false` in `configs/pipeline/m2_assignment.yaml`. Ambiguous cases are marked as `unresolved` (`ambiguous_between_parts`), deferring confirmation safely to surveyor review (Module 9).
- **Activating Dual-Gating**: Set `split_components: true`. Any touching panel with $\ge 400\text{ px}$ and $\ge 20\%$ parent area receives a partitioned damage observation; minor slivers (<20%) are **pruned** so undamaged panels are never flagged for false repair claims.
- **Reference Documentation**:
  - Full Report: [`artifacts/benchmarks/m2_seam_split_experiment.md`](artifacts/benchmarks/m2_seam_split_experiment.md)
  - Decision Tree Diagram: [`artifacts/benchmarks/visualizations/m2_seam_split_decision_tree.png`](artifacts/benchmarks/visualizations/m2_seam_split_decision_tree.png)

---

## 6. Verification: Quick Health Checks

To verify your local environment and model setup, run:

```bash
# 1. Run M1 serving adapter unit tests (fast, synthetic inputs)
pytest tests/unit/test_parts_adapter.py -v

# 2. Run M2 assignment and seam splitting unit tests (42 tests, ~1.2s)
pytest tests/unit/m2/test_m2_assignment.py tests/unit/m2/test_m2_regions_config.py -v

# 3. (Optional) Run the live SegFormer test against the unpacked checkpoint
pytest tests/unit/test_parts_segmenter_live.py -v

# 4. (Optional) Re-run the full seam splitting experiment across all 132 test images
python pipelines/vision/experiment_seam_split.py
```

---

## 7. Next Steps: Where to Continue Development

### Primary Next Task: Module 3 (Part Summary & Photographic Coverage Gating)
- **Specification**: [`docs/specs/module-03-part-summary-and-coverage.md`](docs/specs/module-03-part-summary-and-coverage.md)
- **What to build**:
  1. Consume `cmev.evt.parts-segmented.v1` (from M1) and `cmev.evt.damage-localized.v1` (from M2).
  2. Aggregate part observations across multiple claim photos.
  3. Determine vehicle viewpoint coverage (front, rear, left, right) and flag missing photo coverage.
  4. Emit `cmev.evt.part-summary-created.v1` event to feed downstream Module 8 assessment checks.

### Secondary Next Tasks:
- **Module 4 (Document Extraction)**: Extract estimate line items from scanned PDF estimates ([`docs/specs/module-04-document-extraction.md`](docs/specs/module-04-document-extraction.md)).
- **Module 8 (Cross-Check Assessment)**: Cross-reference declared repair line items against visual evidence from M2/M3 ([`docs/specs/module-08-damage-to-repair-crosscheck.md`](docs/specs/module-08-damage-to-repair-crosscheck.md)).
- **Module 9 (Review Workbench UI)**: Render parts and damage overlays in the browser for surveyor validation ([`docs/specs/ui_specification.md`](docs/specs/ui_specification.md)).
