# Shared coding-agent workflows

The seven project skills contain portable Markdown instructions and standard name/description frontmatter. They reference the repository specifications instead of duplicating them, and do not require a particular agent API, model, plugin, shell or permission setting.

## Skill catalogue

| Skill | Use it for | Maintained source |
| --- | --- | --- |
| cmev-implement-module | Implement a selected requirement/TODO and its integration | [SKILL.md](../.agents/skills/cmev-implement-module/SKILL.md) |
| cmev-change-contract | Evolve shared schemas, payloads, taxonomy and persistence coherently | [SKILL.md](../.agents/skills/cmev-change-contract/SKILL.md) |
| cmev-prepare-dataset | Register/convert source data, map labels and create grouped splits | [SKILL.md](../.agents/skills/cmev-prepare-dataset/SKILL.md) |
| cmev-run-experiment | Run a bounded reproducible model/evaluation experiment | [SKILL.md](../.agents/skills/cmev-run-experiment/SKILL.md) |
| cmev-review-evidence-logic | Review uncertainty, comparison and missing-repair rules | [SKILL.md](../.agents/skills/cmev-review-evidence-logic/SKILL.md) |
| cmev-refresh-cost-reference | Validate/build reference ranges and perform a requested promotion | [SKILL.md](../.agents/skills/cmev-refresh-cost-reference/SKILL.md) |
| cmev-verify-workbench | Check Screens 2-4, evidence, saved reviews and recovery | [SKILL.md](../.agents/skills/cmev-verify-workbench/SKILL.md) |

## Discovery and use

Codex reads repository skills under .agents/skills. Invoke a skill explicitly with its dollar-prefixed name or let the agent select it from its description. [Official OpenAI documentation](https://learn.chatgpt.com/docs/build-skills)

Claude Code reads the committed copies under .claude/skills and supports slash-prefixed invocation. Root [CLAUDE.md](../CLAUDE.md) imports [AGENTS.md](../AGENTS.md) and [CONTEXT.md](../CONTEXT.md) using its documented import syntax. [Claude skills](https://code.claude.com/docs/en/skills), [Claude project memory](https://code.claude.com/docs/en/memory)

For example, after this change is in the checkout:

```text
Codex:
$cmev-implement-module Implement the stable entry-ID TODO in M05.

Claude Code:
/cmev-implement-module Implement the stable entry-ID TODO in M05.

Any agent able to read local files:
Read AGENTS.md, CONTEXT.md and
.agents/skills/cmev-implement-module/SKILL.md, then implement
the stable entry-ID TODO in M05 within its documented scope.
```

These examples select a workflow, not a claim that M05 is implemented. Hosts with different discovery conventions can load the canonical file directly. Agents do not automatically share private chat history; CONTEXT.md and version-controlled artifacts provide the shared handoff.

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
| “Run the RQ2 baseline once with this config.” | Bounded reproducible experiment; no indefinite tuning |
| “Review missing-photo handling.” | Findings by default; no unsolicited code changes |
| “Build a candidate cost reference.” | Evaluated candidate; no automatic promotion or agreed-only training data |
| “Verify review retry after connection loss.” | Synthetic test interaction; do not present unavailable app checks as passed |

These are manual behavioural acceptance prompts, not executed application tests. After substantive skill changes, try representative prompts in the team's installed clients and record actual results in CONTEXT.md.

## Team handoff

Read CONTEXT.md at task start, verify its snapshot against the checkout, and update it after meaningful implementation, experiment or decision work. Use its template to capture evidence, open work and the next owner. Preserve previous entries and resolve simultaneous edits by combining relevant history. Sharing requires committing/pulling the files through the team's normal workflow; creating the context file alone does not transmit it to teammates.
