---
name: cmev-verify-workbench
description: >-
  Verify CLAIM-CMEV Screens 2-4, evidence navigation, review persistence and
  reconnect/conflict behaviour against the workbench specification. Use for
  targeted UI acceptance or regression checks, not general visual redesign.
---

# Verify the CLAIM-CMEV workbench

Produce evidence that the requested workbench behaviours work, fail, or remain untested.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), [M09](../../../docs/specs/module-09-surveyor-workbench.md), the [platform API](../../../docs/specs/application_platform.md) and relevant [product requirements](../../../docs/specs/product_specification.md).

## Prepare an appropriate test session

Inspect current startup/test commands, running application and backend, supported browser tools, and available synthetic cases. Use the agent's available browser or existing automation runner; this skill requires no particular vendor tool.

Verify the environment is a local/disposable test environment before creating review events or simulating failures. Use clearly synthetic claims and record whether results came from fixtures or actual models. An unavailable app, checkpoint or browser tool is a reported prerequisite, not a passed UI check. Do not implement the entire workbench merely because verification was requested.

## Exercise the relevant scenarios

Select checks proportional to the change; a focused display fix does not require rerunning every system scenario.

- **Vehicle overview:** damaged parts, adequate coverage without detected damage, unseen/poor/unresolved coverage, and photo-only awaiting-declaration state are distinct.
- **Repair list:** show original declaration, edited/agreed values, individual checks and four overall outcomes with reasons. Display range criteria/count/date/version/currency and synthetic status. Proposed additions have no invented declared price.
- **Evidence:** navigate covering views, toggle part/damage overlays, open the original photo in one tap, and verify report page/highlight alignment.
- **Review:** confirm/edit/add/remove, dismiss by reason without an extra confirmation, save agreed amounts, reload and verify persisted state.
- **Recovery:** simulate offline connection or a timeout after server commit; pending edits survive and replay once. A stale/concurrent review produces a visible conflict retaining local work.
- **Revisions/export:** corrected declarations/new evidence create a distinguishable assessment; same-assessment dismissals stay saved. Export identifies the exact input/assessment/review and cost/model versions.
- **Failure/accessibility:** processing failure differs from a clean assessment; evidence-load errors preserve context/original access. Check tablet controls, keyboard focus and non-colour status cues.

Queue, standalone cost detail, operations and audit screens are static design deliverables. Do not count static mockups as working Screens 2-4. Full offline file synchronisation, settlement recommendations and live insurer approval are outside the prototype scope.

## Report and hand off

For each exercised scenario record environment/run identity, input/assessment revision, steps, expected result, observed result and pass/fail/untested status. Store safe screenshots/logs in the designated ignored artifact location and report paths. Do not capture real claim material or credentials in committed reports.

Verification alone does not authorise unrelated fixes or shared-data cleanup. If fixes are requested, make the scoped change and rerun affected checks. After meaningful verification work requested for the team, update [CONTEXT.md](../../../CONTEXT.md) with results/limitations, distinguishing fixture checks from integrated model checks and preliminary usability from measured productivity gains.
