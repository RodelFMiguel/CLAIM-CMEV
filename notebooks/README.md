# Manual vision training notebooks

| Task | Notebook | Readable training plan and cell reference |
| --- | --- | --- |
| M1: 21 vehicle parts plus background | [M1_HITL_parts_training.ipynb](M1_HITL_parts_training.ipynb) | [M1 training workflow](M1_HITL_parts_training.md) |
| M2: 8 HITL damage types plus background | [M2_HITL_damage_training.ipynb](M2_HITL_damage_training.ipynb) | [M2 training workflow](M2_HITL_damage_training.md) |

Both notebooks default to an ImageNet-pretrained SegFormer-B2 encoder with a new semantic segmentation head. Editable configuration selects SegFormer variants, DeepLabV3 with ResNet-50/101, or a compatible Hugging Face semantic segmentation model. CUDA, Apple MPS and CPU are selected automatically in that order; an explicit device override is available. The supplied notebooks contain no training outputs or measured model results.

Create/select a Python kernel with [requirements-training.txt](../requirements-training.txt), then open either notebook from this checkout and run cells in order. An optional installation cell is disabled until explicitly enabled. On NVIDIA hardware, select the PyTorch build appropriate for the installed CUDA environment. The user supplied the original HITL archive and extracted Supervisely data under `data/raw/` on 2026-09-30; on a different machine, follow [dataset acquisition](../docs/dataset-downloads.md) or copy these inputs first.

The current Mac has a prepared Python 3.12 `.venv`. Start from the repository root:

```sh
.venv/bin/jupyter lab notebooks/
```

Select that environment's Python kernel in Jupyter or your IDE. On a new machine, create a Python 3.12 environment and install `requirements-training.txt` first. The initial SegFormer-B2 encoder is cached under `artifacts/models/.cache/huggingface/` on this Mac; the first use on a new machine needs network access or a copied cache. Training remains a manual cell execution.

Both notebooks prepare the **union of both HITL subsets** with identical configuration in `data/processed/hitl/v1` (seed 20260922, 70/15/15 train/validation/test). Class titles identify each task because historical folder names were swapped. Grouping includes exact bytes, decoded pixels and optional vehicle/source groups; review the near-duplicate audit and provide missing groups before freezing experiments. A changed source or preparation configuration requires a new prepared-data directory. Reservations for the separate damage-to-part assignment study must be shared across both models.

Use validation curves, per-class metrics, pixel confusion matrices and prediction overlays to select a model. Test evaluation is behind `FINAL_EVAL=False`; enable it only after selection. Compare runs of the same task and frozen split. M2 uses the user's explicitly selected HITL taxonomy and does not inherit the CarDD target or imply runtime compatibility. Neither model resolves left/right side.

Model checkpoints, config and histories go to `artifacts/models/parts/<run_id>/` or `artifacts/models/damage-hitl/<run_id>/`; plots and reports go to `artifacts/evaluation/<run_id>/`. New training executions create isolated runs. Saved checkpoints can be reloaded for evaluation; interrupted-run optimizer resume is not exposed by these notebooks. Reusable conversion/training/evaluation code lives in [pipelines/vision](../pipelines/vision).

Clear notebook outputs before committing. Keep datasets, weights and generated exports in their ignored locations. These notebooks are experiment entrypoints, not production workers or evidence that M1/M2 acceptance targets have been achieved.
