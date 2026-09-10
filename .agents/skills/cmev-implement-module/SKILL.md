---
name: cmev-implement-module
description: >-
  Implement a selected CLAIM-CMEV module requirement or TODO using its specification,
  shared contracts, and acceptance criteria. Use for module implementation and
  integration work, not standalone reviews or unrelated documentation edits.
---

# Implement a CLAIM-CMEV module

Deliver the requested behaviour with evidence of what works and what remains. This skill is portable across coding agents; use the available file, shell and test tools, without depending on a particular agent API.

## Establish the task

Read [AGENTS.md](../../../AGENTS.md), the current [handoff context](../../../CONTEXT.md), and the relevant row in the [specification index](../../../docs/specs/README.md). Resolve paths against the repository root, including when invoked from a subdirectory.

Identify the module, requested requirement/TODO, existing implementation and acceptance criteria. Read that module's specification, relevant [contracts](../../../docs/specs/data_contracts.md), and affected accepted [ADRs](../../../docs/adr/README.md). Load other specifications only when a dependency needs them.

The proposed framework choices are not accepted decisions merely because they appear in ADR 0001. Use existing manifests and current user decisions when available. A routine implementation choice does not need a new approval ceremony; record consequential new decisions and unresolved assumptions accurately.

## Implement

- Trace inputs, outputs and the owning runtime before changing code. Keep domain logic in its package, HTTP wiring in the backend and training outside request processing.
- Reuse existing schemas, taxonomy and storage interfaces. If the task changes a shared contract, update affected producers and consumers rather than making a private variant.
- Preserve input/assessment/review versions, evidence references and uncertainty states. A failed worker cannot become an empty successful result.
- Implement the requested slice and required integration. Use fixtures for integration where appropriate, with explicit fixture provenance; fixtures do not fulfil trained-model acceptance.
- Keep unrelated work and user changes intact. Inspect actual commands/manifests before running checks; do not invent a test/startup command because the directory exists.

If an essential dependency is absent, complete independent work and report the specific missing input. Do not fabricate model outputs, dataset results or successful runtime checks, or bootstrap the entire project to answer a narrow request.

## Verify and hand off

Run checks that demonstrate the changed behaviour, including relevant failure or uncertainty cases from the module specification. For interface changes, include a meaningful producer/consumer check. Run training or broader evaluations only when needed and within the requested experiment scope.

Update only TODOs whose acceptance conditions are demonstrated. Distinguish implemented, checked with fixtures, integrated with real model output, and evaluated against held-out targets.

Report changed paths, requirement coverage, commands/results and remaining limitations. Update [CONTEXT.md](../../../CONTEXT.md) after meaningful implementation work using its handoff format; preserve previous team history.
