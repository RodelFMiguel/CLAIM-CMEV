---
name: cmev-change-contract
description: >-
  Change a CLAIM-CMEV shared record, API payload, taxonomy, or persisted schema
  consistently across producers and consumers. Use for interface evolution,
  compatibility analysis, and migrations, not internal-only refactors.
---

# Change a shared CLAIM-CMEV contract

Deliver a coherent contract change and its compatibility evidence. Use the available agent's normal tools; no vendor-specific commands or integrations are required.

## Scope and sources

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), [data contracts](../../../docs/specs/data_contracts.md), and affected module specifications from the [index](../../../docs/specs/README.md). Read the [platform specification](../../../docs/specs/application_platform.md) when APIs, revisions or persistence are affected.

Inspect the actual schema/code before planning a migration. At scaffold stage, a contract may exist only as documentation; do not claim a database migration or compatibility test exists unless implemented.

## Trace the change

1. Identify the field/enum/record, its meaning, nullable/default semantics, and why it changes.
2. Locate producers, consumers, persisted representations, exports, fixtures and generated clients. Record whether existing historical records or model artifacts depend on the old shape.
3. Decide whether the change is additive, requires a version transition, or changes meaning even if its type stays the same. Define how old records will be read.
4. Update authoritative schemas and documentation together. Generate derived client/schema files using existing tooling when available; do not maintain conflicting copies.
5. Implement required adapters/migrations and affected call sites within the requested scope. Preserve historical meaning; a backfill must not invent evidence or approval.

## Domain invariants to inspect

- Claim, input, assessment and review identities remain distinct; stale results cannot replace newer results.
- Money retains exact decimal representation, currency, units and declared/agreed/approved semantics. Missing is not zero.
- Parts preserve original names, side and mapping uncertainty. Unknown side is not a left/right match.
- Photo masks and report boxes retain source IDs, dimensions/transforms and original-coordinate navigation.
- Overall outcomes retain individual check results and reasons; skipped or failed processing is not a passed check.
- Proposed additions have no declared entry/amount until a human creates them.
- Versioned cost ranges and model artifacts still explain historical assessments.

Inspect only invariants affected by the change; this is not a mandatory full-system audit for every field addition.

## Acceptance and output

Exercise a representative producer-to-consumer round trip, relevant invalid/null/unknown input, old/new compatibility, and persistence/export behaviour when changed. For a migration, verify the documented upgrade/restore approach against a disposable test database if available; do not run it on a shared live database merely to validate a schema.

Return the semantic change, affected paths, compatibility/version decision, migration implications and actual check results. Update relevant specification TODOs and [CONTEXT.md](../../../CONTEXT.md) after implementation. If asked only for analysis, return the impact assessment without modifying code.
