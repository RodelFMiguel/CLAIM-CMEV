# Application platform specification

Owner: Lane 5, with Lane 4 for comparison and approvals. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), section 10. This supporting component is required alongside the nine functional modules.

Code locations: `apps/api/`, `workers/image/`, `workers/document/`, `src/claim_cmev/orchestration/`, `src/claim_cmev/persistence/`, `infra/`. Contracts: [shared records](data_contracts.md).

## Responsibilities and boundaries

Provide intake, file storage, claim/input/assessment/review revisions, durable jobs, branch coordination, evidence access, review persistence, structured export, controlled approval import, and reference-version registration. Call M07-M08 through domain interfaces. Worker entrypoints load approved versioned model bundles and run M01-M03 or M04-M05.

The prototype uses a persistent job table. It does not need a message broker, nine services, production SSO, or an insurer approval integration. A minimal intake API/development form supports the demonstration even though the full claim queue is a static mockup.

## Proposed HTTP surface

Version the API under `/api/v1`. Requests and responses validate against shared contracts; export the accepted API schema for the workbench client.

| Method and path | Behaviour |
| --- | --- |
| POST `/claims` | Create claim metadata and return stable claim ID |
| POST `/claims/{id}/files` | Stage validated originals; return file IDs and hashes without starting incomplete input |
| POST `/claims/{id}/input-revisions` | Atomically commit metadata, file IDs, and declaration source; return revision and job IDs |
| GET `/claims/{id}/processing` | Branch/job status, current revisions, error/retry details, and awaiting-input reason |
| POST `/claims/{id}/jobs/{job_id}/retry` | Retry a failed task with the same execution signature; do not duplicate successes |
| GET `/claims/{id}/image-summary?input_revision=...` | Coverage and merged damage, including photo-only results |
| GET `/claims/{id}/assessments/{revision}` | Immutable entries/findings, additions, dependency versions, and current review |
| POST `/claims/{id}/assessments` | Explicit reassessment referencing input and selected version bundle |
| GET `/claims/{id}/files/{file_id}` | Authorised original/render/mask artifact; reject unrelated-claim access |
| POST `/claims/{id}/assessments/{revision}/review-events` | Save action batch with idempotency key and expected review revision |
| GET `/claims/{id}/assessments/{revision}/export` | Versioned structured JSON export including the selected review revision |
| POST `/approval-imports` | Controlled synthetic final-approval import with row-level validation results |

Mutations return committed identifiers and revision numbers. Async submissions return accepted job references, never premature “ready”. Use validation errors for invalid fields/files, not-found/access errors for invalid resources, and conflict responses for stale revisions or reuse of an idempotency key with a different payload. A replay with the same key and payload returns the original committed result.

An input correction from the review screen commits the review action and new input revision consistently and schedules reassessment. An agreed amount does not rewrite the declared amount or trigger an approval.

## State model

| Level | States and transitions |
| --- | --- |
| Job | `queued -> running -> succeeded`; `running -> failed`; retry or expired lease returns eligible work to queued |
| Assessment processing | `queued -> processing -> ready` when all required results and comparison are committed |
| Partial progress | `awaiting_declared_entries` when valid images exist but declarations do not; `incomplete` when a required branch failed and useful results remain |
| Total failure | `failed` when required processing cannot produce usable results; explicit retry may resume |
| Review | `unreviewed / in_review / reviewed` on a ready assessment; events append review revisions |
| Approval | `not_supplied / pending / approved / rejected / superseded`, separate from assessment and review |

A `ready` assessment may contain insufficient-evidence findings: processing completion is different from evidence adequacy. Worker failure blocks “ready”. A missing cost-table artifact is an operational failure; a valid table lacking a comparable range is an insufficient-support finding. Photo-only image results remain viewable without labelling comparison complete.

## Job execution and revision safety

Each job stores ID, claim/input revision, task, full version signature, state, attempt count, scheduled time, lease owner/expiry, heartbeat, result IDs, and structured error. Use atomic claims/leases and unique keys. Configure retry limits/backoff; retain terminal errors after exhaustion.

Complete artifacts in temporary locations then publish them under immutable IDs. In one transaction attach result metadata and mark job success. Reclaim expired work after worker crash. Replaying the completion transaction must not duplicate observations or findings.

The coordinator compares only validated results for the same input revision and pinned execution bundle. A newer input does not delete historical results. Stale completion may update its own job but must not replace the current pointer. Concurrent review saves use optimistic revision checks; never silently overwrite another user's edit.

## Persistence and access

Use relational transactions for revisions, jobs, findings, review events and approval lineage; artifact storage for originals, renders, masks and exports. Keep hashes and original names separate from generated storage paths. Validate type/signature, readability, configurable file/page/size limits, and safe storage names before accepting an input revision. Failed uploads must not create a complete claim input.

Backend access checks cover claim records and all derived artifacts. A controlled demo identity is sufficient for the prototype; server-side scope checks are still required. Retain uncommitted upload metadata for explicit later cleanup; define cleanup/retention rather than deleting originals during ordinary retries.

Exports include schema version, exact input/assessment/review revisions, original declarations, human edits and agreed amounts, findings/additions, evidence IDs, model/config/taxonomy/cost-table versions, synthetic markers, and approval status. Export is an assessment record, not a settlement instruction. Do not issue PDF or insurer-specific report generation as a hidden prerequisite.

## Approval and cost-table support

The importer checks role, source/test identifiers, amounts, units, reviewed entry lineage, synthetic status, timestamps, duplicates and supersession. Import retries are idempotent. Save invalid row reasons and a batch summary; use an explicit documented transaction policy for partial imports.

Expose eligible approved records to offline M06 and persist its membership/cutoff manifest. A cost-table promotion changes only the future default; historical assessments retain their table IDs. Include demo approval imports as visibly synthetic and never as evidence of insurer approval integration.

## Acceptance scenarios

| Scenario | Required result |
| --- | --- |
| Same input submitted twice with one idempotency key | Same revision/jobs returned |
| One worker fails after the other succeeds | Successful branch remains accessible; assessment incomplete |
| Worker crashes after writing masks before committing | Retry publishes one result set; orphan artifacts do not appear as findings |
| Old job completes after new photographs | Current assessment continues to reference the new input |
| Review save times out after server commit | Replay acknowledges one saved action |
| Two edits use the same expected review revision | One commits; other sees conflict with its local edits preserved |
| Same approved row imported twice | One eligible reference member |
| New cost table promoted while a claim is open | Open assessment unchanged; explicit reassessment creates a new revision |
| Wrong-claim evidence ID requested | Access denied without returning artifact bytes |

## Tasks

- [ ] Implement API/worker entrypoints, configuration, and dependency manifests.
- [ ] Implement migrations and repositories for all shared records.
- [ ] Implement safe file staging, atomic input commit, and evidence routes.
- [ ] Implement leases, retries, heartbeats, job uniqueness, and branch coordination.
- [ ] Implement photo-only flow and report/structured-input source selection.
- [ ] Integrate M07-M08 with fixed cost/model/config versions.
- [ ] Implement review-event idempotency, optimistic concurrency, and export.
- [ ] Implement controlled approval import, supersession, and refresh eligibility.
- [ ] Add health/readiness checks and correlated logs without raw claim content.
- [ ] Execute all acceptance scenarios and document local deployment/restore.
