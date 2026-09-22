# Design deliverables for the described-only views

Status: no mockups exist yet. Owner: Lane 5 with the report authors. Source: [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) sections 3.3, 7.1 to 7.5 and 12.1.

## What belongs here and what does not

Version 2 builds three surfaces: the upload page, the review overview page with its evidence panel, and the print view. A processing status page is required as well, because processing is asynchronous. All four are specified as wireframes, fields, states, interactions and stories in the [user interface specification](../specs/ui_specification.md), and they are implemented under `apps/workbench/` as the `cmev-web` container.

**Static pictures do not satisfy the acceptance criteria of the built screens.** Those criteria are listed in [section 11 of the user interface specification](../specs/ui_specification.md) and in [M9](../specs/module-09-review-report.md). Do not create a mockup of a built screen and record it as progress on that screen.

This folder holds the four views that proposal v2 section 7.5 describes but does not build. They are report and design deliverables only. They are not additional implementation commitments inside the 50-person-day budget.

## The four described-only views

| View | Purpose | Fields the design must cover |
| --- | --- | --- |
| Claim list | Find a claim and see where it is | Claim reference, vehicle, photograph and page counts, input and assessment revisions, status of queued, processing, ready, incomplete or failed, finding counts by result, pending mark count, last reviewer and time |
| Cost-range detail | Explain one reference range | Cost key of part, operation, vehicle class and currency, lower and upper bounds as decimal money, independent base-case count, nominal and observed interval coverage, table version and build date, fixed cost basis, synthetic marker, and the reason when no range exists |
| Operations dashboard | Watch throughput and review activity | Claims by state, stage failure and retry counts, time from upload to ready, time from ready to finalize, edits per claim, mark confirmation and correction counts, dismissal reasons, and the share of entries withheld by reason |
| Audit view | Reconstruct one assessment | Input, assessment and review revisions, original extracted values beside every correction, each mark decision with actor and time, dismissal reasons, model, parser, rule and cost-table versions, evidence identifiers, synthetic provenance and approval status |

Two rules constrain these designs:

- The cost-range detail view is not built, so the line-item row and the evidence panel must still show bounds, independent support count, table version and cost basis inline. The design here does not excuse omitting them from the built screen.
- The audit view is not built, so the print footer and the stored record carry the same facts. The design here does not replace them.

## How to store a design

- Keep the editable source and an exported preview together, named after the view, for example `claim-list.excalidraw` beside `claim-list.png`.
- Mark every value in a design as synthetic and illustrative. Use the same worked example as the user interface specification so that the report reads consistently: claim `CLM-2026-004821`, a Toyota Corolla Altis 2019, class `compact-sedan`, SGD.
- Money is a decimal string with its currency and cost basis. A missing value carries a reason, never a zero.
- Do not show a language model anywhere in a decision path. Findings come from deterministic rules. The optional stretch service `cmev-explainer` is off by default, changes no result, and would be labelled if it were used.
- Link a finished design from the table above and from the report section that discusses it.

## Tasks

- [ ] Agree whether these four views are produced as drawings or as annotated field lists in the report.
- [ ] Draw or write up the claim list, cost-range detail, operations dashboard and audit view.
- [ ] Check each design against the field list above and against the [data contracts](../specs/data_contracts.md).
- [ ] Record in the report that these views are described only and were not implemented or evaluated.
