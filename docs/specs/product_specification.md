# Product specification

Status: implementation baseline. Product owner: Lane 5 coordinates; named owner to confirm. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), sections 2-9 and 13-16.

## Problem and intended outcome

Surveyors repeatedly identify damaged parts, compile repair lists, and check declared repairs and costs. Existing files often lack reusable links between a vehicle, damaged part, repair operation, evidence, and approved cost.

CLAIM-CMEV assists preparation and checking of the surveyor's declared assessment. It presents evidence-linked findings and possible missing repairs for human review. It does not determine the technically necessary operation, approve a claim, choose a settlement, or identify fraud from a price difference.

The immediate product hypothesis is that proposed lists and accessible evidence reduce preparation and checking effort without increasing missed repairs or misleading flags. The project evaluates model quality, editing effort, and preliminary usability. Productivity gains, real repair-price accuracy, commercial savings, and fraud detection are not established by this prototype.

## Users and responsibilities

| Persona | Need | Prototype interaction |
| --- | --- | --- |
| P1 Surveyor / motor assessor | Review evidence, correct repairs, record agreed costs | Primary user of Screens 2-4 |
| P2 Claim handler | Consume reviewed assessment and supply final approval later | Structured export and controlled synthetic approval import |
| P3 SIU investigator | Inspect selected evidence | Evidence viewer; no automated escalation |
| P4 Operations manager | Monitor workflow and review activity | Static queue and operations mockups |
| P5 Model risk / compliance | Reconstruct assessments and decisions | Stored audit records; static audit mockup |
| P6 Workshop manager | Explain individual repair entries | Discussion supported by surveyor; no portal |
| P7 Policyholder | Complete repairs and efficient review | Indirect beneficiary; no account |

The intended buyer is a motor insurer. Platform fees plus per-claim processing are a future commercial hypothesis; billing and multi-customer operation are outside the project build.

## Scope and priorities

| Priority | Deliverable |
| --- | --- |
| P0: required prototype | Nine functional modules; file intake, durable jobs, storage, review API, versioning, and structured export |
| P0 | Working vehicle overview, repair-list comparison, and evidence viewer using model outputs |
| P0 | Photograph-only preparation, uncertain-evidence handling, persisted review actions, and reconnect/retry of unsent edits |
| P0 | Separate synthetic final-approval import and evaluated offline reference refresh |
| P0 | Module, comparison, service, and preliminary workflow evaluations |
| P1: design deliverable | Static claim queue, cost-range detail, operations dashboard, and audit-view mockups |
| Research deliverable | RQ1-RQ4; joint image-text and image-text alignment comparators where eligible held-out data supports them |
| Future only | Production hosting and identity, insurer integrations, settlement, live approvals, full offline file sync, portfolio reporting, billing |

The working repair-list screen must still display essential cost-range details even though the standalone cost-detail screen is a mockup.

## Core journey

1. Create a claim with vehicle metadata and photographs.
2. Review the image-derived parts list while comparison displays “awaiting declared entries” if no report or structured list exists.
3. Add a draft survey report or surveyor-entered repair list.
4. Review entry-level photographic and cost checks and a separate list of possible missing repairs.
5. Open covering photographs, overlays, original images, and source report locations.
6. Correct extraction or declared entries, request/add evidence, or record a justified manual judgement.
7. Accept or dismiss findings, negotiate and record agreed amounts, and save a reviewed assessment.
8. Export the structured assessment. A separate later import records final approval and feeds an eligible future reference build.

Changing photographs or declared entries creates a new input/assessment revision. Review decisions and agreed amounts create review revisions without overwriting declarations or predictions. An assessment remains bound to its original cost-table version.

## Functional requirements

| ID | Requirement | Acceptance evidence | Owner |
| --- | --- | --- | --- |
| PR-01 | Accept photos with an optional report or explicit structured entries; retain original inputs | Photo-only and full-input flows persist distinct revisions | Platform |
| PR-02 | Show supported parts, observed damage, and coverage separately | A visible undamaged panel differs from an unseen panel | M01-M03, M09 |
| PR-03 | Extract part, side, operation, cost, and source location; expose uncertainty | An uncertain amount or side can be corrected without a confident adverse finding | M04-M05 |
| PR-04 | Show the four overall outcomes below and their individual checks | Ordered decision fixtures and understandable explanations | M07-M09 |
| PR-05 | Show possible missing repairs separately without inventing a cost | Surveyor can add, edit, or dismiss an observation-derived candidate | M08-M09 |
| PR-06 | Link each finding to retrievable original evidence | One-tap original photo; correct report page and highlighted entry | Platform, M09 |
| PR-07 | Preserve original values and record corrections, reasons, agreed costs | Save/reload retains distinct machine and human records | Platform, M09 |
| PR-08 | Dismiss by selecting a reason without another confirmation | Same finding stays dismissed on the same assessment after reload/retry | M09 |
| PR-09 | Preserve unsent edits across a connection interruption | Reconnect sends each action once; conflicts are visible and retain local work | Platform, M09 |
| PR-10 | Display processing and evidence limitations truthfully | Worker failure never appears as a completed clean assessment | Platform, M09 |
| PR-11 | Export a basic structured assessment with evidence and versions | Export validates against the agreed contract | Platform |
| PR-12 | Record final approval separately and refresh eligible costs offline | Agreed-only and duplicate approvals are excluded from new history | Platform, M06 |
| PR-13 | Identify synthetic cost references and comparison limits | List, range details, and export carry synthetic provenance | M06-M09 |
| PR-14 | Specify the remaining screens with static mockups | Queue, cost detail, operations, and audit designs cover their proposal fields | M09 |

