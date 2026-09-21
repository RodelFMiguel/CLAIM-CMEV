# ADR 0002 - Containerised event-driven runtime with Kafka, PostgreSQL and MinIO

Status: accepted. Date: 2026-09-22. Owner: Lane 5 with all lanes. Supersedes [ADR 0001](0001-prototype-runtime.md).

## Context

[ADR 0001](0001-prototype-runtime.md) proposed a workbench, backend, image worker and document worker from proposal v1 section 10.1, with PostgreSQL, a durable job table and local artifact storage. [Proposal v2](../CLAIM-CMEV_project_proposal_v2.md) section 9.1 then simplified this further: two processes from one code base (one FastAPI API and one worker), a database jobs table, explicitly **no message broker**, SQLite in write-ahead-log mode, local files, and optional Docker Compose.

On 2026-09-22 the user directed a different runtime: **containerise each module, and use Kafka, or a proposed equivalent, as the main transport between modules.** This is a change on top of proposal v2, not something v2 already said. It is recorded here so the difference stays visible.

Neither ADR 0001's runtime nor proposal v2 section 9.1's runtime has been implemented or smoke-tested. The repository is still a scaffold with no application code, so this decision changes a plan, not a working system.

Two further facts shape the decision. First, the project has a fixed budget of 50 person-days, of which Lane 5 holds 5 implementation days. Second, proposal v2's domain rules do not depend on the runtime: the decision rules in section 8, the records in section 9.3, the module scope in section 10, the datasets in section 11 and the evaluation plan in section 13 are unchanged by this ADR.

## Decision

Run CLAIM-CMEV as a containerised, event-driven system.

1. **One container per module**, plus a platform orchestrator and a consolidator. The canonical services are `cmev-web`, `cmev-api`, `cmev-orchestrator`, `cmev-worker-parts`, `cmev-worker-damage`, `cmev-worker-summary`, `cmev-worker-ocr`, `cmev-worker-lineitems`, `cmev-worker-penmarks`, `cmev-consolidator`, `cmev-kafka`, `cmev-db` and `cmev-objectstore`.
2. **Kafka is the main transport.** Topics are named `cmev.<kind>.<name>.v1`, where `cmd` is work to do and `evt` is a fact produced. The message key is `claim_id`, so one claim keeps its order. Messages carry references, never image bytes. Delivery is at-least-once, and every consumer is idempotent through the job-key uniqueness rule already in proposal v2 section 9.6.
3. **Redpanda is the broker** in development: Kafka API compatible, one container, no ZooKeeper, low memory. Swapping it for Apache Kafka changes an image and a bootstrap address, not application code.
4. **PostgreSQL 16 replaces SQLite.** This is forced by decision 1: SQLite takes one writer at a time and relies on file locks that are not reliable across containers. Nine containers committing rows for one claim cannot share it, and the single transaction that commits a job row together with its result rows is what makes at-least-once delivery safe. This is a consequence of the directive, not a separate scope increase.
5. **MinIO behind the existing storage adapter** for claim evidence and derived images. The model registry at `artifacts/models/<model_id>/<version>/` and the cost tables at `artifacts/cost_tables/<version>/` stay read-only host mounts so a worker can verify its weights before the object store is healthy.
6. **Two Compose profiles.** `lean` is web, api, one combined worker, kafka, db and object store, for a laptop demonstration. `full` is every module container. Same code, same topic contracts, same records in both, and their equality on identical inputs is an acceptance test.
7. **Evidence is served through `cmev-api`** with a claim authorisation check. Presigned object-store URLs are never handed to the browser.
8. **No language model sits in the decision path.** Part and damage integration is deterministic mask overlap in M2. Vision and document integration is the deterministic rule set in M8. Findings must be reproducible, version-pinned and immutable, and a language model cannot be pinned or evaluated inside the 50-person-day budget. An optional stretch service `cmev-explainer` may turn an already-computed finding into a readable sentence; it is off by default, never changes a result or a state, and is marked in provenance.

Module workers are independent **processes**: each owns its model loading, its consumer group, its failure handling and its scaling, and needs no synchronous call from any other module. They are not independent **authorities**: no independent schema, no independent taxonomy version, and no decision authority outside the module's own contract.

## Alternatives considered

