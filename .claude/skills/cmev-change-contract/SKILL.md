---
name: cmev-change-contract
description: >-
  Change a CLAIM-CMEV shared record, API payload, taxonomy, or persisted schema
  consistently across producers and consumers. Use for interface evolution,
  compatibility analysis, and migrations, not internal-only refactors.
---

# Change a shared CLAIM-CMEV contract

Deliver a coherent contract change and its compatibility evidence. Use the available agent's normal tools; no vendor-specific commands or integrations are required.

Use the current specifications below for implementation details and acceptance criteria. [Proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope; [ADR 0002](../../../docs/adr/0002-containerised-event-runtime.md) records the later runtime change. Read the sections relevant to the task, preserve proposed versus accepted status, and reconcile concrete conflicts with current user decisions. A specification is not evidence that its behaviour is implemented.

## Scope and sources

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), [data contracts](../../../docs/specs/data_contracts.md), and the actual affected module specifications linked from the [index](../../../docs/specs/README.md). Use [integration contracts](../../../docs/specs/integration_contracts.md) for messages/topics and adapter compatibility, the [platform specification](../../../docs/specs/application_platform.md) for API/revision/persistence changes, and the [technical specification](../../../docs/specs/technical_specification.md) for runtime ownership. Include the [UI specification](../../../docs/specs/ui_specification.md) when changed fields or states are displayed or edited.

Use the current data/integration contracts to identify record meaning and the proposal for scope rationale; consult v2 section 16 only when handling older fixtures or schemas. In particular, v2 M6 is pen marks, M7 is reference ranges and M8 combines the old cost/evidence checks. Inspect the actual schema/code before planning a migration. At scaffold stage, a contract may exist only as documentation; do not claim a database migration or compatibility test exists unless implemented.

## Trace the change

1. Identify the field/enum/record, its meaning, nullable/default semantics, and why it changes.
2. Locate producers, consumers, persisted representations, exports, fixtures and generated clients. Record whether existing historical records or model artifacts depend on the old shape.
3. Decide whether the change is additive, requires a version transition, or changes meaning even if its type stays the same. Define how old records will be read. Give changed v1 amount/taxonomy semantics a distinct version and explicit conversion or rejection policy; do not reinterpret old fixtures or bundles silently.
4. Update authoritative schemas and documentation together. Generate derived client/schema files using existing tooling when available; do not maintain conflicting copies.
5. Implement required adapters/migrations and affected call sites within the requested scope. Preserve historical meaning; a backfill must not invent evidence or approval.

## Domain invariants to inspect

- Claim, input, assessment and review identities remain distinct; stale results cannot replace newer results.
- Money retains exact decimals and separate printed, human-confirmed effective/agreed and final-approved amounts. Optional OCR amount suggestions cannot overwrite confirmed values; a pending price change leaves the effective amount unresolved. Missing is not zero.
- Pen marks retain detection IDs/boxes, nullable row links and candidate rows, pending/confirmed/rejected state and confirmation-action provenance. Confirmed exclusion is a row state, not an `ok` finding.
- Declaration completeness distinguishes complete, partial, unreadable and explicitly empty, with human confirmation provenance. A no-row parser result cannot silently become an empty scope.
- Parts preserve original names, side and mapping uncertainty. HITL part predictions do not resolve side; human physical-identity/coverage confirmations remain separate. Summary records retain every observation without claiming a physical-damage count.
- CarDD uses six damage categories; conversion from instance annotations records overlap/background policy. A HITL-damage fallback needs its own taxonomy version, not relabelled CarDD outputs.
- Photo masks and report boxes retain source IDs, dimensions/transforms and original-coordinate navigation.
- Overall outcomes retain individual check results and reasons; skipped or failed processing is not a passed check.
- Proposed additions have no declared entry/amount until a human creates them.
- Cost generation, features and lookup share part/operation/vehicle-class/currency and the fixed single-part SGD basis; year, side and damage type are excluded from pricing dimensions. Independent base-case support differs from quote counts.
- Decision-changing confirmations/corrections create new input/assessment revisions with explicit artifact reuse; print uses the completed assessment and matching frozen review. Versioned cost ranges and model artifacts still explain historical assessments.

Inspect only invariants affected by the change; this is not a mandatory full-system audit for every field addition.

## Acceptance and output

Exercise a representative producer-to-consumer round trip, relevant invalid/null/unknown input, old/new compatibility, and persistence/export behaviour when changed. For a migration, verify the documented upgrade/restore approach against a disposable test database if available; do not run it on a shared live database merely to validate a schema.

Return the semantic change, affected paths, compatibility/version decision, migration implications and actual check results. Update relevant specification TODOs and [CONTEXT.md](../../../CONTEXT.md) after implementation. If asked only for analysis, return the impact assessment without modifying code.
