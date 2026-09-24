# M9 - Review overview and report

> Baseline implementation update (2026-09-22): the user requested a public information page, login and working claim dashboard, plus real FastAPI endpoints with explicitly mocked processing. New code lives in `src/workbench/` and `src/claim_cmev/api/`, superseding the earlier `apps/` locations for this implementation. See [ADR 0003](../adr/0003-fixture-ui-api-baseline.md) for the scoped extension; the specifications below remain the full target, not a claim that every requirement is implemented.

Owner: Lane 5. Runtime containers: `cmev-web` (React, TypeScript, Vite, served by nginx) and `cmev-api` (FastAPI, **no neural weights**). Current baseline code is under `src/workbench/` and `src/claim_cmev/` as recorded in ADR 0003. Source: [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) sections 6, 7.1 to 7.6, 8.4, 9.2, 9.3, 9.6, 10 (M9), 13.3 and 13.5. Status: the fixture-backed slice is implemented and verified; the remaining acceptance criteria describe the full target and are not implied complete.

## Purpose and scope

M9 is the only part of the system a person touches. One surveyor uploads a claim, works through one overview page, corrects what the machine got wrong, and prints the reviewed report to PDF from their browser.

This document specifies **what the module must do and when it is correct**. Wireframes, field tables, component structure and user stories live in the [UI specification](ui_specification.md) and are not repeated here.

