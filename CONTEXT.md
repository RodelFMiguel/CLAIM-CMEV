# CLAIM-CMEV shared development context

Last updated: 2026-09-23 (Asia/Singapore).
Baseline inspected for this update: commit `ed5431a` on branch `code-skeleton`. Confirm the current branch/HEAD and working tree when resuming; this is a dated snapshot.

This file is the team's maintained handoff, not an automatically synchronised chat transcript. Share it with the skills and source changes through the team's normal version-control workflow.

## Project and scope

CLAIM-CMEV compares the surveyor's repair scope, reconstructed from a marked workshop estimate, with photographic damage evidence and synthetic reference cost ranges. The surveyor confirms marks, enters revised amounts and reviews findings; final claim approval remains separate.

[Proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope. The [specification index](docs/specs/README.md) now identifies the v2 implementation baseline, with product, technical, platform, integration, data, training, UI, evaluation and nine module specifications. Start implementation from those documents, not the proposal alone. The project skills directly route to the relevant specifications; earlier migration warnings below are historical snapshots.

[ADR 0002](docs/adr/0002-containerised-event-runtime.md) records the later container/Kafka runtime decision, superseding the original v2 API-plus-one-worker/SQLite plan. Use current technical and integration specifications for runtime boundaries; proposed dependencies and recipes are not validated by being documented. Core scope remains HITL parts, CarDD damage subject to consent/files, pretrained OCR, a parser for 2-3 layout families, pen-mark detection with human confirmation/manual amounts, synthetic costs, upload, review overview/evidence and browser PDF. LayoutLMv3/CORD/alignment, TrOCR and scripted approval refresh remain stretch goals. The original 50-person-day budget and the runtime decision's added effort must be accounted for explicitly.


## Current state

| Area | Verified state at this handoff |
| --- | --- |
| Repository | The earlier scaffold now has a baseline React workbench and FastAPI/worker code under `src`; specification routing and application changes are committed on `code-skeleton` |
| Specifications | The v2 index and shared/module specifications define the full target; the implemented fixture baseline is a limited slice recorded in ADR 0003 |
| Architecture | ADR 0002 governs container/Kafka boundaries. The default lean Compose profile built and started six healthy services on Docker Desktop 4.92.0; fixture containers omit unused host model/cost mounts |
| Application | Public information page, login, searchable queue, claim intake, review, evidence originals, mark decision, notes, finalization and frozen print view; FastAPI persistence and asynchronous fixture worker |
| Data and models | Existing acquisition tooling/catalogue remain. No trained model is loaded or evaluated by the baseline; uploaded originals are preserved and fixture results are explicitly labelled |
| Shared agent workflows | Seven canonical skills reference the specifications directly and match their generated Claude copies; stable invocation names retained |
| Validation | 17 backend tests pass; frontend TypeScript/production build passes; local and container browser walkthroughs pass login through PDF and 390px mobile queue; API and storage readiness are healthy |
| Evaluation | No model accuracy, price calibration, real claim outcome, usability comparison or financial benefit has been measured |

## Decisions and rules that affect implementation

- Preserve declared, agreed and approved costs separately. Only eligible final approvals or explicitly documented synthetic seeds feed reference builds.
- Missing/uncertain evidence yields an insufficient-evidence explanation, not a confident discrepancy.
- Every assessment retains its input and model/taxonomy/config/cost-table versions. New inputs or explicit reassessment produce new revisions.
- Shared schemas and vocabulary connect the lanes. Unknown sides/parts and absent ranges must remain explicit.
- The older [architecture sketch](docs/solution_architecture.md) is historical; its direct agreed-cost feedback loop is superseded by the proposal.
- [ADR 0002](docs/adr/0002-containerised-event-runtime.md) supersedes ADR 0001 and the original v2 runtime. Follow technical/integration specifications while preserving proposed dependency status; this handoff does not establish a deployed or smoke-tested runtime.
- V2 uses pending/confirmed/rejected mark states. Pending price changes do not fall back to printed prices. Decision-changing human actions create new assessments; original machine results remain immutable.
- HITL predictions do not resolve side. V2 preserves unknown identity, uses recorded human identity/coverage confirmation where needed, and treats grouped-view evaluation as exploratory.
- V2 cost references use part/operation/vehicle class/currency under a fixed single-part SGD basis. Model year is metadata only; independent base cases determine support. Rule tests, ordinary interval coverage and injected-anomaly experiments are reported separately.
- Repository skills use standard name/description frontmatter and agent-neutral instructions. Canonical sources live in .agents/skills; generated regular-file copies in .claude/skills avoid platform-specific symlink setup.

## Immediate next work

1. Use the now-running lean fixture stack for scoped module integration. Keep fixture and real outputs visibly separate; add read-only model/cost mounts and version checks when real consumers arrive.
2. Keep the baseline fixture label visible and preserve revision/approval gates while expanding toward M9 and the other module specifications. Current UI lacks full additions/corrections/dismissals, overlays and a durable offline action queue.
3. Confirm CarDD consent/files, HITL parts access, supported layouts and marking rules; reserve grouped evaluation cases before model fitting. Do not count fixture outputs as model evidence.
4. Assign named lane/adapter owners and reconcile the full runtime effort with the 50-person-day proposal. LayoutLMv3/CORD/label-alignment, TrOCR and approval refresh remain stretch work.

These are project priorities, not claims that the next contributor has been assigned all of them. Take the scoped task agreed with the team.

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
