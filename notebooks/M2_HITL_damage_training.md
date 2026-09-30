# M2 · HITL damage identification and segmentation

**Manual experiment workbook · SegFormer-B2 default · editable alternatives · unexecuted template.**

SegFormer-B2 is the model architecture; HITL is the dataset. Run cells in order, inspect the prepared data, then start training when ready. All weights, logs and evaluation exports go under `artifacts/`; original downloads remain under `data/raw/`.

This notebook implements the user's **HITL contingency experiment**, fine-tuning a separate **9-class model: 8 HITL damage types plus background**. The source titles are Missing part, Broken part, Scratch, Cracked, Dent, Flaking, Paint chip and Corrosion. The prepared manifest is authoritative for their ordered canonical codes.

HITL's taxonomy is distinct from the six CarDD categories. No class remapping or pooling with CarDD is performed, and the CarDD mIoU ≥ 0.45 target does **not** carry over. Establish a HITL-specific target before final evaluation; until then report measured per-class results without claiming acceptance. Pixel-level semantic labels identify damage types; this does not establish instance counts, severity, repair operation, cost, or damage-to-part assignment accuracy. M1 masks and a separately validated deterministic assignment stage are still needed for the complete M2 module.

This notebook supports offline model development. It neither installs a trained model into the application nor establishes a runtime acceptance result. The SegFormer-B2 choice follows the current user request and supersedes the older proposed B0 recipe for these experiments.

## Training plan and workflow

1. Acquire the two permitted HITL Supervisely exports, retain the original archives and source provenance, and extract them under `data/raw/`. The user supplied `data/raw/CarPartsAndCarDamages.zip` and its extracted `data/raw/CarPartsAndCarDamages/` directory on 2026-09-30. On another machine, copy these inputs or complete the acquisition instructions before conversion.
2. Inspect `meta.json` and actual annotations to identify the parts/damage tasks; historical folder names were swapped. Convert both tasks together, audit polygons and overlap, and freeze one split manifest over their image union.
3. Inspect **training/validation** mask overlays and rare-class support. Review near-duplicate candidates and group related views before training. Reserve assignment evaluation examples across both tasks with the same reservation file.
4. Start with SegFormer-B2, the explicit configuration below, and a one-epoch exploratory run if hardware capacity is uncertain. Give every run a distinct artifact directory. An exploratory run is not a final model result.
5. Fine-tune the pretrained encoder and new task head; select the best checkpoint and hyperparameters using validation foreground mIoU. Compare architectures on identical prepared data and splits, changing one factor at a time.
6. Freeze the chosen configuration, taxonomy, class inclusion policy and checkpoint. Enable final test evaluation only after selection is complete; inspect all class results and failure overlays without retuning on test.

The default is a 30-epoch starting experiment, batch size 2 with accumulation of 4 batches, AdamW with an epoch-level cosine learning-rate schedule, differential learning rates and validation early stopping. These are adjustable starting values, not measured optimal settings. GPU seeds improve repeatability but do not guarantee bitwise equality across hardware.

## Environment and repository paths

Use a dedicated Python environment and select it as the Jupyter kernel. `requirements-training.txt` defines the training dependencies. On an NVIDIA machine, install the appropriate PyTorch/CUDA build for that machine first. On Apple Silicon, the native macOS PyTorch build can use Metal (`mps`). The optional installation cell is disabled by default and runs in the current kernel's Python only when explicitly enabled. Restart the kernel after installation, then rerun from the first cell.

```python
from pathlib import Path
import sys
import json
import os

# Works when Jupyter starts in the repository root or a subdirectory such as notebooks/.
ROOT = next((p for p in [Path.cwd(), *Path.cwd().parents]
             if (p / "docs/specs/README.md").is_file()
             and (p / "pipelines").is_dir()), None)
if ROOT is None:
    raise RuntimeError("Start Jupyter inside the CLAIM-CMEV checkout, or set ROOT to its path.")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Set caches before importing transformers/torch; preserve any user overrides.
os.environ.setdefault("HF_HOME", str(ROOT / "artifacts/models/.cache/huggingface"))
os.environ.setdefault("TORCH_HOME", str(ROOT / "artifacts/models/.cache/torch"))
print("Repository:", ROOT)
print("Kernel Python:", sys.executable)
```

```python
INSTALL_DEPENDENCIES = False  # Set True only to install into this kernel's environment.
if INSTALL_DEPENDENCIES:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r",
                           str(ROOT / "requirements-training.txt")])
    print("Restart the kernel after installation, then rerun this notebook.")
```

