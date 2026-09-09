# Document worker

Owner: Lane 3; Lane 5 supplies job/storage integration.

Entrypoint for [M04](../../docs/specs/module-04-document-text-layout.md) and [M05](../../docs/specs/module-05-repair-line-items.md). Lease a revision-scoped report job, preserve original/page evidence, run extraction, and commit results with model/mapping/config versions.

A failed page or empty uncertain extraction must not become a confirmed empty repair list. Structured declarations bypass this worker through the backend contract. Keep training in `pipelines/documents/`. No worker entrypoint is implemented yet.
