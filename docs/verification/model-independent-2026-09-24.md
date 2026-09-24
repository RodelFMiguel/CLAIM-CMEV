# Model-independent implementation verification, 2026-09-24

Scope: Claude's uncommitted implementation was preserved, inspected, and extended by Codex. Baseline commit: 1c9ca28 on code-skeleton. This record is not a model evaluation.

## Nine-priority status

| Priority | Implemented and exercised | Boundary / remaining work |
| --- | --- | --- |
| 1 Contracts | Typed records, event schemas, fixture round trips, rejection cases, exact monetary strings and revision checks | Proposed taxonomy/configuration choices remain subject to the team's freeze |
| 2 M8 | Deterministic comparison, coverage gates, pinned cost lookup, possible additions, immutable findings and content hashes | Rules applied to fixtures do not establish detection accuracy |
| 3 Orchestration | Persisted branch join, outbox, deduplication, stage reuse, retry/dead-letter logic and real M8 consolidation. Lean and split-service full Compose both ran | Full packages fixture producers in one container; six real inference containers, GPU profile, capacity and outage/restore acceptance remain |
| 4 M6 | Linking/state machine plus API actions for link, confirm/reject, add mark and revised amount; typed M9-to-M6 amount mapping | Detector accuracy and real page overlay alignment require actual outputs/artifacts |
| 5 M4 | Existing page rasterisation/geometry/OCR adapter and synthetic PaddleOCR smoke preserved | No real-page OCR evaluation; live OCR worker not connected to the fixture pipeline |
| 6 M5 | Existing layout parser, aliases, exact values, row completeness and OCR-box tests preserved | Team-reviewed layouts/aliases and live M4-to-M5 worker wiring remain |
| 7 M2/M3 | Pure assignment/coverage; human identity and coverage corrections now rerun M3 over stored M1/M2 records with lineage | No trained M1/M2 outputs or measured view-quality calibration |
| 8 M7 | Offline synthetic table build, eligibility/support rules, pinned runtime lookup and read-only container mount | No real-price accuracy claim; LightGBM/RQ4 experiments remain |
| 9 M9 | Pure review integrated into API, correction replay, separate review overlays, accepted human scope, workbench controls, durable request retry, frozen reports | Real-artifact overlays, comprehensive accessibility/usability and full offline synchronisation are not claimed |

## Checks and evidence

- Fresh full Python run: **1287 passed, 1 skipped**, two dependency deprecation warnings, 71.19 seconds.
- TypeScript check and Vite production build passed. WSL Git diff whitespace check passed.
- Both Compose profiles passed configuration validation. Docker images built. Isolated project cmev-codex-review, web port 18080, ran PostgreSQL, Redpanda and MinIO.
- Lean: six long-running services healthy; offline cost bootstrap exited successfully.
- Full: API, web, database, broker, object store, orchestrator, fixture producers and consolidator healthy. Combined worker stopped before switching.
- tests/e2e/baseline.mjs passed against lean Docker: synthetic upload persisted to object storage; new claim processed through Kafka; fixture provenance; lost acknowledgement after note commit survived reload/replay without duplication; mark confirmation created input/assessment 2; finalize froze review 2; browser PDF and mobile overflow checks passed.
- tests/e2e/review-controls.mjs passed in local development and full Docker: accepted scope with human operation/absent-amount reason; row correction; dismissal without changing findings; completeness reassessment; conflict draft retained through reload/recovery; tablet overflow check. The fixture seed was retained across runs, so previously saved additions/dismissals were verified rather than resubmitted on reruns.
- Backend tests additionally cover identity/coverage summary-only reruns with original observations, missed marks, revised amounts, unchanged-content dismissal carry, stale requests and immutable prior assessments.
- Rendered browser PDF inspection exposed clipping/overlap in the old fixed footer. Print now uses CSS page margin boxes for the complete pinned version list and page numbers, with reserved footer space. Chromium is the verified print engine.

Generated evidence is ignored under artifacts/evaluation/ui-baseline/ and artifacts/evaluation/ui-review/. Final layout checks use ui-review/frozen-report.pdf; earlier baseline exports predate the footer correction.

## Recorded implementation decisions

- POST /claims/{id}/assessments/{revision}/review-actions is the typed action entry point. Existing notes and mark-decision URLs remain compatibility adapters.
- Decision-changing actions append an audited correction and create a new input/assessment. Notes/dismissals change only review revision. Corrections retain the assessment's pinned cost table.
- Finalize freezes the presented review revision without incrementing it. The original frozen pairing remains immutable.
- An accepted addition is separate surveyor scope. It is not fabricated into a printed row, given a page box, or assigned an invented operation/amount.
- Carried dismissals remain separate overlays. Only the same entry with verified unchanged finding content inherits a dismissal; origin action/finding metadata remains visible.
- IndexedDB stores pending requests before sending, scoped to actor/claim, with original path, revisions, payload and idempotency key. One unresolved request is allowed per scope. Retry is explicit; stale conflicts require refresh/reapplication. Note/amount drafts survive reload. This is not offline evidence-file synchronisation.
- The split runtime uses the same image and handlers. A 100 ms idle poll avoids accumulating a one-second delay for every combined-worker consumer group.
- Installed images explicitly locate M3/M9 configurations beside M8/taxonomy configuration rather than assuming source-tree-relative paths.

No trained model was run, no real claim data was used, and no real repair-price, fraud, accuracy or productivity result is claimed.