```python
from dataclasses import asdict
from collections import Counter
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from PIL import Image
import torch
from IPython.display import display

from pipelines.vision.hitl_data import prepare_hitl
from pipelines.vision.training import (
    TrainingConfig, train, evaluate_run, select_device,
    plot_history, plot_confusion, show_predictions, compare_runs,
)

DEVICE = "auto"  # auto -> CUDA, then Apple MPS, then CPU; or "cuda", "mps", "cpu".
print("PyTorch:", torch.__version__)
print("Selected device:", select_device(DEVICE))
print("CUDA available:", torch.cuda.is_available())
print("Apple MPS available:", torch.backends.mps.is_available())
if torch.cuda.is_available():
    print("CUDA device:", torch.cuda.get_device_name(0))
```

## Shared HITL preprocessing and leakage controls

The converter reads Supervisely `meta.json` and asserts the exact task vocabulary rather than trusting directory names. It rasterizes polygon exteriors and interior holes at source resolution. Same-class overlap is merged; pixels with conflicting classes are marked ignore ID 255 rather than assigned an invented class priority. This conservative policy differs from the older proposed smaller-region-wins recipe and is persisted with the class IDs. Background is ID 0; padding and conflicting overlap are ID 255 and are ignored by the loss and metrics. **Do not reduce the class IDs** as for some ADE20K examples: HITL background is a real training class.

Images and masks are orientation aligned during conversion; ambiguous EXIF/annotation frames fail for manual review rather than silently guessing alignment. Training preserves aspect ratio, resizes images with bilinear interpolation and masks with nearest-neighbor interpolation, and pads centrally to the configured square size. Padding uses the normalization mean in RGB and ignore ID 255 in labels. The conversion manifest retains original dimensions and orientation; the recorded image size and centered-letterbox policy reproduce the model-frame geometry. Metrics are measured on this resized/padded grid with ignored pixels excluded, not on native-resolution masks. ImageNet mean/std are explicit configuration below; change them together when a different checkpoint requires different normalization.

Only training examples receive paired horizontal flips and image brightness/contrast jitter. Horizontal flips are valid because this taxonomy is unsided; if sided labels are introduced, update the augmentation accordingly. No validation/test augmentation is applied. Background means no annotated target, which is not a guarantee of an undamaged or fully documented region. Small damage can disappear after resizing: examine small-region examples and compare higher resolution as a separate validation experiment.

Both notebooks must use the same `PREPARED_DIR`, split seed, ratios, optional group CSV and optional reservation file. Byte hashes and decoded-pixel hashes link duplicate photographs across both subsets. If vehicle/source identity is known, supply a CSV with `sample_id` or `content_sha256`, plus `group_id`; this adds grouping beyond exact image duplicates. `RESERVED_IDS` can identify samples/content hashes to exclude from segmentation training, validation and test for a separate assignment study.

Review the conversion and near-duplicate audit before training. Numerical comparison with publisher `masks_machine` is not yet performed because its palette semantics have not been established; the manifest records this limitation, and the overlays below require visual inspection. Exact hashes alone do not establish vehicle independence; unresolved near duplicates or missing vehicle IDs remain stated limitations. Adjust groups before freezing the manifest and use a new prepared-data version if the split policy or source changes. Never move test examples after seeing their model errors.

```python
# Keep these settings IDENTICAL in M1 and M2.
RAW_ROOT = ROOT / "data/raw"
PREPARED_DIR = ROOT / "data/processed/hitl/v1"
SPLIT_SEED = 20260922
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15
GROUP_CSV = None  # e.g. ROOT / "data/manifests/hitl_vehicle_groups.csv"
RESERVED_IDS = None  # e.g. ROOT / "data/manifests/assignment_reservations.json"

# Both parts AND damage subsets must be present. Missing inputs fail clearly.
# Source content is inspected; do not rename folders just to infer the task.
manifest = prepare_hitl(
    raw_root=RAW_ROOT,
    output_dir=PREPARED_DIR,
    seed=SPLIT_SEED,
    val_fraction=VAL_FRACTION,
    test_fraction=TEST_FRACTION,
    group_csv=GROUP_CSV,
    reserved_ids=RESERVED_IDS,
)
print("Prepared data:", PREPARED_DIR)
print("Shared manifest hash:", manifest["manifest_hash"])
print("Review converter reports and grouping limitations in:", PREPARED_DIR)
display(manifest.get("near_duplicate_audit", {}))
display(manifest.get("split", {}))
```

