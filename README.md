# CLAIM-CMEV

Evidence-led motor own-damage claim review: vehicle photographs, marked workshop estimates and surveyor decisions in one workspace.

## Working baseline

The baseline provides a public information page, session login, searchable claim dashboard, claim intake, review and browser print views. React/TypeScript calls real FastAPI endpoints. Persistence and review actions are real; the separate worker supplies clearly labelled fixture processing results. No trained model analyses uploaded evidence, and synthetic costs do not validate real prices or final claim approval.

Application code lives under `src`, as recorded in [ADR 0003](docs/adr/0003-fixture-ui-api-baseline.md). The [current handoff](CONTEXT.md) records checks actually run and remaining work.

## Run with containers

Requires Docker Engine/Desktop with Compose support. From the repository root:

```sh
cp infra/compose/.env.example infra/compose/.env
# Edit the local demo password and infrastructure credentials in the copied file.
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile lean up --build
```

Open **http://localhost:8080**. Sign in with `surveyor@claim-cmev.demo` and the `CMEV_DEMO_PASSWORD` configured above. The login page includes a default demo credential suggestion; replace its password if you configured a different value. API documentation is at **http://localhost:8080/api/v1/docs**; the OpenAPI schema is at `/api/v1/openapi.json`.

The lean baseline contains the web app, API, fixture worker, Kafka-compatible broker, PostgreSQL and S3-compatible object storage. Infrastructure services are private; only the web port is published to loopback. See [infra/README.md](infra/README.md) for configuration, persistent volumes, image dependencies and validation limitations. A complete per-module `full` runtime is not implemented by this baseline.

## Local development without containers

Requires Python 3.12+ and Node.js 22+. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
uvicorn claim_cmev.api.main:app --host 127.0.0.1 --port 8000
```

In a second terminal with the same virtual environment:

```sh
python -m claim_cmev.worker --local
```

In a third terminal:

```sh
cd src/workbench
npm ci
npm run dev -- --host 127.0.0.1
```

Open **http://localhost:5173**. The Vite proxy forwards `/api` to port 8000. Default local demo credentials are `surveyor@claim-cmev.demo` / `Demo2026!`. Local development uses SQLite, local files and the explicitly selected local worker transport. It does not exercise Kafka, PostgreSQL or object storage. Use only synthetic demonstration material.

## Checks

```sh
.venv/bin/python -m pytest tests/backend -q
cd src/workbench
npm run build
# With the local API, worker and frontend already running:
npx playwright install chromium
npm run test:e2e
```

The browser check uses the running application and synthetic fixtures. Generated screenshots and runtime data stay in ignored locations. Linux browser installations may need the Playwright system libraries.

## Source map

| Path | Responsibility |
| --- | --- |
| `src/workbench/` | React UI, API client and local decorative assets |
| `src/claim_cmev/api/` | FastAPI HTTP and session boundary |
| `src/claim_cmev/worker.py` | Separate fixture worker and transactional outbox delivery |
| `src/claim_cmev/runtime.py` | Baseline persistence/configuration |
| `src/claim_cmev/storage.py` | Local/S3 evidence adapter; bytes served through the API |
| `src/claim_cmev/fixtures.py` | Explicit synthetic seed records and mocked results |
| `infra/` | Container build/proxy/Compose configuration |
| `tests/backend/`, `tests/e2e/` | API invariants and browser walkthrough |
| `pipelines/`, `configs/`, `data/`, `artifacts/` | Model/data workspaces, largely reserved for later integration |
| `docs/specs/`, `docs/adr/` | Full implementation target and recorded decisions |

## Project guidance

[Proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md) governs scope; [ADR 0002](docs/adr/0002-containerised-event-runtime.md) records the runtime change. Start coding from the [specification index](docs/specs/README.md), [AGENTS.md](AGENTS.md) and [CONTEXT.md](CONTEXT.md). Shared [agent workflows](docs/agent-workflows.md) live in `.agents/skills/` with generated Claude copies.

The decorative car image was generated for this UI and is not claim evidence. Public-page content and style are informed by the supplied [Gamma reference](https://claim-cmev-m1mw11h.gamma.site/); project specifications govern behaviour. Keep claim material, credentials, model weights and generated exports out of Git.
