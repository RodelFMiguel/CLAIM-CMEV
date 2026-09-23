---
name: cmev-verify-workbench
description: >-
  Verify the CLAIM-CMEV upload, review overview, mark confirmation, evidence panel
  and browser PDF workflow, including persistence and stale revisions. Use for
  targeted UI acceptance or regression checks, not general visual redesign.
---

# Verify the CLAIM-CMEV workbench

Produce evidence that the requested workbench behaviours work, fail, or remain untested.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), the [UI specification](../../../docs/specs/ui_specification.md) for screens/states/interactions, [M9 review and report](../../../docs/specs/module-09-review-report.md) for module acceptance, and the relevant [platform API](../../../docs/specs/application_platform.md), [product requirements](../../../docs/specs/product_specification.md) and [evaluation plan](../../../docs/specs/evaluation_plan.md). For payload or asynchronous-state checks, use [data contracts](../../../docs/specs/data_contracts.md) and [integration contracts](../../../docs/specs/integration_contracts.md). Derive expected UI behaviour from these specifications; use proposal section 7 for scope context.

Use the current specifications below for implementation details and acceptance criteria. [Proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope; [ADR 0002](../../../docs/adr/0002-containerised-event-runtime.md) records the later runtime change. Read the sections relevant to the task, preserve proposed versus accepted status, and reconcile concrete conflicts with current user decisions. A specification is not evidence that its behaviour is implemented.

## Prepare an appropriate test session

Inspect current startup/test commands, running application and backend, supported browser tools, and available synthetic cases. Use the agent's available browser or existing automation runner; this skill requires no particular vendor tool.

Verify the environment is a local/disposable test environment before creating review events or simulating failures. Use clearly synthetic claims and record whether results came from fixtures or actual models. An unavailable app, checkpoint or browser tool is a reported prerequisite, not a passed UI check. Do not implement the entire workbench merely because verification was requested.

## Exercise the relevant scenarios

Select checks proportional to the change; a focused display fix does not require rerunning every system scenario.

- **Upload/overview:** upload photos and estimate pages; distinguish queued/failed/incomplete jobs, photo-only waiting for estimate, declaration gaps and completed checks. Show supported damage, confirmed adequate views without damage and unresolved/inadequate coverage separately.
- **Repair list:** show printed and effective/agreed amounts, four outcomes and reasons, individual photo/cost checks, pinned range/basis/support and synthetic status. Only confirmed exclusions are struck through and not checked; exclusion is not a pass.
- **Marks:** pending/confirmed/rejected, conflicting/unlinked proposals and candidate rows remain visible. Confirm/reject, correct row association, add a missed mark and enter a revised amount. Pending repricing cannot use the printed price; optional TrOCR suggestions remain separate from human-confirmed values.
- **Evidence:** navigate covering views and part/damage overlays, open originals in one tap, and verify row/mark highlights after coordinate transforms. Confirm physical identity/coverage with recorded provenance without altering predictions.
- **Possible additions:** incomplete/ambiguous declarations withhold suggestions; same-part confirmed exclusions suppress additions with an informational note, opposite-side exclusions do not. Added items need human-supplied operation/amount.
- **Review/recovery:** save and reload corrections; retry a timeout after server commit without duplicate actions. Reject stale conflicting writes visibly without silently overwriting newer decisions; inspect what local work is retained. Do not assume a full offline queue is implemented.
- **Reassessment:** changed part/side/operation/amount, mark decision, completeness or coverage creates new input/assessment revisions and recomputes affected findings. Explicitly reused artifacts retain lineage. Notes/dismissals stay attached to their findings and do not silently transfer.
- **Finalize/print:** reject pending/unlinked marks, unfinished/failed required processing and stale findings. Freeze the matching review and print from the browser. Inspect printed/effective amounts, exclusions, reasons, synthetic label/fixed basis, absent final approval and input/assessment/review/model/parser/config/cost versions. Remaining insufficient evidence stays visible; a later cost table cannot change a historical report.
- **Failure/accessibility:** evidence-load errors preserve context/original access. Check readable tablet controls, keyboard focus and non-colour status cues.

Claim list, cost-range detail, operations and audit views are described in the report, not additional working-screen commitments. Scripted approval import/refresh is S3 only. Full offline file synchronisation, settlement recommendations and live insurer approval are outside the prototype scope. For usability work, follow v2 section 13.5 and distinguish assisted effort from a measured manual-versus-assisted comparison.

## Report and hand off

For each exercised scenario record environment/run identity, input/assessment revision, steps, expected result, observed result and pass/fail/untested status. Store safe screenshots/logs in the designated ignored artifact location and report paths. Do not capture real claim material or credentials in committed reports.

Verification alone does not authorise unrelated fixes or shared-data cleanup. If fixes are requested, make the scoped change and rerun affected checks. After meaningful verification work requested for the team, update [CONTEXT.md](../../../CONTEXT.md) with results/limitations, distinguishing fixture checks from integrated model checks and preliminary usability from measured productivity gains.
