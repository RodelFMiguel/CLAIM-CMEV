# Repository guidance for coding agents

This guidance applies to work in CLAIM-CMEV. Follow the user's current request and the host agent's permission rules; these files do not grant additional execution permissions.

## Start with current evidence

Read [CONTEXT.md](CONTEXT.md) for the latest team handoff, then inspect the working tree and relevant code. Context is a dated summary, not a substitute for current files or Git history. Preserve other contributors' changes.

Use the [specification index](docs/specs/README.md) to locate the requested module and owner. The [proposal](docs/CLAIM-CMEV_project_proposal_v1.md) governs project scope; specifications describe implementation and accepted ADRs record decisions. A proposed ADR is not accepted merely because it exists. Record a material new decision where appropriate without imposing an extra approval step on already authorised work.

Read only relevant module/contracts/evaluation sections. The older architecture sketch is historical and does not override the proposal's approval/refresh rules.

## Project invariants

- Keep declared estimates, surveyor-agreed amounts and final approvals separate.
- Unphotographed, ambiguous or poor-quality evidence must not imply an unsupported repair.
- Preserve original evidence, human actions and claim/input/assessment/review versions.
- Failed or skipped processing is not a passed check. Fixture outputs are not model results.
- Cost references remain pinned per assessment; synthetic costs do not establish real repair-price accuracy or fraud.
- Preserve unknown part/side identity and exact currency/cost-unit semantics.

## Skills and implementation

Canonical portable workflows live in [.agents/skills](.agents/skills). Select the workflow matching the task using the [skill guide](docs/agent-workflows.md). Claude Code has generated copies under .claude/skills; edit the canonical source and run the documented sync/check utility. Other agents can read the same SKILL.md directly.

Use the existing module/runtime boundaries. Discover actual dependency and startup/test commands before invoking them. The project initially contains a scaffold; do not assume an empty directory is an implemented component.

Run checks appropriate to the change and report what was actually exercised. Mark TODOs complete only when their requirements are met; distinguish fixture integration from evaluated model integration. Keep datasets, checkpoints, claim material and generated exports in the documented ignored locations.

## Shared handoff

After meaningful implementation, experiment or decision work, update CONTEXT.md with changed paths, decisions/status, validation evidence, limitations and the next concrete step. Follow its handoff template. Pure questions and read-only reviews do not require rewriting project history unless a written handoff was requested.

Re-read before saving shared context and preserve other contributors' entries. Share updates through the team's normal version-control workflow; do not invent a commit hash or claim an uncommitted change has been pushed.