## Outcome semantics and content

| Internal result | User-facing label | Meaning |
| --- | --- | --- |
| `ok` | No discrepancy found in the available checks | All required checks succeeded within their limits |
| `unsupported` | No visible damage detected in adequate views | Assessable covering views exist; model did not detect supporting damage |
| `cost_outlier` | Cost outside reference range | Photographic support exists and a compatible, sufficiently supported range is exceeded |
| `insufficient_evidence` | More information needed | A required documentary, photographic, or cost condition cannot be assessed |

Informational missing-evidence states must look different from discrepancy flags. Individual checks remain visible, including photographic support when cost history is inadequate. A cost outlier can be below or above a range; neither direction proves fraud.

Proposed dismissal reasons: hidden damage, ADAS/calibration cost, parts price change, inadequate photograph, system error, and other. Capture a note for “other”; retain reasons and reviewer identity. Human agreement with a flag is not a fraud label or a final approved cost.

## Interaction and quality requirements

Use large controls and readable tablet layouts under varied lighting. Do not rely on colour alone. Keep versions visible, preserve original values beside edits, and distinguish pending, saved, failed, and conflicting submissions. Test evidence navigation on a representative tablet viewport and keyboard interaction.

The prototype demonstrates durable processing and review recovery. It has no production availability or real-time processing guarantee. Measure actual latency and memory on recorded demonstration hardware before setting operational targets.

## Delivery milestones

| Milestone | Exit condition |
| --- | --- |
| D0: first working session | Owners, taxonomy, contracts, states, and cost semantics agreed |
| D1: first week | Upload-to-review integration with labelled fixtures and persistence |
| D2: model integration | Evaluated model outputs replace fixtures; uncertain cases and evidence navigation work |
| D3: evaluation freeze | Held-out partitions, checkpoints, mappings, thresholds, and cost table fixed |
| D4: demonstration | Unseen case processed and reviewed; export, synthetic approval, and separate refresh demonstrated |

Dates and named owners remain to be agreed. Each milestone is judged by evidence, not elapsed time.

## Success measures and release acceptance

Follow the numeric targets in the [evaluation plan](evaluation_plan.md). Required evidence includes entry/list accuracy, edits per claim, duplicate removal with damage preservation, interval calibration, discrepancy precision and recall, correct withholding, review time, finding comprehension, and evidence access. Initial targets are aspirations, not achieved results.

Release acceptance requires an unseen complete case, measured module results or explained shortfalls, at least 0.95 correct withholding for unphotographed parts, course mapping confirmation, answers to all four research questions, working Screens 2-4 with persisted actions, and a controlled approval/refresh demonstration. Record limitations if practitioners or real grouped/report/cost datasets are unavailable.

## Risks and unresolved product decisions

The main risks are poor coverage creating false findings, synthetic results being mistaken for real-world validation, noisy proposals creating editing work, and excessive UI scope. Follow the [module tasks](README.md) for data access, model, and evidence risks. Future rollout stages are shadow mode, assisted review, controlled cost-history updates, then portfolio reporting; the project does not deploy these stages.

## Product tasks

- [ ] Assign product owner and lane members; confirm course rubric mapping.
- [ ] Validate workflow and timing assumptions with a practising surveyor, or record the limitation.
- [ ] Confirm demonstration case, supported part/operation scope, and delivery dates.
- [ ] Agree dismissal reasons and test the “other” note interaction.
- [ ] Review PR-01 through PR-14 with module owners.
- [ ] Define comparable manual/assisted cases and alternate review order.
- [ ] Create and review the four static mockups.
- [ ] Capture metric results, limitations, and release acceptance evidence.
- [ ] Verify any industry figures before reusing them in product or presentation material.
