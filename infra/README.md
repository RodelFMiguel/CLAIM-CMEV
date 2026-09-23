# Containerized baseline

The baseline implements the `lean` packaging from [ADR 0002](../docs/adr/0002-containerised-event-runtime.md): React/nginx, FastAPI, one combined **fixture** worker, Redpanda, PostgreSQL 16 and MinIO. Application code is under `src/workbench` and `src/claim_cmev`; infrastructure contains packaging only. Processing results are explicitly mocked. This is not trained-model integration or a production deployment.

## Run from the repository root

Install Docker Engine/Desktop with Compose v2 or later, then:

```sh
cp infra/compose/.env.example infra/compose/.env
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile lean config --quiet
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile lean up --build -d
```

PowerShell users can use `Copy-Item infra/compose/.env.example infra/compose/.env` for the first command. The local `.env` is ignored by Git. Edit its demonstration credentials as needed. Use URL-safe characters for `CMEV_DB_PASSWORD`, because Compose inserts it into a database URL. If the repository is inside WSL and `docker` is unavailable in that distro, enable Ubuntu under Docker Desktop [WSL Integration](https://docs.docker.com/desktop/features/wsl/use-wsl/) or run Compose from Windows with Docker Desktop CLI on `PATH`.

Open <http://localhost:8080>. The supplied demonstration account is `surveyor@claim-cmev.demo` / `Demo2026!`; this is an intentionally public demonstration value. `CMEV_WEB_PORT` changes the published port. The web port is bound to loopback. The browser uses `/api/v1` through nginx, keeping cookie authentication on the same origin.

```sh
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile lean ps
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile lean logs --tail 100 cmev-api cmev-worker-combined
```

Stop without deleting stored claims:

```sh
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile lean down
```

Named volumes `postgres-data`, `kafka-data` and `objectstore-data` retain state across container replacement. `down --volumes` would discard all local demonstration state. PostgreSQL metadata and MinIO evidence must be backed up/restored together; the Kafka volume preserves dispatch history. This baseline does not automate coordinated snapshots.

## Runtime boundaries

| Service | Responsibility | Readiness |
| --- | --- | --- |
| `cmev-web` | Compiled frontend, same-origin API proxy | nginx `/healthz` |
| `cmev-api` | Authentication, claims, uploads, reviews and persisted API state | `/api/v1/readyz` dependency checks |
| `cmev-worker-combined` | Outbox relay, Kafka consumption and mocked processing | Fresh heartbeat after successful Kafka polling |
| `cmev-kafka` | Kafka-compatible Redpanda transport | `rpk cluster health` |
| `cmev-db` | PostgreSQL application state | `pg_isready` |
| `cmev-objectstore` | Private S3-compatible evidence storage | `/minio/health/ready` |

Only nginx publishes a host port. Database, broker, object store and API communicate on an internal network. Evidence goes through the authenticated API. This fixture slice consumes no model or cost-table files, so its containers have empty registry directories and no host bind mounts. Real module services must add the read-only registry/cost mounts and version verification required by ADR 0002 when those modules are implemented. Cost-table building remains offline.

API startup initializes the baseline schema, fixtures and evidence bucket. The combined worker starts after API readiness and broker health. API startup does not require a healthy broker: a broker outage can leave a persisted request pending for later dispatch. Redpanda topic auto-creation is enabled for this local baseline. Dedicated migrations/bootstrap jobs, explicit topic retention/partition configuration, separate module containers and `full`/GPU profile parity remain future work. The full runtime specification is not considered satisfied by this fixture implementation.

`CMEV_FIXTURE_MODE=true` is explicit in Compose. Setting it false does not enable real inference: the backend refuses unsupported live processing. The demo uses one local account and HTTP cookies; deployment authentication, TLS, scoped service credentials and production hardening are outside this baseline.

## Image selection and verification

- Python 3.12, Node 22, nginx 1.28 and PostgreSQL 16 images use minor/major tags. Record image digests when validating a release. Python dependency ranges are in `pyproject.toml`; npm dependencies use the application lockfile. A fully locked Python environment remains future work.
- Redpanda uses release tag `v26.2.2`. This is a single development broker with one CPU and a 1 GiB configured memory budget, not a measured whole-stack capacity target.
- The [official MinIO repository](https://github.com/minio/minio) is archived and its community distribution is source-only. `Dockerfile.objectstore` builds official release `RELEASE.2025-10-15T17-29-55Z` at exact commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, retaining its AGPL license. This avoids depending on unavailable historical image tags and preserves the ADR's S3 adapter choice. The archived dependency needs a separate supported-provider decision before a non-demo deployment. Its first source build downloads Go dependencies and can take several minutes.

Validated on 2026-09-23 with Docker Desktop 4.92.0/Linux Engine and Compose v5.5.1: `config --quiet` passed; all images built, including MinIO from the pinned source commit; the default `lean` profile started all six services healthy. Through nginx on localhost:8080, `/api/v1/healthz`, `/readyz` and `/docs` returned 200. The synthetic browser walkthrough passed login, upload to MinIO, Kafka worker processing, note/mark reassessment, finalization, frozen PDF, mobile layout and logout. A claim created before an API/worker image rebuild remained in PostgreSQL afterward. These checks do not measure real models, price accuracy, broker outage recovery, coordinated backups or the unimplemented `full` profile. The fixture profile omits empty host model/cost bind mounts; add ADR 0002 read-only mounts when real consumers are implemented.
