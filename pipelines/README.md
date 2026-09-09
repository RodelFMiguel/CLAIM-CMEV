# Offline pipelines

| Directory | Owners | Work |
| --- | --- | --- |
| `vision/` | Lanes 1-2 | Shared conversion, split creation, part/damage training and multi-view experiments |
| `documents/` | Lane 3 | Layout conversion, document training and source-localisation evaluation |
| `costs/` | Lane 4 | Synthetic/approved record preparation, interval training, calibration and controlled refresh |

Follow the [technical model/data lifecycle](../docs/specs/technical_specification.md). All entrypoints must take versioned config and explicit input/output locations, then record source/split hashes, code revision, seeds and metrics. Do not train during claim processing.

Cost refresh consumes eligible final approvals or explicitly documented synthetic seeds. It must not consume surveyor agreement directly. Current directories reserve pipeline locations; no training command is implemented.
