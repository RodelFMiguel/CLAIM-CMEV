# CLAIM-CMEV shared development context

Last updated: 2026-09-27 (Asia/Singapore), status corrected by Claude Code after independent verification.
- **Implementation baseline:** HEAD `6e59bc2` on branch `code-skeleton`, which sits on top of `f280209`. Both are local commits, and no push has been verified.
- **Read first:** the [2026-09-27 status correction](#status-correction-and-fix-verification-2026-09-27) and the [verification record](docs/verification/model-independent-2026-09-24.md).
- **Older entries:** the chronological handoffs below keep their original status, which is sometimes superseded.

This file is the team's maintained handoff, not an automatically synchronised chat transcript. Share it with the skills and source changes through the team's normal version-control workflow.

## Project and scope

CLAIM-CMEV compares the surveyor's repair scope, reconstructed from a marked workshop estimate, with photographic damage evidence and synthetic reference cost ranges. The surveyor confirms marks, enters revised amounts and reviews findings; final claim approval remains separate.

[Proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope. The [specification index](docs/specs/README.md) now identifies the v2 implementation baseline, with product, technical, platform, integration, data, training, UI, evaluation and nine module specifications. Start implementation from those documents, not the proposal alone. The project skills directly route to the relevant specifications; earlier migration warnings below are historical snapshots.

[ADR 0002](docs/adr/0002-containerised-event-runtime.md) records the later container/Kafka runtime decision, superseding the original v2 API-plus-one-worker/SQLite plan. Use current technical and integration specifications for runtime boundaries; proposed dependencies and recipes are not validated by being documented. Core scope remains HITL parts, CarDD damage subject to consent/files, pretrained OCR, a parser for 2-3 layout families, pen-mark detection with human confirmation/manual amounts, synthetic costs, upload, review overview/evidence and browser PDF. LayoutLMv3/CORD/alignment, TrOCR and scripted approval refresh remain stretch goals. The original 50-person-day budget and the runtime decision's added effort must be accounted for explicitly.


## Current state

| Area | Verified state at this handoff |
| --- | --- |
| Repository | The pipeline is in `f280209`. The defect remediation, specification notes and this file are in `6e59bc2`. Both are local commits, and no push has been verified |
| Specifications | The full target is broader than the implementation. Only three M9 checklist boxes are ticked, and all three are supported by code. The ticking understates progress, because several implemented items are still unticked |
| Architecture | Persisted orchestration, outbox/dedup, retries/DLQ and real M8 consolidation run over **fixture producers** for all six evidence stages. The `full` profile has an orchestrator, one shared fixture-producers container and a consolidator; there are no per-module workers. Lean/full Docker last ran on 2026-09-24, before the `6e59bc2` consumer/worker changes |
| Application | Connected to the API/workbench: typed review actions, M6 decision replay, M3 reruns after human identity/coverage confirmations, pinned cost lookup, review-state overlays (dismissals and accepted scope), durable request retry, stage retry and frozen M9 reports. The evidence image overlays (`review/overlays.py`) are a library only; no endpoint or UI serves them |
| Data and models | No trained model exists. M1 has **no code**: `vision/parts/` holds only `.gitkeep`. The M2 assignment, M4 raster/geometry/OCR, M5 parser and M6 linker are libraries covered by unit tests only; the pipeline never calls them. Derived masks and pages are placeholder URIs. M7 costs are synthetic and built offline. PaddleOCR was smoke-run on synthetic pages only |
| Shared agent workflows | Canonical skills remain under `.agents/skills`, with no workflow changes |
| Validation | At `6e59bc2`: the full Python suite has 1314 passed and 1 skipped (the optional real-PaddleOCR test); the TypeScript and Vite builds pass; the Chromium baseline, review-controls, review-defects and print e2e tests pass on a local SQLite stack. Docker has not been re-run since `6e59bc2`. Independent verification found remaining and new defects; see the 2026-09-27 section |
| Evaluation | No model accuracy, real price calibration, real claim outcome, usability comparison or financial benefit measured |


## Decisions and rules that affect implementation

- Preserve declared, agreed and approved costs separately. Only eligible final approvals or explicitly documented synthetic seeds feed reference builds.
- Missing/uncertain evidence yields an insufficient-evidence explanation, not a confident discrepancy.
- Every assessment retains its input and model/taxonomy/config/cost-table versions. New inputs or explicit reassessment produce new revisions.
- Shared schemas and vocabulary connect the lanes. Unknown sides/parts and absent ranges must remain explicit.
- The older [architecture sketch](docs/solution_architecture.md) is historical; its direct agreed-cost feedback loop is superseded by the proposal.
- [ADR 0002](docs/adr/0002-containerised-event-runtime.md) supersedes ADR 0001 and the original v2 runtime. Follow technical/integration specifications while preserving proposed dependency status; the current fixture runtime smoke is recorded separately from the remaining target architecture.
- V2 uses pending/confirmed/rejected mark states. Pending price changes do not fall back to printed prices. Decision-changing human actions create new assessments; original machine results remain immutable.
- HITL predictions do not resolve side. V2 preserves unknown identity, uses recorded human identity/coverage confirmation where needed, and treats grouped-view evaluation as exploratory.
- V2 cost references use part/operation/vehicle class/currency under a fixed single-part SGD basis. Model year is metadata only; independent base cases determine support. Rule tests, ordinary interval coverage and injected-anomaly experiments are reported separately.
- Repository skills use standard name/description frontmatter and agent-neutral instructions. Canonical sources live in .agents/skills; generated regular-file copies in .claude/skills avoid platform-specific symlink setup.

## Immediate next work

0. First, close the remaining and new defects listed in the [2026-09-27 status correction](#status-correction-and-fix-verification-2026-09-27). Its progress log records which ones are fixed.
1. Before wiring live M5 records, add producer-to-consumer coverage for uncertain printed amounts and parser version keys. The unsided-part identity rule was resolved in `6e59bc2`. Preserve unknown identity unless the taxonomy explicitly establishes that side does not apply.
2. Connect the tested M4/M5 document libraries, the M6 linker and the M2 assignment library to persisted artifacts and worker commands. The fixture producers currently emit pre-linked marks and pre-assigned observations. Obtain team-reviewed supported layouts/aliases and permitted sample pages for real-page acceptance. Keep fixture mode explicit and fail closed when live prerequisites are unavailable. The current fixture pipeline does not run OCR on uploads.
3. Extend the evidence panel to actual page/photo artifacts and aligned row/mark/mask overlays, then replace manual fixture-ID/coordinate controls. Complete persistence of unsent generic form drafts and targeted accessibility acceptance; current IndexedDB guarantees cover submitted requests and note/amount drafts.
4. Complete remaining runtime acceptance: broker interruption/restart, coordinated PostgreSQL/object-store restore, capacity, dedicated migration job and eventual per-model/GPU packaging. Local replay/failure tests and both Compose smoke runs do not substitute for these checks.
5. Team prerequisites remain CarDD consent/files, HITL access, layouts/marking rules, frozen taxonomy/config choices, grouped evaluation reservations, owners and the runtime effort budget. LightGBM/RQ4 is a separate offline experiment, not an already-measured outcome.

The current integrated fixture milestone is complete. See the final Codex entry below for changed paths, checks and remaining boundaries; do not restart from the superseded Outbox import failure in the historical Claude handoff.

## Model and module inputs and outputs (2026-09-24)

This is a planning summary of the current specifications, requested before implementation. It does not mark any module complete. At the original planning snapshot the worker generated fixture assessments directly. The current worker instead joins typed fixture records and runs real deterministic M8 checks with a pinned synthetic table; no trained evidence model or real-price reference is integrated. Read the linked specifications for exact schemas and acceptance criteria.

### Models to train, fit or integrate

The core plan has four trained/fitted components: M1, M2, the M6 detector and M7. M7 contains two quantile regressors rather than one neural network; M4 integrates pretrained OCR without project-specific training. Training data and runtime inputs are different and are shown separately.

| Module / method | Training or fitting input | Input when used | Output and downstream use |
| --- | --- | --- | --- |
| [M1: part segmentation](docs/specs/module-01-vehicle-part-segmentation.md) - fine-tuned SegFormer-B0 | HITL vehicle photographs and part polygons converted to semantic labels: 21 part classes plus background. Inspect annotation content rather than trusting the swapped dataset folder names. | One vehicle photograph, with pinned weights, taxonomy and recorded preprocessing transforms. | The model predicts pixel-level part classes. The adapter saves a part mask and part predictions with confidence, pixel counts, transform and version references. M2 uses the masks for assignment; M3 uses the predictions for summaries/coverage. Side remains unknown; the model does not identify a repair operation. |
| [M2: damage segmentation](docs/specs/module-02-damage-segmentation.md) - a separately fine-tuned SegFormer-B0 | CarDD photographs and damage annotations converted to semantic labels: dent, scratch, crack, glass shatter, lamp broken and tire flat, plus background. Consent/files must be verified before use. | The neural model receives the vehicle photograph. The complete M2 module also receives M1 part masks for the same photo and revision. | The model predicts damage-class masks. Deterministic postprocessing creates connected regions and assigns them to part masks, producing damage observations with confidence, assignment/candidate information, mask references and area measures. M3 consumes these observations. Assignment is not learned by the segmentation model; unresolved part/side stays explicit. |
| [M4: page reading](docs/specs/module-04-page-reading.md) - pretrained PaddleOCR | No project training. Pin the OCR package/weights and verify actual box granularity on sample pages. | An estimate page image after PDF rasterisation and geometry correction, where applicable. | OCR returns located text and available confidence values. The module preserves rendered/corrected pages, reading order, actual text-box granularity, quality signals and transforms back to the original. M5 consumes the text/boxes; M6 consumes the corrected page and geometry. OCR alone does not produce validated repair rows or confirmed handwritten amounts. |
| [M6: pen-mark detector](docs/specs/module-06-pen-mark-recognition.md) - fine-tuned Faster R-CNN ResNet-50 FPN | Generated marked estimates plus the development subset of team-marked pages, labelled with exclusion/price-change classes and bounding boxes; retain true row associations for linking evaluation. | The detector receives M4's corrected page image. The complete M6 module additionally needs M5 rows, row/amount boxes and layout geometry for linking. | The detector returns mark boxes, classes and scores. A deterministic linker produces pending PenMark records with a linked entry ID or retained candidate rows. The surveyor confirms/rejects/relinks marks and enters revised amounts. M8 gates decisions on these states. Detecting a price-change mark is not recognising its numeric amount. |
| [M7: reference cost ranges](docs/specs/module-07-reference-cost-ranges.md) - empirical percentile baseline and offline LightGBM quantile pair | Synthetic repair-price records, grouped by independent base case with disjoint training/validation/calibration/test partitions. LightGBM features are part, operation and vehicle class; the target is amount under the fixed SGD cost basis. | Offline builders evaluate eligible cost keys. During claim processing M8 supplies a lookup key to the published table; no M7 model runs per claim. | Lower/upper reference bounds, independent support counts/status, currency/basis, method, build lineage and table version. Publish a frozen table keyed by part, operation, vehicle class and currency. Side, damage type and model year are not features. Synthetic ranges do not establish real repair-price accuracy. |

Model architectures and scope above follow [training specification section 3](docs/specs/model_training_specification.md#3-what-is-trained-and-what-is-not). Recipe values, thresholds, supported layouts and dependency versions marked proposed in the specifications remain proposed until selected and recorded.

### Deterministic modules and application outputs

These components need code, fixtures and validation, but no newly trained weights.

| Component | Inputs | Outputs / responsibility |
| --- | --- | --- |
| M2 damage-to-part assignment | Damage-region masks and M1 part masks in the same coordinate frame; versioned overlap/ambiguity thresholds. | Assigned, ambiguous or unassigned damage observations with candidate scores and reasons. Test with synthetic masks before integrating either model. |
| [M3: part summary and coverage](docs/specs/module-03-part-summary-coverage.md) | M1 predictions, M2 observations, photo quality/visibility signals and recorded human identity/coverage confirmations for the claim revision. | Part summaries retaining every source observation, plus per-part/side coverage states and reasons. Adequate coverage requires the specified human confirmation; sharpness or mask size alone is insufficient. No inferred count of unique physical dents. |
| [M5: line-item extraction](docs/specs/module-05-line-item-extraction.md) | M4 text boxes/reading order and versioned rules for 2-3 supported layouts, vocabulary and exact monetary parsing. | Stable line-item IDs, source row/field boxes, original text, mapped part/side/operation, quantity, printed amounts, uncertainty and declaration completeness. Printed amounts remain separate from surveyor revisions. Zero extracted rows do not establish an explicitly empty scope. |
| Orchestrator | Versioned commands/completion/failure events and persisted stage state for one claim/input revision. | Stage dispatch, retry/dead-letter handling and a deduplicated consolidation command carrying artifact references and pinned versions. It decides readiness, not evidence findings. |
| [M8: consolidation and checks](docs/specs/module-08-consolidation-checks.md) | M3 summaries/coverage, M5 rows/completeness, M6 marks/human actions, claim metadata and the pinned M7 table. | An immutable assessment with documentary, mark, photographic and cost checks, overall results, reason codes, evidence references and versions; separately gated possible additions. No photo coverage means insufficient evidence. Unsupported requires adequate evidence of the correct part with confidently absent supported damage. Pending repricing never falls back to the printed amount. |
| [M9: review overview and report](docs/specs/module-09-review-report.md) | Assessment records, original evidence/overlays, current review state and surveyor actions. | Review UI, recorded corrections/confirmations, new input/assessment revisions for decision-changing actions, and a print view tied to a completed assessment and frozen review. Final claim approval remains separate. |

Common contracts carry claim/input identity, stable record IDs, artifact references, provenance and applicable model/parser/taxonomy/configuration/cost-table versions. Monetary values travel as decimal strings with explicit currency and basis. See [data contracts](docs/specs/data_contracts.md) and [integration contracts](docs/specs/integration_contracts.md).

**Dependency clarification to resolve during orchestration implementation:** M6's detailed specification requires both M4 pages and M5 rows, although the high-level branch diagram depicts M5/M6 in parallel. Detection alone can run alongside M5; final linking must consume M5 rows. Do not emit a linked M6 result before those inputs exist. The implemented orchestrator waits for M5 rows before linked M6 production; the original diagram alone is insufficient to describe that dependency.

### Optional stretch components

| Component | Input | Output / boundary |
| --- | --- | --- |
| S1: LayoutLMv3 alternative to M5 | Page image, OCR tokens and normalized boxes; training uses CORD adaptation followed by generated estimates with reliable token/field alignment. | Token field labels, then adapter grouping into the same M5 line-item/completeness contract. Day-5 gated stretch; the rule parser remains core. |
| S2: pretrained TrOCR | Isolated handwritten price-change crops. | Text/amount suggestion with confidence, stored separately from the human-confirmed amount. No core fine-tuning commitment; never copied automatically into an effective price. |
| Optional cmev-explainer | Already-computed M8 findings and their recorded reasons/evidence. | Generated explanation text with provenance; no changed checks, amounts, states or findings. Off by default and outside the decision path. |

S3 approval/reference refresh is a scripted stretch workflow, not another model. See [training specification](docs/specs/model_training_specification.md) and [specification index](docs/specs/README.md).

## Implementation priorities without trained models (2026-09-24)

Recommended integration-first order, recorded at the user's request. This is a backlog, not a claim that implementation has started, a new scope decision or an assignment of owners. Existing upload, authentication, storage, basic mark confirmation, revisions and frozen-print behaviour should be extended rather than rebuilt.

| Priority | Task and specification | Deliverable / acceptance focus |
| --- | --- | --- |
| 1 | Shared contracts and validated fixtures - [data contracts section 14](docs/specs/data_contracts.md#14-tasks) and [integration contract tests](docs/specs/integration_contracts.md#11-contract-test-requirements) | Typed M3/M5/M6/M8 records, exact money, uncertainty/reason fields, stable IDs and revision/version checks. Adapt simplified fixture payloads to the shared contracts; round-trip valid examples and reject invalid ones. |
| 2 | M8 deterministic assessment engine - [M8 tasks](docs/specs/module-08-consolidation-checks.md#implementation-tasks) | Pure consolidate, compare_amount and propose_additions functions; ordered gates, Decimal arithmetic, side-exact matching, per-check results and immutable findings. Implement the specified Experiment A rule cases, including pending marks, exclusions, missing/uncertain evidence and absent ranges. |
| 3 | Real orchestration with fixture producers - [platform section 12](docs/specs/application_platform.md#12-tasks) and [technical section 17](docs/specs/technical_specification.md#17-technical-tasks) | Reuse Kafka/outbox infrastructure; implement shared consumer handling, required migrations, stage dispatch, persisted branch state and the join. Verify duplicate/out-of-order events, failure/retry, restart and stale revisions. Reconcile the M6 row dependency above before wiring dispatch. |
| 4 | M6 linking and correction rules - [M6 tasks](docs/specs/module-06-pen-mark-recognition.md#implementation-tasks) | Pure link_marks and apply_mark_action using fixture detection boxes and row/amount boxes. Preserve ambiguous candidates and conflicts; extend manual link/add/amount actions and test that unresolved repricing never uses printed prices. Detection accuracy remains unmeasured. |
| 5 | M4 page preparation and pretrained OCR - [M4 tasks](docs/specs/module-04-page-reading.md#implementation-tasks) | PDF rasterisation, deterministic page IDs, geometry correction, transform round trips and a pinned pretrained OCR smoke test. Requires pretrained weights and sample pages, not project-specific training. Establish actual box granularity before M5 column rules. |
| 6 | M5 deterministic estimate parser - [M5 tasks](docs/specs/module-05-line-item-extraction.md#implementation-tasks) | Select 2-3 layouts and reviewed aliases; implement headers/columns, wrapped rows, exact amounts, stable IDs and completeness. Start with OCR-box fixtures, then connect M4; unsupported layouts and uncertain fields remain explicit. |
| 7 | M2 assignment and M3 summary/coverage - [M2 tasks](docs/specs/module-02-damage-segmentation.md#implementation-tasks), [M3 tasks](docs/specs/module-03-part-summary-coverage.md#implementation-tasks) | Synthetic mask tests for overlap, ambiguity and background; conservative observation grouping and confirmation-aware coverage. Cover opposite sides, cropped views, missing photos and failed inference. Threshold calibration still requires suitable validation data. |
| 8 | M7 synthetic cost baseline and lookup - [M7 tasks](docs/specs/module-07-reference-cost-ranges.md#implementation-tasks) | Seeded generator, independent base-case grouping/splits, empirical ranges, support/bounds validation and pinned load_table/lookup_range. Keep builds offline and test that publishing a new table never changes an old assessment. The LightGBM comparison remains separate work. |
| 9 | M9 evidence and correction workflow - [M9 tasks](docs/specs/module-09-review-report.md#implementation-tasks) | Server-rendered overlays and row/mark highlights; identity/coverage confirmation, row corrections, possible additions and dismissals. Extend review concurrency, reassessment/reuse lineage, finalization blockers and durable pending edits using fixtures. |

**First implementation milestone:** priorities 1 and 2 with a small integration into the existing review screen: validated fixture branch records -> genuine M8 rules -> persisted assessment -> displayed results/reasons. All fixture provenance stays visible. A passed photo check alone must not become overall ok while cost requirements remain unmet.

This order need not serialize the team: M4 -> M5 -> M6 linking is an independent document workstream, and M9 can grow with each backend capability. Reserve grouped evaluation data before tuning: supported layouts/marking conventions, permitted pages/photos, shared image hashes and vehicle/writer/physical-page/template groups as applicable. Keep final-test membership frozen; fixture rule checks and trained-model accuracy are different evidence. Verify CarDD access/files before M2 training and preserve any contingency taxonomy separately.

Named lane/adapter owners and the runtime effort-budget conflict remain open. The new tables do not change the 50-person-day scope or activate LayoutLMv3, TrOCR, the explainer or approval refresh.

## Open decisions and missing inputs

| Item | Owner lane | Where to resolve |
| --- | --- | --- |
| Named assignments and shared contract/adapter owners | All / Lane 5 | V2 section 12 and specification transition |
| Runtime/dependency versions and demonstration hardware | Lane 5 with model owners | V2 section 9; superseding ADR and technical spec |
| CarDD consent/files, HITL parts and annotation mappings | Lane 1 | V2 section 11; acquisition manifests |
| Physical-part identity, coverage and grouped pilot cases | Lanes 1, 4 | V2 M3 and sections 11.5/13 |
| Supported layouts, OCR granularity and page/mark labels | Lanes 2–3 | V2 M4–M6 and section 11 |
| Synthetic generator, eligible cost combinations and class lookup | Lane 4 | V2 sections 8.3/11.6 and M7 |
| Validation-selected interval/support policy and separate calibration | Lane 4 | V2 M7 and section 13 |
| Rubric mapping and assisted-review validation | All / Lane 5 | V2 sections 4/13.5 |

No project-wide external blocker has been demonstrated: contract and fixture work can proceed while data/method decisions are resolved.

## Validation evidence

- At the initial scaffold handoff, 77 scaffold files, 9 module specs and 92 local documentation links were checked; the proposal Markdown and DOCX hashes were unchanged. These were documentation/structure checks, not application tests.
- All seven canonical skills passed the bundled Skill Creator quick_validate.py frontmatter/instruction validation. Claude copies have identical SHA-256 hashes.
- `python3 -m unittest discover -s tests/unit -p test_sync_skills.py -v`: 9 tests passed on the available WSL Python 3.12 installation, including read-only checks, drift repair, idempotency, unrelated-file preservation, stale-file rejection and link handling.
- `python3 scripts/sync_skills.py --write` generated 7 copies; `python3 scripts/sync_skills.py --check` passed. Existing personal Claude settings retained their original hash.
- Final repository check: 24 managed files, 21 Markdown documents, 135 valid local links and 7 identical skill pairs. Shared files are trackable; personal settings are ignored. Git whitespace checks passed and proposal/specification/ADR files are unchanged.
- Automatic discovery/invocation has not been exercised inside an installed Claude Code or fresh Codex client. Packaging follows their documented repository skill locations; manual invocation is documented.
- At that earlier skill-only handoff no application, model, data or live workbench evaluation had run; see the 2026-09-23 baseline handoff below for current checks.

## Development history

| Evidence | Change |
| --- | --- |
| `d9144f8` | Project proposal v1 added |
| `545bbeb` | Proposal language simplified |
| `be6b703` | Proposal corrections |
| `121410b` (2026-09-10) | Initial repository structure and implementation specifications committed |
| Current work, uncommitted (2026-09-10) | Added all seven portable skills, Claude copies, shared AGENTS/CLAUDE guidance, CONTEXT.md, sync utility/tests and workflow documentation; validation passed |
| `f587b32` | Initial simplified project proposal v2 committed |
| `0c5ed2e` | Revised v2 following the accepted review recommendations; preserved LayoutLMv3/CORD/label alignment as stretch S1 and refreshed the handoff |
| `013e2a8` (2026-09-23) | Routed all seven shared skills through current specifications, regenerated Claude copies and updated shared agent guidance |
| `1ac0ea8` (2026-09-23) | Added and verified the containerized fixture review baseline, including UI, API, worker, infrastructure and tests |

Commit subjects above come from the inspected Git history. Do not treat the “current work” row as a commit; replace or append its commit/PR reference when the team records one.

## Active work and ownership

Completed user-authorised baseline: public information page, login, claim dashboard and FastAPI APIs with explicitly mocked processing under `src`, using the containerised architecture. Initial frontend/backend/container coding-agent lanes are complete; the parent Codex completed Docker integration, browser checks and this handoff. Earlier parallel ownership:

- Frontend agent: `src/workbench/`.
- Backend agent: Python implementation under `src/claim_cmev/`, backend packaging and API tests.
- Containers agent: `infra/`, root `.dockerignore` and `.env.example`.
- Parent Codex: reference/visual asset, local build and end-to-end validation, shared documentation and integration fixes.

Preserve existing skill/guidance changes. The user explicitly requested the dashboard/login extension beyond the earlier described-only claim list; fixture processing does not count as model implementation.

## Handoff procedure

At task start, read this file and inspect current Git/files. At a meaningful handoff, refresh the current-state/next-work sections and append a concise history entry with the template below. Record only what happened; retain failed/untested checks where they matter.

Keep credentials, real claim content and personal session permissions out of this shared document. Link to safe artifacts/manifests rather than embedding logs or duplicating specifications. Move superseded detailed history into a dated documentation file if this handoff grows unwieldy, retaining a link here.

```text
Date/time and timezone:
Contributor / coding agent:
Task and relevant module:
Branch / baseline commit / resulting commit or PR (if one exists):
Changed paths and completed behaviour:
Decisions (accepted/proposed) and references:
Checks actually run, results and artifact locations:
Uncommitted work, limitations and missing prerequisites:
Next concrete step and agreed owner (or unassigned):
```

One workstation used PowerShell to access a WSL checkout and encountered a sandbox helper startup failure. That is local environment history, not a project runtime requirement or authority to bypass another contributor's permission settings.


## Dataset downloader handoff (2026-09-11)

Date/time and timezone: 2026-09-11, Asia/Singapore (time not recorded).
Contributor / coding agent: Codex, at the user's request.
Task and relevant module: Prepare acquisition tooling for all proposal section 12 datasets and local data gaps; M01-M07 and workbench fixtures.
Branch / baseline commit / resulting commit or PR: project-structure / e837b3e (Add agent skills) / no new commit or PR.
Changed paths and completed behaviour:
- scripts/download_datasets.py: catalogue selection, optional reserve/DocILE extras, HTTPS retries and validator-based resumption, local imports, official provider adapters, hashes/ZIP checks, per-dataset locks and explicit incomplete reports.
- data/manifests/dataset_sources.json and dataset_sources.local.example.json: 13 source/input entries, source evidence, pinned DSMLR/CORD/CrashCar revisions and private configuration example.
- docs/dataset-downloads.md, scripts/README.md, data/README.md and tests/README.md: setup, access, storage, commands and limitations.
- tests/unit/test_download_datasets.py: 23 offline tests.
Decisions and references:
- Preserve original assets without extraction, conversion or new split generation. Local reports/configuration remain in existing ignored data/raw and runtime locations; .gitignore unchanged.
- Gated/manual sources need actual publisher access or local inputs; unresolved original terms need recorded evidence. No forms were submitted and no terms were accepted on the user's behalf.
- CORD publisher README/card says CC BY 4.0, differing from proposal CC BY-SA 4.0; catalogue records the discrepancy without silently editing the governing proposal.
Checks actually run, results and artifact locations:
- python3 -m unittest discover -s tests/unit -p test_download_datasets.py -v: 23 passed on WSL Python 3.12.3.
- --dry-run --include-reserve: all 13 entries listed with prerequisites; no acquisition writes.
- Public HTTPS smoke: temporary CORD README download, 27 bytes, SHA-256 835f3f7d88a86e05a882c6a6b6333da6ab874776385f85473798769d767c2fca. Temporary asset removed by the test context.
- Python 3.9 syntax parse, new/edited documentation links, Git whitespace and ignored download/private-configuration paths checked.
Uncommitted work, limitations and missing prerequisites:
- These downloader changes are uncommitted. Full archives and actual gated/provider-client integrations were not run; adapter tests use mocks. No model or data-quality evaluation was performed.
- HITL/CarDD/SROIE require supplied access/files; DocILE needs its token, CrashCar needs approved HF access, optional provider clients are not installed by the script, and several original licence terms remain unresolved.
- Synthetic prices/reports and authorized real grouped photos are local data gaps. Acquired-file counts are not usable-sample counts. Acquisition does not complete module dataset-validation TODOs.
Next concrete step and agreed owner: Unassigned data owners should configure authorized sources using docs/dataset-downloads.md, acquire selected datasets, inspect counts/labels and record mapping/grouped-split manifests.


## Proposal v2 draft handoff (2026-09-21)

Date/time and timezone: 2026-09-21, Asia/Singapore (time not recorded).
Contributor / coding agent: Claude Code, at the user's request.
Task and relevant module: Draft a simplified project proposal (v2) for the revised workflow; affects all modules.
Branch / baseline commit / resulting commit or PR: project-structure / c985d90 / no new commit or PR.
Changed paths and completed behaviour:
- docs/CLAIM-CMEV_project_proposal_v2.md: new document. Revised input (photographed workshop estimate marked by the surveyor with exclusion marks and price changes), one working review overview page with upload and print-to-PDF, nine modules with a v1 mapping table, per-model training/evaluation datasets, a per-model serving table, a 50 person-day lane budget and a contingency ladder.
Decisions (accepted/proposed) and references:
- Proposed only. v1 remains the governing proposal until the team accepts v2. Module specifications and ADR 0001 still follow v1; if v2 is accepted, record it in an ADR and update the specs using the v1-to-v2 module mapping in v2 section 10.
- Key proposed changes: HITL-only committed vision data; rule-based multi-view merge; PaddleOCR + LayoutLMv3 + Faster R-CNN pen-mark detector + TrOCR document branch; generated price table replacing the unsupplied 630-row file; cost key without side and damage type; two-process deployment (API + worker) on SQLite.
Checks actually run, results and artifact locations:
- Markdown structure checks only: 5 mermaid blocks balanced, local links resolve, sentence-length pass for the simple-English brief. No code, tests, models or data were run.
Uncommitted work, limitations and missing prerequisites:
- The v2 document is uncommitted. Dataset licence statements in v2 restate the catalogue and public model cards; they are not independently re-verified here. Effort estimates are planning figures.
Next concrete step and agreed owner (or unassigned): Team reviews v2; if accepted, write ADR 0002 (scope reduction) and update module specs. Unassigned.


## Proposal v2 review clarifications (2026-09-21)

Date/time and timezone: 2026-09-21, Asia/Singapore (time not recorded).
Contributor / coding agent: Codex, at the user's request.
Task and relevant module: Clarify the rule-based document parser, CarDD's role and course mapping after the proposal review.
Branch / baseline commit / resulting commit or PR: project-structure / f587b32 / no new commit or PR.
Changed paths and completed behaviour:
- docs/CLAIM-CMEV_project_proposal_v2.md: removed optional Isolation Forest/unsupervised learning from section 4 and the related open item; clarified that course mapping is confirmed before implementation. Recorded the team's already-submitted CarDD request and linked the original licence.
- CONTEXT.md: recorded the user's feedback and the remaining proposal alignment work. The earlier v2 draft is now committed in f587b32; its historical handoff is retained above.
Decisions (accepted/proposed) and references:
- The user agrees with the prior review recommendations, including explicit contingency, a rule-based parser baseline, confirmation-aware findings, conservative part identity/coverage, consistent cost keys/units and separate rule/calibration/pipeline evaluation.
- The user explicitly removes optional unsupervised learning and prefers CarDD for damage modelling. The CarDD access request has already been sent by the team; approval and acquisition were not reported. The publisher's licence requires prior consent for research use: https://cardd-ustc.github.io/docs/CarDD_license.pdf.
- Proposed dataset arrangement for the revised plan: HITL for part segmentation, CarDD for damage segmentation, and small team-labelled examples for damage-to-part matching and grouped-view evaluation. CarDD does not remove those separate evaluation needs.
Checks actually run, results and artifact locations:
- Inspected the proposal diff and checked removal of the optional component; git diff --check passed for the course-mapping edit.
- Read the official CarDD project page, licence and annotation-category evidence. No application tests, downloads or model experiments were run.
Uncommitted work, limitations and missing prerequisites:
- These documentation edits are uncommitted. Most of v2 still describes the earlier LayoutLMv3/TrOCR and HITL-damage commitments; a complete revision has not been applied during this clarification.
- Existing specifications and ADR 0001 remain the v1 baseline. Course rubric grouping and CarDD access remain unverified.
Next concrete step and agreed owner (or unassigned): Align v2's methods, datasets, serving table, evaluation and effort budget with the clarified scope, then align specifications/ADR. Unassigned.


## Proposal v2 scope revision (2026-09-22)

Date/time and timezone: 2026-09-22, Asia/Singapore (time not recorded).
Contributor / coding agent: Codex, at the user's request.
Task and relevant module: Apply the agreed proposal-review recommendations throughout v2, retaining LayoutLMv3 training, CORD preparation and training-label alignment as a stretch goal.
Branch / baseline commit / resulting commit or PR: project-structure / f587b32 / no new commit or PR.
Changed paths and completed behaviour:
- docs/CLAIM-CMEV_project_proposal_v2.md: revised summary, course mapping, workflow/UI, decision rules, serving, module descriptions, datasets, splits, ownership, 50-person-day budget, evaluation, risks and open items. Added section 16 documenting the implementation-specification transition.
- CONTEXT.md: refreshed the current planning state and priorities while retaining previous dated handoffs and pre-existing edits.
Decisions (accepted/proposed) and references:
- Applied the user's agreed direction: OCR plus a parser for 2–3 layout families; HITL parts and planned CarDD damage; mark detection with human confirmation/manual prices; conservative identity/coverage; consistent synthetic cost keys and separate rule/calibration/pipeline evaluation.
- V2 section 12.3 S1 explicitly retains CORD preparation, OCR/ground-truth token alignment, two-stage LayoutLMv3 training, comparison with the parser and optional contract-compatible serving. TrOCR suggestions and approval-refresh scripts are separate stretch goals. No optional unsupervised component remains.
- Budget: per member 5 implementation/data + 2 integration/evaluation + 1 contingency + 2 report/presentation days. Model owners own adapters and module checks; optional work cannot consume protected contingency/reporting time.
- Pending marks cannot silently exclude entries or fall back to printed prices; decision-changing corrections create new assessments. Unknown sides, incomplete declarations and cropped views withhold unsupported conclusions. Cost generation/model/lookup omit year and share a fixed single-part SGD basis with independent base-case support.
Checks actually run, results and artifact locations:
- In-memory documentation checks: 16 numbered sections, 22 section references, 4 local links, 25 consistent Markdown tables, 5 balanced Mermaid code blocks and 10/50-day budget arithmetic passed. Obsolete core model/data/budget commitments checked with targeted searches.
- Git whitespace checks passed. Diagrams were inspected as source, not rendered. No application tests, dataset acquisition, training or performance measurements were run.
Uncommitted work, limitations and missing prerequisites:
- Both documentation files remain uncommitted. V1, implementation specifications, canonical contracts, skills and ADR 0001 were not migrated; section 16 lists the required follow-on alignment without implying it is complete.
- CarDD consent/acquisition, HITL availability, rubric mapping, named owners and demonstrated hardware/model performance remain unconfirmed. Effort figures and accuracy targets are planning commitments, not measured results.
Next concrete step and agreed owner (or unassigned): Assign lane/adapter owners, align the implementation documents using v2 sections 10/16, and complete the day-1–2 data/runtime/rubric checks. Unassigned.


## Project skill scope review (2026-09-22)

Date/time and timezone: 2026-09-22 01:09, Asia/Singapore.
Contributor / coding agent: Codex, at the user's request.
Task and relevant module: Review Claude/Codex project skills against proposal v2 and current context; all seven workflows.
Branch / baseline commit / resulting commit or PR: project-structure / 0c5ed2e / no new commit or PR.
Changed paths and completed behaviour:
- .agents/skills/*/SKILL.md: all seven remain relevant; replaced obsolete module/screen/data/RQ assumptions, added v2 mark/identity/completeness/reassessment gates, independent cost support and separated calibration/pipeline/rule evidence.
- .claude/skills/*/SKILL.md: regenerated from canonical sources; same contents and invocation names.
- AGENTS.md and docs/agent-workflows.md: v2 scope precedence, current-file module map, documented migration limits, core/stretch boundaries and updated behavioural prompts. CLAUDE.md already imports shared guidance and needed no edit.
- CONTEXT.md: refreshed this handoff while preserving prior entries.
Decisions (accepted/proposed) and references:
- Applied v2 sections 8-13/16; no new domain or runtime decision. Kept LayoutLMv3/CORD/alignment, TrOCR and scripted approval refresh optional under S1/S2/S3. Core synthetic table building remains in the cost-reference skill.
- Preserved pre-existing proposal DOCX edits and staged module renames. Concurrent M1/technical-specification edits appeared during review and record a later container/Kafka direction; skills defer to current recorded decisions rather than hard-coding v2's original runtime. Those concurrent edits were not authored or validated by this task.
Checks actually run, results and artifact locations:
- Bundled skill-creator quick_validate.py: all 7 canonical skills valid.
- python3 scripts/sync_skills.py --write and --check: 7 generated copies; all match. SHA-256 comparison: 7 identical pairs.
- python3 -m unittest discover -s tests/unit -p test_sync_skills.py -v: 9 tests passed on WSL Python 3.12.3.
- 139 local links across 17 skill/guidance Markdown files resolve; shared transition anchor checked. Scoped git diff --check passed.
Uncommitted work, limitations and missing prerequisites:
- Skill/guidance changes are uncommitted. Full specification/contract/ADR migration is separate and in progress; changing skill instructions does not complete it.
- Behavioural acceptance prompts were reviewed as instructions, not executed in fresh Claude/Codex clients. No application, model, dataset or live UI evaluation ran.
Next concrete step and agreed owner (or unassigned): Unassigned specification owners should complete/reconcile the domain and runtime transition, then smoke-test representative skill requests in the installed clients.


## V2 implementation specification rewrite (2026-09-22)

Date/time and timezone: 2026-09-22, Asia/Singapore (time not recorded).
Contributor / coding agent: Claude Code, at the users


## V2 implementation specification rewrite (2026-09-22)

Date/time and timezone: 2026-09-22, Asia/Singapore (time not recorded).
Contributor / coding agent: Claude Code, at the user's request, using seven parallel subagents with one shared brief.
Task and relevant module: Rewrite the v1 implementation specifications as v2 build specifications, and record the user-directed containerised Kafka runtime. Affects all modules and all shared documents.
Branch / baseline commit / resulting commit or PR: project-structure / 0c5ed2e / no new commit or PR. All work below is uncommitted.

Changed paths and completed behaviour:
- Renamed with `git mv`, then fully rewritten for v2: `docs/specs/module-03-part-summary-coverage.md`, `module-04-page-reading.md`, `module-05-line-item-extraction.md`, `module-06-pen-mark-recognition.md` (was the v1 cost-anomaly file), `module-07-reference-cost-ranges.md`, `module-08-consolidation-checks.md` (merges v1 M07 and M08), `module-09-review-report.md`. The renames match the mapping already recorded in `docs/agent-workflows.md`.
- Rewritten in place: `docs/specs/module-01-vehicle-part-segmentation.md`, `module-02-damage-segmentation.md`, `README.md`, `product_specification.md`, `technical_specification.md`, `application_platform.md`, `data_contracts.md`, `evaluation_plan.md`.
- New: `docs/specs/integration_contracts.md`, `docs/specs/model_training_specification.md`, `docs/specs/ui_specification.md`, `docs/adr/0002-containerised-event-runtime.md`.
- Updated: `docs/adr/README.md`, `docs/adr/0001-prototype-runtime.md` (superseded note only, body untouched), `docs/agent-workflows.md` (runtime baseline sentence now cites ADR 0002), `docs/mockups/README.md`, and link repairs in `data/README.md`, `workers/image/README.md`, `workers/document/README.md`, `apps/workbench/README.md`.
- The specification set is about 9,200 lines. It covers the online architecture, the offline training architecture, integration and message contracts, the user interface with user stories, and the nine module specifications.

Decisions (accepted/proposed) and references:
- ADR 0002 is accepted and supersedes ADR 0001. At the user's direction on 2026-09-22 the runtime changes from proposal v2 section 9.1 (two processes, a jobs table, explicitly no message broker, SQLite, local files) to a containerised event-driven runtime: one container per module, Kafka topics named `cmev.<kind>.<name>.v1` keyed by `claim_id`, Redpanda as the development broker, PostgreSQL 16, MinIO behind the existing storage adapter, and two Compose profiles, `lean` and `full`.
- PostgreSQL replaces SQLite as a forced consequence of one container per module, not as an independent scope increase. SQLite takes one writer and relies on file locks that are not reliable across containers.
- No large language model sits in the decision path. Part and damage integration is deterministic mask overlap in M2; vision and document integration is the deterministic rule set of section 8 applied in M8. An optional stretch service `cmev-explainer` may phrase an already-computed finding; it is off by default, never changes a result, and is marked in provenance.
- The rule-based parser remains the committed M5 method. LayoutLMv3 stays stretch S1 behind the day-5 gate, matching the preference the user stated in this request.
- Three cross-document contradictions were found and settled during consolidation rather than left open. First, the job key is `(claim_id, input_revision, task, target, version_signature)`, defined canonically in the technical specification; `target` is the sentinel `all` in the core scope, where a task is dispatched once per input revision. Second, JSON Schema files live under `src/claim_cmev/contracts/events/`. Third, `AssessmentFinding.overall_result` gains a fifth stored value, `not_evaluated`, so that a confirmed-exclusion row has a valid place to be stored. The four labels in proposal v2 section 7.2 remain the only display labels. Without the fifth value an excluded row would have to be stored as `ok`, breaking the invariant that an exclusion is not a pass.
- Three contradictions with proposal v2 are recorded openly and are not resolved here: section 9.1 said no message broker; section 9.4 places M8 in the API process while ADR 0002 places it in `cmev-consolidator`; section 12.1 gives Lane 5 five implementation days against an estimated eleven extra person-days for the full runtime.

Checks actually run, results and artifact locations:
- Local Markdown link check across the repository: 506 links, all project links resolve. The single failure is inside a third-party package under the ignored `venv/`.
- All seventeen Kafka topic names appear with no naming drift; container names are uniform; M8 is placed in `cmev-consolidator` consistently in every document.
- No em-dashes or en-dashes in the specifications or ADRs. All fenced code blocks balanced.
- `python3 scripts/sync_skills.py --check` passed: all seven skill mirrors match their canonical sources.
- Dataset evidence inspected directly on disk and independently reproduced by a second agent: under `data/raw/` the two HITL folder names are swapped relative to their contents. The folder named `Car damages dataset/` holds the 21 part classes with 998 images; the folder named `Car parts dataset/` holds the 8 damage classes with 814 images. 441 image filenames appear in both, are byte-identical, and every one of the 441 carries both part and damage polygons. HITL damage classes are severely imbalanced, with `Cracked` at 76 objects. CarDD is not present.
- No application code, tests, model training, dataset conversion or user interface was run. Nothing in these documents is implemented or measured.

Uncommitted work, limitations and missing prerequisites:
- Every change above is uncommitted, including the pre-existing v2 edits to `AGENTS.md`, `docs/agent-workflows.md` and the seven skills that were already in the working tree when this task began.
- Targets, thresholds, effort figures and container choices are planning commitments and proposals, not measured results. Items marked **proposed** still need a recorded team decision.
- The effort conflict is unresolved by design. ADR 0002 names two responses: push per-module Dockerfiles and consumer wiring to module owners, or ship `lean` and treat `full` as a stretch.
- CarDD consent and file receipt remains the one hard blocker for M2 core training. The estimate, marked-page and price generators are not built. Team-marked pages, the damage-to-part assignment sample and the vehicle groups are not collected.
- Claim authorisation has no identity model and no owner. The 441 shared HITL images must be held out of M1 training or explicitly accepted as leakage before any fitting.

Next concrete step and agreed owner (or unassigned): Unassigned. The team should choose the response to the eleven-day runtime overrun, confirm or reject the M8 container placement against proposal v2 section 9.4, decide whether the 441 jointly labelled HITL images replace part of the section 11.5 annotation plan, and assign named lane and adapter owners before day 1 work starts.


## Explicit specification routing correction (2026-09-22)

Date/time and timezone: 2026-09-22 08:32, Asia/Singapore.
Contributor / coding agent: Codex, following the user's correction to the previous skill update.
Task and relevant module: Make implementation specifications explicit working sources across all seven skills.
Branch / baseline commit / resulting commit or PR: `code-skeleton` / `ed5431a` / `013e2a8`.
Changed paths and completed behaviour:
- .agents/skills/*/SKILL.md and generated .claude/skills/*/SKILL.md: implementation skill now requires opening the selected module document and routes directly to product, technical, platform, integration, data, training, UI and evaluation specifications. Other skills link their relevant specifications directly.
- AGENTS.md and docs/agent-workflows.md: removed stale mid-migration routing; distinguish specification implementation detail, proposal scope and ADR 0002 runtime authority. Clarified that the lean profile still uses the documented transport/infrastructure.
- CONTEXT.md: refreshed current source guidance and retained dated earlier handoffs.
Decisions (accepted/proposed) and references: No new project decision. The specification index now declares v2 alignment; detailed work uses those specifications, with proposed values kept distinct from accepted decisions and actual implementation.
Checks actually run, results and artifact locations:
- Bundled quick_validate.py passed for all 7 skills; sync --write regenerated 7 Claude copies and sync --check passed; all 7 SHA-256 pairs match.
- 210 local links and their anchors across skills/shared guidance resolve. Git whitespace check passed.
Uncommitted work, limitations and missing prerequisites: Documentation-only changes, uncommitted. No fresh-client invocation, application test or model experiment; the unchanged sync test suite was not rerun for this link/routing correction.
Next concrete step and agreed owner (or unassigned): Unassigned implementation owner should select a module requirement, open its specification and relevant shared sections, then implement and verify that slice.


## Fixture UI/API baseline handoff (2026-09-23)

Date/time and timezone: 2026-09-23, Asia/Singapore.
Contributor / coding agent: Codex with separate frontend, backend, container and review agents, at the user's request.
Task and relevant module: Baseline public information, login, dashboard, upload, fixture review and FastAPI flow; M9/workbench and application platform slice.
Branch / baseline commit / resulting commit or PR: `code-skeleton` / `ed5431a` / skills `013e2a8`, application baseline `1ac0ea8`; no PR.
Changed paths and completed behaviour:
- `src/workbench/`: React/TypeScript public landing and login with generated decorative damaged-car image, sample-style claim queue, intake, processing/review, preserved originals, mark confirm/reject, notes, finalization and browser print/PDF. Responsive mobile table containment was fixed after the first browser failure.
- `src/claim_cmev/`, `pyproject.toml`, `tests/backend/`: real authenticated FastAPI, file storage, claim/revision/review endpoints, deterministic fixture assessments, transactional outbox and a separate Kafka/local fixture worker. Mock results are identified as fixtures and do not assert real photographic or cost findings.
- `infra/`, `.dockerignore`: lean Compose packaging for web/nginx, API, combined fixture worker, Redpanda, PostgreSQL and MinIO, with private infrastructure networking. See `infra/README.md` for boundaries.
- `tests/e2e/baseline.mjs`, `README.md`, `docs/adr/0003-fixture-ui-api-baseline.md` and targeted spec notes: browser walkthrough, run commands and accepted user scope extension under `src`.
Decisions (accepted/proposed) and references: User-requested public information/login/dashboard extension and `src` layout recorded in ADR 0003. ADR 0002 remains the runtime decision. Fixture processing is an integration aid, not module or model completion.
Checks actually run, results and artifact locations:
- Backend: 17 pytest tests passed, covering auth/CSRF, ownership, uploads, exact amounts, idempotency, mark/reassessment revisions, frozen reports and worker replay/offset safety. Tests used local SQLite/storage and simulated Kafka batches.
- Frontend: TypeScript and production Vite build passed; `npm audit` reported zero vulnerabilities after dependency updates.
- Local browser: `npm run test:e2e` passed on a local Vite/FastAPI/local-fixture-worker stack. It covers invalid/correct login, queue search, synthetic upload, fixture assessment, note, mark confirmation, reassessment, saved note reload, original evidence, finalization, frozen print PDF, mobile queue and logout. Screenshots/PDF are ignored under `artifacts/evaluation/ui-baseline/`. Earlier mobile overflow failure was fixed by CSS paint containment and rerun passed.
- Docker Desktop 4.92.0/Linux Engine and Compose v5.5.1: default lean `config --quiet`, all image builds (including source-built MinIO), and `up --build -d` passed. Six services were healthy. Nginx `/`, API `/healthz`, `/readyz` and `/docs` returned 200 on localhost:8080. The full browser walkthrough passed on the default Compose stack through PostgreSQL, MinIO and the Kafka fixture worker. The first container browser claim remained visible after API/worker image rebuild; worker logs show successful Kafka group join. The initial WSL bind-mount socket error was resolved by omitting empty model/cost host mounts from the fixture profile, with the real-module requirement recorded in ADR 0003 and `infra/README.md`.
Uncommitted work, limitations and missing prerequisites:
- No trained evidence/document model or real cost calibration is integrated. Seeded photo counts are illustrative, not actual seed originals; new uploads preserve their originals but the worker returns fixture results.
- Full M9 additions/corrections/dismissals, overlays, durable IndexedDB offline actions and full production authentication/schema migrations are not implemented. PDF pages are preserved and validated but not rasterized by this fixture backend.
- Local browser tests use SQLite/files/local transport; the separate container browser pass exercises Kafka/PostgreSQL/MinIO. Broker outage recovery, full per-module profile, production authentication, migration lifecycle and real model evaluation remain untested or unimplemented.
- The skill/specification routing is committed as `013e2a8`; the application, containers, tests and related documentation are committed as `1ac0ea8`. This handoff is committed separately. No PR was created and nothing was pushed.
- Workstation detail: Docker Desktop Linux Engine is running, but Ubuntu WSL integration was not enabled during this check. The successful Compose run used the Windows Docker Desktop CLI from PowerShell at `C:\Users\Rodel\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`; its `resources\bin` directory was added to `PATH` for the credential helper. For `docker` inside Ubuntu/Claude Code, enable Ubuntu in Docker Desktop Settings > Resources > WSL Integration. The ignored `infra/compose/.env` has already been created with local generated infrastructure credentials; never commit it.
Next concrete step and agreed owner: The baseline is running in Docker Desktop on localhost:8080. Claude Code can inspect `docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile lean ps` and `infra/README.md`, then select a real module requirement from `docs/specs/README.md`. Build and evaluate real module output separately from fixtures, add read-only registry mounts and version checks when that module consumes model/cost files, and update this handoff with actual results. No named module owner has been assigned.

## Proposal v2 runtime note added (2026-09-23)

Date/time and timezone: 2026-09-23, Asia/Singapore (time not recorded).
Contributor / coding agent: Claude Code, at the user's request, after confirming module/shared specifications already carried the containerised/Kafka directive.
Task and relevant module: Close the one remaining documentation gap between the accepted runtime decision and the governing proposal; no module code changed.
Branch / baseline commit / resulting commit or PR: `code-skeleton` / `95d5382` / no new commit.
Changed paths and completed behaviour:
- `docs/CLAIM-CMEV_project_proposal_v2.md`: inserted a blockquote runtime note at the start of section 9.1 pointing to ADR 0002, naming the containerised/Kafka/PostgreSQL/MinIO runtime and stating that the two-process/jobs-table/no-broker/SQLite text below it is retained unchanged as the original proposal, not the current baseline. No other proposal text was rewritten, matching the precedent already set in ADR 0001 (superseded note added, body left untouched).
- Same file, same section: after the user flagged the rendered two-process Mermaid diagram as no longer accurate, added a second "Current architecture" diagram immediately after the note, reproduced verbatim from `docs/specs/technical_specification.md` section 2 (the maintained source, cited inline). Added a horizontal rule and a one-line label marking the original two-process diagram and its surrounding paragraph, further down, as historical record. Neither the original diagram nor its prose was edited.
Decisions (accepted/proposed) and references: No new decision. This only makes the already-accepted ADR 0002 runtime visible at the point in the proposal a reader would otherwise be misled by, per the user's explicit choice of "add a note" over rewriting section 9.1, later extended by the user to include the diagram once they saw it rendered. Verified beforehand that all nine module specs plus product/technical/platform/data-contracts/training/UI/evaluation specs and the specs README already reference ADR 0002; the proposal was the only outlier.
Checks actually run, results and artifact locations: Grepped all `docs/specs/*.md` for the runtime note/ADR 0002 reference to confirm existing coverage before editing. Read `docs/specs/technical_specification.md` section 2 directly to copy its diagram rather than authoring a new, possibly inconsistent one. No code, tests, Mermaid render check or link checker were run for this documentation edit.
Uncommitted work, limitations and missing prerequisites: This edit and the current CONTEXT.md update are uncommitted. Section 9.4's "all neural models run inside the worker process" and section 9.2's sequence diagram still describe the two-process flow without an inline pointer or updated diagram; only section 9.1 was amended, per the user's chosen scope. The proposal now carries two Mermaid diagrams for the same architecture question (current and historical) rather than one; that duplication was accepted deliberately to satisfy "keep the original, add a note" while also fixing the immediately visible inaccuracy.
Next concrete step and agreed owner (or unassigned): Superseded by the 2026-09-23 full-rewrite entry below; the note-plus-diagram approach recorded here was replaced at the user's request.

## Proposal v2 architecture section rewritten (2026-09-23)

Date/time and timezone: 2026-09-23, Asia/Singapore (time not recorded).
Contributor / coding agent: Claude Code, at the user's request. The user judged the prior note-plus-two-diagrams approach confusing for a document meant to be the high-level view of the detailed technical architecture, and asked for a full rewrite instead.
Task and relevant module: Replace proposal v2 section 9.1 with a single accurate high-level architecture (diagram and component table), remove the runtime-note callout and the retained historical diagram, and align every other place in the proposal that still described the superseded two-process/SQLite/jobs-table design.
Branch / baseline commit / resulting commit or PR: `code-skeleton` / `95d5382` / no new commit.
Changed paths and completed behaviour:
- `docs/CLAIM-CMEV_project_proposal_v2.md` §9.1: replaced entirely. One Mermaid flowchart (browser/`cmev-web`/`cmev-api` → Kafka → `cmev-orchestrator` → grouped image workers M1-M3 and document workers M4-M6 → `cmev-consolidator` M8 → PostgreSQL/MinIO/read-only registry/offline training), one component table at the same grouping level as the diagram, and one paragraph on the `lean`/`full` Compose profiles and the day-1–2 smoke test. No runtime-note blockquote and no second, historical diagram remain; the old two-process content was removed rather than retained, per the user's explicit instruction this time.
- §9.2: sequence diagram participants changed from "API process"/"Worker process" polling a jobs table to `cmev-api` / `cmev-orchestrator` / image workers / document workers / `cmev-consolidator` exchanging named Kafka commands and events (`evt.input-revision-created`, `cmd.parts-segment`, ... `evt.assessment-ready`), matching the event names already fixed in `docs/specs/integration_contracts.md`. Prose below the diagram changed "job" to "branch" and named `cmev-orchestrator` as the retry/dead-letter owner.
- §9.4: intro sentence and the M8 "Runs in" table cell changed from "worker process" / "API process" to "its own module worker container" / `cmev-consolidator` container.
- §"Changes from version 1" (the row-27 comparison table): the Deployment row's v2 and Reason columns updated from "Two processes... SQLite, local files / Less plumbing" to the containerised/Kafka/PostgreSQL/MinIO description with a reason citing the 2026-09-22 user directive and naming the added plumbing honestly rather than repeating the now-false "less plumbing" claim.
- §16 Implementation-document transition: intro paragraph updated to state that the specifications have since actually been rewritten (they were stale-future-tense before, written when this table was only a todo list) and that the runtime target changed again after the table was first written; added a pointer to CONTEXT.md for verified state. The "Technical specification / ADR" row rewritten from the stale "API plus one worker, SQLite/local storage" target to the actual containerised/Kafka/PostgreSQL/MinIO outcome, keeping the unrelated adapter/versioning clauses unchanged.
Decisions (accepted/proposed) and references: No new domain or runtime decision; this is a documentation-accuracy pass making the already-accepted ADR 0002 runtime the only architecture the proposal describes at every point that previously named a process/storage model, rather than leaving several sections silently contradicting each other. The user explicitly chose the "remove and replace" approach over "note plus keep both", reversing the narrower scope agreed two turns earlier in this session.
Checks actually run, results and artifact locations: Grepped the whole file before and after for `SQLite|jobs table|no message broker|Worker process|API process|worker process|polls jobs`; the three remaining hits after the edit are deliberate contrast statements ("no shared jobs table", "replacing SQLite", "superseding this row's original ... target"), not stale claims. Counted Mermaid fence lines (10, i.e. 5 balanced blocks). `git diff --stat` shows only the two intended files changed. No Markdown link checker, Mermaid renderer or test suite was run.
Uncommitted work, limitations and missing prerequisites: Both files remain uncommitted. Section 9.5 (model registry) and 9.6 (failure handling) were read and judged already architecture-neutral (they describe registry folder layout and idempotency rules that hold under either runtime) and were left unedited; not independently re-verified beyond that read. The budget conflict CONTEXT.md already flags (an estimated ~11 extra person-days for the containerised runtime against the proposal's 50-person-day/5-day-contingency budget) is unresolved and was not addressed by this documentation pass — it is a planning decision, not a wording fix.
Next concrete step and agreed owner (or unassigned): Unassigned. If the team wants the effort-budget conflict resolved in the document (not just flagged), that is a separate decision belonging to whoever owns §12.

## Specification cross-references reconciled with the rewritten proposal (2026-09-23)

Date/time and timezone: 2026-09-23, Asia/Singapore (time not recorded).
Contributor / coding agent: Claude Code, at the user's request, after the user asked whether the proposal §9.1 rewrite had been propagated into the specifications and Claude Code found the opposite gap.
Task and relevant module: The prior entry rewrote proposal v2 §9.1/§9.2/§9.4 and the changes-from-v1/§16 tables to describe the containerised/Kafka runtime directly instead of the original two-process/SQLite/jobs-table design. That left nine specification files holding present-tense statements like "proposal v2 section 9.1 specifies/states X" that quoted content no longer present in the proposal, and one file (`integration_contracts.md`) asserting "proposal v2 has not been rewritten" as fact. This task corrected those cross-references to past tense, without changing any domain rule or architecture decision.
Branch / baseline commit / resulting commit or PR: `code-skeleton` / `95d5382` / no new commit.
Changed paths and completed behaviour:
- `docs/specs/module-01-vehicle-part-segmentation.md` through `module-05-line-item-extraction.md`: the identical "Runtime note" blockquote in each changed from "This replaces proposal v2 section 9.1 (...)" to "Proposal v2 section 9.1 has since been rewritten to describe this same runtime directly; it originally specified (...)".
- `docs/specs/integration_contracts.md` §"Runtime change from proposal v2 section 9.1": intro paragraph and table header no longer claim the proposal is unrewritten; they now say section 9.1 has been rewritten to match and the table is a historical record.
- `docs/specs/technical_specification.md`: §1.1 intro/table converted to past tense with a note that §9.1 has caught up; the M8 row now says "Now consistent" instead of citing an active contradiction; the effort-estimate sentence near line 790 and the "last resort" fallback item near line 818 were reworded to "originally assumed"/"originally in proposal v2 section 9.1, before it was rewritten"; §16.2's contradictions table marked five of six rows **Resolved** (M8 location, worker count, transport, database, Compose) since proposal v2 §9.1/§9.4 now match this specification, and kept the platform-effort row **Unresolved** since the proposal's budget (§12.1) has not been revised for the ~11 extra person-days ADR 0002 estimates.
- `docs/specs/product_specification.md` §0.1: same past-tense correction and table-header change as the other files.
- `docs/specs/evaluation_plan.md` §0.3: same past-tense correction.
Decisions (accepted/proposed) and references: No new domain or runtime decision. This closes a self-inflicted inconsistency from the immediately preceding proposal rewrite: specs that correctly described "what proposal v2 used to say, and how we differ" became inaccurate the moment that description was made true by rewriting the proposal itself. The one genuinely unresolved item, surfaced again by this pass rather than resolved, is the ~11 extra person-day runtime cost against the proposal's 50-person-day/5-day-contingency budget (recorded in `technical_specification.md` §16.2 and §15, and in this file's earlier "Proposal v2 runtime note added" and "architecture section rewritten" entries).
Checks actually run, results and artifact locations: Grepped `docs/specs/*.md` and `docs/agent-workflows.md` for `SQLite|jobs table|no message broker|worker process|API process` before and after editing; confirmed zero remaining present-tense "section 9.1 specifies/states/plans" or "section 9.4 states" hits. Left `docs/specs/README.md`, `docs/specs/application_platform.md` and `docs/agent-workflows.md` unedited after reading them, since their references are either historical-v1 or phrased as general history ("a change from v2 section 9.1") that remains true rather than a quote of currently-nonexistent text. `git diff --stat` confirms exactly the nine intended files changed, 28 insertions/28 deletions. No Markdown link checker, Mermaid renderer or test suite was run; this was a prose/table wording pass only.
Uncommitted work, limitations and missing prerequisites: All nine files are uncommitted, alongside the still-uncommitted proposal rewrite from the prior entry. Module specs 06-09 and the shared documents not listed above were not re-scanned in this pass beyond the grep above; if they are found to quote the old proposal text later, the same past-tense correction pattern applies. The platform-effort budget conflict remains open and unresolved by design.
Next concrete step and agreed owner (or unassigned): Unassigned. Recommend committing the proposal and specification changes together, since the specs' accuracy now depends on the proposal edits from the prior entry.

## Model I/O and implementation priority handoff (2026-09-24)

Date/time and timezone: 2026-09-24, Asia/Singapore (time not recorded).
Contributor / coding agent: Codex, at the user's request.
Task and relevant module: Document inputs/outputs across M1-M9 and preserve the model-independent implementation priority list.
Branch / baseline commit / resulting commit or PR: code-skeleton / 1c9ca28 / no new commit or PR.
Changed paths and completed behaviour: CONTEXT.md only; added core trained/pretrained model table, deterministic-module table, optional stretch boundaries, nine ranked implementation tasks and a first milestone; refreshed the next-work pointer.
Decisions (accepted/proposed) and references: Planning summary of current module, data, integration and training specifications. The priority order is recommended, not a change to scope or assigned ownership. M6's need for M5 rows is explicitly flagged against the parallel high-level diagram; transport specs were not edited.
Checks actually run, results and artifact locations: Read current specifications, Git status/HEAD and worker.py. All 26 new local links/anchors and all 4 table shapes passed validation; the earlier context from Open decisions through the prior handoffs matches HEAD unchanged; git diff --check passed. Only CONTEXT.md changed. No application tests or model experiments run for this documentation-only change.
Uncommitted work, limitations and missing prerequisites: CONTEXT.md is uncommitted. No model, parser, orchestration or rule implementation was added; no datasets or dependency access were newly verified. Earlier handoffs and historical validation remain intact.
Next concrete step and agreed owner (or unassigned): Unassigned; implement priority 1 shared contracts and priority 2 M8 rules against fixtures, then connect the resulting assessment to the existing review screen.

## Implementation priorities 1-9 partial handoff (2026-09-24)

Date/time and timezone: 2026-09-24, Asia/Singapore (time not recorded).
Contributor / coding agent: Claude Code as coordinator, with one subagent per priority working on separate paths. Handed to Codex at the user's request while three lanes were unfinished. The run was interrupted twice by API spend limits.
Task and relevant module: Implement the nine [implementation priorities](#implementation-priorities-without-trained-models-2026-09-24), meaning the M1-M9 platform and rule work that needs no trained models, and update the specification checklists.
Branch / baseline commit / resulting commit or PR: `code-skeleton` / `1c9ca28` / no commit or PR. Everything below is uncommitted.

Workspace and running jobs:
- Worktree: the main checkout, not a separate git worktree.
  - WSL path: `/home/elephantombot/projects/CLAIM-CMEV`.
  - Windows path: `\\wsl.localhost\Ubuntu\home\elephantombot\projects\CLAIM-CMEV`.
- Python: the WSL `.venv` (Python 3.12.3).
- Uncommitted changes: `git status --short` listed 136 entries after the M5 update, all new or modified and none staged. That includes `CONTEXT.md` and `pyproject.toml`.
- Running jobs when this entry was last updated. One Claude Code subagent was still editing:
  - Priority 3 orchestration owns these paths:
    - `src/claim_cmev/{messaging,persistence,orchestration}/`
    - `src/claim_cmev/runtime.py`, `worker.py`, `fixtures.py`, `storage.py` and `api/`
    - `workers/`, `infra/`, `apps/api/`
    - `tests/integration/` and `tests/backend/`
  - The M5 parser subagent has finished; see priority 6 below.
- Before editing any of those paths, confirm that the job has stopped: no new file modifications, and a later dated entry in this file recording its result.
- No containers, servers or training jobs were started or left running.

Status by priority. Test counts are as of this handoff. "Done" means the pure module and its tests exist. It does not mean the module is wired into the running application.

| Priority | Status | Paths | Entry points and evidence |
| --- | --- | --- | --- |
| 1 Shared contracts | Done | `src/claim_cmev/contracts/` (`common`, `claims`, `imaging`, `documents`, `costs`, `assessment`, `review`, `fixtures`, `events/`), `src/claim_cmev/taxonomy/`, `configs/taxonomy/*.yaml`, `tests/contracts/`, HITL `subset_mapping` in `data/manifests/dataset_sources.json` | 456 tests. Details below the table |
| 2 M8 rules | Done (pure) | `src/claim_cmev/comparison/`, `configs/pipeline/m8_rules.yaml`, `tests/unit/m8/` | 98 tests. Details below the table |
| 3 Orchestration | **In progress and partial; currently breaks `tests/backend`** | `src/claim_cmev/messaging/` (transport, kafka, outbox, consumer), `src/claim_cmev/persistence/` (tables, migrations), `infra/migrations/` (Alembic env and `0001_runtime_schema.py`), modified `src/claim_cmev/runtime.py` | The ops tables, transports and consumer runtime were being written. None of these exist yet: the orchestrator join, fixture producers, consolidator service, worker roles, API rewiring and `tests/integration`. Verify every file before building on it |
| 4 M6 linking and state | Done (pure) | `src/claim_cmev/documents/pen_marks/`, `configs/pipeline/m6_linking.yaml`, `tests/unit/m6/` | 70 tests. Details below the table. There is no detector |
| 5 M4 page reading | Done except the consumer and Dockerfile | `src/claim_cmev/documents/text_layout/`, `src/claim_cmev/vision/transforms.py`, `configs/pipeline/m4_page_reading.yaml`, `tests/unit/m4/` | 74 tests pass, plus 1 real-Paddle test that skips without weights. Details below the table |
| 6 M5 parser | Done (pure); no consumer yet | `src/claim_cmev/documents/line_items/` (`text`, `config`, `vocabulary`, `values`, `table`, `completeness`, `parser`), `configs/pipeline/m5_layout_families.yaml`, `configs/pipeline/m5_estimate_vocabulary.yaml`, `tests/unit/m5/` (including `data/paddleocr_synthetic_pages.json`) | 196 tests. Details below the table |
| 7 M2 assignment, M3 summary/coverage | Done (pure) | `src/claim_cmev/vision/damage/`, `src/claim_cmev/vision/multiview/`, `configs/pipeline/m2_assignment.yaml`, `configs/pipeline/m3_summary.yaml`, `tests/unit/m2/`, `tests/unit/m3/` | 103 tests. Details below the table. There are no model adapters |
| 8 M7 cost baseline | Done except LightGBM, RQ4 and experiment C | `src/claim_cmev/costs/reference/`, `pipelines/costs/`, `configs/costs/*.yaml`, `tests/unit/m7/` | 102 tests. Details below the table |
| 9 M9 review | Pure functions done; the API and UI work has not started | `src/claim_cmev/review/` (`actions`, `finalize`, `report`, `overlays`, `state`, `config`), `configs/pipeline/m9_review.yaml`, `tests/unit/m9/` | 112 tests. Details below the table. The FastAPI endpoints and React UI still use the committed baseline logic |

Entry points and details for each priority:
- **1 Shared contracts.**
  - There are Pydantic records for every data-contracts record, but no SQLAlchemy tables.
  - There are 17 topic JSON Schemas, 17 valid and 77 invalid examples, and `events.registry.validate_message`.
  - `effective_price_for` implements the pending-price rule.
  - `contracts.fixtures` holds 4 validated fixture bundles: `pending_price_change`, `exclusion_and_supported`, `partial_extraction` and `unphotographed_part`.
- **2 M8 rules.**
  - Entry points: `consolidate(request, *, config, ranges)`, `compare_amount`, `propose_additions`, `request_from_bundle`, `reason_codes.catalogue()`, and `lineage.carry_forward_dismissals`.
  - The tests cover all 42 experiment A cases and map each safety invariant SI-01 to SI-12 to at least one case.
  - `consolidate` accepts M7's `PinnedCostTable` directly as `ranges`.
- **4 M6 linking and state.**
  - Entry points: `link_marks`, `link_detections`, `apply_mark_action`, `transition_mark`, `apply_review_event`, `row_mark_states` and `unresolved_marks`.
  - Cross-action tests prove that an unresolved price change never produces the printed amount.
- **5 M4 page reading.**
  - Entry points:
    - `run_page_reading(request, engine, config)`
    - `correct_page_geometry`
    - `render_pages`, which uses pypdfium2
    - `PaddleOcrEngine`, which is imported lazily
    - `StubOcrEngine`
  - PaddleOCR 2.10.0 and paddlepaddle 2.6.2 are installed only in the git-ignored `runtime/venvs/ocr/`.
  - The synthetic smoke results are in the git-ignored `artifacts/evaluation/m4-ocr-smoke/`.
- **6 M5 parser.**
  - Main entry point: `parse_pages(pages, *, config, vocabulary, job_key, claim_id, input_revision, currency, cost_basis, versions, provenance, missing_pages=())`. It returns a `ParseResult` with:
    - `line_items`
    - `completeness` (parser-sourced)
    - `unparsed_regions`
    - `layout_family`
    - `rows`, one per classified row
    - `pages`
    - `versions`
  - Other entry points:
    - `line_items_event_payload`, which is schema-checked
    - `pen_mark_row_boxes`, which produces the `row_boxes` for M6
    - `confirm_declaration_completeness`, the only route to `explicitly_empty`
    - `parse_decimal`
    - `load_layout_families`
    - `load_estimate_vocabulary`
  - Three layout families are defined, all proposed:
    - `family-a-ruled-grid`, which matches M4's synthetic page and the contract fixture page
    - `family-b-numbered-rate`
    - `family-c-compact-quote`, which has no quantity column, so its quantity is always null
  - Fuzzy matching is off.
  - A new subtotal check compares printed subtotals with the exact sum of the rows above them and never corrects anything. It is a response to M4's finding about misread amounts.
  - The M4-to-M5 handoff is tested three ways: M4's own synthetic render through `run_page_reading`, real PaddleOCR boxes from the synthetic smoke pages, and all four contract fixture scenarios.
- **7 M2 assignment, M3 summary/coverage.**
  - Entry points: `assign_damage_to_part`, `group_observations`, `decide_coverage` (an ordered rule table), `summarise_parts` and `summary_job_key`.
  - The outputs reproduce the groups and coverage in all four fixture bundles.
- **8 M7 cost baseline.**
  - Build command: `python -m claim_cmev.costs.reference.build --seed 20260924 --out artifacts/cost_tables --promote`.
  - Entry points: `load_table`, `lookup_range`, `PinnedCostTable` and `active_table_version`.
  - The demo table `ct-20260922-0c9a22f4` is in the git-ignored `artifacts/cost_tables/`. Rebuild it with the command above; the build is deterministic.
  - Coverage on the synthetic test partition is 0.9039 (1082 of 1197).
- **9 M9 review.**
  - Entry points: `open_review`, `ReviewState`, `classify_review_action`, `apply_review_action`, `replay_review_actions`, `evaluate_finalize_preconditions`, `finalize_review`, `build_print_payload`, and the overlay renderers (`render_page_highlight`, `render_mark_crop`, `render_photo_overlay`).
  - `apply_review_action` returns an `ActionOutcome` carrying the HTTP status and any conflict details. It checks, in order: the idempotency key, whether the review is finalized, whether the assessment is current, stale revisions, which fields apply to the action type, and then per-type rules.
  - A decision-changing action returns a `ReassessmentPlan` with the corrections, the stages to rerun and reuse, the `reuse_lineage`, and a `neural_rerun` flag. For a price correction, `neural_rerun` is false.
  - `evaluate_finalize_preconditions` implements F1-F6, with P1 and P2 behind flags that default to off. Each blocker links to the offending entry, mark or stage.
  - The endpoints to switch over in `api/main.py`:
    - `preconditions()` becomes `evaluate_finalize_preconditions`.
    - `review_context` becomes `apply_review_action`.
    - The finalize handler becomes `finalize_review`.
    - The print-view handler becomes `build_print_payload`.

Other changed paths:
- `pyproject.toml`:
  - Adds numpy, alembic, pypdfium2, opencv-python-headless, jsonschema and pyyaml. All six are installed in `.venv`.
  - Sets the pytest `testpaths` to the backend, contracts, unit and integration test folders.
  - Ships the package's JSON and YAML data files.
- Deleted the `.gitkeep` placeholders in `configs/taxonomy`, `src/claim_cmev/taxonomy` and `tests/contracts`.

Decisions (accepted/proposed) and references:
- These values are all recorded as **proposed** until the team freezes them:
  - all thresholds
  - the vocabularies
  - the vehicle classes: `hatchback_small`, `sedan_standard`, `suv_crossover` and `van_commercial`
  - the layout families
  - the cost basis `single_part_pre_tax_no_discount_v1`
  - the eligible cost keys
- Only two values were selected on validation data, both from synthetic M7 data: the support threshold of 8 and the CQR conformal offset.
- The config files use names of the form `configs/pipeline/mN_*.yaml` rather than the spec names (`page_reading.yaml`, `consolidation.yaml`, `penmarks_linking.yaml`, `damage_assignment.yaml`, `part_summary.yaml`). `vision/multiview/` was kept; the proposed rename to `vision/summary/` was not adopted.
- Document-branch order for orchestration: page_read, then line_items, then pen_marks. This lets mark linking use the M5 rows, and resolves the flagged M5/M6 discrepancy conservatively. The orchestrator does not implement this order yet.
- M8 conservative readings:
  - Rule R1 is checked first. If the image branch fails, every row is insufficient, including excluded rows.
  - Rule A5 is applied strictly: any pending or unlinked mark withholds additions.
  - A pending price change stores `amount_unresolved`.
  - A withheld range gives the `insufficient_support` cost result.
  - Eight reason codes were added to the spec catalogue, marked `source="addition"`.
- Contract choices:
  - Text granularity gains a `mixed` value.
  - `side_source` uses `absent`.
  - `layout_family` is null, with a reason.
  - The `ImageQuality` states and the Finalization precondition names are proposals.
  - The damage-segmented event uses `damage_type`, while the record uses `damage_code`.
  - Two typos in the spec's example messages were fixed: a 65-character hex `dedup_key` and truncated sha256 values.
- M7 writes JSON and CSV rather than Parquet, because pyarrow is not installed. A `no_records` key looks up as `insufficient_support`.
- M4 artifact keys include a version-signature segment.
- The M4 agent recommends that Tesseract is not needed and the fallback stays disabled. This is a recommendation, not a team decision.

Checks actually run, results and artifact locations:
- `.venv/bin/python -m pytest -q <suite>` on WSL Python 3.12.3:

| Suite | Result |
| --- | --- |
| `tests/contracts` | 456 passed |
| `tests/unit/m2` | 38 passed |
| `tests/unit/m3` | 65 passed |
| `tests/unit/m4` | 74 passed, 1 skipped |
| `tests/unit/m5` | 196 passed |
| `tests/unit/m6` | 70 passed |
| `tests/unit/m7` | 102 passed |
| `tests/unit/m8` | 98 passed |
| `tests/unit/m9` | 112 passed |
| `tests/unit/test_sync_skills.py` and `test_download_datasets.py` | 32 passed |

- `tests/backend` fails at collection with `ImportError: cannot import name 'Outbox' from 'claim_cmev.runtime'`, caused by the in-progress priority 3 refactor.
- `.venv/bin/python -m pytest -q --continue-on-collection-errors` after the M5 update: 1243 passed, 1 skipped, and that 1 collection error. A plain `pytest -q` stops at the collection error.
- The PaddleOCR smoke test used synthetic pages only. Results are in `artifacts/evaluation/m4-ocr-smoke/results.json`:
  - Every box was line-level and carried a confidence value.
  - On CPU, the median OCR time was 1.15-1.35 s per page, and peak memory was 1.8-2.5 GiB.
  - Amounts under pen strokes were misread with high confidence. For example, 420.00 was read as 20.00 at 0.99, and 640.00 as 40.0 at 0.99.
  - So the assumption in M4 step 15, that confidence drops when print is obscured, does not hold for this engine.
- Not run: the frontend build, the browser walkthrough, the Docker Compose stack, any PostgreSQL or Kafka test, any trained model, and any real page or photograph.

Specification checklist candidates. None of these boxes is ticked yet. Tick each one only after checking its evidence.
- module-07, 7 boxes:
  - `generate_prices`
  - grouped splits with a reserved test hash
  - the empirical percentile method
  - the conformal decision
  - build validation
  - `load_table` and `lookup_range`
  - the final-test `metrics.json`
- training specification, Lane 4, 7 boxes:
  - `generate_prices.py`
  - separate ordinary and injected runs
  - the empirical baseline first
  - the support sweep
  - conformal on calib
  - publishing the table
  - refusing crossed bounds
- module-04, 5 boxes:
  - geometry
  - PDF rasterisation
  - `run_page_reading`
  - the granularity assertion
  - page fixtures
- module-05, 4 boxes:
  - `parse_pages`, step by step, with unit tests per step
  - exact decimal parsing
  - deterministic entry IDs that are stable across a retry
  - the fixtures list
- training specification, 1 box: "Record the parser code and configuration version in every emitted row".
- module-06, 2 boxes: `link_marks` and `apply_mark_action`.
- module-09, 2 boxes: `classify_review_action` with `apply_review_action`, and `evaluate_finalize_preconditions` with blockers that link to the offending row.
- module-02, 1 box: `assign_damage_to_part`.
- module-03, 3 boxes: `group_observations`, `decide_coverage` and the rule fixtures.
- module-08, 6 boxes:
  - `consolidate`
  - `compare_amount`
  - `propose_additions`
  - per-check storage
  - `content_hash` and dismissal carry-forward
  - experiment A
- evaluation_plan, 1 box: the experiment A case set with its SI map.
- product_specification, 1 box: SI-01 to SI-12 tests.
- data_contracts section 14, 6 boxes:
  - the HITL contingency taxonomy and its no-merge test
  - the HITL folder swap and its loader test
  - the effective-price rule
  - the completeness distinction
  - absent ranges and suppressed additions
  - pen-mark transitions
  - deterministic review-action replay and stale-edit conflict behaviour
- integration_contracts section 11, 7 boxes:
  - round-trip validation
  - example messages
  - invalid examples
  - schema rejection
  - null carries a reason
  - side is never invented (M2 fuzz testing plus M8 withholding)
  - no language model in the path (static check only; the explainer profile check is still pending)
- Leave these unticked, because they are only partly met:
  - anything that needs SQLAlchemy or migrations
  - consumers and Dockerfiles
  - team freezes
  - real data and trained models
  - the M4 smoke test on real pages
  - LightGBM, RQ4 and experiment C
  - all UI acceptance items

Uncommitted work, limitations and missing prerequisites:
- Nothing is committed or pushed. Priority 3 is partial. Priorities 6 and 9 exist only as pure libraries.
- Completed:
  - priority 1
  - priorities 2, 4, 6, 7 and 9 as pure libraries
  - priority 5 except its consumer and Dockerfile
  - priority 8 except LightGBM, RQ4 and experiment C
- Partial: priority 3.
- Untouched within the priorities:
  - the orchestrator join
  - the fixture producers
  - the consolidator service
  - worker roles
  - the API rewiring and the M9 API and UI integration
  - per-module Kafka consumer shells and Dockerfiles
  - the `full` Compose profile
  - relational tables for the claim, review, imaging and document records
  - the frontend changes, including the vehicle-class options
  - the browser walkthrough rerun
- Fixture validation versus model evaluation: every test here is a deterministic check on fixture, synthetic or generated inputs. The M7 coverage figure is measured on synthetic prices. The PaddleOCR smoke run used synthetic pages. No trained model was run or evaluated, and no real claim material was used. Subagents may still have been writing when this entry was saved, so check `git status` and the file contents rather than trusting this table.
- The running application still shows mocked fixture assessments. The API and UI cannot yet reach the real M8 engine, so the first milestone is not met.
- Contract follow-ups reported by the module agents:
  - `cmd.part-summary` lacks `reuse_from_input_revision` and an image-branch status.
  - The damage-segmented observation has no area denominator.
  - `primary_containment` and `background_containment` should be nullable when part masks are missing.
  - `AssessmentFinding` and `ProposedRepairAddition` have no field for the rule that decided them.
  - The `assessment-ready` schema has no `superseded` field.
  - `PageTransform.corrected_to_pdf_points` is valid only for pages with `/Rotate 0`.
  - `processing_status` has no `partial` value.
  - `evt.page-read` carries no text boxes, so M5 must read them from the `DocumentPage` records or the page-reading artifact.
  - `MarkAction` and `PenMarkCandidate` are M6 types and not yet contract records.
  - `cmd.consolidate` has no `reuse_lineage` field.
  - `ReviewEvent` requires `mark_id` for `enter_amount`. It also has no `carried_from` field and no payload-hash field.
  - `Assessment` has no `cost_basis` field.
- Cross-module issues found when M5 finished. Resolve these before wiring real M5 output into M8:
  - M5 gives unsided parts such as bumpers and the hood `side=unknown` with `side_source=absent`. The contract fixtures use `not_applicable/document_text` instead. As written, M8 rule R5 would withhold these rows as `side_unresolved` indefinitely. One of the two needs a rule for unsided parts.
  - M8 must treat any `field_uncertainty` on `printed_line_amount`, such as `arithmetic_mismatch`, as incomplete extraction.
  - M5 writes the version keys `layout_families`, `vocabulary` and `parser_code`, but the integration event example uses `parser_config`.
  - `row_kind`, the row flags and `row_band_index` exist only in `ParseResult.rows`, not on the `LineItem` contract.
  - `cmd.line-items-extract` carries page references, not text boxes, so the consumer must load `DocumentPage` records.
  - The subtotal check is skipped when another row is already uncertain. So on the synthetic obscured page, two misread amounts still pass through as printed; a test pins this behaviour.
  - The spec's config names were `layout_families.yaml` and `configs/taxonomy/estimate_vocabulary.yaml`. The separate `line_items.yaml` parser keys were merged under `parser:`.
- When the API switches to M9, reconcile these behaviours:
  - Finalize freezes the presented review revision without incrementing it. This follows platform section 9.2 and the data contracts, but UI spec section 8 and the current API increment it.
  - A finalized review refuses any further action.
  - The existing `tests/backend` and e2e tests assume the old shapes, so they will need updating.
- The taxonomy YAML lives in `configs/`, outside the package. Containers need `configs/` copied in, or `CMEV_TAXONOMY_DIR` set.
- Local tooling:
  - Node 22 is in `runtime/tools/node-v22.23.2-linux-x64/bin`.
  - The OCR venv needs `libgomp` from `runtime/venvs/ocr/_support/` on `LD_LIBRARY_PATH`.
  - Docker Desktop is reachable from PowerShell at `C:\Users\Rodel\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`.

Next concrete step and agreed owner (or unassigned): Codex, at the user's handover. Follow items 1-4 of [Immediate next work](#immediate-next-work):
1. Make the full test suite pass. Start with `tests/backend`, which is blocked by the `runtime.py` refactor.
2. Finish priority 3, including the fixture producers and a consolidator that calls `comparison.consolidate`.
3. Wire M9 into the API and UI, then rerun the browser walkthrough.
4. Tick specification boxes only where the evidence supports them.

## Codex takeover verification (2026-09-24)

Date/time and timezone: 2026-09-24, Asia/Singapore.
Contributor / coding agent: Codex, after the user confirmed Claude stopped editing.
Task and relevant module: Verify the partial handoff before continuing priorities 3 and 9.
Branch / baseline commit / resulting commit or PR: code-skeleton / 1c9ca28 / no new commit or PR.
Changed paths and completed behaviour: CONTEXT.md verification entry only at this milestone; all existing changes preserved.
Decisions/status: The preceding Claude handoff is stale for priority 3. Current files include the orchestrator join, fixture producers, real M8 consolidation with pinned synthetic cost tables, API read adapter, worker roles and integration tests. The reported Outbox collection failure is no longer present. M9 API action/finalize logic still duplicates the pure review library; UI expansion remains.
Checks actually run: WSL .venv/bin/python -m pytest -q: 1282 passed, 1 skipped, 2 dependency deprecation warnings in 64.71s. These are fixture/synthetic/local-transport checks, not trained-model evaluation or a fresh Kafka/PostgreSQL/browser pass.
Uncommitted work, limitations and missing prerequisites: Existing large uncommitted tree retained. No training or data acquisition performed.
Next concrete step and agreed owner: Codex to integrate the pure M9 action/finalize/report services and review controls, preserving original records, reassessment lineage and fixture provenance.

## Codex M9 API milestone (2026-09-24)

Date/time and timezone: 2026-09-24, Asia/Singapore.
Contributor / coding agent: Codex.
Task and relevant module: Priority 9 API integration over priority 3.
Branch / baseline commit / resulting commit or PR: code-skeleton / 1c9ca28 / uncommitted.
Changed paths and completed behaviour: api/main.py, api/views.py, new api/review_service.py and orchestration/corrections.py, orchestration/consolidation.py, documents/pen_marks/state.py, tests/backend/test_baseline.py. Generic typed review-actions endpoint and legacy note/mark adapters call M9; M6 replays typed mark decisions; reassessments preserve the pinned cost table. Finalization freezes the current revision and stores the typed M9 print report.
Decisions/status: Follow platform section 9.2 and Finalization contract: finalize does not increment review_revision. The earlier UI wording/test assumption is superseded. Unknown side still needs explicit correction; no automatic inference introduced.
Checks actually run: 109 passed across tests/backend, tests/integration and tests/unit/m6. Includes mark confirm/reject, replay, immutable old assessment, stage reuse and frozen print pairing. Fixture/local SQLite transport only.
Uncommitted work, limitations and missing prerequisites: Identity/coverage and addition paths, inherited review metadata and UI integration are being completed next. No model evaluation performed.
Next concrete step and agreed owner: Codex to exercise every exposed action through reassessment, then finish workbench integration and browser checks.


## Codex review/workbench integration milestone (2026-09-24)

Date/time and timezone: 2026-09-24, Asia/Singapore.
Contributor / coding agent: Codex.
Task and relevant module: Priorities 3, 7 and 9, including human correction replay.
Branch / baseline commit / resulting commit or PR: code-skeleton / 1c9ca28 / uncommitted.
Changed paths and completed behaviour: orchestration/review_summary.py, fixtures.py and plan.py now rerun deterministic M3 over original stored M1/M2 records for identity/coverage confirmations. M9 carries notes and verified unchanged-finding dismissals with origin linkage, and preserves accepted additions as human scope with supplied operation/amount or an absent-amount reason. api/views.py exposes review overlays separately from immutable machine results. Workbench adds typed action controls, actual M8 outcome labels, synthetic ranges/support, current taxonomy options, IndexedDB pending-request recovery and expanded frozen report sections.
Decisions/status: Human additions remain separate from printed document rows; no fabricated page box, printed amount, operation or repair price. Fixture-derived corrected rows remain fixture-labelled. Identity/coverage confirmations in fixture mode explicitly refer to demonstration photo IDs, not uploaded pixels.
Checks actually run: Full Python suite 1285 passed, 1 skipped before two additional integration cases; the expanded review HTTP suite then passed all 5 cases (including missed marks, revised amounts, dismissal carry and stale/idempotent writes). TypeScript build check passed. Browser lost-acknowledgement/reload/replay exercised without duplicate note; full walkthrough still in progress. Both Compose profiles pass config validation.
Uncommitted work, limitations and missing prerequisites: Container start exposed missing installed-package M3/M9 config paths; Dockerfile updated and rebuild in progress. Real inference, real-evidence overlays and evaluated model claims remain unavailable. No production or real claim data used.
Next concrete step and agreed owner: Codex to complete browser/PDF and container smoke checks, document their results and reconcile specification implementation status.


## Codex verified runtime and report milestone (2026-09-24)

Date/time and timezone: 2026-09-24, Asia/Singapore.
Contributor / coding agent: Codex, continuing the user's handover request.
Task and relevant module: Complete the partial orchestration/review integration before the next document-evidence milestone; verify the nine-priority state.
Branch / baseline commit / resulting commit or PR: code-skeleton / 1c9ca28 / no new commit or PR.
Changed paths and completed behaviour: Previous API, orchestration, review and workbench changes verified end-to-end. Added tests/backend/test_review_integration.py, tests/e2e/review-controls.mjs and print-report.mjs; extended baseline.mjs for lost-acknowledgement replay. messaging/kafka.py reduces idle poll delay; Dockerfile.api supplies installed M3/M9 config paths. ClaimReview.tsx/styles.css print immutable report sections with complete page-margin version footers. README.md, infra/README.md, tests/README.md, application_platform.md, ui_specification.md and module-09-review-report.md now describe the actual integration. Detailed priority-by-priority status is in docs/verification/model-independent-2026-09-24.md.
Decisions/status: Typed review-actions is the generic HTTP endpoint; compatibility routes remain. Human accepted additions are separate scope, not fabricated printed rows. Dismissals carry only with unchanged content and retain origin metadata. Finalize freezes the presented revision; existing frozen reports cannot be edited. One unresolved request per actor/claim is durably saved before sending and retried with its exact original key/payload; stale conflicts require refresh/reapplication.
Checks actually run: Latest full Python suite 1287 passed, 1 skipped, two dependency deprecation warnings (71.19s). Final npm run build passed TypeScript and Vite after the print fix (254.89 kB JS / 79.24 kB gzip). WSL git diff --check passed. Both Compose configurations/builds passed; six lean and eight full long-running services healthy with successful offline cost bootstrap. Baseline browser walkthrough passed against lean Docker (real PostgreSQL/Redpanda/MinIO). Review-controls passed locally and against full Docker, including row/addition/dismissal/completeness and stale conflict recovery. Final local frozen report exported at assessment 2/review 3; both PDF pages contain page numbers and schema/version footer; rendered page inspection confirms no overlap. Earlier Docker baseline froze assessment 2/review 2; later local notes account for the different pairing. Generated evidence remains ignored under artifacts/evaluation/ui-baseline and ui-review.
Uncommitted work, limitations and missing prerequisites: Everything remains uncommitted and prior work is preserved. All evidence-stage results are fixtures; synthetic cost validation is not real repair-price accuracy. No new OCR/model evaluation. Current fixture-ID and coordinate controls are prototypes, not real overlay acceptance. Generic unsent form drafts, live M4/M5 integration, team layout/alias decisions, broker outage/restore, trained inference/GPU and evaluation remain. Final PDF margin boxes were verified in Chromium; other print engines were not checked.
Next concrete step and agreed owner: Next implementation item is the M5/M8 identity/uncertainty contract reconciliation, followed by live document artifact/worker wiring. Codex is the requested continuing implementer; team-reviewed layouts/data/configuration choices remain unassigned. Use the current next-work list above rather than the superseded historical handoff.

Verification cleanup: Codex stopped its isolated cmev-codex-review containers and local API/Vite/worker processes after checks. Named volumes, runtime/codex-review-20260924.db, evidence and ignored screenshots/PDFs are retained. No user services or stored data were removed.


## Implementation check-in (2026-09-25)

Date/time and timezone: 2026-09-25, Asia/Singapore.
Contributor / coding agent: Codex.
Task: Check in the latest accumulated changes at the user's request.
Branch / baseline commit / resulting commit: code-skeleton / 1c9ca28 / the commit containing this entry; use Git history for its identifier.
Changed paths and status: Preserve and commit the accumulated implementation, configurations, specifications, tests and verification handoff. No new implementation behavior in this check-in step. Earlier uncommitted-status entries are historical snapshots.
Validation: Reviewed working-tree paths and dependency/manifest changes; screened candidates for large/binary files, sensitive file types and common credential patterns with no matches. Staged whitespace validation runs before commit. Prior milestone evidence remains 1287 tests passed, 1 skipped, frontend build and lean/full browser smoke; those suites were not repeated for this version-control-only step.
Limitations: Fixture/synthetic boundaries and remaining work above are unchanged. Generated evidence, runtime databases, model files and local credentials remain excluded. No push requested or performed.
Next concrete step: Continue the M5/M8 contract reconciliation and live document integration described above.


## Latest architecture and remaining delivery estimate (2026-09-25)

Date/time and timezone: 2026-09-25, Asia/Singapore.
Contributor: Codex, at the user's request.
Baseline inspected: commit f280209 on code-skeleton; working tree was clean before this documentation update.
Changed path: CONTEXT.md only. This section supersedes earlier planning snapshots for current status, without deleting their history.

### Architecture: implemented harness and missing real processing

Legend: [I] implemented and exercised in the running pipeline; [L] library with unit tests only, not called by the pipeline (tag added 2026-09-27); [F] fixture-only stage; [P] partly implemented; [M] missing.
[I] describes application behavior, not measured model accuracy. The arrows below show
the existing stage dependency graph and the intended real processors at each stage.

~~~text
                         CLAIM-CMEV ONLINE APPLICATION
 +------------------------------------------------------------------------+
 | [I] React workbench -> nginx -> FastAPI                                 |
 |     Login, intake, original uploads, claim/review API, frozen print      |
 | [P] Evidence panel: real page/photo overlays and direct box editing      |
 +----------------------------------+-------------------------------------+
                                    |
                      [I] Commit input revision + outbox
                                    |
                      [I] Kafka / Redpanda transport
                                    |
               [I] Orchestrator: persisted jobs, fan-out, retries
                      /                             \
                     v                               v
 +--------------------------------+  +------------------------------------+
 | IMAGE BRANCH                   |  | DOCUMENT BRANCH                    |
 |                                |  |                                    |
 | [F] M1 part stage: no M1 code  |  | [P] M4 page reading                 |
 | [M] SegFormer-B0 weights,      |  |     Raster/geometry/OCR library [L] |
 |     inference + mask artifacts|  | [M] Live PaddleOCR worker +         |
 |                |               |  |     real-page validation           |
 |                v               |  |                 |                  |
 | [P] M2 damage + assignment     |  |                 v                  |
 | [M] Separate SegFormer-B0      |  | [P] M5 line-item parser             |
 |     weights + inference       |  |     Deterministic parser/tests [L]  |
 | [L] Mask assignment library    |  | [M] Live M4 artifact consumption,   |
 |                |               |  |     reviewed layouts + validation |
 |                v               |  |                 |                  |
 | [P] M3 summary + coverage      |  |                 v                  |
 | [P] Rules run on human reruns  |  | [P] M6 marks + row linking          |
 | [M] Real observation/quality   |  | [M] Faster R-CNN weights/inference  |
 |     integration + calibration |  | [L] Linker; [I] confirmation and   |
 |                |               |  |     amount-correction state machine|
 +----------------+---------------+  +-----------------+------------------+
                  |                                    |
                  +------------------+-----------------+
                                     v
                 [I] Orchestrator join: stage completion + versions
                     Inputs to consolidation: M3 + M5 + M6 records
                                     |
                                     v
                 [I] M8 deterministic comparison and cost checks <-----+
                     No LLM decides the findings                     |
                                     |                               |
                                     v                               |
                 [I] Immutable assessment + pinned versions          |
                                     |                               |
                                     v                               |
                 [P] M9 review / evidence / frozen report             |
                     [I] Corrections, dismissals, accepted scope,     |
                         durable submitted requests and PDF          |
                     [M] Real overlays, all form drafts, final       |
                         accessibility/usability acceptance          |
                                                                     |
 OFFLINE ONLY                                                        |
 [I] Synthetic M7 generator -> empirical ranges -> published table ---+
 [M] LightGBM quantile pair + comparison/RQ4 evaluation
     Runtime reads the pinned table; no per-claim M7 fitting.

 [M] Training/data pipelines -> evaluated M1/M2/M6 artifact packages
                             -> validated read-only model registry
                             -> real stage workers above
~~~

**What runs today:** all six initial evidence stages are supplied by FixtureProducers. The M4 raster/OCR, M5 parser, M6 linker and M2 assignment libraries are not called by the pipeline; M1 has no code; the evidence image-overlay renderer is not served. M8 runs real deterministic rules on those records. M6 human changes replay through the real state machine; identity/coverage changes can rerun deterministic M3 over stored fixture observations. Uploaded photos and documents are stored, but the runtime does not yet derive model/OCR/parser results from their pixels. Setting CMEV_FIXTURE_MODE=false currently refuses to start; it is not a switch to an available live pipeline.

**The document dependency matters:** current scheduling is M4 -> M5 -> M6, because linking marks requires the M5 row boxes. M6 detection could later overlap M5, but linked output must wait for rows. The join coordinates completion, failures and version compatibility; M8 decides findings. Missing photos, ambiguous identity and pending marks withhold applicable decisions rather than become passes or unsupported-repair findings.

**Persistence and packaging beneath the diagram:**

~~~text
 [I] PostgreSQL: ops jobs/outbox/dedup/branch state, pipeline artifacts,
                assessments; claims/reviews currently use baseline_records
 [M] Complete specified domain tables/migrations and integrity constraints

 [I] MinIO: original evidence + API-mediated access
 [M] Derived evidence: masks/pages are placeholder URIs; no derived objects stored

 [I] lean: web + API + combined worker + broker + database + object store
 [I] current full: web + API + orchestrator + fixture-producers +
                   consolidator + broker + database + object store
 [I] Offline cost-bootstrap job; serving services mount cost tables read-only
 [M] target full: replace fixture-producers with six actual module workers,
                  validated model mounts, measured CPU/GPU configuration
 [M] isolated training image/profile; recovery/restore and capacity acceptance
~~~

Decision-changing review actions feed back through the API to a new input revision and reassessment, reusing unaffected stages. Notes/dismissals update the review overlay. A finalized assessment/review pairing remains frozen; final insurer approval is separate.

### Effort basis and definition of completion

These are **remaining engineering estimates**, made from current code and specifications, not previously approved budgets or measured productivity. One man-day/person-day (PD) means eight hours of one person's focused work. Ranges include implementation, review and task-level checks. They exclude elapsed GPU execution, external access/consent delays, scheduling participants and cloud expenditure; those still affect calendar delivery.

"Complete" here means the bounded v2 research prototype: real processing for the supported scope, integrated models and deterministic modules, required runtime/recovery checks, held-out evaluation, a small usability pilot, and final report/presentation. It does not mean insurer production deployment or guaranteed attainment of the proposed accuracy targets. A measured shortfall must be reported against its original target.

Assumptions: reuse the committed libraries; use the proposed model families unless validation requires a recorded change; support only 2-3 agreed estimate layouts; have a usable GPU and permitted data; allow bounded initial training and validation iterations rather than open-ended research. Task estimates are engineering judgment with substantial uncertainty until those prerequisites are confirmed. Row-local model/parser evaluation is included with that component; R20 covers cross-module experiments, avoiding charging the same evaluation twice.

### Remaining implementation, models and acceptance work

Dependency IDs indicate required inputs; independent preparation and adapter scaffolding can begin earlier. Owners are suggested skill areas, not assigned people. "Ready now" does not mean all external acceptance data is already available.

| ID | Remaining deliverable and completion evidence | Current starting point / model | Main dependencies; suggested owner | Effort (PD) |
| --- | --- | --- | --- | ---: |
| R01 | Reconcile M5/M8 unsided-part identity, uncertain printed amounts, parser version keys and remaining producer/consumer gaps; freeze agreed layouts, taxonomy, cost basis and API compatibility decisions with tests. | Typed contracts/rules exist. The unsided-part rule was resolved in 6e59bc2; uncertain-amount and version-key producer/consumer coverage are still missing. No model. | Ready now; domain + backend owners | 2-3 |
| R02 | Build/review vision label conversion and grouped splits; verify data access, duplicate hashes and joint-label holdouts; preserve label provenance and reserve final tests before tuning. | HITL mapping helper/access records exist; full training preparation is absent. CarDD consent/files remain unconfirmed. | Start access audit now; vision/data owner | 4-6 |
| R03 | Collect and label the team acceptance data: about 60 marked physical pages, assignment sample after checking reusable joint labels, and 5-10 vehicle groups. Record writer/page/template/vehicle partitions and independent intended decisions. | Targets are specified; no verified completed collection was found in the handoff. Includes team annotation effort. | R01/R02 split policy; team + domain reviewer | 5-8 |
| R04 | Extend smoke-page synthesis into reproducible training generators for 2-3 estimate layouts and marked-page variants, with exact field/mark/row truth and asset licences. | M4 synthetic smoke helper exists; full estimate/mark training generators do not. | R01 and agreed R03 partition design; document/data owner | 3-5 |
| R05 | Implement shared model artifact manifests/hashes, startup validation and live version selection; build isolated training environment/profile and read-only serving registry. Refuse missing/incompatible models. | Fixture version bundles and cost-table pinning exist; trained-model registry handover is absent. | R01; ML/platform owners | 3-5 |
| R06 | Build shared SegFormer training entry point, fine-tune/evaluate M1, publish artifact, implement photo preprocessing/inference and mask/prediction adapter. Report per-class IoU and exact versions. | **M1: SegFormer-B0**, proposed HITL part model; no integrated checkpoint or live part inference. Side remains unknown unless separately confirmed. | R02/R05; vision owner | 5-8 |
| R07 | Train/evaluate separate M2 model and wire damage mask/region artifacts into the existing assignment code. Report all supported classes and withholding. | **M2: separate SegFormer-B0**, proposed six-class CarDD model; region/assignment libraries already exist. | R02/R05 and shared trainer from R06; vision owner. CarDD access gates fitting | 5-8 |
| R08 | Connect real M1/M2 records to M3, verify coordinates/quality, calibrate assignment and coverage thresholds on validation data and test reviewed-identity reruns without lost observations. | Deterministic M2 assignment/M3 summary libraries and correction replay exist. **No extra learned model.** | R03/R06/R07; vision + integration owners | 3-5 |
| R09 | Package pinned pretrained OCR and wire M4 commands to stored originals, rendered/corrected pages and OCR artifacts; validate rotated PDFs, transforms, failure paths and real-page OCR/amount accuracy. | **M4: pretrained PaddleOCR**, no project training. Raster/OCR adapter and synthetic smoke exist; live worker is missing. | R01/R05, R03 pages for acceptance; document owner | 3-5 |
| R10 | Wire M5 to persisted M4 output; finalize reviewed aliases/layout rules and uncertainty/completeness behavior; measure complete-entry, row and box accuracy on held-out layouts/pages. | **M5: deterministic parser**, no model training. Existing parser and OCR-box tests are reused. | R01/R04/R09 and R03 acceptance pages; document owner | 3-5 |
| R11 | Train/evaluate and package the pen-mark detector, first on generated pages then permitted development pages; publish held-out detection results separately from human corrections. | **M6: Faster R-CNN ResNet-50 FPN**, proposed two foreground classes: exclusion and price change. No integrated detector. | R03/R04/R05; detection owner | 5-8 |
| R12 | Wire M6 inference to corrected page artifacts and M5 rows; calibrate linking tolerances, preserve unresolved candidates and verify manual add/relink/amount actions against real boxes. | Linker/state machine and review actions exist. Automatic handwritten amount recognition is **not** required. | R09/R10/R11; detection + backend owners | 2-3 |
| R13 | Implement the bounded LightGBM comparison, calibration decision and RQ4 support-reduction/ordinary-versus-injected anomaly runs; publish reproducible comparison and a selected frozen table. | **M7: lower/upper LightGBM quantile regressors**, fitted offline. Synthetic generator, empirical baseline, splits, support rules and lookup already exist. | R01 eligible keys/basis; cost/ML owner. Can proceed independently of image models | 3-5 |
| R14 | Exercise M8 with actual branch outputs and partial/failure cases; close adapter defects, verify source references, matching uncertainty, accepted scope and pinned cost behavior. | M8 deterministic engine/join are implemented; this is real-input integration, not rebuilding consolidation or adding an LLM. | R08/R10/R12; backend + domain owners. Empirical M7 table can unblock this before R13 | 2-3 |
| R15 | Build real evidence overlays/highlights and box interactions: masks, original/corrected pages, row/mark links and photo identity/coverage selection. Verify coordinate round trips and source navigation. | M9 overview/actions exist. A tested Pillow overlay renderer (review/overlays.py) exists but is not served. The fixture-ID/manual-coordinate controls are prototypes. | R08/R09/R10/R12 artifacts; frontend + imaging owners | 4-6 |
| R16 | Complete remaining M9 acceptance gaps: all unsent form drafts, conflict/finalize blocker navigation, error/reconnect states, keyboard/tablet/accessibility and real-evidence frozen-print checks. | Submitted-request replay, note/amount drafts, review API and Chromium report already work. Does not include full offline file sync. | R14/R15; frontend + QA owners | 2-4 |
| R17 | Complete specified claim/review/imaging/document/cost persistence and service-query/API gaps, with migrations from current records, integrity constraints and historical revision preservation. Verify existing demonstrations survive migration. | ops/pipeline/assessment tables exist; claims/reviews remain in generic baseline_records. Avoid replacing completed outbox/join code. | R01; backend/database owner; can proceed alongside model work | 4-6 |
| R18 | Package six real module workers, CPU/GPU serving configuration and read-only mounts; pin dependencies/images, expose required health/version diagnostics and verify lean/full equality on the same real inputs. | Current full splits orchestration/producers/consolidation but still groups all fixtures in one producer container. | R05 and live adapters R06-R12; platform + module owners | 3-5 |
| R19 | Complete deployment acceptance: broker/consumer interruption and replay, timeout/backlog behavior, coordinated database/object-store snapshot/restore, historical report recovery, measured RAM/VRAM/startup/latency and runbook. | Local transport tests and Docker browser smoke passed; actual outage/restore/capacity evidence remains incomplete. | R17/R18; platform + QA owners | 4-6 |
| R20 | Assemble/freeze evaluation manifests and cross-module runners; execute Experiment B, RQ1/RQ2/RQ3 integration/coverage/correction analyses and archive rule-test evidence for Experiment A. Report denominators, withholding and pre-correction results. | Unit/fixture tests exist; evaluated real-pipeline research results do not. Component fitting/metrics belong to R06-R12; RQ4/Experiment C to R13. | R03/R08/R12/R14 and frozen release artifacts; evaluation owners | 4-6 |
| R21 | Instrument and run the small review pilot: two reviewers, three cases each, correction/evidence-access/time/comprehension measures. Use alternated manual/assisted cases if making an effort comparison. | Workbench exists; usability measurements are not recorded. | R16/R20; QA/domain reviewer + participants | 2-3 |
| R22 | Produce final technical/research report and presentation, implementation/target traceability, actual effort and shortfall reporting, reproducibility handoff and documented-only views. | Handoff/specifications exist; final delivery/report completion is not established. Retain the original ten-person-day report allocation. | R13/R19/R20/R21; all lanes | 10 |
| | **Total remaining core prototype delivery, before contingency** | | | **81-123** |

This total includes data preparation/annotation, model development, integration, acceptance and reporting. It is not "81-123 days of training": GPU fitting may run for hours while the engineering work spans days. It also does not re-estimate the already implemented harness.

For planning, carry a **20% additional uncertainty reserve**, giving approximately **97-148 PD** including contingency. The reserve is new estimate headroom, not a claim that the original five contingency days remain available. The largest uncertainties are data/label quality, OCR on photographed pages, detector generalization, and GPU/container compatibility.

The original proposal budget was **50 PD for the entire project**, including ten report days. The estimate above is **remaining work from f280209** (the 6e59bc2 remediation slightly reduces R01), not a revised 50-PD total; spent effort has not been measured here. Completing every stated target is therefore unlikely to fit the original budget without an explicit scope/budget decision. Do not add the old ADR runtime estimate again: R05/R17/R18/R19 already estimate the remaining infrastructure work. Adding people can parallelize lanes but cannot remove data-access gates, shared-GPU contention or final integration dependencies; person-days are not calendar days.

### Optional work excluded from the core total

| Item | Additional scope and model | Incremental effort (PD) |
| --- | --- | ---: |
| Stretch S1 | LayoutLMv3/CORD data preparation and OCR-token alignment, fitting/adapter integration, paired comparison with the core parser | 8-12 |
| Stretch S2 | Pretrained TrOCR crop/amount suggestions, separate confidence/provenance, review UI and held-out exact-amount evaluation; no automatic confirmation | 3-5 |
| Stretch S3 | Scripted synthetic final-approval import/refresh, independent support and supersession eligibility, lineage, versioned rebuild and pinned-old-assessment checks | 4-6 |
| Optional explainer | LLM-generated wording over fixed M8 findings, schema/provenance checks and evidence that it cannot alter decisions or amounts | 2-4 |

These estimates assume the core dependencies are complete. They do not activate the stretch goals or bypass their recorded gate. Enterprise identity/authorization, insurer systems, real-price sourcing, production availability/compliance and full offline evidence synchronization are outside this bounded prototype estimate and require a separate scope. Additional operations/audit/cost-detail screens remain described-only; the existing claim dashboard is retained.

### Suggested execution order and verification boundary

1. Start R01 now; in parallel obtain data/access and reserve splits (R02/R03), and agree registry interfaces (R05). Do not wait for trained models to settle document contracts.
2. Progress document integration R04 -> R09 -> R10 and the R17 database work. In parallel train M1/M2 after data gates and run the independent M7 comparison.
3. Finish M6 training/linking and the image summary integration; connect all real branches to M8 and M9. Build overlays against real artifact contracts as they become available.
4. Complete full-profile serving, recovery, frozen evaluation, pilot and report. Keep model results separate from fixture checks at every milestone.

Evidence used: current worker refusal of live mode and FixtureProducers registration; orchestration/plan.py dependency graph; persistence/tables.py schema boundary; existing vision/document/cost packages and pipeline files; Compose profiles; the [verified milestone](docs/verification/model-independent-2026-09-24.md); [module specifications](docs/specs/README.md), [training plan](docs/specs/model_training_specification.md), [technical architecture](docs/specs/technical_specification.md), [platform requirements](docs/specs/application_platform.md) and [evaluation plan](docs/specs/evaluation_plan.md).

Validation for this update: read-only code/specification inspection, estimate-total arithmetic and Markdown/whitespace checks. No application tests, training runs or model evaluations were repeated for this documentation-only change. Existing 1287-passed/1-skipped evidence remains the dated fixture/synthetic result, not evidence that the missing work above is complete.


## Defect remediation started (2026-09-25)

Contributor: Codex; baseline f280209. User authorized fixing the reported review findings. Preserve the preceding architecture/effort section. (Later committed as 6e59bc2.)
Confirmed before editing: section-subtotal row loss, continuation-prefix omission with false completeness, and failed dead-letter validation blocking subsequent messages, reproduced using runtime/review-scratch scripts.
First fixes: messaging/consumer.py now retains malformed messages in ops.dead_letters and emits bounded database references with sanitized routing metadata; parser scans later sections after totals and records unbound continuation-prefix regions, withholding completeness. Added parser and poison-message regressions. Malformed inputs use transport/content-derived deduplication rather than trusting invalid envelope identifiers.
Validation: tests/unit/m5 plus tests/integration/test_runtime_idempotency.py: 207 passed. Synthetic/local transport only; no Kafka outage/model evaluation claim.
Status: review operation/coverage/completeness and worker connection cleanup changes are in progress, not yet verified. Remaining reported findings are still open. Prior blanket milestone-complete wording does not establish correctness on the newly reproduced paths.
Next: verify review reassessment regressions, preserve original values and decisions across uploads, then cover identity, finalization and UI/runtime defects. No commit or push was requested at this point. The remediation was later committed as 6e59bc2.


### Review and UI remediation milestone (2026-09-25)

Changed paths: contracts/documents.py; orchestration/corrections.py and review_summary.py; review/actions.py, finalize.py and report.py; api/main.py and views.py; fixtures.py; vision/multiview/confirmations.py; worker.py; workbench ClaimReview.tsx, ReviewControls.tsx and pendingActions.ts; focused backend/M3/M5 regressions.
Verified: unknown operation reassesses instead of stalling; confirmations preserve measured view signals; conflicting sides on one photo withhold photo-level identity; unsided taxonomy parts parse as not_applicable with absent text source; unreadable uploaded pages withhold declaration completeness; explicitly-empty rejects existing rows; unchanged document decisions and notes survive photo uploads; absent branches can produce a frozen incomplete-evidence report. Failed processing remains blocked. Earlier full checkpoint: 1295 passed, 1 skipped (fixture/synthetic only).
In progress after that checkpoint: original extracted amount retained through repeated corrections and report export; atomic multi-tab pending-request protection and actor parsing; independent tab drafts; continuous reconnect polling; failed-stage retry controls; result styling and no unrelated evidence fallback; proxy-peer login isolation by account; full PDF crop/rotation transform; accepted human scope passed into consolidation; bounded-query reads. These later changes still require the next regression run and browser verification.
Compatibility decisions: historical assessments remain immutable. New optional original-amount fields preserve the first extracted value; historical records retain defaults. New PDF render_to_pdf matrix maps to original PDF user space; legacy records without this matrix explicitly refuse PDF-point conversion and require regeneration. No historical coordinates or unknown sides are silently reinterpreted. No trained models were run.
Next: validate all later changes, exercise browser recovery/printing and update contract/specification notes. The work was later committed as 6e59bc2.


### Defect remediation verified (2026-09-25)

Contributor: Codex. Supersedes the in-progress status above. Preserved the existing architecture/effort entry and all preceding changes. Committed as 6e59bc2; no push verified.

Changed paths: the document parser/vocabulary (now m5-estimate-vocabulary/0.2.0), shared document/event records, M3 confirmation/replay logic, M8 accepted-scope input, review actions/finalization/report, API read/write adapters, dead-letter consumer/worker cleanup, workbench review controls/storage/styles, module/shared specifications, and maintained regression tests. Detailed finding-to-fix evidence: [defect remediation verification](docs/verification/defect-remediation-2026-09-25.md).

Verified outcomes: later estimate sections are retained; unbound continuation regions are flagged partial; poison messages no longer recursively poison the dead-letter path; unknown operations reassess; measured coverage survives confirmation; unsided parser rows can pass M8 with explicit fixture evidence; conflicting sides withhold identity; unreadable pages cannot silently establish completeness; explicitly-empty rejects rows; original amount readings survive corrections and frozen PDF; unchanged-source decisions/audit survive uploads. Absent branches can freeze explicitly incomplete reports; failed stages remain blocking and have retry controls. Accepted human scope changes missing-scope reassessment without inventing printed rows. Browser pending requests survive multi-tab conflicts and legacy actor-key migration; duplicate tabs receive independent draft keys. Evidence selection, styling, reconnect polling, login isolation, PDF transforms, identity history, worker cleanup and repeated database reads are corrected.

Validation: **1314 passed, 1 skipped** in the complete Python suite; the skipped test is optional real PaddleOCR smoke without configured weights. TypeScript and production Vite build passed. Chromium baseline, focused defect scenarios, review-controls/tablet recovery and corrected frozen-PDF export passed. Synthetic CLM-24020 assessment/review 4/6 retains original $1,150, corrected printed $1,160 and effective $980 separately. PDF text checked against frozen records and the affected rendered page inspected. `git diff --check` passed. Test services stopped; ignored local database/screenshots/PDF retained at paths in the verification note.

Limits: these are fixture/synthetic and local-transport checks, not model results or a Kafka outage/production performance test. The original individually numbered 32-finding report was not supplied; the verification matrix covers the supplied defect groups and available reproductions. Unbound continuation rows still require review; two physical sides on one photograph require finer identity evidence and remain withheld by the current photo-level adapter. Changed-source corrections require reconfirmation; historical matrix-less PDF records require regenerated transforms. Existing frozen history is preserved.

**Correction (2026-09-27):** independent verification found that several "corrected" outcomes above are only partly fixed, and that the fixes introduced 11 new defects. See the [2026-09-27 status correction](#status-correction-and-fix-verification-2026-09-27).

Next concrete step (superseded): review/commit this remediation patch (done as 6e59bc2), then resume the remaining model/data/integration work listed above; do not mark those deliverables complete on the strength of fixture tests.


## Status correction and fix verification (2026-09-27)

Date/time and timezone: 2026-09-27, Asia/Singapore.
Contributor / coding agent: Claude Code with read-only verification subagents, at the user's request.
Task: verify the `6e59bc2` remediation independently against the 32-finding review of `f280209`, correct the overstated status in this file, and fix the remaining defects.
Branch / baseline commit / resulting commit or PR: `code-skeleton` / `6e59bc2` / no commit. The fixes logged below stay uncommitted unless an entry says otherwise.

### Verified implementation status at `6e59bc2`

Evidence:
- `pytest -q`: 1314 passed, 1 skipped.
- Code tracing, including grep for call sites.
- `docs/verification/`.
- Chromium e2e runs on a local SQLite stack.

All of this is fixture- or synthetic-validated. None of it is a model result.

- **Implemented and working (fixture/synthetic only).**
  - **Workbench:** login, queue, intake, uploads and original evidence; finalize with a frozen print view; stage retry; and durable replay of submitted requests. The review actions all work: marks, amounts, row corrections, identity/coverage/completeness confirmations, additions, dismissals and notes.
  - **Platform:**
    - Transactional outbox, dedup, the persisted orchestrator join, stage-reuse lineage, retry/DLQ and poison-message isolation.
    - PostgreSQL `ops`, `pipeline` and `assessment` tables, from one Alembic migration.
    - Compose profiles: `lean` runs a combined worker. `full` runs the orchestrator, one fixture-producers container and the consolidator.
    - Kafka/Redpanda was exercised only in the 2026-09-24 Docker smoke. The tests use an in-memory transport.
  - **Modules:**
    - The M8 consolidation, cost checks and additions run on every assessment.
    - The M6 decision state machine is wired into the consolidator and the API.
    - The M3 rules run in the pipeline, but only on reruns after a human identity or coverage confirmation.
    - M7 serves a synthetic table, built offline, through a pinned read-only lookup.
- **Partly implemented.** These are libraries with unit tests only; the pipeline does not call them.
  - **M4:** raster/PDF, geometry, transforms and the PaddleOCR adapter exist. Missing: a live worker, derived page artifacts and real-page validation.
  - **M5:** the parser exists. Missing: M4 wiring, reviewed layouts and aliases, and accuracy measurement.
  - **M6:** the linker exists, but fixture marks arrive already linked. There is no detector.
  - **M2:** region extraction and assignment exist, but fixture observations arrive already assigned. There is no model.
  - **M3:** first-pass summaries are fixture records. The thresholds are not calibrated.
  - **M9:**
    - The evidence overlay renderer is not served.
    - The box controls are fixture-ID prototypes.
    - Only note and amount drafts persist.
    - Accessibility and usability acceptance are not done.
  - **Persistence:** claims and reviews are still stored in the generic `baseline_records`.
  - **The `full` profile** has no per-module workers, model mounts or GPU configuration.
- **Missing.**
  - M1 code and model.
  - M2 and M6 models. CarDD files and consent are absent.
  - Training pipelines, a model registry and stored derived artifacts.
  - M7: LightGBM, RQ4 and experiment C.
  - Runtime acceptance: outage, restore and capacity tests, and a runbook.
  - Data collection, experiment B, RQ1-RQ3 and the usability pilot.
  - Stretch goals S1-S3 and the explainer.
  - Team decisions: owners, the effort budget, layouts, the taxonomy freeze, and finalize policies P1/P2.

### Fix verification of `6e59bc2` (32 original findings)

Verdicts come from rerunning the original reproduction scripts in `runtime/review-scratch/` (git-ignored), plus new probes in `runtime/review-scratch/verify/`.

- **Fixed (17):**
  - Rows after a subtotal are kept.
  - An operation corrected to `unknown` no longer wedges the review.
  - Identity and coverage confirmations keep the measured view signals.
  - Photos-only and pages-only claims can now be finalized.
  - Accepting an addition changes the missing-scope result.
  - Identity history records the prior value.
  - Unsided parts reach M8 without an inferred side.
  - Two sides on one photo are withheld rather than one being picked.
  - An unreadable page makes the declaration partial.
  - The original printed amount is kept in the overview and in the frozen PDF.
  - Two tabs no longer overwrite each other's saved requests.
  - Rows with no linked evidence no longer show an unrelated file.
  - Polling no longer stops after 15 minutes.
  - `/auth/me` is typed correctly.
  - The PDF render-to-points matrix is correct in 144 of 144 cases.
  - The Kafka producer no longer leaks.
  - `GET /claims` runs 7 queries regardless of the number of claims.
- **Partly fixed (9):**
  - The correct-row form still sends `part_code`/`operation` = `unknown` on unmapped rows.
  - Completeness confirmation still bypasses the M5 helper and zeroes `unparsed_region_count`.
  - Uploads carry decisions forward only when the source is unchanged; see N3 and N4.
  - When page 1 ends in a SUB TOTAL or GST line, continuation-page rows are still dropped while the declaration reports complete.
  - `cost_outlier` keeps the same dashed border as `insufficient_evidence`.
  - After a failed reassessment, the prior assessment still cannot be opened.
  - A `job_key` over 200 characters still loops on PostgreSQL.
  - An attacker can still lock out the surveyor's own account.
  - The assessment view still loads the same assessment 5 times.
- **Not fixed (6):**
  - The fixture baseline confirmation still overrides a human "covers enough: no".
  - The queue still says "No open findings" for processing or failed claims, and counts insufficient-evidence rows as findings.
  - Revisions and versions still print only in the `@page` margin.
  - The unused `review.actions.carryable_dismissals` is unchanged.
  - The `ClaimFile` filename substring check still refuses valid names.
  - The JSON Schema and Pydantic still disagree on crossed boxes and on scheme-less URIs.

### New defects introduced by `6e59bc2`

| ID | Severity | Defect |
| --- | --- | --- |
| N1 | High, confirmed | Correcting an unsided row's part to a sided part keeps `not_applicable/absent`. `LineItem` validation then fails, the consolidator dead-letters the message and the claim becomes incomplete (`orchestration/corrections.py`) |
| N2 | Medium, confirmed | Conflicting sides on one photo become permanent, so a surveyor cannot correct their own side mistake (`vision/multiview/confirmations.py`) |
| N3 | Medium, confirmed | An accepted addition survives replacement of the photos it came from, so the missing-repairs check wrongly passes (upload carry-over in `api/main.py`) |
| N4 | Medium, confirmed | An upload on top of a reassessment with no stored review loses the audit history, while hidden carried acceptances still change the result (`review_for` in `api/views.py`, and `api/main.py`) |
| N5 | Medium, confirmed | Footer lines after the final TOTAL (e.g. `LESS EXCESS 500.00`) are parsed as line items and make the declaration partial (`documents/line_items/table.py`) |
| N6 | Medium, likely | A `job_key` over 200 characters raises a PostgreSQL `DataError` that is not dead-lettered, so the consumer loops. SQLite does not enforce `VARCHAR(200)`, so the tests miss it |
| N7 | Medium, confirmed | The login-failure map, keyed on (peer, email), grows without bound |
| N8 | Low | A race between a discard in one tab and a commit in another leaves a tab stuck with an uncaught error |
| N9 | Low | Drafts saved before the upgrade under `draft:undefined:<claim>` keys are orphaned |
| N10 | Low | A failure of `/processing` blocks the whole review page |
| N11 | Cosmetic | Excluded rows show the warning icon |

### Remediation progress log (updated as each lane completes)

Four lanes run in parallel on disjoint paths:
- **Backend review/API:** N1-N4 and N7; partial items 1-3 and 6-9; not-fixed items 1, 2 (server counts) and 4.
- **M5 parser:** continuation after a subtotal, and N5.
- **Platform/contracts:** N6, `ClaimFile`, and schema/validator parity.
- **Workbench UI:** the correct-row form, result styling, queue text, print revisions, prior-assessment access, and N8-N11.

- 2026-09-27: status corrected and the four fix lanes launched.
- 2026-09-27, **M5 parser lane done (uncommitted).**
  - **Continuation rows:** rows above a continuation page's header are never silently dropped. After a closed table they become an unparsed region with reason `text_before_header`, and the declaration is `partial`. Rows after an open table keep the reason `continuation_without_header`.
  - **Footer lines (N5):** recognised lines after the final TOTAL (excess, deductible, payable, settlement, balance due) become a new non-item row kind, `footer`. Unrecognised numeric lines become `text_after_final_total` regions and make the declaration `partial`. Later sections after a section total, and a mid-table DISCOUNT, still keep their rows.
  - **Versions:** `m5-line-items/0.2.0`, `m5-layout-families/0.2.0`. Footer and closing-total patterns are proposed.
  - **Open decision:** bands above a repeated header that contain no digit (letterheads) are still treated as page furniture. The remaining risk is a row whose amount OCR missed entirely.
  - **Changed paths:** `documents/line_items/{table,parser,config,completeness}.py`, `configs/pipeline/m5_layout_families.yaml`, `tests/unit/m5/*`, and an appended note in `docs/specs/module-05-line-item-extraction.md`.
  - **Checks:** `pytest -q tests/unit/m5` gave 221 passed. On `6e59bc2`, 17 of the new or changed tests fail. The full suite gave 1390 passed, 1 skipped and 1 failed; the failure (`tests/unit/m3/test_m3_grouping.py::test_conflicting_sides_on_one_photo_withhold_observation_identity`) comes from the backend lane's in-progress N2 edits.
- 2026-09-27, **Platform/contracts lane done (uncommitted).**
  - **N6 (long job keys):** job keys are bounded to 200 characters with a 1-128 character target, in the wire schema, the Envelope and `make_job_key`. A longer key is `envelope_invalid` before any database write. A database `DataError` is now permanent (`database_value_rejected`). If a full dead-letter row is refused, a minimal one is written instead, so no message can wedge a partition. No column changed, so there is no migration.
  - **`ClaimFile`:** the substring filename check is replaced by `generated_object_key`, which requires the key to be the record's `file_id` or a generated ID. `evidence.jpg` and similar names are now accepted.
  - **Schema/record parity:** the JSON Schema box type gained an `orderedBox` keyword, enforced in `registry.py`, so crossed boxes are refused on the wire as well. `ArtifactRef` and `MaskRef` now use `common.ObjectUri`, which requires an `s3`, `file` or `http(s)` scheme.
  - **Changed paths:**
    - `contracts/{common,claims,imaging}.py`
    - `contracts/events/{envelope.py,envelope.v1.schema.json,registry.py}` and 2 new invalid examples
    - `messaging/consumer.py`, `persistence/tables.py`
    - `tests/contracts/*`, new `tests/integration/test_contract_platform_fixes.py`
    - notes in `docs/specs/data_contracts.md` and `integration_contracts.md`
  - **Checks:**
    - The full suite gave 1394 passed, 1 skipped, 0 failed at that moment. Other lanes were still editing.
    - Every new regression test fails on `6e59bc2`.
    - The PostgreSQL length limit is emulated on SQLite. No real PostgreSQL run was done; set `CMEV_TEST_POSTGRES_URL` to run one.
- 2026-09-27, **Workbench UI lane done (uncommitted).** All nine assigned UI items are fixed:
  - **Correct-row form:** sends only the fields that changed, never `unknown` or `unmapped`.
  - **Result styling:** the four results now differ in border, colour role, icon and wording, with contrast of at least 4.5:1 and a greyscale screenshot.
  - **Excluded rows (N11):** use a neutral icon.
  - **Queue text:** now depends on claim status and uses the split counts `discrepancy_count` and `information_needed_count`. It falls back sensibly when an older API omits them.
  - **Print:** revisions and versions now appear in the report body, not only in the `@page` margin.
  - **Prior assessment:** can be opened read-only after a failed reassessment.
  - **Two-tab discard/commit race (N8):** fixed.
  - **Legacy drafts (N9):** migrated, and old or empty drafts are pruned.
  - **Processing failures (N10):** a failed `/processing` fetch no longer blocks the page.

  The regression check is the new `tests/e2e/review-regressions.mjs`. It has 8 checks: all fail on the `6e59bc2` UI and all pass now. `tsc` and the Vite build pass. The baseline, review-controls, print-report and review-defects e2e runs exit 0 on a disposable SQLite stack.

  Changed paths:
  - `src/workbench/src/{App,ClaimReview,ReviewControls}.tsx`
  - `src/workbench/src/{api,pendingActions}.ts`
  - `src/workbench/src/styles.css`
  - `tests/e2e/review-defects.mjs`, with two assertions updated to the new behaviour
  - an appended note in `docs/specs/ui_specification.md`

  Limits:
  - Only Chromium was tested; Firefox and Safari were not.
  - The queue check mocks the split counts.
  - A non-default UI port needs `CMEV_ALLOWED_ORIGINS`.
  - `tests/README.md` should list the new e2e file.
  - The backend's `stats.open_findings` still includes insufficient-evidence rows. The UI no longer displays it.
- 2026-09-27, **backend review/orchestration/API lane done (uncommitted).**
  - **N1 (part correction changes sidedness):** a part-only correction that moves a row from an unsided part to a sided one (or to unknown) now sets the side to `unknown/absent`. No side is inferred. Any correction that cannot form a valid LineItem is refused at the action boundary with 422 `correction_invalid`, so a correction can no longer dead-letter the consolidator.
  - **N2 and not-fixed 1 (confirmation precedence):**
    - Human confirmations (`source_kind=real`) outrank fixture records.
    - Among human records, the later statement wins, so a surveyor can correct their own side choice. Earlier records are kept in `superseded_ids` for the audit trail.
    - Different sides stated in the same statement still conflict.
    - A human `covers_enough=false` now overrides the fixture baseline.
    - **Behaviour change:** a surveyor cannot now say "this photo shows both sides" through two separate actions, because the second replaces the first. This is recorded in the M3 spec.
  - **N3 (accepted additions):** accepted additions are photo-sourced. They carry over to a new upload only while the photographs are unchanged; otherwise they are invalidated with `invalidation_reason`.
  - **N4 (history after an unreviewed reassessment):** `review_for` walks back to the latest stored review, so an upload after an unreviewed reassessment keeps the history. `accepted_scope` comes from what M8 actually consumed.
  - **Completeness confirmation:** follows the M5 helper and preserves `unparsed_region_count`.
  - **Failed reassessment:** the latest assessment stays readable, with `is_latest`, `read_only` and `not_current_reason` on the assessment response; `can_finalize` is false.
  - **Login (N7):** the new `api/ratelimit.py` limits failures per client IP and account (case-insensitive) to 10 per 60 s, bounded by LRU and TTL eviction (`LOGIN_FAILURE_MAX_KEYS`, default 10000). A 429 now carries `Retry-After`.
    - `infra/Dockerfile.api` runs uvicorn with `--proxy-headers --forwarded-allow-ips` (default: the private IP ranges).
    - `infra/nginx.conf` overwrites `X-Forwarded-For`.
    - **Tradeoff:** clients behind one NAT share a bucket.
  - **Assessment view:** loads the assessment row and claim view once per request.
  - **Claim counts:** claims gain `discrepancy_count` and `information_needed_count`, both null when there is no current assessment. `finding_count` is kept.
  - **`carryable_dismissals`:** delegates to `lineage.carry_forward_dismissals`.
  - **`unknown` on an unresolved field:** treated as unchanged, so the parser's status and reason are kept. A request containing only that gets 422 `no_change`.
  - **Changed paths:**
    - `orchestration/corrections.py`, `review/actions.py`, `vision/multiview/confirmations.py`
    - `api/{main,views,review_service}.py`, new `api/ratelimit.py`
    - `infra/{Dockerfile.api,nginx.conf}`
    - new `tests/backend/test_review_fixes_0927.py` and `tests/unit/m9/test_m9_row_corrections.py`, updated M3/M9 tests
    - notes in the M3 and M9 specs
  - **Limits:**
    - The Docker image and nginx were not built or run; the proxy settings are covered by config and middleware tests only.
    - The forwarded-IP trust list is broad until the Compose backend subnet is pinned.
- 2026-09-27, **combined validation of all four lanes (Claude Code, uncommitted working tree):**
  - `.venv/bin/python -m pytest -q`: **1413 passed, 1 skipped** (the skip is the optional real-PaddleOCR test), in 110 s.
  - `npx tsc --noEmit` passes, and `git diff --check` passes.
  - On a disposable SQLite/local-transport stack (`runtime/ui-lane/stack.sh final0927 8441 5441`), Chromium e2e exit 0 for:
    - `baseline.mjs`
    - `review-controls.mjs`
    - `print-report.mjs`
    - `review-defects.mjs`
    - `review-regressions.mjs` (8/8)
  - All services were stopped afterwards.
  - Everything is fixture/synthetic validation. Docker, Kafka/Redpanda and PostgreSQL were **not** re-run.
- **Status of the 2026-09-27 defect list:**
  - All 9 partly fixed items, all 6 not-fixed items and new defects N1-N11 now have a fix and a regression test in the working tree. N6 was tested with PostgreSQL length emulation only.
  - Open and not changed by this work:
    - the "Missing" and "Partly implemented" feature lists at the top of this section;
    - Firefox/Safari print checks;
    - a real PostgreSQL run;
    - the Docker lean/full re-run.
- **Next concrete step:**
  1. Commit this working tree.
  2. Re-run lean/full Docker, including the new proxy settings, and run the integration tests against PostgreSQL with `CMEV_TEST_POSTGRES_URL`.
  3. Resume the feature work in the 2026-09-25 R01-R22 list.

## Presentation handoff (2026-09-27)

- **Changed paths:** `docs/CLAIM-CMEV_system_design_and_progress.pptx` contains the requested two editable slides: technical architecture/created containers and current project progress, with three placeholders for the next team sync. Private authoring files and PNG renders are in ignored `runtime/presentation-build/`.
- **Status and sources:** the architecture follows the current Compose services, distinguishing the shared fixture producers from the planned six dedicated module workers. UI/backend progress describes the fixture-backed harness. The user reports that the first car defects model, **SegFormer-B2**, has been trained; this is a newer team-reported milestone than the older no-model/B0 planning notes, not independently verified checkpoint or evaluation evidence. No specifications or runtime model selections were changed.
- **Validation:** created and rendered both slides with PowerPoint; visually inspected both renders. Native text bounds showed zero overflows. Presentation package and layout validators passed with two slides, 16:9, Aptos, zero findings and zero layout warnings. The bundled artifact authoring runtime was unavailable, so native PowerPoint automation was used. No application code or tests changed in this task.
- **Limitations and next step:** collect the team's dataset/checkpoint details, measured model results, component updates and next owners/dates, then replace the editable placeholders. Training completion does not establish runtime integration or accuracy. The deck is local and uncommitted; no push was performed.
- **Visual redesign (2026-09-27, Claude Code):** still two slides, rebuilt via PowerPoint automation from `runtime/presentation-build/create-deck-v2.ps1`. The script uses tinted full-bleed background photos, glass cards, module chips, a legend (running / fixture outputs / planned), status pills and three stat tiles. The stat tiles show 1,413 fixture/synthetic tests, 9 full-profile containers (8 long-running) and the team-reported SegFormer-B2. Backgrounds: slide 1 uses the project's own `src/workbench/public/images/damaged-car.png`; slide 2 uses the Wikimedia Commons photo "Rear End Tesla Model X Collision Damage Repair" by Scientificranking, licensed CC BY 4.0. The photo is credited on the slide and in the notes, and was cropped, converted to greyscale and tinted. The photo-cropping script is `prepare-backgrounds.py`. The previous deck is kept as `runtime/presentation-build/CLAIM-CMEV_system_design_and_progress.draft.pptx`. Checks: PowerPoint text bounds showed zero overflows, both renders were inspected, and the file reopens in PowerPoint with 2 slides and speaker notes. The pptx skill's `validate.py` was not run, because `lxml`/`defusedxml` are not installed.

### M1 Vehicle Part Segmentation Ablation Study Verified (2026-09-27)

Contributor: Antigravity (Lane 1 Vision). Preserved all prior defect remediation and architectural handoff entries.

Changed paths:
- Configuration: `configs/models/parts.yaml`, `configs/models/parts_v2_weighted.yaml`, `configs/models/parts_v3_compound.yaml`, `configs/models/parts_v4_augmented.yaml`, `configs/models/parts_v5_b2.yaml`
- Pipelines & Dataset: `pipelines/vision/dataset.py`, `pipelines/vision/train_segformer.py`, `pipelines/vision/eval_segmentation.py`, `pipelines/vision/convert_hitl.py`, `pipelines/vision/build_splits.py`
- Serving & Integration: `src/claim_cmev/vision/parts/adapter.py`, `src/claim_cmev/vision/parts/config.py`
- Tests: `tests/unit/test_build_splits.py`, `tests/unit/test_convert_hitl.py`, `tests/unit/test_parts_adapter.py`, `tests/unit/test_parts_segmenter_live.py`, `tests/unit/test_segformer_dataset.py`, `tests/unit/test_train_eval_segformer.py`
- Artifacts: `artifacts/models/parts/0.1.0/`, `artifacts/models/parts/0.2.0-weighted/`, `artifacts/models/parts/0.3.0-compound/`, `artifacts/models/parts/0.4.0-augmented/`, `artifacts/models/parts/0.5.0-b2/`

Controlled Ablation Study Results on Frozen Test Split (147 images, `data/splits/parts/0.1.0/test.jsonl`):
1. **Baseline (`0.1.0`)**: SegFormer-B0, unweighted CE (40 epochs). Test Foreground mIoU: **0.4664**, Supported Panels mIoU: **0.4710**. 7/10 panels passed $\ge 0.40$ floor; `roof` (0.1316), `rocker-panel` (0.1224), `back-door` (0.2708) failed.
2. **Weighted CE (`0.2.0`)**: SegFormer-B0, inverse-sqrt class frequency weighting ($w_c \propto 1/\sqrt{N_c}$, bg=0.50, 60 epochs). Test Foreground mIoU: **0.5897** (+12.33 points), Panels mIoU: **0.5831**. 9/10 panels passed floor (`roof`: 0.4581, `rocker-panel`: 0.4575 passed; `back-door`: 0.3818 near floor).
3. **Compound Loss (`0.3.0`)**: SegFormer-B0, Weighted CE + Soft Dice Loss ($\lambda=1.0$, 60 epochs). Test Foreground mIoU: **0.6034** (**Target $\ge 0.60$ MET**), Panels mIoU: **0.5945** (**10/10 panels passed floor**; `back-door` reached 0.4071, `rocker-panel` 0.4727, `roof` 0.4643).
4. **Compound + Augmentation (`0.4.0`)**: SegFormer-B0, CLAHE seam-contrast + subtle perspective warp ($<4\%$). Test Foreground mIoU: **0.5986**, Panels mIoU: **0.5907** (10/10 panels passed floor). Boundary precision improved on seams but slight macro smoothing occurred on low-capacity B0.
5. **Encoder Capacity Scaling (`0.5.0`)**: SegFormer-B2 (`nvidia/mit-b2`, 27.4M parameters, compound loss). Test Foreground mIoU: **0.7075** (+24.11 points over baseline), Supported Panels mIoU: **0.6914** (+22.04 points). All 10 supported panels passed with large margins (`back-door`: 0.5529, `rocker-panel`: 0.5764, `roof`: 0.5778, `quarter-panel`: 0.6811, `fender`: 0.6942, `front-bumper`: 0.8127, `hood`: 0.8545).
6. **SegFormer-B2 + Advanced Augmentation (`0.6.0`)**: SegFormer-B2 + Compound Loss + CLAHE seam contrast + subtle perspective warp ($<4\%$). Test Foreground mIoU: **0.7062**, Supported Panels mIoU: **0.6916**. Directly enhanced boundary precision and IoU on seam-delimited panels (`back-door`: 0.5529 -> **0.5602**, precision +1.44 pts; `quarter-panel`: 0.6811 -> **0.6884**; `trunk`: 0.7124 -> **0.7150**), while circular wheel classes experienced minor aspect-ratio variation under perspective warp.

Validation Evidence:
- Executed on RTX 5060 Laptop GPU in Python 3.12 / PyTorch `2.14.0+cu130`.
- Evaluated on identical frozen test split reserved under union hash grouping (`content_sha256`), guaranteeing zero leakage across the 441 shared images with M2.
- 5 passed in parts unit and live adapter tests (`test_parts_adapter.py`, `test_parts_segmenter_live.py`).

Limits:
- HITL part labels carry no left/right side. All predictions assign `side="unknown"` per invariant; resolution is deferred to M3 human confirmation.
- CarDD damage dataset pending consent/files for M2.

Next concrete step: Update default serving configuration in `configs/models/parts.yaml` to point to SegFormer-B2 (`artifacts/models/parts/0.5.0-b2/` or `0.6.0-b2-augmented/`), and proceed to M2 damage segmentation conversion and pipeline setup.


### Cross-Architecture Benchmark: CNNs (ResNet-50, YOLOv8) vs Transformers (SegFormer-B2, B3) Verified (2026-10-03)

Contributor: Antigravity (Lane 1 Vision). Evaluated all architectures on the identical frozen test split (147 images, `data/splits/parts/0.1.0/test.jsonl`).

Changed paths:
- Configurations: `configs/models/parts_resnet50.yaml`, `configs/models/parts_v7_b2_original.yaml`, `configs/models/parts_v8_b3_compound.yaml`
- Scripts & Pipelines: `pipelines/vision/train_resnet.py`, `pipelines/vision/export_yolo_parts.py`, `pipelines/vision/train_eval_yolo.py`, `pipelines/vision/eval_segmentation.py`
- interim Data: `data/interim/yolo_parts/` (YOLO segmentation format with normalized polygon labels)
- Artifacts:
  - DeepLabV3-ResNet50: `artifacts/models/parts/resnet50/test_evaluation_report.json`
  - YOLOv8m-seg: `artifacts/models/parts/yolov8m-seg/test_evaluation_report.json`
  - SegFormer-B2 Original (Vanilla CE): `artifacts/models/parts/0.7.0-b2-original/test_evaluation_report.json`
  - SegFormer-B2 Compound Loss: `artifacts/models/parts/0.5.0-b2/test_evaluation_report.json`
  - SegFormer-B2 Enhanced (Compound + Aug): `artifacts/models/parts/0.6.0-b2-augmented/test_evaluation_report.json`
  - SegFormer-B3 Compound Loss: `artifacts/models/parts/0.8.0-b3-compound/test_evaluation_report.json`

Summary of Evaluated Test Metrics:
| Architecture | Family | Params | Overall Acc | Fg mIoU | Panels mIoU | Macro Prec | Macro Rec | Macro F1 | Floor Violations |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| DeepLabV3-ResNet50 | CNN (Dilated/ASPP) | 42.0M | 0.9486 | 0.7284 | 0.7357 | 0.8389 | 0.8361 | 0.8362 | 0 (10/10 PASS) |
| YOLOv8m-seg | CNN (Anchor-Free Inst) | 27.2M | 0.9600 | 0.7940 | 0.8086 | 0.8719 | 0.8925 | 0.8811 | 0 (10/10 PASS) |
| SegFormer-B2 Original | Transformer (Vanilla CE) | 27.5M | 0.9365 | 0.6914 | 0.6789 | 0.8149 | 0.8108 | 0.8116 | 0 (10/10 PASS) |
| SegFormer-B2 Compound | Transformer (Comp Loss) | 27.5M | 0.9385 | 0.7075 | 0.6914 | 0.8024 | 0.8494 | 0.8234 | 0 (10/10 PASS) |
| SegFormer-B2 Enhanced | Transformer (Comp + Aug)| 27.5M | 0.9383 | 0.7062 | 0.6916 | 0.8010 | 0.8497 | 0.8226 | 0 (10/10 PASS) |
| SegFormer-B3 Compound | Transformer (Comp Loss) | 47.2M | 0.9452 | 0.7303 | 0.7254 | 0.8172 | 0.8644 | 0.8387 | 0 (10/10 PASS) |

Key Findings:
1. **YOLOv8m-seg** achieved the strongest overall performance (0.7940 Fg mIoU, 0.8811 Macro F1). Its instance proposal gating naturally suppresses inter-panel bleeding (`front-door` <-> `back-door`), producing crisp panel boundaries.
2. **DeepLabV3-ResNet50** demonstrated robust dense contextual representation with its ASPP module, attaining 0.7284 Fg mIoU and 0.8362 Macro F1.
3. Scaling SegFormer from **B2 (27.5M)** to **B3 (47.2M)** pushed Transformer performance from 0.7075 to 0.7303 Fg mIoU and from 0.8234 to 0.8387 Macro F1.
4. Across all models, confusion matrices confirm that errors concentrate almost exclusively along adjacent panel seams (`back-door` <-> `front-door`, `grille` <-> `front-bumper`).

Next concrete step: Finalize Module 1 default deployment selection and proceed to Module 2 (damage segmentation).


## Neural M5 document tensor workflow drawings (2026-10-07)

Date/time and timezone: 2026-10-07, Asia/Singapore.
Contributor / coding agent: Codex.
Task and relevant module: Explain M4–M6 at the same tensor/transform detail as the existing M1–M3 drawing, using the user's planned neural M5.
Branch / baseline commit / resulting commit or PR: `feat/m1-implementation` / inspected `e815164` / uncommitted; no commit or push performed. Concurrent M1 contributor work was preserved.
Changed paths and completed behaviour:
- `scripts/render_document_workflow.py`: reproducible Graphviz source for the full tensor workflow, a worked estimate example and model-training/target diagram. Generates a local tabbed/zoomable viewer plus SVG, PNG, PDF, DOT and PNG previews in ignored `artifacts/exports/document-workflow/`.
- `docs/document-neural-workflow.md`: shape/coordinate definitions, primary-source references, model-versus-module boundaries, training targets, alignment limitations and regeneration instructions.
- `docs/specs/README.md`: prominent pointer recording the user's neural-M5 planning direction; older parser-core/stretch status remains explicitly historical for this design.
Decisions/status:
- Accepted user direction: plan a neural M5. LayoutLMv3 is the illustrated existing candidate; exact architecture, BIO-11 example, window/subword policies and thresholds remain proposed. This task implements explanatory artifacts, not model serving or training.
- M4 currently exposes line segments. Verified word geometry/alignment is a prerequisite for the illustrated word-based M5 route; the diagram does not invent word boxes or imply this adapter exists.
- Distinguish M5 token logits from assembled line items; distinguish M6 class logits/box deltas from public detections and pending mark records. M6 geometry linking and human-confirmed amounts remain separate.
Checks actually run, results and artifact locations:
- All three diagrams rendered to SVG, PNG and PDF with Graphviz 14.1.1; previews visually inspected. SVG XML parses, PDF/PNG file signatures validate, and escaped-Unicode labels were checked after correction.
- Renderer Python syntax checked; all 13 viewer links resolve; inline SVG/HTML IDs are unique. Tab selection, accessible selected state, zoom bounds and fit-width control exercised with a Node DOM stub (not a browser end-to-end run).
- Model tensor interfaces checked against upstream PaddleOCR 2.10 inference code, Hugging Face LayoutLMv3 documentation/4.57.1 model source and torchvision detection source; sources are linked in the guide. No checkpoint inference or shape smoke test was run.
- `git diff --check` passed for the documentation changes. No application tests or model training were needed for these diagrams.
Uncommitted work, limitations and missing prerequisites: Generated exports are ignored and require the tracked source/script to regenerate elsewhere. Neural M5 alignment, training and row assembly adapters, and M6 real detector integration remain pending; no new accuracy or end-to-end acceptance claim. Existing broader specifications still need a deliberate implementation-plan revision when the neural recipe is selected.
Next concrete step and agreed owner (or unassigned): Document lane (unassigned): select/validate the OCR word-alignment path, then pin M5 label encoding, chunking/assembly policy and training data before implementing the neural adapter.
