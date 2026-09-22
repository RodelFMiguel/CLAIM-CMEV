---
name: cmev-implement-module
description: >-
  Implement a selected CLAIM-CMEV module requirement or TODO using its specification,
  shared contracts, and acceptance criteria. Use for module implementation and
  integration work, not standalone reviews or unrelated documentation edits.
---

# Implement a CLAIM-CMEV module

Deliver the requested behaviour with evidence of what works and what remains. This skill is portable across coding agents; use the available file, shell and test tools, without depending on a particular agent API.

Use the current specifications below for implementation details and acceptance criteria. [Proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope; [ADR 0002](../../../docs/adr/0002-containerised-event-runtime.md) records the later runtime change. Read the sections relevant to the task, preserve proposed versus accepted status, and reconcile concrete conflicts with current user decisions. A specification is not evidence that its behaviour is implemented.

## Establish the task

Read [AGENTS.md](../../../AGENTS.md) and [CONTEXT.md](../../../CONTEXT.md), then use the [specification index](../../../docs/specs/README.md) to locate the selected module, owner and implementation path. Open that module specification; the index and proposal alone are not sufficient. Resolve links against the skill location even when invoked from a subdirectory.

Before coding, identify the applicable requirement IDs, input/output contracts, owning runtime, acceptance criteria and existing implementation. Read the relevant sections of these specifications, following the index's shared-document order where several apply:

| Source | What to use it for |
| --- | --- |
| [Product specification](../../../docs/specs/product_specification.md) | Required behaviour, FR/NFR IDs, invariants and scope boundaries |
| [Technical specification](../../../docs/specs/technical_specification.md) | Containers, architecture, storage, deployment and model-serving boundaries |
| [Application platform](../../../docs/specs/application_platform.md) | Intake, HTTP API, orchestration, revisions, review persistence and finalization |
| [Integration contracts](../../../docs/specs/integration_contracts.md) | Kafka topics, envelopes, producers/consumers, idempotency, retries and dead letters |
| [Data contracts](../../../docs/specs/data_contracts.md) | Shared record fields, schema/taxonomy versions, evidence references and money semantics |
| [Model training specification](../../../docs/specs/model_training_specification.md) | Dataset preparation, training, thresholds, checkpoints and registry handover when model work is involved |
| [UI specification](../../../docs/specs/ui_specification.md) | Screens, states, interactions and user stories when user-facing behaviour is involved |
| [Evaluation plan](../../../docs/specs/evaluation_plan.md) | Applicable module, integration, safety and service checks and how completion is demonstrated |
| Selected [module specification](../../../docs/specs/README.md#functional-modules) | Module algorithm, dependencies, TODOs and acceptance criteria; open the actual linked file |

Use [ADR 0002](../../../docs/adr/0002-containerised-event-runtime.md) and the technical/integration specifications for the container/Kafka runtime, rather than recreating v2's earlier SQLite/jobs-table design. Keep M7 builds offline. Inspect other [ADRs](../../../docs/adr/README.md) only when they affect the task. Follow actual manifests and current user decisions; proposed dependencies and recipes are not installed or validated merely because they are specified. Record consequential decisions without adding an approval gate to already authorised work.

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
