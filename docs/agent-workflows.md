# Shared coding-agent workflows

The seven project skills contain portable Markdown instructions and standard name/description frontmatter. They use proposal v2 for scope and reconcile the relevant implementation specifications, and do not require a particular agent API, model, plugin, shell or permission setting.

## Skill catalogue

| Skill | Use it for | Maintained source |
| --- | --- | --- |
| cmev-implement-module | Implement a selected requirement/TODO and its integration | [SKILL.md](../.agents/skills/cmev-implement-module/SKILL.md) |
| cmev-change-contract | Evolve shared schemas, payloads, taxonomy and persistence coherently | [SKILL.md](../.agents/skills/cmev-change-contract/SKILL.md) |
| cmev-prepare-dataset | Register/convert source data, map labels and create grouped splits | [SKILL.md](../.agents/skills/cmev-prepare-dataset/SKILL.md) |
| cmev-run-experiment | Run a bounded reproducible model/evaluation experiment | [SKILL.md](../.agents/skills/cmev-run-experiment/SKILL.md) |
| cmev-review-evidence-logic | Review uncertainty, comparison and missing-repair rules | [SKILL.md](../.agents/skills/cmev-review-evidence-logic/SKILL.md) |
| cmev-refresh-cost-reference | Build core synthetic ranges; approval refresh is optional S3 | [SKILL.md](../.agents/skills/cmev-refresh-cost-reference/SKILL.md) |
| cmev-verify-workbench | Check upload, overview, marks, evidence, review persistence and browser PDF | [SKILL.md](../.agents/skills/cmev-verify-workbench/SKILL.md) |

## Scope and specification transition

[Proposal v2](CLAIM-CMEV_project_proposal_v2.md) replaces v1 for planning. Use its sections 10 and 12 for module/lane mapping, section 13 for evaluation and section 16 for the implementation-document transition. [CONTEXT.md](../CONTEXT.md) records progress; inspect current files before relying on its snapshot. Align affected contracts/specifications within the requested task; this does not require migrating the entire repository for a small change or requesting approval again for the agreed scope.

At the start of the 2026-09-22 skill review, seven module files had been renamed while retaining v1 contents; the file named for M6 pen marks contained old M07 cost-anomaly rules. Concurrent M1 and technical-specification edits then began arriving. The table locates current files and the intended mapping, not a certification of completed migration. Re-read the affected files and distinguish newer explicitly recorded user decisions from unmigrated v1 requirements. Never infer semantic migration from a filename.

| V2 module / owner lane | Existing specification location | Mapping / transition needed |
| --- | --- | --- |
| M1 parts / Lane 1 | [Parts](specs/module-01-vehicle-part-segmentation.md) | Old M01; HITL parts, unresolved side |
| M2 damage and assignment / Lane 1 | [Damage](specs/module-02-damage-segmentation.md) | Old M02; CarDD six-category semantic damage and separate assignment evaluation |
| M3 summary and coverage / Lane 1 | [Summary](specs/module-03-part-summary-coverage.md) | Old M03; conservative physical identity, retained observations and confirmed coverage |
| M4 page reading / Lane 2 | [Page reading](specs/module-04-page-reading.md) | Old M04; photographed pages and pretrained OCR |
| M5 line items / Lane 2 | [Line items](specs/module-05-line-item-extraction.md) | Old M05; parser for 2-3 families; LayoutLMv3 is S1 |
| M6 pen marks / Lane 3 | [Renamed M6 file](specs/module-06-pen-mark-recognition.md) | New module; replace legacy cost-check content only during the scoped specification migration |
| M7 reference ranges / Lane 4 | [Ranges](specs/module-07-reference-cost-ranges.md) | Old M06; generated prices and fixed cost basis |
| M8 consolidation / Lane 4 | [Checks](specs/module-08-consolidation-checks.md) | Old M07 + M08; move retained cost-check rules here and add confirmed-input gates |
| M9 overview/report / Lane 5 | [Review](specs/module-09-review-report.md) | Old M09; upload, one overview/evidence panel and browser PDF |

Check transition progress in the [shared contracts](specs/data_contracts.md), [platform](specs/application_platform.md), [technical specification](specs/technical_specification.md), [evaluation plan](specs/evaluation_plan.md) and [ADR index](adr/README.md); do not assume old [ADR 0001](adr/0001-prototype-runtime.md) remains current merely because it exists. Preserve old fixture/artifact meaning with versioned conversion or rejection where needed; this skill update does not claim an implemented migration.

V2 section 9.1 originally plans a browser, API and one sequential worker with SQLite and local files. [ADR 0002](adr/0002-containerised-event-runtime.md), accepted 2026-09-22 at the direction of the user, supersedes that with a containerised event-driven runtime: one container per module communicating over Kafka topics, with PostgreSQL and object storage. The v2 shape is retained as the lean Compose profile and the named fallback. V2 domain rules are unchanged by that decision. Neither runtime has been implemented or smoke-tested, so confirm the current state from the checkout rather than from the decision record. Cost builds remain offline. Core data/methods are HITL parts, CarDD damage subject to access, pretrained OCR, the bounded parser, mark detection with human confirmation/manual amounts and synthetic cost ranges. Dataset tooling may still catalogue v1 sources; select the requested v2 inputs explicitly.

