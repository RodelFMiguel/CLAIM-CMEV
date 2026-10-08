# Manual vision training notebooks

| Task | Notebook (including the training plan) |
| --- | --- |
| M1: 21 vehicle parts plus background | [M1_HITL_parts_training.ipynb](M1_HITL_parts_training.ipynb) |
| M2: 8 HITL damage types plus background | [M2_HITL_damage_training.ipynb](M2_HITL_damage_training.ipynb) |
| M2: missed-label review and tiny training-subset diagnostic | [M2_HITL_failure_audit.ipynb](M2_HITL_failure_audit.ipynb) |
| M2: 6 CarDD damage types plus background (the specified M2 dataset) | [M2_CarDD_damage_training.ipynb](M2_CarDD_damage_training.ipynb) |

The CarDD notebook reads the original release from `data/raw/CarDD_release/` (COCO annotation files for train, val and test, plus their images), prepares masks once into `data/processed/cardd/v1`, and saves runs under `artifacts/models/damage-cardd/`. It keeps CarDD's official splits, never reads HITL labels, and reports against the specification target of 0.45 test mIoU. Its scores are not comparable with HITL runs.

The M2 worker serves only a model trained in the M1 frame at 512. The CarDD notebook's default recipe, `damage_focus`, trains in the centred frame at 640 and cannot be served. Choose `RECIPE = "serving_512"` for a run that can, then set `EXPORT_FOR_SERVING = True` in the export section to write it to the registry as `damage-cardd/<name>`. The existing run `7981a76f` (validation mIoU 0.737) is a centred-frame run. Serving an exported model is a separate step: name it in `configs/models/damage.yaml`.

For a stalled M2 run, use the failure-audit notebook to rank missed flaking/paint-chip examples, export 24 review pages and a CSV, then optionally fit 4–8 training images you explicitly mark clean. It loads the reference checkpoint independently of the main notebook. Same-image fitting scores are diagnostics, never held-out validation results. Outputs go to `artifacts/diagnostics/m2/`; validation/test examples cannot enter its fitting test.

M1 uses an ImageNet-pretrained SegFormer-B2 encoder. M2 now defaults to the `damage_focus` candidate. It builds on `focused_640` (ADE20K-pretrained SegFormer-B2 encoder/decoder, a new nine-class classifier, adaptive decoder BatchNorm, mixed full-image/random/damage-focused training crops, validation-plateau learning-rate reduction) and adds rare-class oversampling, a damage-weighted cross-entropy, stochastic depth, decoder dropout, stronger weight decay and EMA weight averaging. After training, a background offset can be tuned on validation and is reused unchanged on test. Its `previous` preset preserves the completed 0.2224 validation mIoU reference configuration; intermediate presets isolate changes, and optional rare-class sampling and UperNet–ConvNeXt support further comparisons. These new settings are experiments, not demonstrated accuracy improvements. The duplicate Markdown copies have been removed; plans live inside the notebooks.

Editable configuration also selects SegFormer variants, DeepLabV3 with ResNet-50/101, or a compatible Hugging Face semantic segmentation model. CUDA, Apple MPS and CPU are selected automatically in that order; an explicit device override is available.

Create/select a Python kernel with [requirements-training.txt](../requirements-training.txt) and the application package (`pip install -e ".[vision]"`), then open either notebook from this checkout and run cells in order. An optional installation cell is disabled until explicitly enabled. On NVIDIA hardware, select the PyTorch build appropriate for the installed CUDA environment. The user supplied the original HITL archive and extracted Supervisely data under `data/raw/` on 2026-09-30; on a different machine, follow [dataset acquisition](../docs/dataset-downloads.md) or copy these inputs first.

The current Mac has a prepared Python 3.12 `venv` (the earlier `.venv` path no longer exists). Start from the repository root:

```sh
venv/bin/python -m jupyterlab notebooks/
```

Select that environment's Python kernel in Jupyter or your IDE. On a new machine, create a Python 3.12 environment and install `requirements-training.txt` and the application package first. Both pin transformers 5.17 or later, the version the served M1 checkpoint was saved with. Runs saved under transformers 4 still reload. The initial SegFormer-B2 encoder is cached under `artifacts/models/.cache/huggingface/` on this Mac; the first use on a new machine needs network access or a copied cache. Training remains a manual cell execution.

In VS Code, select **Select Kernel → Select Another Kernel → Jupyter Kernels → CLAIM-CMEV (Python 3.12)**. This named kernel points to `venv/bin/python`. If the environment path changes, register it again using the new environment's interpreter:

```sh
venv/bin/python -m ipykernel install --user --name claim-cmev --display-name "CLAIM-CMEV (Python 3.12)"
```

Run **Developer: Reload Window** from the Command Palette if VS Code retains an old kernel selection. A prompt to install `ipykernel` with `/opt/homebrew/bin/python3.12` refers to the base interpreter; select the project kernel instead. You can verify the selected interpreter in a cell with `import sys; print(sys.executable)`.

Both notebooks prepare the **union of both HITL subsets** with identical configuration in `data/processed/hitl/v3` (`nested` overlap policy, split adopted from `data/splits/parts/0.1.1`). v3 has the masks of v2 and another split: every parts photograph, and the same photograph in the damage subset, takes its partition from the published parts split that the script pipeline and the served checkpoint use (parts 706/145/147). The seed 20260922 and the 70/15/15 ratios place only the 373 photographs that exist in the damage subset alone. v2 drew its own split, and 109 parts photographs it trained on are in the published test partition, so v2 and v3 scores are not comparable; runs saved on v2 are evaluated on v2 (`PREPARED_DIR` v2, `SPLIT_FROM = None`). v2 has the same images and split membership as v1 but different masks: a polygon at least 80% inside a larger different-class polygon (window in door, plate in bumper, scratch in dent) keeps its pixels instead of being ignored. M1 retains its `baseline`, `fixed` and `fixed_aug` presets and now trains in the M1 worker's model frame (`frame="serving"`: photograph top-left, black padding); its last section can export a run as a registry candidate for the worker. The M2 notebooks keep the centred frame. M2 has its own staged presets documented in its configuration cell. Class titles identify each task because historical folder names were swapped. Grouping includes exact bytes, decoded pixels and optional vehicle/source groups; review the near-duplicate audit and provide missing groups before freezing experiments. A changed source or preparation configuration requires a new prepared-data directory. Reservations for the separate damage-to-part assignment study must be shared across both models.

Use validation curves, per-class metrics, pixel confusion matrices and prediction overlays to select a model. Test evaluation is behind `FINAL_EVAL=False`; enable it only after selection. Compare runs of the same task and frozen split. M2 uses the user's explicitly selected HITL taxonomy and does not inherit the CarDD target or imply runtime compatibility. Neither model resolves left/right side.

Model checkpoints, config and histories go to `artifacts/models/parts/<run_id>/` or `artifacts/models/damage-hitl/<run_id>/`; plots and reports go to `artifacts/evaluation/<run_id>/`. New training executions create isolated runs. Saved checkpoints can be reloaded for evaluation; interrupted-run optimizer resume is not exposed by these notebooks. Reusable conversion/training/evaluation code lives in [pipelines/vision](../pipelines/vision).

Clear notebook outputs before committing. Keep datasets, weights and generated exports in their ignored locations. These notebooks are experiment entrypoints, not production workers or evidence that M1/M2 acceptance targets have been achieved. An exported M1 run is a candidate registry entry; serving it is a separate change to `configs/models/parts.yaml`.