```python
TASK = 'damage'
class_names = manifest["class_names"][TASK]
records = [r for r in manifest["records"] if r["task"] == TASK]
assert len(class_names) == 9, f"Unexpected taxonomy: {class_names}"
assert class_names[0] == "background"
print("Class IDs:", dict(enumerate(class_names)))
display(pd.DataFrame(sorted(Counter(r["split"] for r in records).items()),
                     columns=["partition", "images"]))

# Show training pixel support only; validation and final reports report their own support.
train_records = [r for r in records if r["split"] == "train"]
assert train_records, "No training samples: inspect conversion/split reports."
pixel_counts = np.zeros(len(class_names), dtype=np.int64)
for record in train_records:
    with Image.open(record["mask_path"]) as im:
        labels = np.asarray(im)
        pixel_counts += np.bincount(labels[labels < len(class_names)].ravel(),
                                    minlength=len(class_names))
support = pd.DataFrame({"class": class_names, "training_pixels": pixel_counts})
display(support)
ax = support.set_index("class")["training_pixels"].plot.bar(figsize=(12, 4), logy=True)
ax.set_ylabel("Annotated training pixels (log scale)")
ax.set_title("Training class support — inspect rare classes before selecting a loss")
plt.tight_layout()
```

```python
# Visual audit before training; these examples are drawn ONLY from the training split.
preview_records = train_records[:4]
fig, axes = plt.subplots(len(preview_records), 3,
                         figsize=(13, 3.5 * len(preview_records)), squeeze=False)
cmap = plt.get_cmap("turbo", len(class_names))
for row, record in enumerate(preview_records):
    rgb = np.asarray(Image.open(record["image_path"]).convert("RGB"))
    labels = np.asarray(Image.open(record["mask_path"]))
    axes[row, 0].imshow(rgb)
    axes[row, 1].imshow(np.ma.masked_where(labels == 255, labels), cmap=cmap, vmin=0, vmax=len(class_names) - 1,
                        interpolation="nearest")
    axes[row, 2].imshow(rgb)
    axes[row, 2].imshow(np.ma.masked_where((labels == 0) | (labels == 255), labels),
                        cmap=cmap, vmin=0, vmax=len(class_names) - 1, alpha=0.5,
                        interpolation="nearest")
    for column, heading in enumerate(["Photograph", "Semantic target", "Target overlay"]):
        axes[row, column].set_title(heading + " · " + str(record["sample_id"])[:12])
        axes[row, column].axis("off")
plt.tight_layout()
plt.show()
```

## Encoder, segmentation head and backbone alternatives

