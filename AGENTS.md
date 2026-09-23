# Repository guidance for coding agents

This guidance applies to work in CLAIM-CMEV. Follow the user's current request and the host agent's permission rules; these files do not grant additional execution permissions.

## Start with current evidence

Read [CONTEXT.md](CONTEXT.md) for the latest team handoff, then inspect the working tree and relevant code. Context is a dated summary, not a substitute for current files or Git history. Preserve other contributors' changes.

Start implementation from the [specification index](docs/specs/README.md), the selected module specification and the relevant shared specifications. The [product specification](docs/specs/product_specification.md) defines requirements; the [technical specification](docs/specs/technical_specification.md) and [platform specification](docs/specs/application_platform.md) define architecture and service behaviour; [integration contracts](docs/specs/integration_contracts.md) and [data contracts](docs/specs/data_contracts.md) define interfaces. Use the [training specification](docs/specs/model_training_specification.md), [UI specification](docs/specs/ui_specification.md) and [evaluation plan](docs/specs/evaluation_plan.md) when those aspects are involved.

[Proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md) governs scope; [ADR 0002](docs/adr/0002-containerised-event-runtime.md) records the later container/Kafka runtime decision. V1 is historical. Do not substitute the proposal for detailed specifications or treat specified/proposed behaviour as implemented. Reconcile actual conflicts with current user decisions, preserving proposed versus accepted status. Record material new decisions without adding an approval gate to already authorised work.

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

Trace current code and the technical/integration specifications before choosing process, transport or storage boundaries. ADR 0002 supersedes the original v2 runtime; implementation and dependency validation remain separate from the decision record. Keep cost builds offline. The core document branch is pretrained OCR, a parser for 2-3 layout families and a pen-mark detector with human confirmation/manual revised amounts. LayoutLMv3/CORD/label alignment, TrOCR and scripted approval refresh are separate stretch goals under v2 section 12.3. Discover actual dependency and startup/test commands before invoking them. The project initially contains a scaffold; do not assume an empty directory is an implemented component.

Run checks appropriate to the change and report what was actually exercised. Mark TODOs complete only when their requirements are met; distinguish fixture integration from evaluated model integration. Keep datasets, checkpoints, claim material and generated exports in the documented ignored locations.

## Shared handoff

After meaningful implementation, experiment or decision work, update CONTEXT.md with changed paths, decisions/status, validation evidence, limitations and the next concrete step. Follow its handoff template. Pure questions and read-only reviews do not require rewriting project history unless a written handoff was requested.

Re-read before saving shared context and preserve other contributors' entries. Share updates through the team's normal version-control workflow; do not invent a commit hash or claim an uncommitted change has been pushed.
