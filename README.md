# CLAIM-CMEV

Cross-Modal Evidence Verification of declared repair scope against photographic damage evidence in motor own-damage claims.

The project compares survey-report repair entries with vehicle photographs and reference cost ranges, then presents evidence for surveyor review. It preserves declared estimates, reviewed/agreed amounts, and later final approvals separately.

## Project status

Repository scaffold and implementation specifications. Application code, trained models, datasets, dependencies, and deployment manifests are still to be implemented. No application startup command is available yet.

Start with the [project proposal](docs/CLAIM-CMEV_project_proposal_v1.md), [specification index](docs/specs/README.md), [product specification](docs/specs/product_specification.md), and [technical specification](docs/specs/technical_specification.md).

## Shared development context

Read [CONTEXT.md](CONTEXT.md) for current status, decisions, validation evidence and the next team handoff. [AGENTS.md](AGENTS.md) supplies shared repository guidance; [CLAUDE.md](CLAUDE.md) imports that guidance for Claude Code.

The [coding-agent workflow guide](docs/agent-workflows.md) describes all seven portable skills, their Codex/Claude invocation, and how to keep their copies in sync. Canonical skills live in `.agents/skills/`; Claude Code copies live in `.claude/skills/`.

## Repository map

| Path | Responsibility |
| --- | --- |
| `apps/api/` | Backend entrypoint: intake, jobs, comparison, review, and exports |
| `apps/workbench/` | Module 9; working vehicle overview, repair-list review, and evidence viewer |
| `workers/image/`, `workers/document/` | Background entrypoints for Modules 1-3 and 4-5 |
| `src/claim_cmev/` | Python domain modules, contracts, taxonomy, storage, and orchestration |
| `pipelines/` | Offline vision/document training and cost training/refresh |
| `configs/` | Versioned model, taxonomy, pipeline, cost, and evaluation configuration |
| `data/` | Local datasets; tracked manifests and split definitions |
| `artifacts/` | Local model files, cost-table builds, evaluation results, and exports |
| `tests/` | Unit, contract, integration, end-to-end, evaluation, and synthetic fixtures |
| `infra/` | Future local deployment manifests and database migrations |
| `scripts/`, `notebooks/` | Reproducible utilities and exploratory analysis |
| `docs/specs/`, `docs/adr/`, `docs/mockups/` | Specifications, architecture decisions, and future static screen mockups |

## Getting started

1. Assign the five ownership lanes and review the [shared data contracts](docs/specs/data_contracts.md).
2. Record the proposed technology decisions in [docs/adr](docs/adr/README.md).
3. Implement the [application platform](docs/specs/application_platform.md) and connect explicitly synthetic fixtures before model integration.
4. Work through the nine module task lists and [evaluation plan](docs/specs/evaluation_plan.md).

Store claim files, datasets, weights, credentials, and generated exports outside tracked source files. See the [data policy](data/README.md) and [artifact policy](artifacts/README.md). Synthetic costs demonstrate comparison logic; they do not establish real repair-price accuracy or savings.
