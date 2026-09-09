# ADR 0001 - Prototype runtime and proposed stack

Status: proposed implementation stack. Date: 2026-09-09. Owner: Lane 5 with all lanes.

## Context

The [proposal](../CLAIM-CMEV_project_proposal_v1.md), section 10.1, requires a workbench, backend, image worker and document worker. Offline training and cost refresh sit outside request processing. A five-member team needs shared contracts and manageable deployment.

## Proposed decision

Use a monorepo with Python domain modules, a proposed FastAPI backend, a proposed React/TypeScript workbench, Python worker entrypoints, PostgreSQL for relational records and durable jobs, and local artifact storage behind an adapter. Use a private Compose deployment after entrypoints exist. Pin tool/runtime/library versions after an environment/model compatibility smoke test.

The four runtime boundaries come from the proposal. Frameworks, database selection and package versions remain team implementation choices; this ADR does not claim they are installed.

## Alternatives considered

Nine separately deployed module services add deployment/contracts overhead beyond the proposal. A single synchronous HTTP process does not provide the required independent background model processing. A separate broker may become useful later but is not required for the prototype's durable job table.

## Consequences and validation

The backend owns coordination, comparison, access and review transactions. Workers require tested leases, retries and atomic result publication. PostgreSQL and artifact volumes must be restorable together. No production availability or scaling guarantees are assumed.

Before accepting: confirm team skills and hardware; smoke-test dependencies/OCR/model loading; demonstrate one database-leased fixture job and review save; document package locks and private deployment configuration.

Affected documents: [technical specification](../specs/technical_specification.md), [platform](../specs/application_platform.md), [contracts](../specs/data_contracts.md).