The default checkpoint is [`nvidia/mit-b2`](https://huggingface.co/nvidia/mit-b2): an ImageNet-pretrained **Mix Transformer B2 encoder**, not already HITL-trained weights. The four hierarchical encoder stages retain pretrained weights and are fine-tuned. A new SegFormer all-MLP decoder projects features from each stage to a shared channel width, upsamples them to a common resolution, concatenates and fuses them, then applies normalization/activation/dropout and a final **1×1 classifier producing 9 logits per spatial location**. Bilinear upsampling aligns logits with the target image grid. Argmax yields the semantic mask; softmax gives pixel-class probabilities. There is no additional whole-image classification branch.

`freeze_encoder_epochs=0` fine-tunes all encoder layers from the start. Set it to a small positive number to warm up the new head first. Encoder learning rate is lower than the head learning rate. Cross-entropy ignores padding; an optional soft Dice term emphasizes foreground overlap. Compare loss choices using validation only.

| `architecture` | Encoder and added/changed layers | `checkpoint` setting |
| --- | --- | --- |
| `segformer` | Pretrained MiT encoder; new SegFormer decoder and 9-class classifier | `nvidia/mit-b2` default; e.g. `nvidia/mit-b0`, `nvidia/mit-b3` for another scale |
| `resnet50` | ImageNet-pretrained ResNet-50 features with DeepLabV3 atrous spatial pyramid pooling and a new 9-class segmentation classifier | Ignored; torchvision's selected pretrained encoder weights are recorded |
| `resnet101` | Same DeepLabV3 task head with deeper ResNet-101 features | Ignored; torchvision's selected pretrained encoder weights are recorded |
| `hf_semantic` | A supported Hugging Face semantic segmentation model with its existing decoder and a resized task classifier | Supply a compatible semantic segmentation checkpoint; a classifier-only ViT needs an explicit decoder adapter and is not accepted automatically |

Change the two configuration fields to switch model families. Use the same data version, taxonomy, input size, normalization policy, seed and evaluation protocol when isolating the effect of the architecture; report deviations and compute budget. Record the exact downloaded revision and licence with the run; `revision` can pin an HF commit. Check checkpoint-specific input/normalization constraints for another ViT. These notebook defaults do not imply every third-party architecture has been exercised.

```python
# Main editable experiment configuration. Re-run this cell after any deliberate change.
cfg = TrainingConfig(
    task=TASK,
    architecture="segformer",  # "segformer", "resnet50", "resnet101", "hf_semantic"
    checkpoint="nvidia/mit-b2",  # HF identifier; ignored for torchvision ResNets
    revision=None,  # Pin a Hugging Face checkpoint commit for a frozen experiment.
    pretrained=True,
    image_size=512,  # Try 384 to reduce memory; compare 640 if small details vanish.
    batch_size=2,
    accumulation_steps=4,  # Effective batch ~= 8; final partial accumulation is handled.
    epochs=30,
    encoder_lr=6e-6,
    head_lr=6e-5,
    weight_decay=0.01,
    patience=8,
    freeze_encoder_epochs=0,
    ce_weight=1.0,
    dice_weight=0.5,
    horizontal_flip=0.5,
    brightness=0.15,
    contrast=0.15,
    mean=(0.485, 0.456, 0.406),
    std=(0.229, 0.224, 0.225),
    freeze_batchnorm=True,  # Stable DeepLab operation with small batches.
    device=DEVICE,
    amp=True,  # Harness enables mixed precision on CUDA; MPS/CPU use float32.
    workers=0,  # Portable notebook default, especially on macOS/Windows.
    seed=SPLIT_SEED,
    artifacts_root=str(ROOT / "artifacts"),
    run_id=None,  # Auto-generate a unique run; explicit existing IDs are rejected.
)
print(json.dumps(asdict(cfg), indent=2))
print("Expected model parent:", ROOT / "artifacts/models/damage-hitl")
```

## Run training manually

Execute the next cell when the data audit and configuration are ready. It loads the pretrained checkpoint (a network download on first use), creates a unique run, and records config, class mapping, split/source fingerprints, environment, hardware, checkpoints and validation history. The training loop evaluates validation only, selects `best.pt` by foreground mIoU, and retains `last.pt` as well.

`auto` selects CUDA first, then Apple MPS, then CPU. Keep `workers=0` for portable notebook execution. If the model exceeds memory, reduce batch size or image size and increase accumulation if useful; record a new run. For an unsupported MPS operation, explicitly select `device="cpu"` or use a compatible PyTorch build and record that hardware change. The harness does not hide an accelerator failure as a successful GPU run.

Each execution starts a **new training run**. Optimizer/scheduler state is retained in checkpoint artifacts, but automatic interrupted-run resume is not provided by this notebook. The reload cell below supports evaluating a saved run without retraining. Do not call a fresh run an exact continuation of a previous one.

```python
run = train(manifest, cfg)
RUN_DIR = Path(run["run_dir"])
EVALUATION_DIR = Path(run["evaluation_dir"])
print("Model and training artifacts:", RUN_DIR)
print("Evaluation artifacts:", EVALUATION_DIR)
```

## Validation metrics and performance plots

Training/validation losses and validation foreground mIoU help reveal underfitting and overfitting. Validation evaluation reloads the **best** checkpoint, so it also checks that the saved weights are usable. Report every class with ground-truth pixel support, IoU, Dice/F1, precision and recall; absent/undefined classes must remain explicit rather than being presented as perfect. Foreground mIoU excludes background; pixel accuracy alone can be misleading when background dominates.

The confusion matrix is a **pixel confusion matrix**, with rows as true classes and columns as predictions. Row normalization highlights which classes are confused even when class support differs. It is not a damage-instance matrix. Overlays should be reviewed alongside numerical metrics for small objects, boundaries, rare labels and misleading background predictions.

```python
plot_history(RUN_DIR)
plt.show()
validation_report = evaluate_run(RUN_DIR, manifest, split="val", device=DEVICE)
display({k: v for k, v in validation_report.items()
         if k not in {"per_class", "confusion_matrix"}})
display(pd.DataFrame(validation_report["per_class"]))
plot_confusion(RUN_DIR, split="val")
plt.show()
show_predictions(RUN_DIR, manifest, split="val", count=4, device=DEVICE)
plt.show()
```

## Compare manual runs on the same frozen split

Run another architecture by editing the configuration and running the training cell, then list its path here. Compare **validation** metrics while selecting models. The comparison helper checks the shared manifest identity, taxonomy and evaluation resolution, so M1 and M2 are compared separately and different-resolution runs require a separately declared comparison. Keep input size, split, seed and training budget fixed for a controlled architecture comparison; record any deliberate difference. After choosing a model, repeat with additional fixed seeds to estimate variability when the compute budget permits. Validation rankings are development evidence, not final held-out accuracy.

```python
RUNS_TO_COMPARE = [str(RUN_DIR)]
# Add paths from earlier runs of this SAME task/data version, for example:
# RUNS_TO_COMPARE += [str(ROOT / "artifacts/models/damage-hitl/<another-run-id>")]
comparison = compare_runs(RUNS_TO_COMPARE)
display(comparison)
# Save only when deliberately recording this comparison:
SAVE_COMPARISON = False
if SAVE_COMPARISON:
    comparison.to_csv(EVALUATION_DIR / "manual_validation_comparison.csv", index=False)
```

## Reload an existing checkpoint without another training run

After a kernel restart, rerun the imports and preparation/configuration cells, set `RELOAD_RUN_DIR` to a saved run below, then run the validation plotting/evaluation cell. Evaluation rebuilds the architecture from its persisted configuration and loads `best.pt`; it checks task/data compatibility. Move the corresponding prepared dataset and manifest with the run when using another machine, and preserve recorded content identities. This is model reload for inference/evaluation, not optimizer resume.

```python
RELOAD_RUN_DIR = None  # e.g. ROOT / "artifacts/models/damage-hitl/<saved-run-id>"
if RELOAD_RUN_DIR is not None:
    RUN_DIR = Path(RELOAD_RUN_DIR).expanduser().resolve()
    if not (RUN_DIR / "best.pt").is_file():
        raise FileNotFoundError(f"No saved best checkpoint in {RUN_DIR}")
    EVALUATION_DIR = ROOT / "artifacts/evaluation" / RUN_DIR.name
    print("Reload selected:", RUN_DIR)
    reloaded_validation_report = evaluate_run(RUN_DIR, manifest, split="val", device=DEVICE)
    display(reloaded_validation_report)
```

## Final held-out evaluation — explicit opt-in

Leave `FINAL_EVAL=False` throughout development. Set it to `True` only after selecting and freezing the final run using validation, with no pending split/taxonomy edits. Evaluation exports metrics, the confusion matrix and qualitative results under `artifacts/evaluation/<run_id>/`. Test results must not become a feedback loop for architecture or threshold selection. A later design change requires a new declared evaluation protocol, not repeated claims of untouched test performance.

```python
FINAL_EVAL = False
if FINAL_EVAL:
    final_report = evaluate_run(RUN_DIR, manifest, split="test", device=DEVICE)
    display({k: v for k, v in final_report.items()
             if k not in {"per_class", "confusion_matrix"}})
    display(pd.DataFrame(final_report["per_class"]))
    plot_confusion(RUN_DIR, split="test")
    plt.show()
    show_predictions(RUN_DIR, manifest, split="test", count=4, device=DEVICE)
    plt.show()
else:
    print("Final test remains unused. Continue model selection with validation only.")
```

## Interpretation, artifacts and next steps

For this HITL M2 experiment, acceptance targets remain to be set before final evaluation. Keep all eight damage classes and their support in reports; do not substitute the CarDD target or class names. Rare Cracked/Corrosion labels warrant explicit error analysis. Damage-to-part assignment needs a separate grouped, reserved evaluation and side remains unresolved.

Model artifacts are isolated under `artifacts/models/damage-hitl/<run_id>/`, and evaluation artifacts under `artifacts/evaluation/<run_id>/`. Keep the dataset/version/config manifest with the checkpoint when copying to another machine. The final evidence package should include best weights, preprocessing and taxonomy, source/split hashes, config and environment, history plots, every class's support/metrics, confusion matrices and sampled prediction overlays. Clear notebook outputs before committing; large generated assets stay ignored.

These notebooks provide trainable semantic segmentation experiments. A completed training cell does not demonstrate calibrated confidence, reliable absence of damage, runtime integration, side identification or required repairs. Production adapters must preserve source coordinates, uncertainty and version references; then be independently integrated and tested.

References: [training specification](../docs/specs/model_training_specification.md), [evaluation plan](../docs/specs/evaluation_plan.md), [M1](../docs/specs/module-01-vehicle-part-segmentation.md), [M2](../docs/specs/module-02-damage-segmentation.md), [dataset acquisition](../docs/dataset-downloads.md), [SegFormer model documentation](https://huggingface.co/docs/transformers/model_doc/segformer), [MiT-B2 model card](https://huggingface.co/nvidia/mit-b2).
