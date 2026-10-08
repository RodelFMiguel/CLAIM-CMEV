# CLAIM-CMEV: Module 1 & Module 2 Team Handshake Guide

Welcome! This document provides a complete onboarding and handoff guide for taking over, extending, or consuming **Module 1 (Vehicle Part Segmentation)** and **Module 2 (Damage Segmentation & Part Matching)** in the CLAIM-CMEV system.

> **Status update (2026-10-08).** Since this guide was written, M2 and M3 gained real workers and the whole image branch can run on the trained models. Section 7's "primary next task" is done as a first version. Corrections made below are marked "(corrected 2026-10-08)". [CONTEXT.md](CONTEXT.md) has the current state and what was measured.

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

# Install the package with the model libraries (PyTorch and transformers 5.17 or later) and the test tools.
# (corrected 2026-10-08: a plain `pip install -e .` installs neither)
pip install -e ".[vision,dev]"
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
        └── damage-hitl/                      # (corrected 2026-10-08: the served version is damage-hitl/0.1.0-b3-compound)
            └── 0.1.0-b3-compound/
                ├── model.safetensors         # <-- [M2 SegFormer Champion, 181 MB]
                ├── config.json
                ├── config.yaml
                └── manifest.json
```

After unpacking the damage folder, complete it into a registry entry the M2 worker verifies (it adds the weight hash, the class numbering and the frame record, and keeps the trainer's manifest):

```python
from pipelines.vision.registry import adopt_damage_run
adopt_damage_run("artifacts/models/damage-hitl/0.1.0-b3-compound")
```

The served M1 model is the SegFormer-B3. The YOLO checkpoints are comparison results; no worker can load them.

> **Note:** `.gitignore` is configured to ignore `*.pt`, `*.safetensors`, and `/artifacts/models/**`, so you will not accidentally commit these large weights.

---

## 4. Key Code Locations & Entry Points

### Module 1: Parts Segmentation
- **Serving Adapter**: [`src/claim_cmev/vision/parts/adapter.py`](src/claim_cmev/vision/parts/adapter.py)
  - `run_parts_segmentation()`: Accepts photos, runs model inference, normalizes geometries, and generates visualization overlays.
- **Color Palette & Overlay Utilities**: [`src/claim_cmev/vision/palette.py`](src/claim_cmev/vision/palette.py)
- **Worker & Service Registration**: [`src/claim_cmev/worker.py`](src/claim_cmev/worker.py) (`role="parts"`), [`infra/Dockerfile.parts`](infra/Dockerfile.parts).
- **Specification**: [`docs/specs/module-01-vehicle-part-segmentation.md`](docs/specs/module-01-vehicle-part-segmentation.md) (corrected 2026-10-08)

### Module 2: Damage & Part Matching
- **Matching & Splitting Engine**: [`src/claim_cmev/vision/damage/assignment.py`](src/claim_cmev/vision/damage/assignment.py)
  - `assign_damage_to_part()`: extracts the damage regions of one photograph, assigns each against the M1 part mask and, only when `split_components` is on, applies the dual-gated seam splitting. `assign_component()` decides one region. (corrected 2026-10-08)
- **Serving Adapter and Worker**: [`src/claim_cmev/vision/damage/adapter.py`](src/claim_cmev/vision/damage/adapter.py) (`run_damage_segmentation`, `make_damage_handler`), served model in [`configs/models/damage.yaml`](configs/models/damage.yaml). (added 2026-10-08)
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
- **Specification**: [`docs/specs/module-03-part-summary-coverage.md`](docs/specs/module-03-part-summary-coverage.md)
- **Status (corrected 2026-10-08)**: a first real M3 worker exists in [`src/claim_cmev/vision/multiview/adapter.py`](src/claim_cmev/vision/multiview/adapter.py). It consumes `cmev.cmd.part-summary.v1`, reads the M1 predictions and M2 observations the command names, measures the screening signals on the real masks and publishes `cmev.evt.part-summarised.v1`. M3 does not estimate viewpoints: coverage is decided per part and side from a surveyor's identity and coverage confirmations plus the screen.
- **What remains**: calibrating the screening thresholds on real photographs, and the items listed in the specification's serving note.

### Secondary Next Tasks:
- **Module 4 (Page Reading)** and **Module 5 (Line-Item Extraction)**: read estimate pages and extract their line items ([`docs/specs/module-04-page-reading.md`](docs/specs/module-04-page-reading.md), [`docs/specs/module-05-line-item-extraction.md`](docs/specs/module-05-line-item-extraction.md)). (corrected 2026-10-08)
- **Module 8 (Cross-Check Assessment)**: Cross-reference declared repair line items against visual evidence from M2/M3 ([`docs/specs/module-08-consolidation-checks.md`](docs/specs/module-08-consolidation-checks.md), corrected 2026-10-08).
- **Module 9 (Review Workbench UI)**: Render parts and damage overlays in the browser for surveyor validation ([`docs/specs/ui_specification.md`](docs/specs/ui_specification.md)).
