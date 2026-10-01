# Manual M1 and M2 training with HITL

Decision recorded 2026-09-30 at the user's request: the two offline notebooks use **SegFormer-B2** and the two **HITL** subsets. SegFormer is the model architecture; HITL is the dataset. M1 predicts 21 part categories plus background. M2 predicts the eight HITL damage categories plus background under `damage-hitl-1.0.0`. This selects the documented HITL contingency for this experiment. It does not activate that taxonomy in the serving contracts or replace the six-class CarDD benchmark with HITL scores.

The runnable entry points and detailed plans are in [the notebook guide](../notebooks/README.md). Shared Python utilities live in `pipelines/vision/`; training stays outside application requests and workers. This work prepares manual experiments; it does not establish trained-model accuracy or production integration.

## Data acquisition

Use only the [HITL publisher](https://humansintheloop.org/resources/datasets/car-parts-and-car-damages-dataset/) release. Its page lists 998 part-labelled and 814 damage-labelled images and CC0 1.0 dedication, with access through a form. The user supplied `data/raw/CarPartsAndCarDamages.zip` and its extracted directory during this task. Current inspection confirms 998 part and 814 damage annotation files, and verifies all 3,626 metadata/annotation/image members against the archive CRC and byte sizes. Original archive SHA-256 is recorded in `data/raw/hitl-source-receipt.json`. The September 22 workspace observations remain dated history.

Supply the two publisher-provided HTTPS archive URLs or existing local archives in ignored `runtime/dataset-sources.local.json`, following [the acquisition guide](dataset-downloads.md). Download/import just HITL:

```sh
python3 scripts/download_datasets.py --datasets hitl --sources runtime/dataset-sources.local.json
```

The current supplied archive remains directly under `data/raw/`; future downloader acquisitions and their receipts default to `data/raw/hitl/`. For a new archive, extract safely into a new directory without replacing original files:

```sh
python3 pipelines/vision/extract_hitl.py data/raw/CarPartsAndCarDamages.zip --output data/raw/hitl-extracted
```

The supplied archive is already extracted under `data/raw/CarPartsAndCarDamages/`, so this command is unnecessary for the current workspace. Both notebooks discover subsets from `meta.json` and annotation class titles, because the original folder names may be swapped. The downloader does not submit access forms or invent download URLs.

## Experiment and evaluation scope

Both notebooks use the same union-of-subsets preparation and split version. Exact duplicate file and decoded-image hashes link samples across tasks. Provide vehicle/related-view group metadata and reserved assignment/cohort IDs where available. Inspect the near-duplicate audit before training; hashes alone cannot establish vehicle-level independence. Augment only training samples, retaining original images and annotations.

The default split is 70% training, 15% validation and 15% final test by groups, with seed 20260922. Fractions are approximate by image count because groups remain indivisible. Reserve assignment/vehicle evaluation groups before fitting; a segmentation test set alone is not an independently labelled assignment benchmark. Freeze the preparation manifest for all backbone comparisons and create a new version when changing data or split configuration.

M1 reports every foreground part class and background separately. M2 reports every HITL damage class, including low-support or absent classes, with support counts and explicit undefined metrics. Foreground macro IoU, per-class IoU, Dice, precision/recall, raw and row-normalized pixel confusion matrices, learning curves and qualitative masks support error analysis. Select checkpoints and hyperparameters on validation only. Final test evaluation is an explicit separate notebook step after freezing the comparison protocol.

The proposal's M1 target remains a hypothesis; a supported-panel subset/floor still needs to be fixed before a formal acceptance run. The CarDD mIoU target is **not transferred** to this HITL experiment. The initial HITL damage objective is descriptive: compare validation foreground mIoU across fixed-split backbones, then report the selected models' held-out per-class results. No HITL numerical acceptance threshold or production readiness is claimed.

M2 learns per-pixel damage identity through its segmentation head. Damage-to-part assignment remains deterministic, requires aligned M1 masks and its own independently reviewed targets, and is outside these segmentation scores. Unlabelled background is an annotation convention, not proof of no damage. HITL part predictions leave side unknown.

## Label preparation v2 and recipe presets (2026-10-01)

HITL annotators nest polygons: doors include their windows and mirrors, bumpers their licence plates and grilles, and dents their scratches. The first preparation (`data/processed/hitl/v1`, `hitl-polygons-1.0.0`) ignored every different-class overlap. As a result it discarded 88% of front-window, 84% of licence-plate, 77% of mirror and 63% of back-window annotated pixels. `data/processed/hitl/v2` (`hitl-polygons-1.1.0`) uses the `nested` policy: when at least 80% of the smaller polygon lies inside a larger different-class polygon, the smaller one keeps the shared pixels. Other different-class overlaps stay ignore 255. On the real data, 89.5% of part-overlap pixels and 73% of damage-overlap pixels came from pairs at least 90% nested. Ignored training pixels fell from 10.3M to 1.0M for parts and from 1.5M to 0.3M for damage. Split membership is identical to v1; masks differ, so v1 and v2 metrics are not comparable. The summary is in `data/manifests/hitl_training_preparation_20261001.json`, and `overlap_policy="ignore_all"` still reloads v1 unchanged.

M1's configuration cell offers `baseline` (the first v1 run: encoder/head learning rates 6e-6/6e-5, per-epoch cosine, 30 epochs), `fixed` (6e-5/6e-4, two-epoch warmup, per-update polynomial decay, up to 100 epochs, patience 15) and `fixed_aug` (`fixed` plus random 0.5-2x scale and crop, up to 10-degree rotation and saturation jitter). Running `baseline` on v2 isolates the label change; `fixed` and `fixed_aug` then isolate the recipe and augmentation. M2 retains that `fixed_aug` configuration as `previous`; its completed result and new presets are described below.

## Models, hardware and artifacts

M1 starts with the [ImageNet-pretrained MiT-B2 encoder](https://huggingface.co/nvidia/mit-b2) and a newly initialized segmentation decoder. M2's revised default transfers the encoder and decoder from [ADE20K-pretrained SegFormer-B2](https://huggingface.co/nvidia/segformer-b2-finetuned-ade-512-512), replacing its classifier for HITL. The notebooks explain the decoder and alternative ResNet-50/101 with DeepLabV3 heads, and expose the model/checkpoint, batch size, resolution, optimizer, learning rates and training schedule. Model-weight terms remain distinct from HITL's dataset terms.

Device selection prefers CUDA, then Apple's MPS, then CPU. Small laptop defaults are editable; device availability is printed and recorded. MPS/CPU use float32, and CUDA mixed precision is configurable. Architecture support does not guarantee every operator works on every accelerator: an unsupported operation must surface an error or require an explicit device change.

Runs save checkpoints/configuration/provenance under `artifacts/models/parts/<run_id>/` or `artifacts/models/damage-hitl/<run_id>/`, with metrics and figures under `artifacts/evaluation/<run_id>/`. Each run has a unique ID. Dataset and model caches, checkpoints and rendered evidence stay in ignored storage. Candidate checkpoints require separate adapter/registry acceptance before serving.


## M2 damage-focused recipe update (2026-10-01)

The completed v2 M2 run `damage-segformer-20261001T003540Z-6203ed59` reached 0.2224 validation foreground mIoU at epoch 9 and stopped after 24 epochs. Its final training mIoU was 0.5565 while validation was 0.1966; these differently augmented metrics suggest a generalization problem, not a demonstrated architectural limit. Flaking and paint-chip IoU were 0.030 and 0.041 at the best checkpoint.

The M2 notebook now includes staged presets for BatchNorm adaptation, ADE20K decoder transfer, validation-plateau scheduling, damage-focused native-detail crops, 512/640 resolution, optional capped repeat-factor sampling, and a separate UperNet–ConvNeXt comparison. The default `focused_640` combines several changes; it is a recipe comparison, not an individual-factor ablation. Crop probabilities, preferred classes, zoom, scheduler patience/floor and sampling caps are editable. Label auditing shows flaking/paint-chip/scratch targets and original annotation paths; no annotations are automatically rewritten.

Binary foreground metrics collapse the existing multiclass argmax and exclude ignored pixels; they do not establish assignment accuracy. Comparing training resolutions explicitly evaluates every candidate at one common resolution and writes separate `val_size<N>_*` artifacts. Original run metrics remain intact. Validation/test never use target-driven crops or training sampling weights. Exact resume and native-resolution tiled inference are not introduced. Legacy config defaults and the M1 notebook remain compatible.

Both companion notebook Markdown copies were removed at the user's request; training explanations remain in notebook Markdown cells. Previous M2 notebook outputs are backed up under ignored `artifacts/exports/notebook-backups/`.
