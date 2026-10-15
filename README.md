# CLAIM-CMEV

Evidence-led motor own-damage claim review: vehicle photographs, marked workshop estimates and surveyor decisions in one workspace.

## Working baseline

The application provides a public information page, session login, searchable claim dashboard, claim intake, review and browser print views. React/TypeScript calls FastAPI endpoints. The live stack connects trained M1/M2 image models, M3 evidence summary, pretrained M4 OCR, the selected M5/M6 notebook exports, pinned M7 costs, deterministic M8 checks and M9 review/reporting. Corrections trigger versioned reassessment, and finalization freezes the reviewed report. M5/M6 remain synthetic-trained candidates with layout/generalization limits; synthetic costs do not validate real prices or final claim approval. The base Compose file alone runs fixture stage producers.

Application code lives under `src`, as recorded in [ADR 0003](docs/adr/0003-fixture-ui-api-baseline.md). The [current handoff](CONTEXT.md) records checks actually run and remaining work.

## Run with containers

Requires Docker Engine/Desktop with Compose support. From the repository root:

```sh
# First setup only; keep your existing .env when restarting.
cp infra/compose/.env.example infra/compose/.env
# Edit the local demo password and infrastructure credentials in the copied file.
sh scripts/live-stack.sh up --build -d
```

Open **http://localhost:8080**. Sign in with `surveyor@claim-cmev.demo` and the `CMEV_DEMO_PASSWORD` configured above. The login page includes a default demo credential suggestion; replace its password if you configured a different value. API documentation is at **http://localhost:8080/api/v1/docs**; the OpenAPI schema is at `/api/v1/openapi.json`.

The live command requires the selected model entries under `artifacts/models/`, including the pinned OCR release. It starts lean orchestration plus real image, OCR, line-item and pen-mark workers. See [live document setup](docs/document-worker-live.md) for model provisioning and limitations. Use `sh scripts/live-stack.sh ps`, `logs`, and `down` to keep the same overlays on every command. Infrastructure services are private; only the web port is published to loopback. See [infra/README.md](infra/README.md) for persistent volumes and other deployment modes.

For a fixture demonstration without model weights, use the base Compose file alone. If live workers are already running, first stop them with `sh scripts/live-stack.sh down` (keeps volumes), then run the base-only command. Dropping overlays from `up` leaves old workers running beside fixture consumers and can cause `model_version_unsupported` failures.

## Local development without containers

Requires Python 3.12+ and Node.js 22+. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
# Offline synthetic reference build, required before starting the app:
python -m claim_cmev.costs.reference.build --seed 20260924 --promote
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
.venv/bin/python -m pytest -q
cd src/workbench
npm run build
# With the local API, worker and frontend already running:
npx playwright install chromium
npm run test:e2e
```

See [test instructions](tests/README.md) for additional review controls and print checks. The browser check uses the running application and synthetic fixtures. Generated screenshots and runtime data stay in ignored locations. Linux browser installations may need the Playwright system libraries.

## Source map

| Path | Responsibility |
| --- | --- |
| `src/workbench/` | React UI, API client and local decorative assets |
| `src/claim_cmev/api/` | FastAPI HTTP and session boundary |
| `src/claim_cmev/worker.py`, `orchestration/`, `messaging/` | Worker roles, persisted stage join, fixture producers and transactional delivery |
| `src/claim_cmev/runtime.py`, `persistence/` | Runtime configuration, SQL records and migrations |
| `src/claim_cmev/contracts/`, `comparison/`, `review/` | Typed records, M8 checks and M9 review/report rules |
| `src/claim_cmev/storage.py` | Local/S3 evidence adapter; bytes served through the API |
| `src/claim_cmev/fixtures.py` | Explicit synthetic seed records and mocked results |
| `infra/` | Container build/proxy/Compose configuration |
| `tests/backend/`, `tests/e2e/` | API invariants and browser walkthrough |
| `pipelines/`, `configs/`, `data/`, `artifacts/` | Model/data workspaces, largely reserved for later integration |
| `docs/specs/`, `docs/adr/` | Full implementation target and recorded decisions |

## Project guidance

[Proposal v2](docs/CLAIM-CMEV_project_proposal_v2.md) governs scope; [ADR 0002](docs/adr/0002-containerised-event-runtime.md) records the runtime change. Start coding from the [specification index](docs/specs/README.md), [AGENTS.md](AGENTS.md) and [CONTEXT.md](CONTEXT.md). Shared [agent workflows](docs/agent-workflows.md) live in `.agents/skills/` with generated Claude copies.

The decorative car image was generated for this UI and is not claim evidence. Public-page content and style are informed by the supplied [Gamma reference](https://claim-cmev-m1mw11h.gamma.site/); project specifications govern behaviour. Keep claim material, credentials, model weights and generated exports out of Git.
