# Containerized baseline

The baseline implements the `lean` packaging from [ADR 0002](../docs/adr/0002-containerised-event-runtime.md): React/nginx, FastAPI, one combined **fixture** worker, Redpanda, PostgreSQL 16 and MinIO. Application code is under `src/workbench` and `src/claim_cmev`; infrastructure contains packaging only. Input evidence records are explicitly fixtures; the join, M8 rules, pinned synthetic cost lookup and review persistence execute real application code. The full profile separates orchestrator, fixture producers and consolidator into three worker services. This is not trained-model integration or a production deployment.

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

Named volumes `postgres-data`, `kafka-data`, `objectstore-data` and `cost-tables` retain state across container replacement. `down --volumes` would discard all local demonstration state. PostgreSQL metadata and MinIO evidence must be backed up/restored together; the Kafka volume preserves dispatch history. This baseline does not automate coordinated snapshots.

## Runtime boundaries

| Service | Responsibility | Readiness |
| --- | --- | --- |
| `cmev-web` | Compiled frontend, same-origin API proxy | nginx `/healthz` |
| `cmev-api` | Authentication, claims, uploads, reviews and persisted API state | `/api/v1/readyz` dependency checks |
| `cmev-worker-combined` | Outbox relay, orchestration, fixture stages and M8 consolidation | Fresh heartbeat after successful Kafka polling |
| `cmev-kafka` | Kafka-compatible Redpanda transport | `rpk cluster health` |
| `cmev-db` | PostgreSQL application state | `pg_isready` |
| `cmev-objectstore` | Private S3-compatible evidence storage | `/minio/health/ready` |

Only nginx publishes a host port. Database, broker, object store and API communicate on an internal network. Evidence goes through the authenticated API. The offline cost-bootstrap job publishes a deterministic synthetic table into the cost-tables volume without network access. API and workers mount that volume read-only and pin a table per assessment. No trained model registry is consumed unless the parts worker's Compose file is added (see below).

API startup applies Alembic migrations, seeds demonstration claims and initializes the evidence bucket. Readiness checks the expected schema head. Workers start after API and broker health and cost-bootstrap completion. Topics are explicitly created by worker startup; broker topic auto-creation is disabled. Outbox requests can remain pending during a broker outage.

Use --profile full instead of --profile lean to run the split fixture services. Stop cmev-worker-combined before switching an existing project to full; stop the split services before switching back. Full still groups all fixture producers in one container. The only per-model container is the opt-in M1 parts worker described below; the other model containers, GPU parity and dedicated migration-job packaging remain future work.


`CMEV_FIXTURE_MODE=true` is explicit in Compose. Setting it false does not enable real inference: the backend refuses unsupported live processing. The one exception is the parts stage, which is switched by adding `docker-compose.parts.yml`. The demo uses one local account and HTTP cookies; deployment authentication, TLS, scoped service credentials and production hardening are outside this baseline.

## Optional real image branch: M1, M2 and M3

`cmev-worker-image` runs the trained part-segmentation model, the trained damage model and the part summary in place of the three fixture image producers. It is off by default. It uses the vision image (`Dockerfile.parts`), the only one with the model libraries.

```sh
# artifacts/models/ must hold a verified entry for the model_version of configs/models/parts.yaml
# (parts/0.8.0-b3-compound) and of configs/models/damage.yaml (damage-hitl/0.1.0-b3-compound).
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.image.yml --profile lean up --build -d
```

That file is the whole switch: it starts the worker and sets `CMEV_PARTS_PRODUCER=real` and `CMEV_DAMAGE_PRODUCER=real` on every service. Use the same two `-f` files for `ps`, `logs` and `down`, and run `down` with both before going back to fixtures, as for the M1-only mode below. Do not add `docker-compose.parts.yml` as well.

What changes:

| Stage | With `docker-compose.image.yml` added |
| --- | --- |
| M1 parts | The parts checkpoint segments each uploaded photograph, as in the M1-only mode |
| M2 damage | The damage checkpoint segments the same photograph in the same model frame. Each damage region is assigned to a part by overlap with the M1 mask of that photograph. Observations carry the HITL damage vocabulary (`damage-hitl-1.0.0`) |
| M3 summary | Groups the real observations and measures the screening signals on the real masks. Only a surveyor's recorded confirmations count, so every coverage slot starts `unresolved` |
| M4 to M6, M8 | Unchanged: the document stages are fixtures and M8 runs its rules. The assessment is still labelled a fixture |

The worker loads and verifies both checkpoints before it reads any message, and exits with code 1 and the reason in its log when either fails the checks listed for the parts worker below. For the damage entry the class numbering comes from `label_schema.json` and must be background plus exactly the configured taxonomy's codes. `pipelines.vision.registry.adopt_damage_run` completes a damage trainer's output folder into such an entry. `CMEV_DAMAGE_PRODUCER=real` without `CMEV_PARTS_PRODUCER=real` is refused by every service.

Limits of this mode:

- The served damage model is weak: 0.158 foreground mIoU on its test split, and near zero for cracked, flaking and paint-chip. Its output is evidence for a surveyor to review, not a finding.
- With no confirmations, M8 withholds every photo conclusion. A surveyor's identity and coverage confirmations create a new input revision that reruns only M3.
- The seed claims have no photo bytes and fail their parts stage in this mode, as in the M1-only mode.
- The served models are named in `configs/models/parts.yaml` and `configs/models/damage.yaml`, which are copied into both images. The orchestrator and the API read them to pin versions and the worker reads them to load weights, so a change of served model needs both images rebuilt (`--build`). A CarDD damage model can be served this way once one is trained and exported; see [ADR 0004](../docs/adr/0004-hitl-damage-model-and-vocabulary.md).
- One process holds both models: about 2 GiB of memory on the CPU.