Keep the v2 section 12 budget: 25 implementation/data + 10 integration/evaluation + 5 contingency + 10 report/presentation person-days. Stretch S1 retains CORD preparation, OCR/training-label alignment and two-stage LayoutLMv3 training; S2 offers TrOCR suggestions; S3 demonstrates synthetic final-approval refresh. Start stretch work only after core workflow/service checks, validation results and frozen tests exist, with an owner-recorded allowance in unused implementation capacity. Disabled stretch work must not block core startup or success.

All seven skills remain relevant. The cost-reference skill covers core table building even though approval refresh is optional; the workbench skill keeps its stable invocation name while targeting the smaller UI. Evaluation now uses mark detection/linking for RQ2 and exploratory conservative summaries for RQ3. There is no mandatory DocILE benchmark, synthetic joint-label RQ2 or physical-damage deduplication claim.

## Discovery and use

Codex reads repository skills under .agents/skills. Invoke a skill explicitly with its dollar-prefixed name or let the agent select it from its description. [Official OpenAI documentation](https://learn.chatgpt.com/docs/build-skills)

Claude Code reads the committed copies under .claude/skills and supports slash-prefixed invocation. Root [CLAUDE.md](../CLAUDE.md) imports [AGENTS.md](../AGENTS.md) and [CONTEXT.md](../CONTEXT.md) using its documented import syntax. [Claude skills](https://code.claude.com/docs/en/skills), [Claude project memory](https://code.claude.com/docs/en/memory)

For example, after this change is in the checkout:

```text
Codex:
$cmev-implement-module Implement stable entry IDs for v2 M5 parser rows.

Claude Code:
/cmev-implement-module Implement stable entry IDs for v2 M5 parser rows.

Any agent able to read local files:
Read AGENTS.md, CONTEXT.md and
.agents/skills/cmev-implement-module/SKILL.md, then implement
stable entry IDs for v2 M5 parser rows within the agreed scope.
```

These examples select a workflow, not a claim that v2 M5 is implemented. Hosts with different discovery conventions can load the canonical file directly. Agents do not automatically share private chat history; CONTEXT.md and version-controlled artifacts provide the shared handoff.

If a newly added skill directory is not visible in an existing client session, restart the client and check its available skills. Actual in-client invocation remains a separate smoke check from file/schema validation.

## Maintain one source

Edit .agents/skills only. The Claude copies contain the same files at the same relative depth so documentation links resolve in both layouts. Regular files work in Windows and Linux checkouts without symlink privileges.

From the repository root, using Python 3.9 or later:

```sh
python3 scripts/sync_skills.py --write
python3 scripts/sync_skills.py --check
python3 -m unittest discover -s tests/unit -p test_sync_skills.py -v
```

On Windows, use `python` in place of `python3` if that is your installed interpreter command. The utility uses the Python standard library only. It never launches a coding agent, downloads dependencies or changes local permission settings.

The utility manages only the seven named skill directories. It detects missing/different mirrors; check mode does not write. It rejects links and unexpected stale files within managed directories before writing, and preserves unrelated skills and .claude configuration. It reports stale files for deliberate reconciliation rather than deleting them. Add new managed names in the utility when intentionally adding another shared skill.

Commit canonical edits and regenerated Claude copies together through the team's normal Git workflow. Do not edit generated copies independently; copy an intended change into its canonical source before syncing. Check mode returns nonzero for drift or invalid inputs, so it can be added to CI when CI exists.

## Behaviour checks when changing a skill

| Example request | Expected scope |
| --- | --- |
| “Implement M08 cost-boundary handling.” | Implement relevant logic/checks; do not claim training targets achieved |
| “Add a side-resolution field.” | Trace affected contract producers/consumers and historical compatibility |
| “Prepare these HITL files.” | Inspect provided data and preserve unresolved sides and group partitions |
| “Run the RQ2 baseline once with this config.” | Measure mark detection/linking on held-out team pages before corrections; no indefinite tuning |
| “Review missing-photo handling.” | Findings by default; no unsolicited code changes |
| “Build a candidate cost reference.” | Synthetic v2 keys/basis and independent support; separate calibration from injected anomalies; no implicit S3 importer |
| “Verify review retry after connection loss.” | Synthetic test interaction and idempotent save; no assumed offline queue or unavailable-app pass |
| "Check a pending price-change mark." | Withhold effective-price comparison; no printed-price fallback; reassess after confirmation |
| "Prepare CORD for LayoutLMv3." | S1 gate, publisher splits, actual OCR alignment and no invented repair-operation labels |
| "Finalize after correcting coverage." | Completed new assessment and matching frozen review; stale findings cannot print |

These are manual behavioural acceptance prompts, not executed application tests. After substantive skill changes, try representative prompts in the team's installed clients and record actual results in CONTEXT.md.

## Team handoff

Read CONTEXT.md at task start, verify its snapshot against the checkout, and update it after meaningful implementation, experiment or decision work. Use its template to capture evidence, open work and the next owner. Preserve previous entries and resolve simultaneous edits by combining relevant history. Sharing requires committing/pulling the files through the team's normal workflow; creating the context file alone does not transmit it to teammates.