Built deliverables: the upload page, the review overview page with its four sections, the evidence panel, the finalize gate and the print view. Described but not built: claim list, cost-range detail, operations dashboard and audit view, covered in [section 7.5](#views-described-but-not-built).

Requirements covered, from the [product specification](product_specification.md): FR-01 to FR-06, FR-27, FR-34 to FR-47, FR-49, FR-50, NFR-04, NFR-14. Safety invariants: SI-01, SI-05, SI-06, SI-07, SI-09, SI-12.

### No language model in the decision path

No large language model takes part in any decision. M8 combines the image branch, the document branch and the cost ranges with deterministic rules only, because findings must be reproducible, version pinned and immutable. An optional stretch service `cmev-explainer` may turn an already computed finding into a readable sentence. It is off by default, it never changes a result or a state, and its use is recorded in provenance.

For this module that means every sentence on screen and in the printed PDF is rendered from a reason code owned by [M8](module-08-consolidation-checks.md#reason-codes). The front end maps a code to display text. It never composes an explanation of its own, and the printed report can therefore be reproduced exactly from the stored revision.

## Inputs, outputs and dependencies

| Direction | Contract |
| --- | --- |
| Input | Uploaded photographs, estimate pages and vehicle details from the surveyor; claim and job state; assessments and findings from [M8](module-08-consolidation-checks.md); marks from [M6](module-06-pen-mark-recognition.md); the pinned range detail from [M7](module-07-reference-cost-ranges.md) |
| Output | Input revisions, review actions, review revisions, a finalized review revision and a printable report view |
| Consumers | The surveyor, and the claim handler who receives the PDF |
| Dependencies | [Application platform](application_platform.md), [integration contracts](integration_contracts.md), [data contracts](data_contracts.md), [UI specification](ui_specification.md) |

`cmev-web` talks only to `cmev-api`. It never reaches Kafka, the database or the object store directly. Overlays and highlighted page images are rendered by the server and delivered as images, so the browser draws nothing.

## Container and transport

| Item | `cmev-web` | `cmev-api` |
| --- | --- | --- |
| Profiles | `lean` and `full` | `lean` and `full` |
| Kafka role | none | producer and consumer |
| Consumer group | none | `cmev-api` |
| Consumes | none | `cmev.evt.assessment-ready.v1`, `cmev.evt.job-failed.v1` |
| Produces | none | `cmev.cmd.consolidate.v1` for a reassessment, plus the intake commands defined in [integration contracts](integration_contracts.md) |
| Neural weights | none | **none** |

On `cmev.evt.assessment-ready.v1`, `cmev-api` advances `claim.current_assessment_revision` only when the event's `input_revision` equals the claim's current input revision and `superseded` is false. A superseded assessment is stored and readable, never shown as current and never printed.

On `cmev.evt.job-failed.v1`, `cmev-api` sets the claim to `incomplete` or `failed` with the reason. A failure is never rendered as a finished assessment with no findings.

**Duplicate delivery.** Both consumers are idempotent. `assessment-ready` is applied by `dedup_key`; a repeat is a no-op because the assessment row already exists. `job-failed` is applied by `job_key`; a repeat does not add a second failure record.

**Superseded revisions.** A `cmev.cmd.consolidate.v1` that `cmev-api` publishes always carries the current `input_revision` at the moment of publication. If a further edit lands first, the earlier command becomes superseded and M8 handles it as described in its own specification.

## Module responsibilities

### Upload page

Fields: claim reference, vehicle make, model and year, currency, photographs as JPEG or PNG, estimate pages as JPEG, PNG or PDF. Model year is metadata only and never enters a cost lookup.

- Cost comparison supports SGD only. Another currency is accepted, stays visible and receives no cost check, with the reason shown.
- Claim state is one of `queued`, `processing`, `ready_for_review`, `incomplete` or `failed`. These five states are visually distinct and not by colour alone.
- A rejected file appears in the list with its reason. It is never a silent absence.
- Photographs with no estimate are accepted, and pages with no photographs are accepted.
- Adding files later creates a new input revision. Earlier revisions and their assessments stay readable.

### Review overview page

The main working screen, with four sections, matching proposal section 7.2.

| Section | Responsibility |
| --- | --- |
| Damage summary | One row per resolved physical part and side, with unresolved observations listed separately. Each row expands to its source observations. Coverage state and its reason are always visible |
| Line items | One row per estimate line item, with the columns listed in the [UI specification](ui_specification.md). Printed price, pen marks with state, effective price, photo check, cost check, overall result and actions |
| Possible additions | Eligible observations with no matching row. Accept, edit or dismiss with a reason. An accepted addition has no invented price or operation |
| Finalize | The gate described below, plus the count of anything still blocking it |

Display rules the module must enforce:

- `insufficient_evidence` is informational and must look different from a discrepancy flag. It is not a failure and not a pass.
- A confirmed exclusion is a **row state**, shown struck through and not checked. It is never labelled `ok`.
- The original extracted value stays visible beside every edit.
- Every displayed range carries the synthetic marker and the fixed cost basis.
- Model, parser, configuration and cost-table versions are visible on the page, not only in the print view.
- Fixture output is labelled as fixture wherever it appears.
- Declaration completeness and any unreadable or unlinked region are shown on the page.

### Evidence panel

Opens beside the overview when a row is selected. It shows the covering photographs with part and damage overlays and a one-tap link to the original photograph, the estimate page image with the row box and any mark boxes highlighted, and the controls to correct a row or mark association and to confirm visible part identity or view coverage.

A human confirmation is recorded beside the model output with actor, source and revision. It never rewrites the model output.

## Review lifecycle

1. The surveyor opens a claim that is `ready_for_review`. `cmev-api` returns the current assessment, its findings, the marks, the line items, the damage summary and the current `review_revision`.
2. The surveyor performs an action. Each action carries an `Idempotency-Key` and the `expected_review_revision` the client holds.
3. `cmev-api` classifies the action as **decision-changing** or **review-only** using `classify_review_action`.
4. A review-only action, dismissing a finding with a reason or adding a note, writes a `ReviewEvent` row against the current assessment and increments `review_revision`. No recomputation happens.
5. A decision-changing action writes the corrected value with its original value and provenance, creates a new `input_revision`, and publishes the recompute work. For the rows in the [M8 reassessment table](module-08-consolidation-checks.md#reassessment-and-lineage) whose only rerun is M8, it publishes `cmev.cmd.consolidate.v1` directly with `reuse_lineage`, so no neural model reruns for a price correction.
6. The claim returns to `processing` until `cmev.evt.assessment-ready.v1` arrives for the new revision. The overview shows that recomputation is in progress and does not present stale findings as current.
7. When the new assessment arrives, the page reloads the findings and starts a fresh `review_revision` against that assessment.
8. The surveyor finalizes. The gate is evaluated, the review revision is frozen and the print view opens.

### Action classification

The action types are the `ReviewEvent` list in [data contracts](data_contracts.md) section 10. `classify_review_action` must return exactly this split, and a test asserts it for every type, because a misclassified action either skips a needed reassessment or forces a pointless one.

| Decision-changing, new input and assessment revision | Review-only, new review revision only |
| --- | --- |
| `confirm_mark`, `reject_mark`, `add_mark`, `correct_mark_link`, `enter_amount` | `dismiss_finding` |
| `correct_line_item` | `dismiss_addition` |
| `confirm_identity`, `confirm_coverage` | `add_note` |
| `confirm_declaration_completeness`, `accept_addition` | |

### Idempotent save

- Every write endpoint requires an `Idempotency-Key` header and an `expected_review_revision` body field.
- A repeat with the same key returns the original result, the same action identifier and the same review revision, with `replayed: true`. It never creates a second row.
- A request whose `expected_review_revision` is behind the server is rejected with `409` and the current revision. The client reloads and re-applies. A stale write never silently overwrites a newer decision.
- The client keeps unsent actions in durable browser storage scoped to claim, assessment, actor and client action identifier, and retries them with the same key after a reconnection.
- Pending, retrying, saved, failed and conflict are distinct visible states.

### Review revision model

| Object | Rule |
| --- | --- |
| `input_revision` | Increments on any decision-changing edit or new file. Immutable once created |
| `assessment_revision` | One per completed M8 run. Immutable. Pins its full version set and cost-table version |
| `review_revision` | Increments on each saved review action. Belongs to exactly one assessment revision |
| Finalized review | One frozen `review_revision` marked final, with its assessment revision and the actor and time |

A dismissal belongs to the finding it was made against. It carries into a new assessment only when the finding identity and `content_hash` are unchanged, and the carry-forward is its own recorded event. Otherwise the finding is active again. That rule is owned by [M8](module-08-consolidation-checks.md#reassessment-and-lineage); M9 displays it, showing a carried dismissal as carried rather than as fresh.

## Finalize preconditions

`GET /claims/{claim_id}/assessments/{r}/finalize-preconditions` returns each condition separately so the page can name exactly what is blocking. `POST /finalize` returns `412` with the failing list when any condition fails. The six conditions and their reason codes are owned by [application platform](application_platform.md) section 9.1.

| # | Condition | Reason code | Source |
| --- | --- | --- | --- |
| F1 | No required job for the current input revision is unfinished or failed | `processing_incomplete` | Job state |
| F2 | Every decision-changing edit has produced an assessment that is `ready`, so the current assessment's `input_revision` equals the claim's current `input_revision` | `reassessment_pending` | Revision comparison |
| F3 | No pen mark is `pending` | `marks_pending` | M6 mark states |
| F4 | No pen mark is unlinked. Every mark has a resolved entry association or has been rejected | `marks_unlinked` | M6 `entry_id` and `state` |
| F5 | Every confirmed price change carries a typed `confirmed_amount` with its currency and cost basis | `amount_missing` | M6 validation |
| F6 | The review revision presented equals the current review revision | `stale_review_revision` | Idempotency check |

Two further conditions are **proposed** by this document and are not yet in the platform list. They need a team decision before implementation, and they are repeated in [open decisions](#open-decisions):

| # | Proposed condition | Reason code | Why |
| --- | --- | --- | --- |
| P1 | The current assessment is not `superseded` | `assessment_superseded` | Printing a superseded assessment would print findings that a later edit already invalidated |
| P2 | Declaration completeness is `complete`, or the surveyor has recorded a confirmation, including an explicit confirmation of an empty scope | `completeness_unconfirmed` | Without it, a partly read estimate can be finalized while possible additions were never evaluated |

What does **not** block finalize:

- `insufficient_evidence` results. They stay in the report with their reasons. Finalization never converts one into a pass. This is the central rule of the gate: the system must not pressure anyone into clearing a row just to print.
- Undismissed `unsupported` or `cost_outlier` findings. The surveyor may print a report that still carries them.
- Possible additions left undismissed. An accepted addition must carry an operation supplied by the surveyor; its amount may be absent with a reason.

Finalize is idempotent. Finalizing an already finalized review revision returns the same frozen revision.

## Print view and report

The print view renders one frozen review revision and its assessment. It never mixes new values with stale findings, and it is the browser's print target for PDF export. The stored review revision is the authoritative record; the PDF is a rendering of it.

Contents, from proposal section 7.4:

1. Claim and vehicle details.
2. The damage summary.
3. The final line items with printed and effective prices, both shown.
4. Confirmed excluded items and the full record of mark decisions.
5. Possible additions accepted by the surveyor.
6. Findings, unresolved evidence, declaration gaps and dismissal reasons.
7. A clear synthetic-cost label and the fixed cost basis. Final approval is shown as **not recorded** unless an approval was actually imported in stretch S3.
8. The footer.

Print footer, required fields, all on every printed page or at least on the last page:

| Footer field | Source |
| --- | --- |
| `input_revision` | Claim input |
| `assessment_revision` | Assessment header |
| `review_revision` (frozen) | Finalization record |
| Model versions for M1, M2 and the M6 detector | Assessment version set |
| OCR version and parser or rule configuration version | Assessment version set |
| Rule configuration version for M8 | Assessment header |
| `cost_table_version` | Assessment header |
| Fixed cost basis identifier | Assessment header |
| Synthetic-cost statement | Fixed text |
| Fixture marker, when any input was a fixture | Provenance |
| Printed-at timestamp and actor | Print request |

A report printed from an older revision reprints identically later, even after a new cost table is published, because every version is pinned in the assessment.

## Views described but not built

Claim list, cost-range detail, operations dashboard and audit view are described in the final report and are **not** implementation commitments inside the 50 person-day plan. The working overview still shows the essential range detail, so the unbuilt cost-range view is not an excuse to omit bounds, support count, table version or the synthetic marker from the overview or the evidence panel.

## Python adapter entry points

The review rules are a pure library in `src/claim_cmev/review/`, imported by `apps/api/`. The same functions are the unit-test surface, so the tests and the service cannot drift apart.

```python
# src/claim_cmev/review/actions.py  (pure)

def classify_review_action(action: ReviewAction) -> ActionClass:
    """decision_changing or review_only, plus which branches must rerun."""


def apply_review_action(
    action: ReviewAction,            # kind, actor, payload, idempotency_key, expected_review_revision
    *,
    state: ReviewState,              # claim, current assessment, marks, line items, review revision
    now: datetime,
) -> ReviewOutcome:                  # new rows, new revisions, replayed flag, conflict, errors
    ...


# src/claim_cmev/review/finalize.py  (pure)

def evaluate_finalize_preconditions(
    state: ReviewState,
) -> FinalizeGate:                   # allowed: bool, blockers: list[Blocker] with code and entry_id
    ...


# src/claim_cmev/review/report.py  (pure)

def build_report_view(
    assessment: Assessment,
    review: FrozenReviewRevision,
    *,
    display: DisplayCatalogue,       # reason code to display text, owned by M8
) -> ReportView:                     # sections, footer fields, synthetic and fixture markers
    ...
```

`build_report_view` takes an already frozen revision. It cannot read the live database, which is what guarantees that a printed report is a rendering of a stored revision and nothing else.

## Database rows

| Table | Contents |
| --- | --- |
| `claim` | `claim_id`, external reference, vehicle make, model, year, resolved vehicle class or `unknown`, currency, state, `current_input_revision`, `current_assessment_revision` |
| `claim_input_revision` | `input_revision`, file identifiers, prior revision, corrected-input references, created by and at |
| `review_revision` | `review_revision`, `assessment_revision`, created at, finalized flag |
| `review_event` | The `ReviewEvent` record: action identifier, `assessment_revision`, `review_revision`, actor, time, action kind, reason, original value, new value, `expected_review_revision`, idempotency key |
| `finalization` | `claim_id`, frozen `review_revision`, `assessment_revision`, actor, time |
| `usability_event` | **Proposed**, for section 13.5 only: session, case identifier, event kind, elapsed milliseconds. No personal data |

## Configuration

| Key | File | Proposed default |
| --- | --- | --- |
| `review.dismissal_reasons` | `configs/pipeline/review.yaml` | hidden damage, ADAS or specialist procedure, parts price change, inadequate photograph, system error, other with a required note |
| `review.idempotency_ttl_hours` | `configs/pipeline/review.yaml` | `72` |
| `review.pending_edit_storage` | front end | IndexedDB, scoped to claim, assessment, actor and client action identifier |
| `ui.tablet_min_width_px` | front end | `768` |
| `ui.usability_telemetry_enabled` | `configs/pipeline/review.yaml` | `false`, enabled only for the section 13.5 sessions |
| `explainer.enabled` | `configs/pipeline/review.yaml` | `false` |

## Failure and uncertainty handling

| Situation | Behaviour |
| --- | --- |
| A worker container dies mid-claim | The claim shows `incomplete` with the failed task named. It never shows a finished assessment with no findings |
| One branch failed, the other succeeded | Show the branch that succeeded, show the failed branch as failed, and block finalize under F1 |
| Photographs uploaded with no estimate | Show the damage summary; the line-item section reads "waiting for the estimate" |
| Kafka is unavailable | `cmev-api` rejects new work with a clear state rather than accepting it silently. Accepted claims resume when the broker returns |
| An evidence image fails to load | Offer a retry and keep the review context. Never lose unsaved edits |
| An overlay fails to render | The original photograph stays reachable |
| A connection drops mid-save | The action stays in durable storage and retries with the same idempotency key |
| A conflicting concurrent edit | `409` with the current revision. The local work is preserved and the surveyor resolves it explicitly |
| A stale or unfinished reassessment at finalize | Finalize is rejected under F1 or F2 |

The module never recommends a settlement, never ranks workshops by suspicion and never escalates a claim.

## Acceptance criteria

| Criterion | Evidence |
| --- | --- |
| An unseen case completes upload, both branches, consolidation, corrections and PDF export using real model output | The end-to-end demonstration, with the amount of human assistance recorded |
| Evidence links resolve to the correct original photograph, mask, page and mark box, with coordinate transforms preserved | Spot checks across the controlled cases |
| A killed container leaves a visible incomplete claim, never a clean one | Kill and restart during processing |
| A repeated save returns the same action and revision, creating no second row | Replay test with the same idempotency key |
| A stale conflicting write is rejected for reload | Two-client test |
| A pending mark blocks finalize, and no printed price fallback occurs for a pending repricing | Gate tests F3 and F5 |
| An unlinked mark blocks finalize | Gate test F4 |
| `insufficient_evidence` results appear in the printed PDF with their reasons and are not shown as passes | Print inspection on a case that has them |
| A decision-changing correction produces a new assessment, and the original findings stay readable | Revision test |
| A price correction records no neural rerun in the lineage | Lineage inspection |
| Reprinting an old revision after a new cost table gives an identical report | Version pinning test |
| Fixture mode is labelled in the interface and in the PDF | Fixture run |
| The four claim states plus failure are distinguishable in greyscale and by label text | NFR-04 check |

### Usability evaluation hooks

From proposal section 13.5. Two members from outside Lane 5 review three cases each.

| Measure | How it is captured |
| --- | --- |
| Time per case | Session start and finalize timestamps |
| Number of edits | `ReviewEvent` rows by kind |
| Mark-link corrections | `correct_mark_link` action count |
| Amount entries | `confirm_mark` on a price change and `enter_amount` counts |
| Finding comprehension | A short post-case prompt per finding type, recorded outside the system |
| Evidence access | Evidence-panel open events and original-photograph taps |

If the report claims reduced effort, it must include comparable manual cases with the manual and assisted order alternated. Without that baseline, only assisted-review effort is reported. Team familiarity and the small sample are disclosed. No deployment saving is inferred from this pilot.

## Worked example

Claim `CLM-2026-0142`. The surveyor uploads six photographs and two estimate pages, enters make, model, year 2019 and currency SGD. State goes `queued`, then `processing`. Input revision 1.

Both branches finish. `cmev.evt.assessment-ready.v1` arrives with `assessment_revision 1`, so the state becomes `ready_for_review`.

The overview shows four line items. Item `e-003` has a pending exclusion and item `e-005` has an unlinked price change, so both read "More information needed". Item `e-007`, left fender, reads "Take more pictures of this part". A possible addition proposes a bonnet dent.

The surveyor confirms the exclusion on `e-003`. That is decision-changing, so input revision 2 is created, `cmev.cmd.consolidate.v1` is published with the image, OCR and line-item artifacts reused by lineage, and assessment 2 arrives. They then correct the link of the price-change mark to `e-005` and type `980.00`, giving input revision 3 and assessment 3. They accept the bonnet addition and supply the operation `repair`, leaving the amount empty with the reason "workshop to quote", giving input revision 4 and assessment 4.

Finalize passes F1 to F6. If the team adopts proposed condition P2, it is still blocked because the second page parsed as `partial`. The surveyor opens the evidence panel, reads the page image, corrects the two unreadable rows and confirms the estimate is complete. Input revision 5, assessment 5. The gate now passes.

They finalize. Review revision 12 is frozen against assessment 5. The print view opens with a footer reading `input 5 / assessment 5 / review 12`, parts `segformer-parts 0.3.1`, damage `segformer-damage 0.2.4`, penmarks `frcnn-penmarks 0.2.0`, OCR `paddleocr 2.7.0`, parser `parser-0.4.2`, rules `rc-0.1.0`, cost table `2026.09.1`, basis `cb-sgd-single-part-v1`, and the line "Reference costs are synthetic and do not represent real repair prices". `e-007` still reads "More information needed" in the printed report, with its reason, and it is not a pass. Final approval prints as "not recorded". The surveyor prints to PDF from the browser.

## Implementation status (2026-09-24)

The pure review service is connected to the API and workbench through the typed
review-actions endpoint. Identity/coverage rerun M3 over reused records; other
decision-changing corrections rerun consolidation. IndexedDB stores one unresolved
request per actor/claim with its original idempotency key, plus note/amount drafts.
Explicit retry survives reload and lost acknowledgements. Generic form values are
retained once submitted; unsent generic form drafts are not all persisted yet.
Frozen reports retain human scope and notes across reassessments. This is a fixture-backed
integration, with [validation evidence and limitations](../verification/model-independent-2026-09-24.md).
Unticked compound tasks retain their unverified or model-dependent requirements.

## Implementation tasks

- [ ] Agree the API surface and the record shapes with the [application platform](application_platform.md) and [integration contracts](integration_contracts.md) on day 1.
- [ ] Build the upload page with the five claim states and explicit per-file rejection reasons.
- [ ] Build the review overview with all four sections against clearly labelled fixtures by day 2, then swap in real records.
- [ ] Build the evidence panel with server-rendered overlays, one-tap original access and the row and mark box highlight.
- [x] Implement `classify_review_action` and `apply_review_action` as pure functions with their unit tests.
- [ ] Implement idempotency keys, `expected_review_revision` conflict handling and durable pending-edit storage with retry.
- [ ] Implement `evaluate_finalize_preconditions` for F1 to F6, and for P1 and P2 if the team adopts them, with a blocker list that links to the offending row.
- [x] Implement the reassessment publish path with `reuse_lineage`, and verify that a price correction reruns no neural model.
- [ ] Implement the print view from a frozen revision only, with the full footer, the synthetic-cost label and the final-approval status.
- [x] Build the reason-code to display-text catalogue from M8's table, with no free composition.
- [ ] Verify the tablet layout, large controls, keyboard focus, greyscale distinguishability and bright and dim presentation.
- [ ] Run the service checks: container kill, broker stop and restart, duplicate delivery, stale edit, replayed save.
- [ ] Run the section 13.5 usability sessions and publish the measures with their sample limits.
- [ ] Write the four described-only views into the final report without building them.

## Open decisions

- **Proposed finalize condition P1**, blocking finalize on a superseded assessment. It is not in the [application platform](application_platform.md) list of six. Without it, a race between a late edit and a finalize could freeze a review against findings that a newer input revision already replaced.
- **Proposed finalize condition P2**, requiring declaration completeness to be `complete` or confirmed. It is not in the platform list either. Proposal section 7.2 does not name it, but proposal section 8.2 makes an incomplete declaration a reason to propose no additions at all, so finalizing without it prints a report whose missing-repairs check never ran. The team should either adopt P2 or accept that the printed report must state plainly that the missing-repairs check was not evaluated.
- Whether a claim whose document branch failed permanently, for example an unsupported layout, can be finalized at all. Condition F1 currently blocks it. The alternative is a recorded surveyor acknowledgement that converts a permanent failure into a documented gap. This needs a team decision, because it is the difference between a demonstrable workflow and a dead end on a bad photograph.
- Whether a photographs-only claim can be finalized. Proposal section 9.2 allows the state, but there are no line items to review. Linked to the same open item in [M8](module-08-consolidation-checks.md#open-decisions).
- Whether an undismissed `cost_outlier` should require an explicit acknowledgement before finalize. Currently it does not, which keeps finalization honest but lets a finding print with no surveyor comment.
- How a carried-forward dismissal is presented so it is clearly carried and not freshly made.
- Whether `usability_event` telemetry is stored at all, or whether the section 13.5 measures are captured on paper. Storing them is simpler to analyse and adds a table nobody else needs.
- The durable pending-edit storage choice, IndexedDB versus `localStorage`, and its quota behaviour with several open claims.
