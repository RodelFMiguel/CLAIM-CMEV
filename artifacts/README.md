# Generated artifacts

Local, ignored artifact locations:

- `models/`: checkpoints and complete serving manifests.
- `cost_tables/`: immutable reference builds, membership and calibration reports.
- `evaluation/`: run outputs, metrics and plots.
- `exports/`: assessment exports and demonstration outputs.

Use versioned IDs and hashes; retain artifacts referenced by historical assessments. Do not overwrite a serving model or table in place. Keep a safe aggregate evaluation report under documentation when appropriate; generated artifacts remain outside Git.

Artifacts must identify data/split/config/code versions and real/synthetic/fixture provenance. Raw claim evidence lives behind the configured runtime storage adapter (local development may use ignored `runtime/`), not a public frontend directory. See [technical specification](../docs/specs/technical_specification.md).
