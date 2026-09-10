# Verification layout

| Directory | Scope |
| --- | --- |
| `unit/` | Meaningful algorithm checks: mapping, exact money, ordered rules, approval eligibility |
| `contracts/` | Producer/consumer schema, revision compatibility, coordinates and invalid-input examples |
| `integration/` | Worker/coordinator/storage, idempotency, stale results and approval refresh |
| `e2e/` | Intake-to-review/export, original evidence, reconnect and conflicts |
| `evaluation/` | Reproducible metric/ablation runners; distinct from ordinary pass/fail tests |
| `fixtures/synthetic/` | Tiny generated safe examples with expected outcomes and explicit fixture provenance |

Follow the [evaluation plan](../docs/specs/evaluation_plan.md). Application/model test runners and dependencies remain unimplemented. The skill-sync utility has standard-library tests: run `python3 -m unittest discover -s tests/unit -p test_sync_skills.py -v`. See [agent workflows](../docs/agent-workflows.md).

Initial fixtures should cover: supported in-range repair; adequate coverage with no damage; unphotographed or blurred part; unresolved side/amount; absent/sparse/incompatible range; cost below/at/above bounds; possible missing repair; partial report; repeated image observations; worker crash/retry; and agreed versus duplicate/superseded final approvals.

Fixtures prove contracts and logic, not model accuracy. Keep real claim files, public dataset copies and large model outputs out of tracked fixtures.
