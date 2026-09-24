# CLAIM-CMEV shared development context

Last updated: 2026-09-25 (Asia/Singapore), implementation check-in requested by the user.
Implementation baseline: commit 1c9ca28 on branch code-skeleton. The accumulated Claude/Codex changes are included in the commit containing this check-in update. No push was requested or performed. Read the current state and [verification record](docs/verification/model-independent-2026-09-24.md); earlier chronological handoffs below retain their original, sometimes superseded status.

This file is the team's maintained handoff, not an automatically synchronised chat transcript. Share it with the skills and source changes through the team's normal version-control workflow.

## Project and scope

CLAIM-CMEV compares the surveyor's repair scope, reconstructed from a marked workshop estimate, with photographic damage evidence and synthetic reference cost ranges. The surveyor confirms marks, enters revised amounts and reviews findings; final claim approval remains separate.

[Proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope. The [specification index](docs/specs/README.md) now identifies the v2 implementation baseline, with product, technical, platform, integration, data, training, UI, evaluation and nine module specifications. Start implementation from those documents, not the proposal alone. The project skills directly route to the relevant specifications; earlier migration warnings below are historical snapshots.

[ADR 0002](docs/adr/0002-containerised-event-runtime.md) records the later container/Kafka runtime decision, superseding the original v2 API-plus-one-worker/SQLite plan. Use current technical and integration specifications for runtime boundaries; proposed dependencies and recipes are not validated by being documented. Core scope remains HITL parts, CarDD damage subject to consent/files, pretrained OCR, a parser for 2-3 layout families, pen-mark detection with human confirmation/manual amounts, synthetic costs, upload, review overview/evidence and browser PDF. LayoutLMv3/CORD/alignment, TrOCR and scripted approval refresh remain stretch goals. The original 50-person-day budget and the runtime decision's added effort must be accounted for explicitly.


## Current state

| Area | Verified state at this handoff |
| --- | --- |
| Repository | Claude's changes preserved and extended by Codex; accumulated implementation checked in on code-skeleton with this context update |
| Specifications | Full target remains broader than the implementation. Only individually verified M9 checklist items are ticked; nine-priority status and gaps are in the verification record |
| Architecture | Persisted orchestration, outbox/deduplication, fixture producers and real M8 consolidation run in lean and split-service full Compose. Full groups fixture producers in one container; final inference/GPU packaging remains |
| Application | Typed review actions, M6 correction replay, M3 human-confirmation reruns, pinned cost lookup, review overlays, durable request retry and frozen M9 reports are connected to the API/workbench |
| Data and models | No trained model loaded/evaluated. Evidence stages still produce labelled fixtures. M7 costs are synthetic and built offline. Prior PaddleOCR smoke used synthetic pages in a private venv; live M4/M5 workers remain unwired |
| Shared agent workflows | Canonical skills remain under .agents/skills; no workflow changes in this continuation |
| Validation | Latest full suite: 1287 passed, 1 skipped, two dependency warnings. Final TypeScript/Vite build and whitespace checks passed. Lean/full Docker and browser workflows passed; final two-page Chromium PDF inspected with complete version footers |
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

1. Before wiring live M5 records, resolve the previously reported unsided-part identity rule across M5/M8 and add producer-to-consumer coverage for uncertain printed amounts and parser version keys. Preserve unknown identity unless the taxonomy explicitly establishes that side does not apply.
2. Connect the tested M4/M5 document libraries to persisted artifacts and worker commands. Obtain team-reviewed supported layouts/aliases and permitted sample pages for real-page acceptance. Keep fixture mode explicit and fail closed when live prerequisites are unavailable. The current fixture pipeline does not run OCR on uploads.
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
