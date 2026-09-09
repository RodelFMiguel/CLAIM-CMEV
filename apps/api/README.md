# Backend application

Owner: Lane 5; Lane 4 integrates M07-M08.

Reserved for the HTTP/configuration entrypoint. Implement intake, revisions, jobs, evidence access, assessments, review actions, structured exports and controlled approval imports using [the platform specification](../../docs/specs/application_platform.md).

Keep domain logic in `src/claim_cmev/`; this application wires contracts, orchestration and persistence into routes. The proposed FastAPI/Pydantic stack is not installed. Add application/dependency manifests and a documented startup command after the stack decision.

Initial work: bootstrap entrypoint/configuration, implement contract validation and storage migrations, then connect labelled synthetic fixtures before model integration.
