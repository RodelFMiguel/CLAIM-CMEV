# Repository guidance for coding agents

This guidance applies to work in CLAIM-CMEV. Follow the user's current request and the host agent's permission rules; these files do not grant additional execution permissions.

## Start with current evidence

Read [CONTEXT.md](CONTEXT.md) for the latest team handoff, then inspect the working tree and relevant code. Context is a dated summary, not a substitute for current files or Git history. Preserve other contributors' changes.

Use [proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md) for the agreed planning scope, module mapping (section 10) and lane ownership (section 12). Check newer explicitly recorded user decisions in current specifications/ADRs, especially runtime changes. The [specification index](docs/specs/README.md), module files, contracts and ADRs are undergoing the transition in v2 section 16; inspect their contents, since a renamed file does not establish that its requirements have been migrated. V1 is historical. Reconcile affected specifications within the requested task rather than implementing conflicting v1 requirements. Accepted ADRs record decisions; a proposed ADR is not accepted merely because it exists. Record material new decisions without imposing another approval gate on the agreed scope.

Read only relevant module/contracts/evaluation sections. The older architecture sketch is historical and does not override the proposal's approval/refresh rules.

## Project invariants

- Keep declared estimates, surveyor-agreed amounts and final approvals separate.
- Unphotographed, ambiguous or poor-quality evidence must not imply an unsupported repair.
- Preserve original evidence, human actions and claim/input/assessment/review versions.
- Failed or skipped processing is not a passed check. Fixture outputs are not model results.
- Cost references remain pinned per assessment; synthetic costs do not establish real repair-price accuracy or fraud.
- Preserve unknown part/side identity and exact currency/cost-unit semantics.
- Pending/conflicting/unlinked marks withhold decisions; only confirmed exclusions are skipped, and pending repricing never falls back to the printed amount.
- Decision-changing confirmations/corrections create new input/assessment revisions; finalization uses the completed assessment and matching frozen review.
- V2 cost keys use part, operation, vehicle class and currency under the fixed single-part SGD basis; side, damage type and year are not cost features. Count independent base cases.

## Skills and implementation

Canonical portable workflows live in [.agents/skills](.agents/skills). Select the workflow matching the task using the [skill guide](docs/agent-workflows.md). Claude Code has generated copies under .claude/skills; edit the canonical source and run the documented sync/check utility. Other agents can read the same SKILL.md directly.

Trace current code and the latest recorded runtime decision before choosing process, transport or storage boundaries. V2 section 9 describes API plus one worker and SQLite/local files; concurrent technical-specification work records a later container/Kafka direction. Reconcile the affected documents and actual implementation rather than treating either a stale note or proposed dependency as deployed. Keep cost builds offline. The core document branch is pretrained OCR, a parser for 2-3 layout families and a pen-mark detector with human confirmation/manual revised amounts. LayoutLMv3/CORD/label alignment, TrOCR and scripted approval refresh are separate stretch goals under v2 section 12.3. Discover actual dependency and startup/test commands before invoking them. The project initially contains a scaffold; do not assume an empty directory is an implemented component.

Run checks appropriate to the change and report what was actually exercised. Mark TODOs complete only when their requirements are met; distinguish fixture integration from evaluated model integration. Keep datasets, checkpoints, claim material and generated exports in the documented ignored locations.

## Shared handoff

After meaningful implementation, experiment or decision work, update CONTEXT.md with changed paths, decisions/status, validation evidence, limitations and the next concrete step. Follow its handoff template. Pure questions and read-only reviews do not require rewriting project history unless a written handoff was requested.

Re-read before saving shared context and preserve other contributors' entries. Share updates through the team's normal version-control workflow; do not invent a commit hash or claim an uncommitted change has been pushed.
