# HITL notebook preparation verification — 2026-09-30

Scope: manual offline M1/M2 notebooks and reusable data/training helpers. No application worker changed; no full HITL training or held-out model evaluation was performed.

## Inputs and preparation

The user supplied `data/raw/CarPartsAndCarDamages.zip` (3,103,430,533 bytes) and its extracted directory. SHA-256: `b7454257aa2aa08a7ea42003f8c1f15db70e5d77d63567a4373414ae8a998577`. All 3,626 source metadata/annotation/image entries matched the ZIP directory's CRC32 and sizes. [Safe source receipt](../../data/manifests/hitl_training_source_20260930.json).

Actual annotation inspection confirmed swapped folder names, 998 part-labelled and 814 damage-labelled photographs, valid polygon bounds/rings, matching image dimensions and EXIF orientation 1 for all images. Conversion uses source polygons, not guessed palette semantics from the publisher's raster masks. Those raster colors did not match metadata in sampled files; classwise raster agreement was not established.

Prepared outputs: `data/processed/hitl/v1`, approximately 824 MB. Manifest SHA identity: `ea054e856b9f2bf71abe67eebb514e6b704b0291247fc5ec156cc70b54469f49`. All normalized image/mask and split hashes were verified on reload. Repeated preparation reused the identical manifest. [Preparation summary](../../data/manifests/hitl_training_preparation_20260930.json).

| Task | Training | Validation | Test |
| --- | ---: | ---: | ---: |
| M1 parts | 711 | 147 | 140 |
| M2 HITL damage | 558 | 132 | 124 |

The union has 1,371 groups, with 441 identical photographs shared across tasks. Seed 20260922 assigned 959/206/206 groups. No explicit vehicle metadata or assignment reservations were supplied. The dHash audit returned zero candidates at Hamming distance ≤4; this does not establish vehicle independence. Six training-only polygon overlays were visually inspected and aligned with their photographs.

## Executed checks

```sh
MPLBACKEND=Agg .venv/bin/python -m pytest -q \
  tests/unit/test_extract_hitl.py \
  tests/unit/test_hitl_training_data.py \
  tests/unit/test_vision_training.py
```

**24 passed.** Checks cover archive boundaries, polygon holes/conflicts, immutable preparation and shared splits, ignored-label gradients, confusion denominators, image/mask augmentation alignment, bounded synthetic training, checkpoint reload, artifact tampering, failed-run recording, portable evaluation paths and plot generation. Synthetic training metrics are not HITL performance evidence.

Both notebooks passed nbformat validation and code-cell compilation. All seven cells preceding training were executed against the actual prepared dataset, with M1 launched from the repository root and M2 from `notebooks/`. Configuration, class-support plots and overlays worked. Source notebooks retain no outputs.

The sandbox did not expose MPS, so GPU checks were separately executed outside it. On macOS 26.6.2 arm64, automatic device selection chose MPS. The cached ImageNet-pretrained MiT-B2 encoder loaded successfully with new 22-class and 9-class segmentation heads; both completed forward/backward passes with finite loss and encoder gradients. ResNet-50/101 DeepLabV3 also passed MPS forward/backward checks with randomly initialized encoders. These bounded checks used synthetic 64×64 tensors, not full-resolution training. CUDA and other Hugging Face semantic architectures were not exercised.

Environment: Python 3.12, PyTorch 2.14.0, torchvision 0.29.0, transformers 4.57.6, NumPy 2.5.3, Pillow 12.3.0. Cached `nvidia/mit-b2` revision: `3bb39e8739149c3777d0325349b2a6c32c6413db`. `pip check` found no broken requirements. Full local package versions are recorded in `artifacts/evaluation/notebook-validation/environment-freeze.txt`; generated previews/logs/device checks are in the same ignored directory.

Next: review the notebook training settings, add any vehicle groups or assignment reservations before freezing a new split, then run each training cell manually. Select models using validation; keep final test evaluation disabled during tuning.
