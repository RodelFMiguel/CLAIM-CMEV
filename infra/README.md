# Deployment and database infrastructure

Owner: Lane 5.

`compose/` reserves the private local demonstration deployment: backend, workbench, image worker, document worker and database, with manually invoked offline jobs. `migrations/` reserves forward, reviewed database schema changes.

Follow the [deployment specification](../docs/specs/technical_specification.md). No Dockerfiles, Compose files or migrations exist yet, so there is no deploy command. After stack confirmation, document bootstrap order, locked dependencies, volume paths, CPU/GPU compatibility, readiness, secret configuration and restore procedures.

Expose only required UI/API ports; keep database/workers private and serve evidence through authorised API routes. Record demonstration hardware and measured latency before setting limits.
