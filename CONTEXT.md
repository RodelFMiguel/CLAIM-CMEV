# CLAIM-CMEV shared development context

Last updated: 2026-09-22 (Asia/Singapore).
Baseline inspected for this update: commit `f587b32` on branch `project-structure`. Confirm the current branch/HEAD and working tree when resuming; this is a dated snapshot.

This file is the team's maintained handoff, not an automatically synchronised chat transcript. Share it with the skills and source changes through the team's normal version-control workflow.

## Project and scope

CLAIM-CMEV compares the surveyor's repair scope, reconstructed from a marked workshop estimate, with photographic damage evidence and synthetic reference cost ranges. The surveyor confirms marks, enters revised amounts and reviews findings; final claim approval remains separate.

The user requested the agreed scope recommendations be incorporated into [proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md), now the revised planning document. The [specification index](docs/specs/README.md), [product specification](docs/specs/product_specification.md), [technical specification](docs/specs/technical_specification.md), [contracts](docs/specs/data_contracts.md), [evaluation plan](docs/specs/evaluation_plan.md) and existing workflow guidance still derive from [v1](docs/CLAIM-CMEV_project_proposal_v1.md). Align them using v2 sections 10 and 16 before implementation; do not mix the two module-numbering schemes.

V2 plans nine modules in a browser, API and single worker, with SQLite/local files. The core uses HITL parts, CarDD damage (requested access pending confirmation), pretrained OCR, a parser for 2–3 layout families, a pen-mark detector and synthetic cost ranges. Upload, one review overview/evidence panel and browser PDF printing are working deliverables. LayoutLMv3 with CORD preparation/label alignment, TrOCR and a scripted approval-refresh demonstration are optional stretch goals. The budget is 25 implementation/data + 10 integration/evaluation + 5 contingency + 10 report/presentation person-days.

## Current state

| Area | Verified state at this handoff |
| --- | --- |
| Repository | Application, worker, domain, pipeline, configuration, data, artifact, test and infrastructure directories are scaffolded |
| Specifications | Existing product, technical, contract, platform, evaluation and module specifications remain on v1; v2 section 16 records the alignment work |
| Architecture | V2 plans API + one worker, SQLite/local files; existing ADR 0001 still proposes the older runtime and has not been superseded or implemented |
| Application | No application entrypoints, dependency manifests, model pipelines, trained models or deployment manifests are implemented |
| Data | Acquisition script/catalogue exist. No new acquisition was performed in this revision; the team reports its CarDD request sent, but consent and files are not confirmed |
| Shared agent workflows | Seven portable skills and matching Claude copies are present in baseline `e837b3e` |
| Developer tooling | Skill-sync utility has 9 previously passing tests; dataset downloader has 23 passing offline tests and a small public HTTPS metadata smoke check |
| Team allocation | Five lanes defined; named members/contract owners remain unassigned |
| Evaluation | No module accuracy, price calibration, usability or financial benefit has been measured |
| Proposal v2 review | Proposal revised across methods, data, rules, serving, evaluation, budget and stretch goals; implementation specifications/ADR alignment remains pending |

## Decisions and rules that affect implementation

- Preserve declared, agreed and approved costs separately. Only eligible final approvals or explicitly documented synthetic seeds feed reference builds.
- Missing/uncertain evidence yields an insufficient-evidence explanation, not a confident discrepancy.
- Every assessment retains its input and model/taxonomy/config/cost-table versions. New inputs or explicit reassessment produce new revisions.
- Shared schemas and vocabulary connect the lanes. Unknown sides/parts and absent ranges must remain explicit.
- The older [architecture sketch](docs/solution_architecture.md) is historical; its direct agreed-cost feedback loop is superseded by the proposal.
- [ADR 0001](docs/adr/0001-prototype-runtime.md) still describes the earlier PostgreSQL/private Compose proposal. V2's simplified runtime needs a recorded superseding decision and specification alignment; neither runtime has been implemented or smoke-tested.
- V2 uses pending/confirmed/rejected mark states. Pending price changes do not fall back to printed prices. Decision-changing human actions create new assessments; original machine results remain immutable.
- HITL predictions do not resolve side. V2 preserves unknown identity, uses recorded human identity/coverage confirmation where needed, and treats grouped-view evaluation as exploratory.
- V2 cost references use part/operation/vehicle class/currency under a fixed single-part SGD basis. Model year is metadata only; independent base cases determine support. Rule tests, ordinary interval coverage and injected-anomaly experiments are reported separately.
- Repository skills use standard name/description frontmatter and agent-neutral instructions. Canonical sources live in .agents/skills; generated regular-file copies in .claude/skills avoid platform-specific symlink setup.

## Immediate next work

1. Assign named lane/adapter owners and align v1 specifications, contracts and workflow references with v2; record the scope/runtime transition as described in v2 section 16.
2. Confirm CarDD consent/files, HITL parts access and the course rubric mapping. Inspect selected annotations and reserve grouped test data before tuning.
3. Freeze supported layouts, marking rules, part/side and CarDD damage vocabulary, fixed cost basis, versions and processing/review states.
4. Smoke-test dependencies/hardware and connect the core workflow with clearly marked fixtures, then actual bounded model/parser outputs by the day-4 checkpoint.
5. Follow the 50-person-day budget and validation/test separation. LayoutLMv3/CORD/label-alignment work starts only under the stretch gate, with contingency and reporting allocations protected.

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
- No application, model, data or live workbench evaluation has run.

## Development history

| Evidence | Change |
| --- | --- |
| `d9144f8` | Project proposal v1 added |
| `545bbeb` | Proposal language simplified |
| `be6b703` | Proposal corrections |
| `121410b` (2026-09-10) | Initial repository structure and implementation specifications committed |
| Current work, uncommitted (2026-09-10) | Added all seven portable skills, Claude copies, shared AGENTS/CLAUDE guidance, CONTEXT.md, sync utility/tests and workflow documentation; validation passed |
| `f587b32` | Initial simplified project proposal v2 committed |
| Current work, uncommitted (2026-09-22) | Revised v2 following the accepted review recommendations; preserved LayoutLMv3/CORD/label alignment as stretch S1 and refreshed this handoff |

Commit subjects above come from the inspected Git history. Do not treat the “current work” row as a commit; replace or append its commit/PR reference when the team records one.

## Active work and ownership

No named contributor currently has a recorded implementation assignment in this file. Before concurrent work, record the agreed task/owner and affected paths here or in the team's task tracker; do not claim ownership merely by opening a file. Preserve other contributors' entries during handoff merges.

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
