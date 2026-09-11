# CLAIM-CMEV shared development context

Last updated: 2026-09-11 (Asia/Singapore).
Baseline inspected for this update: commit `e837b3e` on the local checkout. Confirm the current branch/HEAD and working tree when resuming; this is a dated snapshot.

This file is the team's maintained handoff, not an automatically synchronised chat transcript. Share it with the skills and source changes through the team's normal version-control workflow.

## Project and scope

CLAIM-CMEV compares the surveyor's declared repair scope with photographic damage evidence and reference cost ranges. The surveyor reviews findings and records corrections/agreed amounts; final claim approval remains separate.

The [proposal](docs/CLAIM-CMEV_project_proposal_v1.md) governs scope. Start from the [specification index](docs/specs/README.md), [product specification](docs/specs/product_specification.md), [technical specification](docs/specs/technical_specification.md), [contracts](docs/specs/data_contracts.md), and [evaluation plan](docs/specs/evaluation_plan.md).

Nine functional modules run across a workbench, backend, image worker and document worker. Training and cost-reference refresh are offline jobs. Screens 2-4 are working prototype deliverables; Screens 1, 5, 6 and the audit view are static design deliverables.

## Current state

| Area | Verified state at this handoff |
| --- | --- |
| Repository | Application, worker, domain, pipeline, configuration, data, artifact, test and infrastructure directories are scaffolded |
| Specifications | Product, technical, contracts, platform, evaluation and all nine module specifications exist with TODOs |
| Architecture | Four runtime boundaries follow the proposal; framework/database choices in ADR 0001 are still proposed |
| Application | No application entrypoints, dependency manifests, model pipelines, trained models or deployment manifests are implemented |
| Data | Acquisition script and 13-entry source catalogue are prepared; full datasets, checkpoints and synthetic price file have not been acquired |
| Shared agent workflows | Seven portable skills and matching Claude copies are present in baseline `e837b3e` |
| Developer tooling | Skill-sync utility has 9 previously passing tests; dataset downloader has 23 passing offline tests and a small public HTTPS metadata smoke check |
| Team allocation | Five lanes defined; named members/contract owners remain unassigned |
| Evaluation | No module accuracy, price calibration, usability or financial benefit has been measured |

## Decisions and rules that affect implementation

- Preserve declared, agreed and approved costs separately. Only eligible final approvals or explicitly documented synthetic seeds feed reference builds.
- Missing/uncertain evidence yields an insufficient-evidence explanation, not a confident discrepancy.
- Every assessment retains its input and model/taxonomy/config/cost-table versions. New inputs or explicit reassessment produce new revisions.
- Shared schemas and vocabulary connect the lanes. Unknown sides/parts and absent ranges must remain explicit.
- The older [architecture sketch](docs/solution_architecture.md) is historical; its direct agreed-cost feedback loop is superseded by the proposal.
- [ADR 0001](docs/adr/0001-prototype-runtime.md) proposes Python/FastAPI, React/TypeScript, PostgreSQL and private Compose deployment. It has not been accepted or smoke-tested.
- Repository skills use standard name/description frontmatter and agent-neutral instructions. Canonical sources live in .agents/skills; generated regular-file copies in .claude/skills avoid platform-specific symlink setup.

## Immediate next work

1. Assign named lane/contract/taxonomy owners and confirm the proposed runtime stack.
2. Review and freeze the initial shared schema, supported part/side/operation vocabulary, cost basis and processing states.
3. Implement the API/storage/job foundation and explicitly synthetic integration fixtures.
4. Connect upload, branch outputs, comparison, review and persistence before replacing fixtures with model outputs.
5. Follow [dataset acquisition setup](docs/dataset-downloads.md), resolve access/local inputs, acquire selected data and inspect annotations; then create leakage-resistant splits before bounded model experiments.

These are project priorities, not claims that the next contributor has been assigned all of them. Take the scoped task agreed with the team.

## Open decisions and missing inputs

| Item | Owner lane | Where to resolve |
| --- | --- | --- |
| Named team assignments and shared schema owner | All / Lane 5 | Proposal section 13; specification index |
| Runtime/dependency versions and demonstration hardware | Lane 5 with model owners | ADR 0001 and technical spec |
| Dataset access/licences, side labels and canonical mappings | Lanes 1-4 | Data manifests and module specs |
| Multi-view method and real grouped evaluation data | Lane 2 | M03 |
| OCR/model choice and representative survey reports | Lane 3 | M04-M05 |
| Actual price file structure, operation/damage fields and units | Lane 4 | M06 |
| Interval method, support threshold and course anomaly classification | Lane 4 | M06-M07 and evaluation plan |
| Dismissal reasons and practitioner workflow validation | Lane 5 | Product specification and M09 |

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
