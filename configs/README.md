# Versioned configuration

| Directory | Contents to implement |
| --- | --- |
| `taxonomy/` | Source aliases, canonical parts/sides/damage/operations, unsupported mappings |
| `models/` | Model architecture/preprocessing and manifest references |
| `pipeline/` | Stage selection, retry/lease policy, input limits and runtime profiles |
| `costs/` | Comparison keys, cost basis, eligibility, grouping, calibration and minimum support |
| `evaluation/` | Dataset/split references, metrics, thresholds, experimental configurations |

Track small reviewed configuration, not secrets or weights. Calibrated thresholds must include selection evidence and a version; absent decisions are not silently replaced by arbitrary defaults. See [shared contracts](../docs/specs/data_contracts.md).
