# M09 - Surveyor workbench and review capture

Owner: Lane 5. Runtime: frontend with backend review persistence. Code: `apps/workbench/`, supported by `apps/api/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), sections 7-8, Module 9, and 14.5. Status: specified; implementation pending.

## Purpose and scope

Let the surveyor inspect model output and original evidence, correct the repair list, record decisions and agreed amounts, and save/export a reviewed assessment. Implement Screens 2-4. Provide static designs for Screens 1, 5, 6 and the audit view.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Processing status, vehicle summary, declared entries, findings/additions, reference details, evidence routes, assessment and review versions |
| Output | Idempotent review actions, declaration corrections/additions, agreed amounts, and a structured export request |
| Dependencies | [Platform API](application_platform.md), [shared records](data_contracts.md), M03/M05/M08 outputs |
| User | Primarily P1 surveyor; evidence also supports P3 investigator |

The frontend never writes storage or reference tables directly. Final approvals enter through a separate controlled importer; agreed amounts do not update cost history.

## Working screen specifications

| Screen | Required behaviour |
| --- | --- |
| 2: Vehicle overview | Highlight damaged parts; show adequate undamaged versus inadequate/unseen/unresolved coverage; list proposed damaged parts; support image-only preparation |
| 3: Repair list and comparison | One row per declared entry with part/side, operation, original amount, check result, reference bounds/support, edits and review actions |
| 4: Evidence viewer | Relevant photos with toggleable part/damage overlays, all covering views, one-tap original photo, report page with entry source spans highlighted |

Show `ok`, `unsupported`, `cost_outlier` and `insufficient_evidence` using the [product labels](product_specification.md). Display individual checks, missing-information reasons, currency, table date/version and synthetic provenance. Missing repairs occupy a separate candidate list; the user adds a cost explicitly if converting one into a declared entry.

Preserve original declaration beside edited and agreed values. Distinguish correcting a declared input (new assessment required) from recording a review decision/agreed amount (new review revision). Historical review must not accidentally edit the latest assessment.

## Review and reconnect behaviour

Accept/confirm, edit, add/remove, dismiss with reason, and record manual assessment or agreed amount. Selecting a dismissal reason saves the action without a second confirmation. Proposed reasons are hidden damage, ADAS/calibration, parts price change, inadequate photograph, system error and other (with note).

Store pending edits in durable browser storage scoped to claim, assessment, actor and client action ID. Retain them over connection loss/reload until server acknowledgement. Send the same idempotency key on retry. Show pending, retrying, saved, failed and conflict states. A conflict preserves local work and requires explicit resolution against the latest revision; do not silently overwrite.

A dismissed finding stays dismissed after reload on the same assessment. New evidence creates a new assessment and displays its review state clearly. Full offline photograph/file synchronisation is outside scope.

## Static design deliverables

Store mockup sources/exports in [docs/mockups](../mockups/README.md).

- Screen 1: claim reference, vehicle, photo count, queued/processing/ready/incomplete/failed status, finding count.
- Screen 5: declared amount, range, support, criteria, currency, table date/version and synthetic marker.
- Screen 6: claims, processing/review time, findings, dismissal rates and cost-reference coverage.
- Audit: input/assessment/review versions, originals/edits, evidence, model/table versions, actors/reasons and approval status.

The working review screen still supplies essential range details; the static Screen 5 does not excuse omitting them.

## Failure, accessibility, and usability

Use readable tablet layouts, large touch controls and non-colour status cues; check keyboard focus and bright/dim presentation. Avoid presenting worker failure as a ready claim with no findings. Evidence load failure shows retry and preserves the review context. Overlay failure must leave the original accessible.

Keep assessment/table versions and save status visible. Do not recommend settlement, rank workshops by suspicion, or escalate claims automatically. A manual explanation records human judgement without editing model evidence.

## Acceptance criteria

- Screens 2-4 use actual integrated model results in the final demonstration; fixture mode is explicitly labelled.
- Original photos open in one tap from a photographic finding; report highlights align.
- Edits/additions/removals/dismissals/agreed amounts persist through reload.
- Simulated lost connection and timeout-after-commit recover without lost/duplicate actions.
- Same-assessment dismissals remain saved; changed-input results are distinguishable.
- Structured export contains exact reviewed revisions and synthetic provenance.
- Preliminary usability records editing effort, review time, comprehension and dismissal/evidence actions.

## Implementation tasks

- [ ] Bootstrap frontend/API client and select durable pending-edit storage.
- [ ] Build vehicle overview and image-only awaiting-declaration state.
- [ ] Build review rows, individual checks, range details and candidate additions.
- [ ] Build overlay/original/photo navigation and report source highlighting.
- [ ] Implement edit semantics, reasons, agreed amounts and exact money display.
- [ ] Implement durable pending actions, idempotent retry and revision conflicts.
- [ ] Implement export retrieval and visible version/provenance indicators.
- [ ] Create/review four static mockups.
- [ ] Validate tablet, keyboard, evidence failure and reconnect scenarios.
- [ ] Run usability and full-pipeline demonstration checks.

## Open decisions

Final dismissal list, tablet target, visual design and durable edit-storage policy. Production authentication and offline file sync remain deferred.
