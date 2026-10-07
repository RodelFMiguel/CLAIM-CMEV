# Offline pipelines

| Directory | Owners | Work |
| --- | --- | --- |
| `vision/` | Lanes 1-2 | Shared conversion, split creation, part/damage training and multi-view experiments |
| `documents/` | Lane 3 | Layout conversion, document training and source-localisation evaluation |
| `costs/` | Lane 4 | Synthetic/approved record preparation, interval training, calibration and controlled refresh |

Follow the [technical model/data lifecycle](../docs/specs/technical_specification.md). All entrypoints must take versioned config and explicit input/output locations, then record source/split hashes, code revision, seeds and metrics. Do not train during claim processing.

Cost refresh consumes eligible final approvals or explicitly documented synthetic seeds. It must not consume surveyor agreement directly. `documents/` only reserves its location; `vision/` holds the M1 part-segmentation scripts below.

## M1 part segmentation (`vision/`)

Run from the repository root, in an environment with the application package and its `vision` extra: `pip install -e ".[vision]"` (PyTorch and transformers 5.17 or later; a checkpoint saved by transformers 5 does not load under 4).

```sh
python pipelines/vision/convert_hitl.py                       # polygons -> masks in data/interim/hitl_parts
python pipelines/vision/train_segformer.py --config configs/models/parts.yaml --output-dir artifacts/models/parts/0.1.0
python pipelines/vision/eval_segmentation.py --model-dir artifacts/models/parts/0.1.0
```

The tracked split is `data/splits/parts/0.1.1`. Its records name each image relative to the HITL parts export and each mask relative to the converter's output folder, so the split holds no workstation path. The masks are derived and ignored by Git: run the converter once per workstation. The trainer checks every mask against the pixel hash in its split record before it starts.

| What | Default | Override |
| --- | --- | --- |
| HITL parts export (`Car damages dataset`) | found under `data/raw`, directly or one folder down | `CMEV_HITL_PARTS_DIR` or `--raw-dir` |
| HITL damage export (`Car parts dataset`), used only to build a split | found the same way | `CMEV_HITL_DAMAGE_DIR` or `--damage-raw-dir` |
| Converted masks and index | `data/interim/hitl_parts` | `CMEV_HITL_PARTS_LABELS_DIR` or `--output-dir` |

`python pipelines/vision/build_splits.py` builds a split from the converter's index. It needs the damage export as well, because images shared by the two exports must stay in one partition; without it the same seed gives a different split, so the command fails instead. `--same-membership-as <split folder>` refuses to write unless every image keeps its partition. Split `0.1.1` was built that way from `0.1.0`, which stays in the repository unchanged with its original workstation paths.

The output folder of `train_segformer.py` is a registry entry the M1 worker can serve: name it after `model_version` in the configuration. `train_resnet.py` and `train_eval_yolo.py` are comparison scripts; the worker cannot load their output.
