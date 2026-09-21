---
name: cmev-implement-module
description: >-
  Implement a selected CLAIM-CMEV module requirement or TODO using its specification,
  shared contracts, and acceptance criteria. Use for module implementation and
  integration work, not standalone reviews or unrelated documentation edits.
---

# Implement a CLAIM-CMEV module

Deliver the requested behaviour with evidence of what works and what remains. This skill is portable across coding agents; use the available file, shell and test tools, without depending on a particular agent API.

Use [proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) for current scope and the [workflow guide](../../../docs/agent-workflows.md#scope-and-specification-transition) for module mapping and specification-transition limits. Read only the v2 sections relevant to this task; unmigrated v1 content does not override v2. Check newer explicitly recorded user decisions and their affected specifications before applying runtime assumptions.

## Establish the task

Read [AGENTS.md](../../../AGENTS.md), the current [handoff context](../../../CONTEXT.md), and the relevant row in the [specification index](../../../docs/specs/README.md). Resolve paths against the repository root, including when invoked from a subdirectory.

Identify the module by v2 number and name, requested requirement/TODO, existing implementation and acceptance criteria. Check v2 sections 10 and 12 for module/lane mapping; an old TODO or renamed file is not proof that the requirement is still in scope. Read that module's specification, relevant [contracts](../../../docs/specs/data_contracts.md), and affected accepted [ADRs](../../../docs/adr/README.md). Load other specifications only when a dependency needs them.

V2 section 9 originally plans a browser, API and one worker with SQLite/local files. Check the current technical specification and ADR status for later runtime decisions before implementing those boundaries; concurrent specification work records a container/Kafka direction. Keep M7 training/builds offline and preserve v2 domain rules under any transport. Record the affected scope/runtime transition, distinguish directed choices from proposed dependencies, and never infer deployment from documentation. Use existing manifests and current user decisions when available. A routine implementation choice does not need a new approval ceremony; record consequential new decisions and unresolved assumptions accurately.

## Implement

- Trace inputs, outputs and the owning runtime before changing code. Keep domain logic in its package, HTTP wiring in the backend and training outside request processing.
- Reuse existing schemas, taxonomy and storage interfaces. If the task changes a shared contract, update affected producers and consumers rather than making a private variant.
- Preserve input/assessment/review versions, original predictions, evidence coordinates and uncertainty states. A failed worker cannot become an empty successful result. Decision-changing mark, amount, identity, coverage or completeness corrections create new assessments; notes/dismissals remain review actions on their findings.
- Apply v2 section 8 gates before checks: pending marks do not exclude rows or enable printed-price fallback; unknown identity or inadequate coverage withholds photo conclusions. Keep declaration completeness explicit for possible additions.
- Implement the requested slice and required integration. Use fixtures for integration where appropriate, with explicit fixture provenance; fixtures do not fulfil trained-model acceptance.
- Keep unrelated work and user changes intact. Inspect actual commands/manifests before running checks; do not invent a test/startup command because the directory exists.

Keep LayoutLMv3/CORD/label alignment (S1), TrOCR suggestions (S2) and approval-refresh scripts (S3) optional under v2 section 12.3. Disabled stretch artifacts are not core startup dependencies. Preserve the 25 implementation/data, 10 integration/evaluation, 5 contingency and 10 reporting person-day allocation; record scoped contingency choices rather than quietly expanding the baseline.

If an essential dependency is absent, complete independent work and report the specific missing input. Do not fabricate model outputs, dataset results or successful runtime checks, or bootstrap the entire project to answer a narrow request.

## Verify and hand off

Run checks that demonstrate the changed behaviour, including relevant failure or uncertainty cases from the module specification. For interface changes, include a meaningful producer/consumer check. Run training or broader evaluations only when needed and within the requested experiment scope.

Update only TODOs whose acceptance conditions are demonstrated. Distinguish implemented, checked with fixtures, integrated with real model output, and evaluated against held-out targets.

Report changed paths, requirement coverage, commands/results and remaining limitations. Update [CONTEXT.md](../../../CONTEXT.md) after meaningful implementation work using its handoff format; preserve previous team history.
