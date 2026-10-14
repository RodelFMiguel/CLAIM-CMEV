# Manual model training notebooks

| Task | Notebook (including the training plan) |
| --- | --- |
| M1: 21 vehicle parts plus background | [M1_HITL_parts_training.ipynb](M1_HITL_parts_training.ipynb) |
| M2: 8 HITL damage types plus background | [M2_HITL_damage_training.ipynb](M2_HITL_damage_training.ipynb) |
| M2: missed-label review and tiny training-subset diagnostic | [M2_HITL_failure_audit.ipynb](M2_HITL_failure_audit.ipynb) |
| M2: 6 CarDD damage types plus background (the specified M2 dataset) | [M2_CarDD_damage_training.ipynb](M2_CarDD_damage_training.ipynb) |
| M5: LayoutLMv3 line items from M4 OCR output | [M5_Document_Worker.ipynb](M5_Document_Worker.ipynb) |
| M6: pen-mark detection and row linking from M4 pages | [M6_Document_Worker.ipynb](M6_Document_Worker.ipynb) |

The CarDD notebook reads the original release from `data/raw/CarDD_release/` (COCO annotation files for train, val and test, plus their images), prepares masks once into `data/processed/cardd/v1`, and saves runs under `artifacts/models/damage-cardd/`. It keeps CarDD's official splits, never reads HITL labels, and reports against the specification target of 0.45 test mIoU. Its scores are not comparable with HITL runs.

All three training notebooks now default to the serving frame at 512 pixels: top-left placement, black padding and the worker's normalisation. M2 HITL and CarDD use `RECIPE = "serving_512"`; M1 keeps `fixed_aug` in the serving frame. Their preflight check refuses incompatible architectures, sizes, frames or normalisation before fitting, and checks M1/M2 geometry alignment. Historical centred-frame runs remain reloadable but cannot be exported through this route.

For a stalled M2 run, use the failure-audit notebook to rank missed flaking/paint-chip examples, export 24 review pages and a CSV, then optionally fit 4–8 training images you explicitly mark clean. It loads the reference checkpoint independently of the main notebook. Same-image fitting scores are diagnostics, never held-out validation results. Outputs go to `artifacts/diagnostics/m2/`; validation/test examples cannot enter its fitting test.

M1 uses an ImageNet-pretrained SegFormer-B2 encoder. M2's `serving_512` preset uses the ADE20K-pretrained SegFormer-B2 encoder/decoder and the `damage_focus` training controls at 512: focused crops, rare-class sampling, weighted loss, regularisation and EMA. Other presets remain offline alternatives (`TRAIN_FOR_SERVING=False` disables the preflight, not the export checks). M2 comparison and final evaluation default to the same 512-pixel grid and zero background offset; optional offset tuning is disabled and is not applied by the worker or exporter. These changes establish preprocessing compatibility, not an accuracy improvement.

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

Both notebooks prepare the **union of both HITL subsets** with identical configuration in `data/processed/hitl/v3` (`nested` overlap policy, split adopted from `data/splits/parts/0.1.1`). v3 has the masks of v2 and another split: every parts photograph, and the same photograph in the damage subset, takes its partition from the published parts split that the script pipeline and the served checkpoint use (parts 706/145/147). The seed 20260922 and the 70/15/15 ratios place only the 373 photographs that exist in the damage subset alone. v2 drew its own split, and 109 parts photographs it trained on are in the published test partition, so v2 and v3 scores are not comparable; runs saved on v2 are evaluated on v2 (`PREPARED_DIR` v2, `SPLIT_FROM = None`). v2 has the same images and split membership as v1 but different masks: a polygon at least 80% inside a larger different-class polygon (window in door, plate in bumper, scratch in dent) keeps its pixels instead of being ignored. M1 retains its `baseline`, `fixed` and `fixed_aug` presets and now trains in the M1 worker's model frame (`frame="serving"`: photograph top-left, black padding); its last section can export a run as a registry candidate for the worker. Both M2 notebooks now default to that same serving frame at 512. M2 has its own staged presets documented in its configuration cell. Class titles identify each task because historical folder names were swapped. Grouping includes exact bytes, decoded pixels and optional vehicle/source groups; review the near-duplicate audit and provide missing groups before freezing experiments. A changed source or preparation configuration requires a new prepared-data directory. Reservations for the separate damage-to-part assignment study must be shared across both models.

Use validation curves, per-class metrics, pixel confusion matrices and prediction overlays to select a model. Test evaluation is behind `FINAL_EVAL=False`; enable it only after selection. Compare runs of the same task and frozen split. The HITL notebook retains its eight-class taxonomy and does not inherit the CarDD target; the CarDD notebook retains its six-class taxonomy. Neither model resolves left/right side.

