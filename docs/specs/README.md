# Specification index

Status: these specifications now derive from [proposal v2](../CLAIM-CMEV_project_proposal_v2.md). The v1 specifications are retired. The containerised, event-driven runtime is a change from v2 section 9.1 and is recorded in [ADR 0002](../adr/0002-containerised-event-runtime.md), which supersedes [ADR 0001](../adr/0001-prototype-runtime.md).

The repository is a scaffold. There is no application code, no dependency manifest and no trained model. Nothing here is implemented or measured. Choices marked **proposed** need a recorded team decision before anything is pinned.

**Where a large language model sits:** no large language model sits in the decision path. Part and damage integration is deterministic mask overlap in M2. Vision and document integration is the deterministic rule set of v2 section 8, applied in M8. An optional stretch service `cmev-explainer` may turn an already-computed finding into a readable sentence. It is off by default and never changes a result.

## Start here

Read in this order.

| # | Document | Purpose |
| --- | --- | --- |
| 1 | [Product specification](product_specification.md) | What the system does and why. Users, workflow, safety invariants, FR and NFR identifiers, scope boundary, milestones, lanes, success criteria |
| 2 | [Technical specification](technical_specification.md) | Architecture, containers, repository layout, model lifecycle, data engineering |
| 3 | [Application platform](application_platform.md) | Intake, API, orchestration, storage, review persistence, revisions, print |
| 4 | [Integration contracts](integration_contracts.md) | Kafka topics, message envelopes, consumer groups, idempotency keys, dead-letter routing |
| 5 | [Data contracts](data_contracts.md) | Shared records, identifiers, units, money with currency and cost basis, versions, null-with-reason rules |
| 6 | [Model training specification](model_training_specification.md) | Training, splits, label mapping, thresholds, checkpoints, model registry |
| 7 | [UI specification](ui_specification.md) | Upload page, review overview, evidence panel, print view, states and interactions |
| 8 | [Evaluation plan](evaluation_plan.md) | Research questions, module metrics, the three consolidation experiments, service checks, reporting rules |

Module specifications below define one module each. Read the shared documents first.

## Functional modules

| v2 | Specification | Container | Implementation path | Lane | Runtime | v1 |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | [Vehicle part segmentation](module-01-vehicle-part-segmentation.md) | `cmev-worker-parts` | `src/claim_cmev/vision/parts/` | 1 | Kafka consumer | M01 |
| M2 | [Damage segmentation and part matching](module-02-damage-segmentation.md) | `cmev-worker-damage` | `src/claim_cmev/vision/damage/` | 1 | Kafka consumer | M02 |
| M3 | [Part summary and coverage](module-03-part-summary-coverage.md) | `cmev-worker-summary` | `src/claim_cmev/vision/multiview/` (rename to `summary/` **proposed**) | 1 | Kafka consumer, plus recompute on reviewed input | M03 |
| M4 | [Page reading](module-04-page-reading.md) | `cmev-worker-ocr` | `src/claim_cmev/documents/text_layout/` (rename to `page_reading/` **proposed**) | 2 | Kafka consumer | M04 |
| M5 | [Line-item extraction](module-05-line-item-extraction.md) | `cmev-worker-lineitems` | `src/claim_cmev/documents/line_items/` | 2 | Kafka consumer, deterministic parser | M05 |
| M6 | [Pen-mark recognition](module-06-pen-mark-recognition.md) | `cmev-worker-penmarks` | `src/claim_cmev/documents/marks/` (**proposed**, new package) | 3 | Kafka consumer, plus review API for confirmation | new in v2 |
| M7 | [Reference cost ranges](module-07-reference-cost-ranges.md) | none, offline only | `src/claim_cmev/costs/reference/`, `pipelines/costs/` | 4 | Offline build, versioned table read by M8 | M06 |
| M8 | [Consolidation and checks](module-08-consolidation-checks.md) | `cmev-consolidator` | `src/claim_cmev/comparison/` | 4 | Kafka consumer, deterministic rules | M07 + M08 |
| M9 | [Review overview and report](module-09-review-report.md) | `cmev-web`, `cmev-api` | `apps/workbench/`, `apps/api/` | 5 | Browser and FastAPI | M09 |

