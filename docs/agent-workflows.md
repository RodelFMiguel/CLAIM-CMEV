# Shared coding-agent workflows

The seven project skills contain portable Markdown instructions and standard name/description frontmatter. They use the current specifications for implementation and acceptance, proposal v2 for scope and ADR 0002 for the later runtime decision, and do not require a particular agent API, model, plugin, shell or permission setting.

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

The [specification index](specs/README.md) now identifies the v2 implementation baseline and links all nine module specifications. Start there, open the selected module file, then read relevant shared sections in the index's order. Do not stop at the proposal or at the index itself.

| Specification | Use for |
| --- | --- |
| [Product](specs/product_specification.md) | Required behaviour, FR/NFR IDs and scope boundaries |
| [Technical](specs/technical_specification.md) | Architecture, containers, storage and deployment |
| [Application platform](specs/application_platform.md) | HTTP API, orchestration, review persistence and revisions |
| [Integration contracts](specs/integration_contracts.md) | Topics, messages, adapters, retries and idempotency |
| [Data contracts](specs/data_contracts.md) | Shared records, versions, uncertainty and money semantics |
| [Model training](specs/model_training_specification.md) | Sources, splits, label conversion, recipes and registry handover |
| [UI](specs/ui_specification.md) | Screens, fields, states and interactions |
| [Evaluation](specs/evaluation_plan.md) | Metrics, rule/pipeline/calibration experiments and acceptance checks |

[Proposal v2](CLAIM-CMEV_project_proposal_v2.md) governs scope and explains the rationale. [ADR 0002](adr/0002-containerised-event-runtime.md) records the later container/Kafka runtime decision; it supersedes the original v2 runtime and ADR 0001. Detailed implementation follows current specifications under those authorities. Proposed fields, dependency versions and training values remain proposed until resolved in the scoped task; a document is not evidence of working code.

Earlier skill-review notes described files mid-migration. They are historical snapshots, not a reason to bypass the now-migrated specifications. Check [CONTEXT.md](../CONTEXT.md) against current files. Handle any actual discrepancy explicitly, preserving old fixture/artifact meaning through versioned conversion or rejection where needed.

The current runtime uses module containers and Kafka, PostgreSQL and object storage. The lean profile combines workers while retaining the documented transport contracts and infrastructure; it is not the original SQLite/no-broker runtime. Neither profile is established as implemented by this guide. Cost builds remain offline. Core data/methods are HITL parts, CarDD damage subject to access, pretrained OCR, the bounded parser, mark detection with human confirmation/manual amounts and synthetic cost ranges. Dataset tooling may still catalogue v1 sources; select requested v2 inputs explicitly.

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