Model checkpoints, config and histories go to `artifacts/models/parts/<run_id>/` or `artifacts/models/damage-hitl/<run_id>/`; plots and reports go to `artifacts/evaluation/<run_id>/`. New training executions create isolated runs. Saved checkpoints can be reloaded for evaluation; interrupted-run optimizer resume is not exposed by these notebooks. Reusable conversion/training/evaluation code lives in [pipelines/vision](../pipelines/vision).

Clear notebook outputs before committing. Keep datasets, weights and generated exports in their ignored locations. These notebooks are experiment entrypoints, not production workers or evidence that M1/M2 acceptance targets have been achieved. An exported M1 run is a candidate registry entry; serving it is a separate change to `configs/models/parts.yaml`.


## Train, export and select a serving model (2026-10-10)

1. Restart the kernel and run the selected notebook with its default serving recipe. Keep the HITL v3 preparation/split settings identical between M1 and HITL M2; CarDD uses its own prepared data and split. The training configuration cell runs `validate_serving_training_config` before fitting. Reduce batch size or increase accumulation for memory limits rather than changing the serving image size.
2. Train a new run and inspect validation metrics/overlays. A former centred-frame checkpoint is not converted by changing a notebook setting. Keep its saved config and results unchanged. Final test evaluation remains opt-in after model selection.
3. Set `EXPORT_FOR_SERVING=True` in the last code section. It exports to `<task>/<run_id>-serving`, distinct from `<task>/<run_id>` containing `best.pt`. Existing entries are never overwritten. The exporter checks weights, vocabulary and preprocessing with the worker's loader and compares logits.
4. Copy the printed YAML fields into `configs/models/parts.yaml` for M1 or `configs/models/damage.yaml` for M2. The fields include checkpoint version, matching taxonomy, a new config version, architecture, label count and input geometry. Keep runtime device/threshold settings. The saved notebook run config is authoritative for the training recipe; historical training-only settings/comments in the serving file are not the new run's recipe.
5. Make the registry entry available to the image worker and rebuild/recreate the API and workers together using the image Compose overlay. Assess a new input revision and verify the pinned model/taxonomy. Existing assessments retain their original versions.

HITL and CarDD switching uses one selected M2 entry and its own vocabulary, never a class-name mapping. Export alone does not modify the active YAML or deploy anything. No new dataset training or model selection was performed by this notebook update. Saved output cells were cleared so previous centred-frame results do not appear to be results of these defaults.


## M5 and M6 document notebooks (2026-10-13)

These notebooks consume the real M4 cache at `data/processed/synthetic_motor_claims_500_w_annotation/m4-v1/` and the annotated synthetic dataset. They use the `claim-cmev` kernel, local cached weights, shared training/serving encoding or detector reconstruction, and explicit candidate exports. Results are **synthetic validation evidence only**; no specification target is established. LayoutLMv3 remains the separately reported neural M5 route.

To inspect a saved run without training, set `RELOAD_RUN_DIR` to its folder. Keep `FINAL_EVAL=False`. With `QUICK_RUN=True`, reload uses a validation subset and isolated reports; it does not exercise training. M6 also skips its optimization walkthrough in reload mode. Set `EXPORT_FOR_SERVING=True` and choose a new `EXPORT_NAME` to export; existing entries are never overwritten. Export does not select or deploy a model. Use `caffeinate -i -s` around long headless jobs on this Mac.

M5 reports the coordinate-only baseline, accepted wrong amounts/quantities, vertical and horizontal shift probes, and atomic `rows_for_m6_validation.json` output from the serving adapter. Its row-slot predictions depend strongly on page height: do not serve the candidate on pages not aligned with the training layout. M6 compares full table bands, oracle printed-cell unions and the actual M5 rows side by side; `M5_ROWS_FILE="auto"` finds the newest row handoff and refuses a quick-run file during full evaluation. Missing rows remain in recall denominators. M6's selected thresholds and recall-preserving alternatives are proposed; `configs/pipeline/m6_linking.yaml` is unchanged.

The corrected executed references are the ignored `artifacts/evaluation/notebook-validation/M{5,6}_Document_Worker-reload-20261013.ipynb` files and their HTML renderings. The original runs were trained on 2026-10-12; the correction pass uses saved weights only, including quick checks. Builders and headless runners are retained under `runtime/handoff-m5-m6-20261013/m5/` and `m6/`; edit their cell parts and rebuild, never hand-edit generated notebook JSON. Builders are local ignored handoff material and must accompany future notebook maintenance.

Live M5/M6 serving is now available through the [document worker integration](../docs/document-worker-live.md). Explicit model YAML selections and the document Compose overlay activate the exported candidates. Notebook export remains separate from deployment; trained-model acceptance remains unestablished.