Shared runtime containers owned by Lane 5: `cmev-orchestrator` (`src/claim_cmev/orchestration/`, `workers/`), `cmev-kafka` (Redpanda), `cmev-db` (PostgreSQL 16), `cmev-objectstore` (MinIO), with compose files under `infra/`. Two profiles: `full` runs every module container, and `lean` runs web, api, `cmev-worker-combined`, kafka, db and object store for a laptop demonstration. `cmev-worker-combined` is the same code with every module handler registered in one process, so both profiles must produce identical findings on identical inputs. `cmev-explainer` is an optional stretch service, off by default.

Lane 4 owns the shared vocabulary with Lanes 1 to 3. Lanes 1 and 2 share the vision training harness. Every module owner supplies their own adapter, fixture records, failure cases and evaluation outputs. Lane 5 coordinates integration and does not take over other lanes' module tests.

## v1 numbering is retired

Do not cite a v1 module number in new work. The v1 to v2 mapping is in the last column above and in v2 section 10. Two structural changes are easy to miss:

- v1 M06 and M07 were two modules, a reference cost model and a cost anomaly detector. In v2 the cost check is one part of M8, so `src/claim_cmev/costs/anomaly/` is retired and its logic moves into `src/claim_cmev/comparison/`.
- v2 M6 pen-mark recognition is new. It reuses the freed file number, not the old content.

Files renamed for v2:

| Old name | New name |
| --- | --- |
| `module-03-multiview-coverage.md` | `module-03-part-summary-coverage.md` |
| `module-04-document-text-layout.md` | `module-04-page-reading.md` |
| `module-05-repair-line-items.md` | `module-05-line-item-extraction.md` |
| `module-07-cost-anomaly-detection.md` | `module-06-pen-mark-recognition.md` |
| `module-06-reference-cost-model.md` | `module-07-reference-cost-ranges.md` |
| `module-08-evidence-comparison.md` | `module-08-consolidation-checks.md` |
| `module-09-surveyor-workbench.md` | `module-09-review-report.md` |

## Implementation order

1. Agree scope, contracts, taxonomy, currency, the fixed cost basis and the container and topic names. Assign contract and adapter owners.
2. Stand up `cmev-kafka`, `cmev-db` and `cmev-objectstore` under the `lean` profile. Reserve final-test membership. Smoke-test the pinned dependencies on the demonstration hardware and record memory and latency.
3. Connect upload to overview to save using clearly marked fixtures across the real topics.
4. Replace fixtures with actual bounded model and parser outputs. Exercise pending marks, manual amounts and recomputation.
5. Add the transport and recovery checks: duplicate delivery, consumer restart, dead-letter routing, broker unavailability and superseded input revisions.
6. Freeze release configurations, thresholds and artifacts. Run the untouched test sets, the service checks and the usability walkthrough. Start a stretch goal only if its day-5 gate is met.

## Coordination tasks

- [ ] Assign named members to lanes, shared contracts and adapters.
- [ ] Record whether the containerised runtime fits Lane 5's allocation or consumes contingency days.
- [ ] Confirm [ADR 0002](../adr/0002-containerised-event-runtime.md) and mark ADR 0001 superseded.
- [ ] Freeze contract version `0.1.0` after review, with a distinct schema version from any v1 fixture.
- [ ] Agree the integration fixtures and confirm every one is marked as a fixture.
- [ ] Confirm every module specification cites FR and NFR identifiers from the [product specification](product_specification.md).
- [ ] Record changes to scope, metrics, thresholds and contracts with their reasons.
- [ ] Review each module's acceptance evidence before declaring it complete.

## What governs what

The [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) governs scope. These specifications define implementation. Where they disagree on scope, the proposal wins and the specification is corrected. Where they disagree on runtime, [ADR 0002](../adr/0002-containerised-event-runtime.md) wins, because the runtime change was directed after v2 was written.

The [earlier architecture sketch](../solution_architecture.md), the [problem statement](../CLAIM-CMEV_problem_statement_v4.md) and [proposal v1](../CLAIM-CMEV_project_proposal_v1.md) are historical references only. Where they differ, follow v2 and these specifications: agreed amounts never update reference costs directly, and the absence of fraud labels does not by itself establish an unsupervised-learning contribution.
