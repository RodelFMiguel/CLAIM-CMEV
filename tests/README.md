# Verification layout

| Directory | Scope |
| --- | --- |
| `unit/` | Meaningful algorithm checks: mapping, exact money, ordered rules, approval eligibility |
| `contracts/` | Producer/consumer schema, revision compatibility, coordinates and invalid-input examples |
| `integration/` | Worker/coordinator/storage, idempotency, stale results and approval refresh |
| `e2e/` | Intake-to-review/export, original evidence, reconnect and conflicts |
| `evaluation/` | Reproducible metric/ablation runners; distinct from ordinary pass/fail tests |
| `fixtures/synthetic/` | Tiny generated safe examples with expected outcomes and explicit fixture provenance |

Follow the [evaluation plan](../docs/specs/evaluation_plan.md). Run the application suite from the repository root with .venv/bin/python -m pytest -q after installing the dev dependencies. Model evaluation remains separate. The skill-sync utility has standard-library tests: run `python3 -m unittest discover -s tests/unit -p test_sync_skills.py -v`. See [agent workflows](../docs/agent-workflows.md).

Initial fixtures should cover: supported in-range repair; adequate coverage with no damage; unphotographed or blurred part; unresolved side/amount; absent/sparse/incompatible range; cost below/at/above bounds; possible missing repair; partial report; repeated image observations; worker crash/retry; and agreed versus duplicate/superseded final approvals.

Fixtures prove contracts and logic, not model accuracy. Keep real claim files, public dataset copies and large model outputs out of tracked fixtures.

Dataset acquisition utility checks: `python3 -m unittest discover -s tests/unit -p test_download_datasets.py -v`. These use synthetic archives and mocked HTTP/provider responses; they do not establish real dataset completeness or model quality.

## Browser checks

Start the API, worker and workbench (or a Compose profile), then install the browser with npx playwright install chromium from src/workbench. From the repository root, using Node 22:

```sh
node tests/e2e/baseline.mjs
node tests/e2e/review-controls.mjs
node tests/e2e/print-report.mjs
```

The default URL is http://127.0.0.1:5173; set CMEV_E2E_URL for Docker. CMEV_DEMO_EMAIL and CMEV_DEMO_PASSWORD override demonstration credentials. Linux needs the Playwright browser system libraries. A nonstandard browser installation can use PLAYWRIGHT_BROWSERS_PATH and LD_LIBRARY_PATH.

Use disposable synthetic state. Baseline uploads evidence and finalizes seed CLM-24019, so rerunning it requires a fresh test database/project. Review-controls exercises CLM-24020; reruns verify previously accepted/dismissed scope rather than duplicating those actions. Print-report exports the frozen CLM-24019 report after baseline. These scripts change demonstration records; do not point them at real claims. Screenshots/PDFs go to ignored artifacts/evaluation directories. See the dated verification record for the exact exercised boundaries.
