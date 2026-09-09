# Shared domain package

Reserved Python package layout. See [module ownership](../../docs/specs/README.md) and [technical boundaries](../../docs/specs/technical_specification.md).

| Directory | Responsibility |
| --- | --- |
| `contracts/` | Shared record validation and serialization |
| `taxonomy/` | Canonical part/side/damage/operation mapping |
| `vision/parts/` | M01 inference |
| `vision/damage/` | M02 segmentation and part matching |
| `vision/multiview/` | M03 duplicate aggregation and coverage |
| `documents/text_layout/` | M04 text/layout extraction |
| `documents/line_items/` | M05 repair entry recognition |
| `costs/reference/` | M06 interval fitting and reference-table contracts/lookup |
| `costs/anomaly/` | M07 cost checks |
| `comparison/` | M08 fusion and reverse matching |
| `orchestration/` | Job/revision coordination |
| `persistence/` | Relational repositories, transactions and artifact adapters |

Domain code must not import HTTP/frontend entrypoints. Contracts are shared by producers and consumers; do not copy schemas into each worker. Add package metadata and imports when implementing the agreed stack.