- **The proposal v2 jobs table.** Workers poll a table, take a row under a lease, and commit the result and the job state in one transaction. This is genuinely the better fit for proposal v2 as written: one fewer container, no broker to learn, no consumer-group rebalance failure mode, exactly-once semantics from a single transaction, and it is debuggable with SQL. Its limits appear only at a scale this project does not reach. It is being set aside by directive, not because it was wrong, and it is retained as the last contingency step below.
- **Redis Streams.** Consumer groups and pending-entry lists would work, but it adds a second data store with weaker durability defaults while the team still writes the same idempotency and outbox code.
- **RabbitMQ.** Mature, with dead-letter exchanges built in, but its natural model is a work queue rather than a replayable log. Replaying a claim's message history is useful when debugging an immutable-assessment system.
- **NATS JetStream.** The strongest runner-up and lighter than Redpanda. Set aside because its client idioms are less widely known and because Kafka API compatibility keeps the directive's Kafka clients in play.
- **Celery.** Its unit is a Python function call, so every worker would need every module's task definitions importable. That conflicts directly with one container per module, and it hides the message contract inside decorators where it cannot be versioned as a `.v1` topic.
- **Synchronous HTTP between modules.** Rejected: inference takes seconds to minutes, which gives a long browser request, no durable record of in-flight work, and loss of one branch's results when another fails.

## Consequences and validation

The orchestrator owns the workflow: fan-out, stage chaining, the branch join and the single emit of `cmd.consolidate.v1`, claimed with a conditional update so it cannot fire twice. `cmev-api` owns intake, evidence, review and finalize, and loads no neural weights. `cmev-consolidator` owns M8. Workers own only their own module.

Safety depends on three mechanisms that must be built and tested first: the unique job key, the `dedup_key` table, and a transactional outbox in the API and in every worker so that "results committed" and "completion event published" cannot diverge. A consequence worth stating plainly is that the outbox also lets an upload succeed while the broker is down; the claim sits in `queued` with a dispatch-pending flag rather than failing.

**Added effort.** The containerised event runtime costs roughly **11 extra person-days** against proposal v2 section 9.1: about 2.0 for Kafka and the topic contracts, 1.5 for the outbox and idempotency, 2.0 for the orchestrator, 2.5 for eight extra images, 1.0 for PostgreSQL, 1.0 for MinIO and 1.0 for the two profiles. Lane 5 holds 5 implementation days, so this does not fit Lane 5 alone. Two responses are available and the team must choose: move the per-module Dockerfile and consumer wiring to each module owner, about 0.5 day each; or ship `lean` and treat `full` as a stretch. The `lean` profile is the named fallback, costing about 4 to 5 extra days rather than 11.

**Contradictions with proposal v2 that this ADR knowingly accepts.** Section 9.1 says two processes, a jobs table, no broker, SQLite and optional Compose; section 9.4 says M8 runs in the API process; section 12.1 gives Lane 5 five implementation days. This decision changes all of these. The M8 module specification must be updated to place M8 in `cmev-consolidator`, and the effort conflict is unresolved and must be reported under proposal v2 section 13.6 criterion 8 if it causes an overrun.

**Contingency ladder**, cut from the top if Lane 5 runs late: drop the `full` profile and ship `lean`; collapse `cmev-orchestrator` into `cmev-api`; drop MinIO for a mounted volume behind the same adapter; reduce every topic to one partition; and, as a last resort, return to the proposal v2 jobs table while keeping the same envelope shape, recorded as a reversal in a new ADR rather than a quiet change.

**Never affected by any of the above:** the separation of printed, effective and approved amounts; explicit failed and incomplete states; unknown part and side identity; version pinning; immutable findings; review persistence; and the rule that a fixture output is never a model result.

Before this decision can be called validated: pin every image digest after the day-2 hardware smoke test; demonstrate one command consumed twice producing one result set; demonstrate the branch join emitting `cmd.consolidate.v1` exactly once under two orchestrator replicas; demonstrate an upload succeeding with the broker stopped and dispatching when it returns; demonstrate a dead-lettered message showing as a failed branch and not as a clean assessment; and demonstrate the same claim producing identical findings under `lean` and `full`.

Affected documents: [technical specification](../specs/technical_specification.md), [application platform](../specs/application_platform.md), [integration contracts](../specs/integration_contracts.md), [data contracts](../specs/data_contracts.md), [module 08 consolidation and checks](../specs/module-08-consolidation-checks.md), [module 09 review and report](../specs/module-09-review-report.md), [evaluation plan](../specs/evaluation_plan.md).
