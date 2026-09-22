# Architecture decisions

Record decisions that affect contracts, dependencies, deployment, model methods or data semantics. [Proposal v2](../CLAIM-CMEV_project_proposal_v2.md) sets the current scope and [v1](../CLAIM-CMEV_project_proposal_v1.md) remains historical context; [technical specifications](../specs/technical_specification.md) describe the implementation baseline.

## Decision log

| ID | Decision | Date | Status |
| --- | --- | --- | --- |
| [0001](0001-prototype-runtime.md) | Prototype runtime and proposed stack | 2026-09-09 | **Superseded** by 0002; retained as history |
| [0002](0002-containerised-event-runtime.md) | Containerised event-driven runtime with Kafka, PostgreSQL and MinIO | 2026-09-22 | **Accepted**; supersedes 0001 |
| [0003](0003-fixture-ui-api-baseline.md) | Public entry, login, dashboard and fixture API baseline under src | 2026-09-22 | **Accepted** user-directed baseline; runtime validation reported separately |

Use numbered Markdown files with: status, date, owner, context, decision, alternatives, consequences, validation evidence and affected specifications. Distinguish accepted decisions from hypotheses. Record a superseding decision rather than silently rewriting a previously accepted architecture.

ADR 0002 records a runtime directed by the user on 2026-09-22 that differs from [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) section 9.1, which specified two processes, a database jobs table, no message broker and SQLite. The proposal is not rewritten by this ADR. The difference, its effort consequence and the `lean` fallback profile are stated in ADR 0002 and in the [technical specification](../specs/technical_specification.md).

Next decisions include the message serialisation format and whether a schema registry is used, OCR granularity and the document model, the part and side taxonomy, cost schema, calibration and support thresholds, and the schema and API compatibility policy.
