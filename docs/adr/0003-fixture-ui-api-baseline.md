# ADR 0003 - Public entry, login and fixture-backed application baseline

Status: accepted implementation scope at the user's direction, 2026-09-22. Runtime validation is recorded separately in CONTEXT.md.

## Decision

Implement a public information page, login and working claim-queue dashboard, using the supplied dashboard image and Gamma site as visual/content references. This explicitly extends the earlier described-only claim list. Retain the review, intake and print workflow in M9. The current demonstration uses synthetic claims and clearly labelled fixture processing, not trained model outputs or real-price validation.

All new application code lives under `src`: React/TypeScript/Vite in `src/workbench`, FastAPI and the fixture worker in `src/claim_cmev`. The former `apps/api` and `apps/workbench` directories remain historical scaffold locations. Packaging and container manifests can live at the repository root and under `infra`.

Keep the ADR 0002 container direction: web, API, fixture combined worker, Kafka-compatible broker, PostgreSQL and S3-compatible object storage in the lean deployment. The browser calls only the same-origin API. Neural inference is replaced by explicit fixtures; real intake, persistence, authentication and revision operations remain executable. This baseline does not claim all module handlers or the full container profile are implemented. The lean fixture containers consume no model or cost-table files and therefore omit empty host bind mounts. Real model/cost consumers must use the ADR 0002 read-only mounts and version checks when added.

A local SQLite/file-storage and local-outbox worker configuration may be used only for development tests when containers are unavailable. It is not evidence that Kafka/PostgreSQL/object-store integration has passed. Container readiness, image builds and end-to-end execution must be reported according to what was actually exercised.

## Interface and data rules

Provide cookie-based demo authentication and authenticated application routes. Demo credentials are for synthetic local demonstrations and are documented/configurable. Original evidence, printed amounts, effective surveyor values and final approvals remain separate. Money uses decimal strings with SGD and the fixed cost basis. Fixture provenance is returned by the API and visible in the review/report.

Processing submissions return queued state; the separate worker supplies versioned mock results. Failed or unavailable processing must remain visible. Stale edits, idempotency-key payload conflicts, pending marks and unfinished reassessment must not silently produce a finalized clean report.

## Visual assets

The supplied reference site is https://claim-cmev-m1mw11h.gamma.site/. Its information hierarchy informs the public page; the project specifications govern behaviour where its prose differs.

`src/workbench/public/images/damaged-car.png` is an AI-generated decorative image, produced with the built-in image-generation tool. It is not claim evidence. Prompt: photorealistic wide 16:9 silver unbranded compact sedan with moderate front-left bumper/fender collision damage in a bright neutral inspection studio, car on the right, pale empty space on the left, no people, readable plate, logos or text; gentle contrast for a light website background.

## Acceptance

Build/type-check the frontend; exercise backend authentication, ownership, persisted mutations, fixture provenance and revision gates; verify the public page, login, queue/search, review and responsive layout in a browser. Record any untested container integrations rather than inferring them from source or Compose validation.