## Optional real M1 parts worker

`cmev-worker-parts` runs the trained part-segmentation model in place of the fixture parts producer. It is off by default and has its own image (`Dockerfile.parts`). That is the only image with the model libraries: it installs the package's `vision` extra (PyTorch and transformers 5.17 or later); the other services do not.

To turn it on, put the registry entry on the host and add one Compose file to the usual command:

```sh
# artifacts/models/<model_version>/ must hold config.json, the weight file and manifest.json, as
# written by pipelines/vision/train_segformer.py. model_version is in configs/models/parts.yaml;
# it is currently parts/0.8.0-b3-compound.
docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.parts.yml --profile lean up --build -d
```

That file is the whole switch. It starts the worker with the `lean` and `full` profiles and sets `CMEV_PARTS_PRODUCER=real` on every service, so the two cannot disagree. The switch is deliberately not read from `.env`: if it could be on while the worker was not started, the fixture producer would leave the parts topic with nobody to take it and new claims would wait in processing with no error. Use the same two `-f` files for `ps`, `logs` and `down`. To go back to fixtures, run `down` with both files first and then `up` with the base file alone. Dropping the second file from `up` is not enough: Compose leaves the worker container running, and it would then share the parts topic with the fixture producer and dead-letter the commands it receives.

`CMEV_MODEL_REGISTRY_HOST_PATH` changes the host folder (default `artifacts/models`); it is mounted read-only. `configs/` is copied into the images, so rebuild after changing `configs/models/parts.yaml`.

What the switch does:

| Service | With `docker-compose.parts.yml` added |
| --- | --- |
| `cmev-api` | Reports the configured parts versions at `/api/v1/version`. Seed claims still run the complete fixture pipeline at first start |
| Orchestrator (`cmev-worker-combined` or `cmev-orchestrator`) | Pins `parts_model`, `parts_config` and `taxonomy` from `configs/models/parts.yaml` on every parts command |
| Fixture producers | Stop consuming `cmev.cmd.parts-segment.v1`; the other five stages are unchanged |
| `cmev-worker-parts` | Loads the checkpoint once, then consumes the parts commands. Its rows carry `source_kind = real` and the pinned versions |

The parts worker refuses to start, with exit code 1 and the reason in its log, when:

- `CMEV_PARTS_PRODUCER` is not `real`, because it would share a consumer group with the fixture producer. This is what happens if the bare `parts` profile is started without the Compose file above;
- the registry entry, its `manifest.json` or its weight file is missing;
- the manifest names another model or taxonomy version than the configuration;
- the weight file's SHA-256 differs from the manifest;
- the checkpoint's class map is not the parts taxonomy's;
- the entry has no `preprocessing.json`, or that file records another input size, resize policy, normalisation or padding value than the worker uses;
- any tensor fails to load. This happens when the checkpoint was saved by another major version of transformers: version 5 renamed SegFormer's decode-head layers, and version 4 then leaves them at random values with only a warning.

It also dead-letters, with reason `model_version_unsupported`, any parts command pinned to other versions than the ones it loaded.

Limits of this mode:

- Only M1 is real. M2 to M8 are still fixtures and do not read the M1 mask or rows, so every assessment remains labelled a fixture.
- A photograph without stored bytes cannot be segmented. The seed claims use placeholders, so a seed that is processed or reassessed while the switch is on fails its parts stage with `artifact_missing`. That includes CLM-24020, which a fresh database leaves queued for the worker. Uploaded claims are unaffected.
- A claim processed under one setting reruns its parts stage under the other, because the pinned versions differ and reuse is refused.
- Checked on 2026-10-07 and 2026-10-08 with the `lean` profile, without `cmev-web`: the stack ran with PostgreSQL, Redpanda and MinIO, and a claim uploaded over the HTTP API with three photographs went through the parts worker to an assessment. See CONTEXT.md for what was measured. Not checked: the `full` profile, the browser, duplicate delivery, superseded revisions, outages, a GPU.

## Image selection and verification

- Python 3.12, Node 22, nginx 1.28 and PostgreSQL 16 images use minor/major tags. Record image digests when validating a release. Python dependency ranges are in `pyproject.toml`; npm dependencies use the application lockfile. A fully locked Python environment remains future work.
- Redpanda uses release tag `v26.2.2`. This is a single development broker with one CPU and a 1 GiB configured memory budget, not a measured whole-stack capacity target.
- The [official MinIO repository](https://github.com/minio/minio) is archived and its community distribution is source-only. `Dockerfile.objectstore` builds official release `RELEASE.2025-10-15T17-29-55Z` at exact commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, retaining its AGPL license. This avoids depending on unavailable historical image tags and preserves the ADR's S3 adapter choice. The archived dependency needs a separate supported-provider decision before a non-demo deployment. Its first source build downloads Go dependencies and can take several minutes.

Validated on 2026-09-24 against an isolated Docker project on port 18080: both profile configurations passed, images built, lean had six healthy long-running services and full had eight. Cost bootstrap completed successfully. Browser checks exercised PostgreSQL persistence, MinIO uploads, Kafka processing, M8 results, review corrections/reassessment, durable replay after a lost acknowledgement, conflicts and frozen reports. See [verification evidence](../docs/verification/model-independent-2026-09-24.md). These checks do not measure trained models, real price accuracy, capacity, broker outage recovery or coordinated backup/restore.
