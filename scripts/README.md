# Project utilities

## Shared skill synchronisation

[sync_skills.py](sync_skills.py) mirrors the seven canonical .agents/skills directories into Claude Code's .claude/skills layout. It uses Python 3.9+ and the standard library; it preserves unrelated agent configuration and skills.

From the repository root:

```sh
python3 scripts/sync_skills.py --write
python3 scripts/sync_skills.py --check
python3 -m unittest discover -s tests/unit -p test_sync_skills.py -v
```

Use `python` instead of `python3` where appropriate for the local installation. Check mode is read-only and returns nonzero for drift. Write mode creates/updates managed copies; stale files and unsafe links require explicit reconciliation and are not deleted automatically.

See the [shared workflow guide](../docs/agent-workflows.md) and [CONTEXT.md](../CONTEXT.md) for invocation and team handoff.

## Dataset acquisition

[download_datasets.py](download_datasets.py) acquires the proposal's public,
gated and locally supplied datasets with provenance records, checksums and
explicit incomplete states. See [dataset download setup](../docs/dataset-downloads.md)
for credentials, licence/access evidence and local-source configuration.

```sh
python3 scripts/download_datasets.py --dry-run --include-reserve
python3 scripts/download_datasets.py
python3 -m unittest discover -s tests/unit -p test_download_datasets.py -v
```

## Future utilities

Add reproducible environment, dataset-manifest, schema-export, fixture and demonstration commands as their implementations become available. Keep learning/comparison logic in src/claim_cmev and offline workflows in pipelines. No application startup, training or deployment command exists yet.
