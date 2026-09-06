# CLAIM-CMEV

## Cross-Modal Evidence Verification of Declared Repair Scope Against Photographic Damage Evidence in Motor Own-Damage Claims

**Project Proposal**

| | |
|---|---|
| **Group ID** | ACERY |
| **Members** | Divakaran Pillai Arunkumar; Miguel Rodel Felipe; Lau Lay Khoon; Lim Chong Jen; Sun Yan |
| **Programme** | NUS-ISS Graduate Certificate in Pattern Recognition Systems |
| **Modules** | PSUPR, PRMLS, ISSM |
| **Document version** | v1, for submission |

**CLAIM-CMEV** denotes Cross-Modal Evidence Verification (CMEV) applied to motor claims. CLAIM is a domain prefix rather than an acronym. The name states the method and the application without asserting that the system adjudicates, which is consistent with the non-goals in §2.6.

---

## 1. Executive summary

Motor own-damage claims in Singapore are settled on the basis of a visual judgement made by one surveyor standing in front of one vehicle. That judgement produces a parts list, the parts list produces a costed estimate, and the estimate determines the payout. The photographs that support the judgement, often several hundred per claim, are archived and never machine-read. No structured record of part, damage state, and cost accumulates.

Two consequences follow. Surveyor capacity is spent on visual recognition work that is repetitive and automatable, which constrains the skilled negotiation and repair-decision work only a surveyor can do. And because no part-level cost history exists, an inflated line item can only be caught by whichever surveyor happens to see it.

CLAIM-CMEV addresses both with a single intervention. The system reads the damage photographs and the survey report as two separate modalities, converts each into a structured representation, and reconciles them. Where the declared repair scope is supported by the photographic evidence, the system confirms it. Where a declared part shows no observable damage, or a declared cost falls outside the reference band for that part and vehicle class, the system raises a flag with a stated reason and the specific photograph that justifies it. Where photographic coverage is insufficient to decide, the system requests an additional view rather than flagging.

The deliverable is a structured pipeline with four stages: a vision branch performing part and damage segmentation, a document branch performing line-item recognition on the survey report, a reconciliation engine, and a surveyor workbench. Every confirmed survey writes a structured record back to a reference cost table, so the productivity system and the cost-baseline system are one system rather than two.

The system is specified as a commercial product rather than as a research prototype. The primary user is the surveyor, working on a tablet in a workshop with vehicles queued behind them, and the interface is designed around that context: pre-filled parts lists reviewed rather than authored, flags dismissible in one action with a recorded reason, and the raw photograph always one tap away from any machine judgement. Sections 5 to 8 set out the commercial positioning, the personas, the surveyor journey, and the interface specification. The product is positioned as a surveyor productivity tool that produces a cost-intelligence asset as a by-product, because the productivity benefit is immediate and measurable while the cost intelligence compounds and is what makes the product durable once installed.

The project satisfies all four of the technical aspects listed in the module requirements, against a stated minimum of three. It uses supervised learning for segmentation and line-item recognition, unsupervised outlier detection for cost anomalies with no fraud labels used anywhere, transformer-based deep learning across both modalities, hybrid fusion of two independently trained branches in the reconciliation engine, and multi-view aggregation as an intelligent sensing and sense-making step.

---

## 2. Problem statement and motivation

### 2.1 The problem, in plain terms

When a car is damaged in Singapore, nobody looks up what the repair costs. One person decides.

The workshop writes a bill first. The insurer does not take it on trust, so it sends its own surveyor to the workshop to check. The surveyor walks around the car, decides which panels are damaged, decides for each one whether it can be beaten out or has to be replaced, negotiates the price down, and writes a report. The report contains a list of parts, a price against each part, and often several hundred photographs of the damage.

Everything downstream comes from that parts list: what the insurer pays, what goes into the loss data, and what the policyholder's premium looks like next year.

Two things are wrong with this arrangement.

**The surveyor spends time on the wrong half of the job.** Looking at a photograph and saying "that is a front bumper and it is dented" is the repetitive half. Judging whether a dented bumper with a radar sensor behind it can be refinished or must be replaced, and arguing the labour hours with a workshop that wants the replacement, is the skilled half. The same person does both, and the first half eats the time available for the second.

**The evidence is then thrown away.** The report is filed as a PDF. The photographs go into an archive and are never read by anything again, so nothing accumulates. The insurer knows it paid S$2,400 on that claim. It does not hold a record saying that a scuffed front bumper on a 2019 Corolla, refinished rather than replaced, costs a particular amount. That is the record that would let it recognise the next quote that is 40% above it.

Without that record, an inflated line item can only be caught by whichever surveyor happens to be standing in front of it, on that day, with that queue behind them.

Both problems have the same cause. The evidence in a motor claim is never converted into data.

### 2.2 Three cost objects, distinguished

The workflow produces three different money figures that must not be conflated:

| Symbol | Object | Produced by | Role |
|---|---|---|---|
| `C_wks` | Opening repair estimate | Workshop | Negotiating position; not the evidence under test |
| `C_est` | Declared costed estimate submitted with the survey report | Surveyor | The evidence under test |
| `C_act` | Post-adjudication approved cost | Claim handler | Realised settlement; the label |

The system interrogates `C_est` against the photographic evidence. `C_act` is the supervisory signal, available only retrospectively.

### 2.3 The task, stated precisely

Given a photograph set **P** (tens to hundreds of images of one damaged vehicle) and a declared scope **S** = {(part, operation, cost), …} extracted from the survey report, determine whether **S** is supported by **P**, and localise any part of **S** that is not.

Three discrepancy classes are in scope:

- **Coverage.** A part appears in the declared scope but no damage to it is observable in any photograph. Sub-cases are phantom parts, and panel spillover, where an adjacent undamaged panel is added to the scope of a genuinely damaged one.
- **Cost band.** A declared operation or price falls outside the observed distribution for that part, damage type, and vehicle class. Sub-cases are labour-hour inflation, and grade substitution, where an OEM part is billed for an aftermarket fitting.
- **Under-scoping.** Damage is visible in the photographs but absent from the declared scope. This is a repair-quality and policyholder-fairness failure rather than a fraud one, and the system should surface it.

### 2.4 The sector-level symptom

Motor is the largest domestic general insurance segment in Singapore, with S$1.28 billion in gross written premiums in 2025 and a 20.9% share, and it recorded an underwriting loss of S$6.9 million that year. Net incurred claims rose 11% while recorded accident counts stayed broadly flat. The industry attributes the divergence to rising severity.

*[Figures to be cited to GIA annual statistics before submission.]*

That attribution may well be correct. EV battery packs, structural aluminium, and ADAS sensors embedded in bumpers and windscreens do raise repair costs. But it cannot currently be tested. Rising repair prices and rising claim inflation are observationally identical in aggregate loss data. They separate only at part level, and no insurer holds part-level cost history linked to damage evidence. The sector cannot diagnose its own loss ratio.

Against this, the General Insurance Association of Singapore estimates that roughly S$140 million per year is consumed paying and investigating fraudulent and inflated claims.

### 2.5 Who is worse off

| Stakeholder | Loss under the status quo |
|---|---|
| Insurer | Leakage on inflated line items; no ability to attribute loss-ratio movement to severity or to inflation |
| Policyholder | Inflation is priced into next year's premiums; under-scoped repairs return the vehicle with undetected damage |
| Honest workshop | Competes against workshops that inflate, in a market where inflation is not systematically detected |
| Surveyor | Scarce expert judgement spent on recognition work |
| Regulator / GIA | No structured basis to distinguish a severity trend from an inflation trend |

### 2.6 What the system does not do

- **It does not decide the claim.** It pre-fills a parts list and raises flags. A surveyor confirms, edits, and enters the agreed amount. Every output is a proposal.
- **It does not treat absence of evidence as evidence of absence.** A part declared but not visible in any supplied photograph produces a request for an additional view rather than a flag. Photographic coverage of a damaged vehicle is incomplete by default, and a system that penalises incomplete coverage would penalise honest claims with bad photography.
- **It does not detect hard fraud.** Staged collisions, phantom passengers, and policy-inception fraud fall outside the evidence this system reads.
- **It does not use fraud labels.** No component of this project is trained on a fraud outcome. The cost-anomaly component is unsupervised by design, which is both a methodological choice and a requirement given that no such labels are available.

### 2.7 Legitimate divergence must be modelled

A declared cost can exceed the photographic evidence for honest reasons. The system is only defensible if these are modelled as a distinct class rather than treated as noise:

- **Hidden damage.** Structural or mechanical damage discovered on teardown that no external photograph could show.
- **Genuine severity.** ADAS recalibration, EV battery inspection protocols, and aluminium repair procedures carry real cost that the visible damage does not convey.
- **Parts price movement.** Supply shocks and model-year changes between the reference period and the claim.
- **Photographic incompleteness.** Angles, lighting, occlusion, dirt, and standing water.

A system that cannot separate these from inflation would penalise severe damage and bad photography, and any claims operation would be right to reject it.

### 2.8 Why this is tractable now

The photographs already exist. They are captured routinely and in volume, then discarded as data, so the input to an automated assessment is a by-product of a process the insurer already runs. Part segmentation and damage segmentation from vehicle imagery are established tasks with published datasets. So are multi-view aggregation and document-layout extraction from scanned reports.

The reconciliation between them does not exist: reading a costed line of text and asking whether the photographs support it. That is the contribution.

---

## 3. Project goals

### 3.1 Primary goal

Build a working structured pipeline that ingests a claim's damage photographs and survey report, produces a per-line-item assessment of whether the declared repair scope and cost are supported by the photographic evidence, and presents that assessment to a surveyor with the supporting image evidence attached.

### 3.2 Specific objectives

1. **Part segmentation.** Train a segmentation model that identifies vehicle panels from claim-style photographs in a vocabulary that can be mapped to survey-report part names.
2. **Damage segmentation.** Train a segmentation model that identifies damage type and extent, and intersect its masks with the part masks to produce (part, damage type) observations.
3. **Multi-view aggregation.** Aggregate per-image observations across the photograph set of one vehicle into a single vehicle-level damage state, with duplicate suppression.
4. **Line-item recognition.** Extract (part, operation, cost) triples from the survey report with page and line localisation, so that any flag can be traced to its source on the page.
5. **Cost band modelling.** Learn a reference cost band per (part, damage type, vehicle class) key, with a calibrated prediction interval rather than a point estimate.
6. **Reconciliation.** Implement the coverage, cost-band, and evidence-sufficiency checks, and produce a per-line-item verdict with a stated reason.
7. **Workbench.** Present the assessment in a surveyor-facing interface, capture confirmations and edits, and write structured records back.

### 3.3 Product objectives

Alongside the modelling objectives, the project delivers the product artefacts that make the system usable rather than merely functional:

8. **Surveyor workbench prototype.** Implement the vehicle overview, reconciliation, and evidence screens as a working interface against real model output (§8.10).
9. **Interface specification.** Specify all six screens and the audit view, including the interaction rules that follow from the field context, so that the unimplemented screens are documented rather than absent.
10. **User journey definition.** Establish the current-state and target-state surveyor journey with the intervention points identified, so that the productivity claim is anchored to specific workflow steps rather than asserted in general.

### 3.4 Research questions

- **RQ1.** Can part and damage segmentation trained on public and synthetic data produce a vehicle-level damage state accurate enough to support line-item reconciliation, and what is the accuracy ceiling imposed by photographic conditions?
- **RQ2.** Does joint part-and-damage supervision from synthetic data improve mask intersection quality over independently trained part and damage models?
- **RQ3.** Can multi-view aggregation reduce duplicate damage counting to a level where the vehicle-level state is usable, and what is the residual error?
- **RQ4.** Does a calibrated interval cost model produce flag rates that a claims operation could act on, and how does flag precision degrade as reference-table support thins?

### 3.5 What is explicitly out of scope

Real-time deployment, integration with a production claims management system, hard-fraud detection, structural damage inference from external photographs, and any claim of validated repair-cost prediction against real workshop invoices. The last of these is discussed in §12.4.

---

## 4. Mapping to course requirements

The module requires that the project develop, integrate, and demonstrate at least three of four listed aspects. This project addresses all four.

| Required aspect | Where it is satisfied | Evidence at assessment |
|---|---|---|
| **Supervised learning** | Module 1 part segmentation (21 classes); Module 2 damage segmentation (8 classes); Module 4 line-item recognition on annotated documents; Module 6 cost band regression | Trained model weights, per-class evaluation metrics, held-out test results |
| **Unsupervised learning** | Module 7 cost outlier detection. No fraud labels are used anywhere in the project. Deviation from the learned band is the anomaly signal, and the band is learned from cost data alone. | Outlier scores on held-out estimates; flag rate distribution; behaviour under injected anomalies |
| **Machine learning / deep learning** | SegFormer hierarchical transformer encoders for both segmentation heads; LayoutLMv3 or Donut for visually rich document understanding; gradient boosting with conformal intervals for the cost model | Architecture documentation, training curves, ablations |
| **Hybrid / ensemble approach** | Module 8 reconciliation engine fuses two independently trained modalities. The vision branch and document branch share no weights and no training data; their outputs are combined only at the assessment stage. | Comparison of reconciliation performance against each branch alone |
| **Intelligent sensing / sense-making** | Module 3 multi-view aggregation converts many raw images into one structured vehicle-level damage state | Duplicate suppression rate; per-vehicle state accuracy against ground truth |

The unsupervised and hybrid aspects deserve a note, because both are easy to claim weakly and both are load-bearing here.

The **unsupervised** claim rests on the absence of fraud labels. This is not a limitation dressed as a feature. No public dataset labels motor claim line items as inflated, and an insurer's internal fraud outcomes are unavailable to this project. The cost model therefore learns what normal costs look like from cost data alone, and anomaly is defined as deviation from the learned interval. This is the appropriate method given the data, and it is genuinely unsupervised.

The **hybrid** claim rests on the two branches being independent. The vision branch is trained on segmentation datasets and never sees a survey report. The document branch is trained on business documents and never sees a vehicle photograph. The reconciliation engine is where the two meet, and its input is two structured representations rather than two feature vectors. This is fusion at the decision level, and the ablation in §14 is designed to demonstrate what the fusion adds.

---

## 5. Product definition and commercial framing

### 5.1 What is being sold

CLAIM-CMEV is decision-support software sold to motor insurers, deployed into the survey and adjudication step of own-damage claims handling. It is not sold as a fraud detection product and it is not sold as an automated settlement engine. Both of those positions have been tried in this market and both create a procurement objection the product cannot answer: an insurer will not buy a system that makes payment decisions it cannot defend to a regulator or a complainant.

The product is positioned instead as a **surveyor productivity tool that produces a cost-intelligence asset as a by-product**. That framing matters commercially as well as technically. The productivity benefit is immediate, measurable in the first month, and attributable to a named cost centre. The cost-intelligence benefit compounds over time and is what makes the product hard to displace once installed, because the accumulated reference table belongs to the deployment and grows with use.

### 5.2 Value proposition by buyer

| Buyer | What they are buying | How they measure it |
|---|---|---|
| Head of Motor Claims | Throughput per surveyor without headcount growth | Claims closed per surveyor per week; survey turnaround time |
| Chief Underwriting Officer | Ability to attribute loss-ratio movement to severity or to inflation | Part-level cost trend series that did not previously exist |
| Head of SIU | Systematic pre-screening of estimates instead of sampled review | Proportion of estimates checked; recovery per investigator hour |
| Chief Risk / Compliance | An auditable, explainable assist that keeps the human decision-maker in place | Complete audit trail per flag; documented model governance |
| CFO | Reduced leakage on inflated line items | Leakage per claim before and after |

### 5.3 Commercial model

The intended model is a per-claim-processed subscription with a platform fee, rather than per-seat licensing. Per-claim aligns the vendor's revenue with the volume the insurer actually runs through the system, and it avoids the failure mode where an insurer buys ten seats and uses two.

Two commercial characteristics follow from the architecture rather than from a pricing preference. First, the reference cost table is **tenant-owned and tenant-isolated**. An insurer's settled-cost history is competitively sensitive and no insurer will accept it being pooled across a vendor's customer base. Second, the system is deployed **inside the insurer's environment or a dedicated tenant**, because claim photographs contain licence plates, location metadata, and in some cases identifiable people.

### 5.4 Deployment sequencing

| Stage | Scope | Commercial purpose |
|---|---|---|
| Pilot | One claim type, one workshop panel, shadow mode with no surveyor-facing output | Establish baseline accuracy against surveyor decisions without changing anyone's workflow |
| Assisted | Surveyor workbench live; pre-fill and flags visible; all decisions human | Prove the productivity claim on real throughput |
| Accumulating | Write-back active; reference table refreshing from confirmed surveys | Cost intelligence becomes available; product becomes hard to remove |
| Portfolio | Cost trend reporting to underwriting and pricing | Expands the buying centre beyond claims |

Shadow mode first is a deliberate commercial choice as much as a technical one. It lets the insurer see accuracy on their own claims before any surveyor is asked to change how they work, which removes the largest objection in the sales conversation.

### 5.5 Competitive position and honest limits

Existing automated damage assessment products predict damage and estimate cost from photographs. CLAIM-CMEV does something narrower and more defensible: it does not produce an independent estimate, it tests a declared estimate against the evidence supplied with it. That distinction is the product's position. It means the system never has to be right about what a repair should cost in absolute terms; it has to be right about whether a specific declared line is supported.

Three limits are stated to buyers rather than discovered by them. The system cannot see hidden or structural damage. It cannot distinguish genuine severity from inflation where the severity is invisible in a photograph. And its cost bands are only as good as the settled-claims history behind them, which means coverage is uneven and thin coverage produces abstention rather than a guess.

---

## 6. Users and personas

The survey step has one primary user. Everyone else consumes its output.

| # | Persona | Role in the product | Frequency of use |
|---|---|---|---|
| **P1** | **Surveyor / motor assessor** | **Primary user.** Works in the workbench on every claim. Confirms, edits, or rejects each proposed line and enters the agreed amount. | Every claim, many times daily |
| P2 | Claims executive / handler | Receives the completed structured assessment and settles against it. Sees flags that survived surveyor review. | Every claim, downstream |
| P3 | SIU investigator | Power user. Reviews claims where flags cluster or repeat across a workshop. Uses the structured history for pattern work. | Selective, deep use |
| P4 | Claims operations manager | Monitors throughput, flag rates, override rates, and queue health. Does not open individual claims routinely. | Daily dashboard |
| P5 | Model risk / compliance officer | Audits how flags were produced, which model version and which cost-table version applied, and whether the human remained the decision-maker. | Periodic and on complaint |
| P6 | Workshop manager | Indirect user. Receives a challenge on a specific line with the photograph attached rather than a general dispute about the total. | Per disputed claim |
| P7 | Policyholder | Not a user. Benefits through faster settlement and through under-scoping being surfaced rather than missed. | Never |

**A note on primacy.** An earlier internal product specification treated the claims executive as the primary user. The current architecture centres the surveyor, and this proposal follows the architecture. The reason is that the surveyor is the only person in the chain who is standing in front of the vehicle at the moment the parts list is created, which makes them both the largest beneficiary of pre-fill and the only person who can supply the confirmation that the write-back loop needs. The claims executive is a consumer of the output, not the operator of the system.

### 6.1 Primary persona detail: the surveyor

**Context of use.** Standing in a workshop, often outdoors or in a covered bay, on a tablet or phone rather than a desk. Frequently one-handed. Poor lighting, poor connectivity, gloves sometimes. Several vehicles queued behind the current one.

**Goals.** Get through the queue. Not miss damage that will resurface as a supplementary claim. Not concede a replacement where a repair would do. Have a defensible reason for every figure entered.

**Frustrations with the status quo.** Re-typing a parts list that the photographs already show. Arguing price without data. Being second-guessed later with no record of why a decision was made.

**What would make them reject the product.** Being told they are wrong by a system that cannot see what they can see. Extra clicks per claim. Anything that slows the queue. Flags they cannot dismiss and cannot explain.

That last point drives the interface design more than any other requirement. Every flag must be dismissible in one action, and every dismissal must be recorded rather than argued with.

---

## 7. Surveyor user journey

### 7.1 Current state

| Step | Action | Time | Pain |
|---|---|---|---|
| 1 | Travel to workshop | 30–60 min | Unavoidable |
| 2 | Walk the vehicle, note damage | 10–20 min | Skilled work, appropriate |
| 3 | Photograph damage from all angles | 10–15 min | Necessary, already digital |
| 4 | Compile parts list manually | 15–25 min | **Repetitive; the target** |
| 5 | Cross-check workshop estimate line by line | 15–30 min | **Rationed by queue pressure; the target** |
| 6 | Negotiate scope and price | 15–45 min | Skilled work, appropriate |
| 7 | Write report, attach photographs | 20–30 min | **Repetitive; partially the target** |
| 8 | Submit; report filed as PDF | 5 min | **Evidence lost as data here** |

Steps 4, 5, and 7 are where the product intervenes. Steps 2 and 6 are deliberately untouched, because they are the work only a surveyor can do.

### 7.2 Target state

| Step | Action | Change |
|---|---|---|
| 1 | Travel to workshop | Unchanged |
| 2 | Walk the vehicle, note damage | Unchanged |
| 3 | Photograph damage; images upload as captured | Capture guidance prompts for missing angles |
| 4 | **Open workbench: parts list already populated** | Compiled, not typed. Surveyor reviews rather than authors. |
| 5 | **Review flagged lines with evidence attached** | Every line pre-checked, not a sample. Flags carry photograph and band. |
| 6 | Negotiate scope and price | Unchanged, but now with the band as a reference position |
| 7 | Confirm, edit, or reject each line; enter agreed amounts | Structured entry replaces free-text authoring |
| 8 | Submit; **structured record written back** | Evidence becomes data. Report generated from the structure. |

### 7.3 The journey in narrative form

The surveyor arrives at the workshop with the claim already open on the tablet. Photographs taken at intake, or by the workshop, have already been processed, so the workbench is populated before the surveyor touches it.

They walk the vehicle as they always have. This is the part the product does not attempt to replace, and the interface stays out of the way during it.

They then open the parts list. It is already populated with the panels the system observed as damaged, each with the damage type and the photograph that shows it. The surveyor's task is review rather than authoring. Where the system got a panel right, they confirm it. Where it missed a panel, they add it, and the addition is recorded as a miss for later evaluation. Where it proposed a panel that is not damaged, they remove it, and that is recorded too.

Next they work the flags. Each flag states what it is, why it fired, and what evidence supports it. A flag reading *front bumper declared for replacement, no damage observed in the four photographs covering that panel* is shown with those four photographs and the part mask overlaid. A flag reading *quarter panel repair declared at S$1,150, reference band S$620 to S$890, based on 47 comparable claims* is shown with the band and its support count. The surveyor either accepts the flag and takes it into the negotiation, or dismisses it in one action with a reason chosen from a short list.

Where the system could not decide, it says so rather than guessing. A part declared but not visible in any photograph produces a request for another view, not an accusation. The surveyor either takes the photograph or records why it cannot be taken.

They then negotiate as they always have, with the band available as a reference position rather than as a ruling.

Finally they enter the agreed amount per line and submit. The report is generated from the structured record rather than typed, and each confirmed line writes back to the reference cost table. The next surveyor working on a comparable vehicle gets a slightly better band because of it.

### 7.4 Critical journey moments

Three moments determine whether the product is adopted or abandoned.

**First open of a populated parts list.** If the pre-filled list is visibly wrong on the first claim, trust is lost and every subsequent list is treated as noise. This is why shadow mode precedes assisted mode, and why pre-fill accuracy is a first-class metric in §14.3.

**First flag the surveyor disagrees with.** The system will be wrong sometimes. What matters is that disagreement is cheap. One action to dismiss, a reason recorded, no argument, no escalation, no repeat of the same flag on the same line.

**First flag the surveyor agrees with.** This is where the product earns its place. A flag that catches a line the surveyor would have passed under queue pressure, presented with the photograph that proves it, converts a sceptic.

---

## 8. User interface specification

Six screens. The workbench is the product; everything else is periphery.

### 8.1 Screen 1 — Claim queue

**User:** P1, P4.

Claims awaiting survey, sorted by age with an optional sort by flag count. Each row shows claim reference, vehicle, photograph count, processing status, and the number of flags raised. Processing status matters because a surveyor arriving at a workshop needs to know whether the vision branch has finished before they open the claim.

### 8.2 Screen 2 — Vehicle overview

**User:** P1.

The observed damage state at a glance, before any line-by-line work. A schematic vehicle diagram with observed-damaged panels highlighted, a photograph coverage indicator per panel region, and a count of declared lines against observed damage.

The coverage indicator is the important element and it is easy to omit. It shows which regions of the vehicle have adequate photographic coverage and which do not, so the surveyor can take the missing photographs before starting the review rather than being interrupted partway through.

### 8.3 Screen 3 — Parts list and reconciliation (the primary screen)

**User:** P1.

A table with one row per declared line item. Columns: part, operation, declared cost, verdict, reference band, and action.

Verdicts render distinctly and in a fixed order of visual weight:

| Verdict | Presentation | Available actions |
|---|---|---|
| `ok` | Neutral, no styling | Confirm; Edit |
| `unsupported` | Flagged, prominent | View evidence; Accept flag; Dismiss with reason |
| `cost_outlier` | Flagged, with band shown inline | View comparables; Accept flag; Dismiss with reason |
| `insufficient_evidence` | Informational, distinct from flags | Request photograph; Mark as unphotographable with reason |

The fourth verdict must not look like a flag. It is a request, not an accusation, and if the interface renders it in the same visual language as a flag then the design has undone the reasoning in §2.6.

**Additions.** A surveyor can add a line the system did not propose. Additions are recorded as system misses and feed evaluation.

**One-action dismissal.** Every flag can be dismissed in a single interaction with a reason selected from a short fixed list: hidden damage expected, ADAS or calibration cost, parts price movement, photograph inadequate, system error, other. The reason list is short deliberately, because a long list under queue pressure produces whatever option sits first.

### 8.4 Screen 4 — Evidence viewer

**User:** P1, P3.

Opened from any flag. Shows the photographs covering the disputed panel with the part mask and damage mask overlaid, a toggle to hide overlays and see the raw image, and the ability to page through every photograph that covers that panel.

Mask overlay must be toggleable. A surveyor needs to see the unmodified photograph to make their own judgement, and an interface that only shows the machine's interpretation of the image is asking them to trust rather than to verify.

### 8.5 Screen 5 — Cost band detail

**User:** P1, P3.

Opened from a cost flag. Shows the band, the declared value against it, the support count behind the band, the as-of date of the cost table version applied, and a distribution view of comparable settled claims.

Support count and as-of date are displayed rather than hidden. A band derived from twelve records is a weak argument and the surveyor should be able to see that it is weak, because presenting a thin band with the same confidence as a well-supported one is how a system loses credibility permanently.

### 8.6 Screen 6 — Operations dashboard

**User:** P4, P5.

Claims processed, average survey time, flag rate by type, surveyor override rate by flag type, and reference table coverage by vehicle class.

**Override rate is the health metric, not flag rate.** A high flag rate with a high override rate means the system is generating noise. A moderate flag rate with a low override rate means it is working. Presenting flag volume alone would create pressure to tune for more flags, which is the wrong incentive.

### 8.7 Audit view

**User:** P5.

For any historical claim: every flag raised, the model version and cost-table version that produced it, the evidence linked, the surveyor's action, the dismissal reason if any, and the final agreed amount. This exists because a complaint or a regulatory query about a specific claim must be answerable months later, and because the governance position in §5.2 is unsupportable without it.

### 8.8 Interface requirements that follow from the field context

| Requirement | Reason |
|---|---|
| Works on tablet at arm's length, one-handed where possible | Surveyor is standing at a vehicle, not at a desk |
| Legible in direct sunlight and in a dim covered bay | Workshop conditions |
| Tolerates intermittent connectivity; queues submissions | Workshops frequently have poor coverage |
| No flag can require more than one action to dismiss | Queue pressure; see §7.4 |
| Raw photograph always reachable in one tap from any flag | Verification, not trust |
| Every screen states which cost-table version is in use | Reproducibility of any flag |

### 8.9 What the interface deliberately does not include

No recommended settlement figure. No confidence score presented as a percentage next to a payout. No ranking of workshops by suspicion. No automatic escalation of a claim without a surveyor action.

Each of these was considered and excluded for the same reason: they move the system from proposing to deciding, which contradicts §2.6 and would make the product unsellable to the compliance buyer in §5.2.

### 8.10 Project scope for the interface

Within this project, the team implements Screens 2, 3, and 4 as a working prototype. These three carry the demonstration end to end: observed state, reconciliation, and evidence. Screens 1, 5, and 6 and the audit view are specified here and implemented as static mockups only. This is stated so that the scope boundary is visible rather than discovered at demonstration.

---

## 9. Solution overview

CLAIM-CMEV is a structured pipeline rather than an end-to-end model. Three considerations drove this choice.

**Traceability.** A flag raised against a claim must be explainable to a surveyor, to a claims manager, and potentially to a policyholder. A structured pipeline produces an intermediate representation at each stage, so a flag can be traced back to a specific mask on a specific photograph and a specific line on a specific page. An end-to-end model would produce a score without that trail.

**Modularity under a fixed budget.** The project has five members and a bounded time budget. A structured pipeline decomposes into modules with defined interfaces, which allows parallel work. An end-to-end model would serialise the team behind a single training loop.

**Data availability.** Each stage can be trained on data that exists. An end-to-end model would require paired (photograph set, survey report, outcome) training data, which does not exist publicly and cannot be assembled within this project.

End-to-end fusion and contrastive alignment between the two modalities are retained as experimental comparators (§14.5) rather than as the deliverable. They answer the question of what the structured pipeline gives up, without putting the deliverable at risk.

The system produces one of four verdicts per declared line item:

| Verdict | Condition | Action presented to surveyor |
|---|---|---|
| `ok` | Declared part observed damaged; declared cost within band | Pre-filled, no flag |
| `unsupported` | Declared part not observed damaged in any photograph that covers it | Flag with the covering photographs shown |
| `cost_outlier` | Part observed damaged; declared cost outside band | Flag with band, support count, and comparable claims |
| `insufficient_evidence` | Declared part not visible in any photograph | Request additional view; no flag raised |

The fourth verdict is the one that makes the system defensible in operation. It is what prevents poor photographic coverage from being read as dishonesty.

---

## 10. Architecture and data flow

### 10.1 Online path: inference on one claim

```
╔════════════════════════════════════════════════════════════════════════════╗
║                           ONLINE  ·  PER CLAIM                             ║
╚════════════════════════════════════════════════════════════════════════════╝

      ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
      │  N damage photos  │   │  surveyor report  │   │  vehicle attrs    │
      │  (tens–hundreds)  │   │  scan / PDF       │   │  make/model/year  │
      └─────────┬─────────┘   └─────────┬─────────┘   └─────────┬─────────┘
                │                       │                       │
   ┌────────────▼────────────┐  ┌───────▼────────────────┐      │
   │  ① VISION BRANCH        │  │  ② DOCUMENT BRANCH     │      │
   │                         │  │                        │      │
   │  ┌───────────────────┐  │  │  ┌──────────────────┐  │      │
   │  │ part segmentation │  │  │  │ text + layout    │  │      │
   │  │ SegFormer 21 cls  │  │  │  │ detection (OCR)  │  │      │
   │  └─────────┬─────────┘  │  │  └────────┬─────────┘  │      │
   │            ▼            │  │           ▼            │      │
   │  ┌───────────────────┐  │  │  ┌──────────────────┐  │      │
   │  │ damage segment'n  │  │  │  │ line-item        │  │      │
   │  │ SegFormer  8 cls  │  │  │  │ recognition      │  │      │
   │  └─────────┬─────────┘  │  │  │ LayoutLMv3/Donut │  │      │
   │            ▼            │  │  └────────┬─────────┘  │      │
   │  ┌───────────────────┐  │  └───────────┼────────────┘      │
   │  │ mask intersection │  │              │                   │
   │  │   part × damage   │  │              ▼                   │
   │  └─────────┬─────────┘  │      DECLARED SCOPE              │
   │            ▼            │   [(part, operation, cost), …]   │
   │  ┌───────────────────┐  │              │                   │
   │  │ multi-view        │  │              │                   │
   │  │ aggregation       │  │              │                   │
   │  │ (sense-making)    │  │              │                   │
   │  └─────────┬─────────┘  │              │                   │
   └────────────┼────────────┘              │                   │
                ▼                           │                   │
     OBSERVED DAMAGE STATE                  │                   │
  [(part, damage type, confidence), …]      │                   │
                │                           │                   │
                └───────────┬───────────────┴───────────────────┘
                            ▼
             ┌──────────────────────────────┐      ┌────────────────────┐
             │  ③ RECONCILIATION ENGINE     │      │  REFERENCE COST    │
             │                              │ read │  TABLE             │
             │  a) coverage check           │◄─────┤                    │
             │     declared ⊆ observed?     │      │  key:  part ×      │
             │     → phantom part,          │      │        damage ×    │
             │       panel spillover        │      │        veh class   │
             │                              │      │  val:  [lo, hi]    │
             │  b) cost band check          │      │        band        │
             │     declared cost vs [lo,hi] │      └─────────▲──────────┘
             │     → labour inflation,      │                │
             │       grade substitution     │                │
             │                              │                │
             │  c) evidence sufficiency     │                │
             │     part declared but in     │                │
             │     no photo → request view, │                │
             │     do NOT flag              │                │
             └──────────────┬───────────────┘                │
                            ▼                                │
             ┌──────────────────────────────┐                │
             │  ④ SURVEYOR WORKBENCH (UI)   │                │
             │                              │                │
             │  · pre-filled parts list     │                │
             │  · flags + reasons           │                │
             │  · mask overlay on photo     │                │
             │  · surveyor confirms / edits │                │
             │    and enters agreed amount  │                │
             └──────────────┬───────────────┘                │
                            │                                │
                            └────────────────────────────────┘
                              WRITE-BACK
                              confirmed (part, damage, agreed cost)

                    ══ this loop is what builds the baseline ══
```

### 10.2 Offline path: training

```
╔════════════════════════════════════════════════════════════════════════════╗
║                          OFFLINE  ·  TRAINING                              ║
╚════════════════════════════════════════════════════════════════════════════╝

   PART masks              DAMAGE masks             DocILE / CORD
   998 img · 21 cls        814 img ·  8 cls         6.7k docs · LIR track
   (polygon, Supervisely)  (polygon, Supervisely)   (line-item annotated)
         │                       │                        │
         └───────────┬───────────┘                        │
                     ▼                                    ▼
   ┌──────────────────────────────────┐    ┌────────────────────────────┐
   │  shared segmentation harness     │    │  VDU fine-tune             │
   │  Supervisely → COCO → SegFormer  │    │  LayoutLMv3 / Donut        │
   │  (two heads, one codebase)       │    │                            │
   └────────────────┬─────────────────┘    └─────────────┬──────────────┘
                    ▼                                    ▼
          part + damage weights                  line-item weights
                    │                                    │
                    └──────────────► ① ◄─────────────────┘  ──► ②


   prices_dataset.csv                    synthetic surveyor reports
   630 rows · 18 parts × 5 models        · layout transfer target ONLY
   × 7 years × 3 competing shops         · never used for accuracy claims
         │                                        │
         │  3 quotes per part = observed          └──► ② (transfer eval)
         │  legitimate price dispersion
         ▼
   ┌──────────────────────────────────┐
   │  cost band model                 │
   │  GBM on (part, damage, vehicle)  │
   │  + quantile / conformal interval │
   │            → [lo, hi]            │
   └────────────────┬─────────────────┘
                    ▼
          REFERENCE COST TABLE  ──► ③
          (seeded offline, grown online by write-back)
```

### 10.3 Interfaces between modules

Three contracts cross lane boundaries. They are fixed in the first working session so the lanes can then run independently.

```
  OBSERVED DAMAGE STATE   (lanes 1, 2 → lane 4)
    [ { part: str, damage: str, confidence: float,
        photo_ids: [str], mask_area_px: int } ]

  DECLARED SCOPE          (lane 3 → lane 4)
    [ { part: str, operation: "repair"|"replace",
        cost: float, line_no: int, page: int } ]

  ASSESSMENT              (lane 4 → lane 5)
    [ { part: str, damage: str, band: [lo, hi], declared: float|null,
        flag: "ok"|"cost_outlier"|"unsupported"|"insufficient_evidence",
        reason: str, evidence: [photo_id] } ]
```

A shared part vocabulary is a prerequisite for all three. The 21 segmentation mask classes, the 18 priced parts in the cost dataset, and the part names appearing in survey reports must be mapped to one canonical list before the reconciliation engine can do anything. This mapping is small, unglamorous, and blocks every other lane, so it is assigned explicitly in §13 and scheduled first.

---

## 11. Modules description and details

### Module 1 — Part segmentation

**Task.** Semantic segmentation of vehicle panels from a single photograph.

**Approach.** SegFormer, comprising a hierarchically structured transformer encoder producing multiscale features and a lightweight MLP decoder. The hierarchical encoder requires no positional encoding, which avoids the interpolation penalty that appears when test resolution differs from training resolution. Claim photographs vary widely in resolution, so this property is directly relevant rather than incidental.

**Classes.** 21, from the Humans in the Loop taxonomy: windshield, back-windshield, front-window, back-window, front-door, back-door, front-wheel, back-wheel, front-bumper, back-bumper, headlight, tail-light, hood, trunk, licence-plate, mirror, roof, grille, rocker-panel, quarter-panel, fender.

**Output.** Per-pixel part labels with confidence.

**Known limitation.** The 21-class vocabulary is not side-aware. A survey report distinguishes near-side from off-side, and this vocabulary does not. Mitigation is discussed in §15, R2.

### Module 2 — Damage segmentation

**Task.** Semantic segmentation of damage regions and types.

**Approach.** The same SegFormer harness with a different label set and a different head. Modules 1 and 2 share one codebase, one data format, and one training pipeline, so the work is to build the harness once and train two heads rather than to build two systems.

**Classes.** 8: dent, cracked, scratch, flaking, broken part, paint chip, missing part, corrosion.

**Mask intersection.** Part masks and damage masks are intersected to yield (part, damage type, mask area) observations per photograph. The intersection is where the two heads become one assessment, and it is the step for which joint part-and-damage supervision from CrashCar101 is expected to help (RQ2).

**Known difficulty.** Dent, scratch, and crack are visually similar and can be intertwined on the same panel. Published work on this task reports these as the hard classes. Per-class metrics will be reported rather than a single aggregate, so that a good mean does not conceal failure on the classes that matter for the repair-versus-replace decision.

### Module 3 — Multi-view aggregation

**Task.** Convert per-photograph observations into one vehicle-level damage state.

**Why it is necessary.** If the same dent is counted three times across three photographs, the observed damage state is wrong and every downstream reconciliation inherits the error. Duplicate suppression is not a refinement; it is a correctness requirement.

**Approach.** Project single-view detections onto a shared 3D vehicle representation and merge detections that project to the same region, following the method of van Ruitenbeek and Bhulai. The published evaluation on a drive-through camera rig reduced duplicate damage detections by nearly 99% and false positives by 96%, using only single-view models and single-view training data. This avoids requiring multi-view training data, which does not exist publicly (§12.3).

**Output.** The observed damage state, with each observation carrying the list of photograph identifiers that support it. That list is what the workbench displays as evidence.

**Course mapping.** This module is the intelligent sensing and sense-making component: many raw sensory inputs are reduced to one structured, interpretable state.

### Module 4 — Document text and layout detection

**Task.** OCR and layout analysis of the survey report.

**Approach.** Text detection and recognition with layout structure preserved, producing tokens with bounding boxes and page positions. Positional information is retained throughout, because the workbench must show where on the page a flagged line item originated.

### Module 5 — Line-item recognition

**Task.** Extract (part, operation, cost) triples from the costed estimate table in the survey report.

**Approach.** LayoutLMv3 or Donut fine-tuned on the DocILE Line Item Recognition track. Business documents carry a table of invoiced goods and services where each item is a set of key information such as name, quantity, and price, and Line Item Recognition is the task of assigning key information to items in that table. This is structurally the same problem as extracting part, operation, and cost from a costed survey estimate.

**Localisation, not just extraction.** DocILE distinguishes Key Information Extraction from Key Information Localization and Extraction, where the difference is positional information. This project needs the localised variant, because a flag without a page and line number is not actionable for a surveyor reviewing a hundred-page report.

**Output.** The declared scope, with page and line number attached to each triple.

### Module 6 — Cost band model

**Task.** Produce a `[lo, hi]` band for each (part, damage type, vehicle class) key.

**Approach.** Gradient boosting over the key features, with quantile regression or conformal prediction to produce a calibrated interval rather than a point estimate. The interval is the deliverable; a point estimate would give no principled basis for deciding whether a declared cost is anomalous.

**Training data.** `prices_dataset.csv`, 630 rows covering 18 parts across 5 vehicle models, 7 model years, and 3 competing workshops. The three competing quotes per part are the important structural feature: they give the model an observation of legitimate price dispersion between workshops, so that normal inter-workshop variation is inside the band rather than flagged as anomalous.

**Support reporting.** Every band carries a support count. A band derived from twelve observations and a band derived from four hundred warrant different confidence, and the reconciliation engine must know which it is holding.

### Module 7 — Cost outlier detection

**Task.** Determine whether a declared cost falls outside the expected distribution for its key.

**Approach.** Unsupervised. No fraud labels exist and none are used. The band from Module 6 defines the expected region, and deviation from it is the anomaly signal. Where the band is unavailable or its support falls below a minimum threshold, the engine abstains rather than guessing.

**Course mapping.** This is the unsupervised learning component.

### Module 8 — Reconciliation engine

**Task.** Combine the observed damage state and the declared scope into a per-line-item assessment.

**Three checks, in order:**

1. **Evidence sufficiency, first.** Is the declared part visible in any photograph at all? If not, the verdict is `insufficient_evidence` and processing of that line stops. This check runs first deliberately, so that a part which was never photographed can never reach the coverage check and be flagged as unsupported.
2. **Coverage.** For parts that are visible, is damage observed? If the part is visible and undamaged in every photograph that covers it, the verdict is `unsupported`.
3. **Cost band.** For parts that are observed damaged, is the declared cost within the band, and does the band have sufficient support? If outside a sufficiently supported band, the verdict is `cost_outlier`.

**Course mapping.** This is the hybrid fusion component. The two branches are trained separately on disjoint data and meet only here.

### Module 9 — Surveyor workbench

**Task.** Present the assessment and capture the surveyor's decision. The full interface is specified in §8; this module is its implementation.

**Scope within this project.** Screens 2, 3, and 4 are built as a working prototype against live model output. Screens 1, 5, and 6 and the audit view are static mockups (§8.10).

**Contents.** The pre-filled parts list; flags with stated reasons; mask overlays on the source photographs; the band and its support count where a cost flag was raised; and controls for the surveyor to confirm, edit, or reject each line and enter the agreed amount.

**Write-back.** Each confirmed line produces a structured (part, damage type, agreed cost, vehicle class) record that updates the reference cost table. This is the mechanism by which the system produces the cost baseline as a by-product of ordinary use.

---

## 12. Data sources

Every dataset below was checked for licence terms. Two entries in the earlier internal architecture note, PASCAL-Part and freMTPL2, have been superseded and the reasons are recorded in §12.6.

### 12.1 Part segmentation

| Dataset | Content | Classes | Licence | Role |
|---|---|---|---|---|
| **HITL Car Parts and Car Damages** | 998 part images plus 814 damage images, polygon masks, 24,851 polygons | 21 part classes; 8 damage classes | CC0 1.0 (public domain) | Primary. The only source found carrying parts and damage under one release, at the cleanest available licence. |
| **DSMLR Car-Parts-Segmentation** (KMITL) | Multi-view images, COCO-format instance masks, plates and faces anonymised | 18 part classes, side-aware | Research use, GitHub release | Secondary, for side-aware labels. |
| **Ultralytics Carparts-Seg** | 3,833 images, pixel masks, pre-split | 23 classes | AGPL-3.0 | Reserve. AGPL propagates and must be assessed before use. |

**Taxonomy shortfall.** None of these vocabularies covers panels that survey reports routinely price: A/B/C pillars, sill, wheel arch liner, radiator support, and the ADAS-bearing components such as radar brackets and camera mounts. Left-right side is present in DSMLR but absent from HITL. Dataset selection alone cannot close this gap; it requires a mapping layer, supplementary annotation, or both, and it is carried as risk R2.

### 12.2 Damage segmentation

| Dataset | Content | Classes | Licence | Role |
|---|---|---|---|---|
| **VehiDE** | 13,945 images, 32,000+ labelled instances | 8 damage classes | Kaggle mirror; original terms to verify | Primary by volume. |
| **CarDD** | 4,000 high-resolution images, 9,000+ instances | 6 damage classes | Signed licensing form required; not self-serve | Primary by quality, subject to access. |
| **CrashCar101** | Procedurally generated synthetic images from damaged 3D car models, pixel-accurate annotations for both parts and damage | Configurable | Academic release (WACV 2024) | Joint supervision and multi-view generation. |

Two operational facts about CarDD. Its images average 684,231 pixels against roughly 50,334 pixels for the older public car-damage dataset, and that gap has consequences beyond appearance: VehiDE's authors ran an annotator experiment and found high-resolution images yielded more discovered damage instances, because low-resolution photographs hid small, indistinct damage from annotators entirely. Image resolution is therefore a confounder in the evidence-sufficiency logic, and a claim photographed at low resolution will systematically under-report damage. Second, CarDD does not own the copyright to its images; access requires agreeing to Flickr and Shutterstock terms via a signed form emailed to the authors. This is a lead-time risk carried as R1.

**CrashCar101 is central rather than supplementary.** It uses a procedural pipeline that damages 3D car models and renders 2D images paired with pixel-accurate annotations for both part and damage categories. Models trained on real plus synthetic data outperformed real-only training for part segmentation, and the authors demonstrated sim-to-real transfer for damage segmentation. It addresses three of this project's problems at once: it supplies the joint part-and-damage annotation that Module 2's mask intersection requires and that no real dataset provides; it allows the team to define its own part taxonomy rather than inherit one, which partially answers §12.1; and it generates controlled multi-view renders of the same vehicle, which is the input Module 3 needs.

### 12.3 The multi-view gap

Module 3 aggregates evidence across many photographs of one vehicle. Every public damage dataset above is a collection of independent single images, not images grouped by vehicle. No public claim-level multi-view damage corpus exists.

Two responses, both viable, with the choice recorded as an open decision in §15:

- Synthesise multi-view groups from CrashCar101, which renders arbitrary camera positions around one damaged 3D model with ground truth.
- Avoid multi-view training entirely, using the single-view projection method described in Module 3.

### 12.4 The cost-label gap

**No public dataset pairs vehicle damage images with actual repair costs.** This was verified directly. Published work in this area either uses proprietary insurer data or manually estimates prices from parts websites, which its own authors acknowledge is unreliable. The 2025 WIREs systematic review of 55 papers on AI vehicle damage detection reaches the same conclusion about data availability being the field's binding constraint.

This drives the following declared assumption, which is stated rather than buried.

**Assumption A1 (seeded baseline).** *An insurer deploying CLAIM-CMEV already holds a settled-claims history from which an initial reference cost table can be derived, and CLAIM-CMEV refreshes that table continuously as confirmed surveys write back.*

A1 is defensible on its own terms. An insurer settling motor own-damage claims for years holds paid amounts, workshop invoices, and parts lines in its claims and finance systems. What it does not hold is that history linked to damage evidence at part level, which is the gap described in §2.1. Seeding the table is a historical-data engineering exercise on records the insurer already owns rather than a new data-collection programme, and it is orthogonal to the modelling contribution of this project.

Four consequences are carried into the design:

- The seeded table is coarse and the refreshed table is fine. Legacy records give aggregate paid amounts and inconsistent part naming, so the seed yields wide bands on a partial key set. Every confirmed survey narrows them.
- Band width must be an explicit output. The reconciliation engine reports the support behind the band it applied and declines to flag against a band below a minimum support threshold.
- The table is versioned and time-decayed. Parts prices move, so a band computed from 2019 records is not evidence about a 2026 claim. Records are weighted by recency and the table carries an as-of date, so any flag can be reproduced against the table version that produced it.
- Coverage is uneven by construction. Common panels on common vehicles get tight bands early; low-volume models and rare damage types stay wide or unsupported for a long time. The workbench surfaces this rather than hiding it behind a default.

**For the project deliverable**, the team uses `prices_dataset.csv`, a documented synthetic table of 630 rows spanning 18 parts, 5 vehicle models, 7 model years, and 3 competing workshops. The evaluation claim concerns the reconciliation logic: whether the engine correctly flags a line item outside a given band and correctly abstains when support is thin. It does not concern the absolute accuracy of the band values, and the report will state this boundary explicitly.

### 12.5 Document extraction

| Dataset | Content | Task | Licence |
|---|---|---|---|
| **DocILE** | 6,680 annotated real business documents, ~100k synthetic, ~1M unlabelled for pre-training; 55 annotation classes | Key Information Localization and Extraction; Line Item Recognition | MIT |
| **CORD** | 1,000 receipts (800/100/100), word- and line-level annotations, OCR output with boxes | 30 entities under 4 super-categories | CC BY-SA 4.0 |
| **SROIE** (ICDAR 2019) | ~1,000 scanned receipts (626 train / 347 test) | Text localisation, OCR, KIE of company, address, date, total | MIT |
| **FUNSD** | 199 noisy scanned forms (149/50) | Entity extraction and linking | Non-commercial academic |

DocILE is the primary document dataset for the reasons given under Module 5. It is MIT licensed, its 55 annotation classes exceed earlier KIE datasets by a wide margin, its test set includes zero- and few-shot layouts, and its published baselines include LayoutLMv3 and a DETR-based Table Transformer, which gives the team a starting point. CORD and SROIE serve as smaller benchmarks for the OCR and field-extraction stages. CORD is share-alike, which matters if any derived artefact is published.

**Transfer gap.** All of these are invoices, receipts, and generic forms. None is a motor survey report and none is in a Singapore insurer's layout. The document models are pre-trained on DocILE and fine-tuned on a small set of synthetic survey reports constructed by the team. Those synthetic reports are a layout-transfer target only and are never used to support an accuracy claim, because a model evaluated on documents generated from the same template it was fine-tuned on would report a number that means nothing.

### 12.6 Superseded data decisions

Two datasets appearing in earlier internal notes have been dropped, and the reasons are recorded here so the decision is not silently reversed.

**freMTPL2** contains 677,991 French motor third-party-liability policies and 26,639 claim amounts. Its `ClaimAmount` field is a third-party-liability claim amount, not a verified own-damage workshop invoice, and it carries no images, no part identifiers, and no repair line items. It cannot serve as repair-cost ground truth for this project, and the tabular anomaly-detection exercise it would support is a separate exercise that does not connect to the vision branch. The unsupervised requirement is satisfied instead by Module 7, which operates on the cost objects this project actually reasons about.

**PASCAL-Part** provides car-component labels but on older natural-scene imagery captured for general object recognition rather than damage assessment, and its part vocabulary is coarser than HITL's. HITL supersedes it on vocabulary, on image relevance, and on licence.

### 12.7 Volume assessment

Combining HITL, DSMLR, VehiDE, and CarDD yields roughly 20,000 real damaged-vehicle images with instance masks, augmentable via CrashCar101. This is adequate for fine-tuning pre-trained segmentation backbones and inadequate for training from scratch, which the project does not attempt. On the document side, DocILE's roughly 6,700 real and 100,000 synthetic documents are ample for pre-training. The binding constraint is the number of realistic survey reports available for fine-tuning, and that number will be stated in the final report rather than left implicit.

---

## 13. Team allocation

Five members. The work divides into five lanes with the interfaces in §10.3 as the boundaries.

| Lane | Scope | Modules | Member |
|---|---|---|---|
| 1 | Part segmentation, 21 classes | 1 | *to confirm* |
| 2 | Damage segmentation, 8 classes; multi-view aggregation | 2, 3 | *to confirm* |
| 3 | Document branch: OCR, layout, line-item recognition | 4, 5 | *to confirm* |
| 4 | Reconciliation engine; cost band model; reference table | 6, 7, 8 | *to confirm* |
| 5 | Systems and product: API, workbench UI (§8.10), pipeline orchestration, evaluation harness, deliverables | 9 | *to confirm* |

Lanes 1 and 2 share one training harness. The data format, the conversion pipeline, and the training loop are identical; only the label set and the head differ. The two members pair on the shared codebase and then split the heads and their evaluation, rather than building the same thing twice.

**Sequencing.** The canonical part vocabulary is built first, before any lane starts modelling. It maps the 21 segmentation classes, the 18 priced parts, and survey-report part names onto one list. Lane 4 cannot begin until it exists, and lanes 1 through 3 will produce mutually incompatible outputs without it. It is small and unglamorous work that blocks everything, so it is assigned to a named owner in the first session and scheduled ahead of all model work.

**Product ownership.** Lane 5 owns the user-facing surface, but the interface rules in §8.8 and §8.9 are product decisions rather than implementation choices and are agreed by the whole team before build. Two of them constrain other lanes. The `insufficient_evidence` verdict must not render as a flag, which requires lane 4 to emit it as a distinct verdict rather than as a low-confidence flag. And every flag must carry the cost-table version that produced it, which requires lane 4 to version the table from the start rather than retrofitting versioning later.

**Integration checkpoints.** The three interface contracts are frozen in the first working session. Each lane produces a stub conforming to its output contract within the first week, so that the end-to-end pipeline runs on stub data before any model is trained. This surfaces interface disagreements while they are cheap to fix.

---

## 14. Evaluation and success criteria

### 14.1 Module-level metrics

| Module | Metric | Target | Basis for target |
|---|---|---|---|
| 1. Part segmentation | mIoU; per-class IoU | mIoU ≥ 0.60; no panel class below 0.40 | Published part-segmentation results on comparable class counts |
| 2. Damage segmentation | mIoU; per-class IoU; AP | mIoU ≥ 0.45; dent, scratch, crack reported separately | These three are the acknowledged hard classes |
| 3. Multi-view aggregation | Duplicate suppression rate; vehicle-level state F1 | ≥ 90% duplicate reduction | Published method reports ~99% on a controlled rig; a lower target reflects uncontrolled claim photography |
| 5. Line-item recognition | F1 on complete (part, operation, cost) triples; localisation accuracy | F1 ≥ 0.75 on DocILE LIR; degradation on synthetic reports reported, not targeted | DocILE published baselines |
| 6. Cost band model | Prediction interval coverage; mean band width | Empirical coverage within 5 points of nominal 90% | Conformal prediction calibration standard |

Targets are stated as targets. They will be re-baselined after the first training run and any revision will be recorded with its reason rather than silently adjusted downward.

### 14.2 Reconciliation evaluation

The reconciliation engine is evaluated on a held-out set of claims with **injected discrepancies of known type**. Because no labelled inflated claims exist, the team constructs the test set by taking claims where declared scope and observed damage agree, then programmatically injecting each discrepancy class: phantom parts, panel spillover, cost inflation at several magnitudes, and grade substitution.

| Quantity | Definition | Target |
|---|---|---|
| Flag precision | Flagged lines that carry an injected discrepancy | ≥ 0.70 |
| Flag recall by class | Injected discrepancies detected, reported per class | Reported per class; no single aggregate |
| False flag rate on clean claims | Flags raised on unmodified claims | ≤ 0.05 per claim |
| Abstention correctness | Lines correctly assigned `insufficient_evidence` when the part is absent from all photographs | ≥ 0.95 |

Abstention correctness carries the highest target of the five deliberately. A system that flags an honest claim because the surveyor photographed the vehicle badly is worse than a system that misses an inflated line, and the evaluation should reflect that ordering.

**Detection floor.** Flag recall is reported against injection magnitude, so the report can state the smallest cost inflation the system reliably detects rather than reporting a single recall figure that conceals it.

### 14.3 End-to-end evaluation

| Quantity | Definition |
|---|---|
| Parts list pre-fill accuracy | Proportion of the surveyor's final confirmed parts list correctly proposed by the system |
| Edit distance | Number of surveyor edits required to reach the final list from the proposed one |
| Evidence traceability | Proportion of flags where the linked photograph and page reference are correct |

Pre-fill accuracy and edit distance are the productivity claim. They are the closest available proxy for surveyor time saved, which cannot be measured without a deployment.

### 14.4 Behaviour under thinning support

The cost-band check is evaluated as reference-table support is artificially reduced, to characterise how flag precision degrades and to set the minimum support threshold empirically rather than by assertion. This answers RQ4 and it also produces the threshold value that Module 8 needs.

### 14.5 Product and usability evaluation

The productivity claim cannot be validated without a deployment, but three things can be evaluated within the project and should be, because a proposal that specifies an interface and never tests it has specified decoration.

| Quantity | Method | Purpose |
|---|---|---|
| Review time per claim | Timed walkthrough of the workbench against manual list compilation, on the same claims | Direct evidence for the step 4 and 5 intervention in §7.2 |
| Flag comprehension | Present flags to a reviewer unfamiliar with the system and ask them to state why each fired | Tests whether the stated reason is sufficient on its own |
| Dismissal cost | Count interactions required to dismiss a flag with a reason | Enforces the one-action rule in §8.8 |

Where a practising surveyor is unavailable, these are run with team members outside the owning lane, and the substitution is stated rather than concealed. The result is indicative rather than a usability study, and the report will say so.

### 14.6 Comparators

Two comparators are run to situate the structured pipeline, neither of which is the deliverable:

- **End-to-end fusion.** A single model taking photographs and report tokens jointly and predicting per-line verdicts. Expected to underperform given the data available, and run to quantify what the structured decomposition costs or saves.
- **Contrastive alignment.** Aligning image and text representations in a shared space, evaluated on whether a declared line retrieves its supporting photograph. Run to test whether cross-modal alignment adds signal over explicit reconciliation.

### 14.7 Overall success criteria

The project succeeds if:

1. The end-to-end pipeline runs on an unseen claim and produces a per-line assessment with evidence attached.
2. Module-level targets in §14.1 are met, or missed with a documented and diagnosed reason.
3. The reconciliation engine meets the abstention-correctness target, which is the non-negotiable one.
4. All four course requirement aspects are demonstrated with evidence per §4.
5. The four research questions are answered, including answers of the form "no, and here is why", which is a valid result.
6. The workbench prototype runs on live model output for the three in-scope screens, and the interface rules in §8.8 hold in the built version rather than only in the specification.

---

## 15. Risks and mitigation

| ID | Risk | Impact | Likelihood | Mitigation | Owner |
|---|---|---|---|---|---|
| **R1** | CarDD access is delayed or refused. It requires a signed licensing form emailed to the authors, and turnaround is unknown. | Loss of the highest-quality damage dataset | Medium | Submit the request in week one. VehiDE alone provides 13,945 images and is sufficient to proceed. Treat CarDD as an enhancement, not a dependency. | Lane 2 |
| **R2** | Panel taxonomy mismatch. No public vocabulary covers pillars, sill, wheel arch liner, radiator support, or ADAS mounts, and HITL is not side-aware. | Reconciliation cannot match declared parts to observed parts | **High** | Build the canonical mapping first (§13). Use DSMLR for side-awareness. Declare uncovered panels as an explicit limitation and route them to `insufficient_evidence` rather than mis-mapping them. | Lane 4 |
| **R3** | No real survey reports available for document fine-tuning. | Line-item recognition is evaluated only on synthetic layouts | **High** | Pre-train on DocILE, which is real business documents. Use synthetic reports for layout transfer only and never for accuracy claims. State the number of realistic reports used in the report. | Lane 3 |
| **R4** | Synthetic cost table undermines any cost-accuracy claim. | Cost-band results cannot be presented as validated | High, and accepted | Scope the evaluation claim to reconciliation logic rather than band accuracy (§12.4). Declare A1 openly. This risk is managed by honest scoping, not by engineering. | Lane 4 |
| **R5** | Sim-to-real gap. CrashCar101 renders may not transfer to real claim photographs. | Joint supervision benefit fails to materialise | Medium | The authors demonstrated sim-to-real transfer for damage segmentation. Validate on real held-out data before relying on synthetic augmentation, and report the transfer result whichever way it falls. | Lanes 1, 2 |
| **R6** | Duplicate damage counting across views inflates the observed state. | Every downstream reconciliation inherits the error | Medium | Module 3 is designed for this. Evaluate duplicate suppression as a first-class metric rather than assuming it works. | Lane 2 |
| **R7** | Scope exceeds the available effort across nine modules and five members. | Incomplete deliverable | **High** | Stub the full pipeline in week one so an end-to-end path exists before any model is good. Degrade module quality rather than dropping modules, so the demonstration stays complete. | Lane 5 |
| **R8** | Interface drift between lanes working in parallel. | Integration failure late in the project | Medium | Freeze the three contracts in §10.3 in the first session. Each lane ships a conforming stub in week one. | Lane 5 |
| **R9** | Class imbalance in damage segmentation, with dent, scratch, and crack visually similar and unevenly represented. | Aggregate metric conceals failure on hard classes | Medium | Report per-class IoU throughout. Apply class-weighted loss. Never report a single aggregate without the per-class breakdown beside it. | Lane 2 |
| **R10** | Image resolution confounds evidence sufficiency. Low-resolution photographs systematically hide small damage. | Under-reported damage read as absent damage | Medium | Record per-photograph resolution as a feature. Fold it into the evidence-sufficiency decision so low-resolution coverage counts as weaker coverage. | Lanes 2, 4 |
| **R11** | Ultralytics Carparts-Seg is AGPL-3.0 and the licence propagates. | Licence contamination of project code | Low | Confirm implications before any use. HITL (CC0) and DSMLR are the primary sources and neither carries this constraint. | Lane 1 |

| **R12** | Surveyor rejection. The interface adds steps, flags noisily, or contradicts what the surveyor can see, and the tool is worked around rather than used. | Product fails regardless of model quality | **High** | Shadow mode before assisted mode (§5.4). One-action dismissal with recorded reason. Raw photograph always reachable. Track override rate rather than flag rate as the health metric (§8.6). | Lane 5 |
| **R13** | Interface scope creep. Six screens plus an audit view is more than the project can build alongside nine modules. | Deliverable incomplete | Medium | Three screens built, the rest specified and mocked, boundary stated up front in §8.10 rather than discovered at demonstration. | Lane 5 |

### Risks accepted without mitigation

Two limitations are inherent rather than manageable, and the report will state them as such rather than presenting weak mitigations.

The system cannot see structural or mechanical damage that no external photograph shows. Hidden damage discovered on teardown is a legitimate source of divergence between declared cost and photographic evidence, and no amount of model quality changes that.

The system cannot distinguish genuine severity from inflation where the severity is invisible. ADAS recalibration and EV battery inspection protocols carry real cost that the visible damage does not convey. Vehicle attributes partially proxy for this, and the residual is a permanent limitation of any vision-based approach.

---

## 16. Open items before submission

- [ ] Confirm lane assignments against member backgrounds and record them
- [ ] Submit the CarDD licence request
- [ ] Verify VehiDE original release terms, not only the Kaggle mirror
- [ ] Instantiate a per-claim leakage figure from GIA data or a defensible proxy, to strengthen §2.4
- [ ] Cite all GIA figures to source and confirm the 2025 statistics are final
- [ ] Build the canonical part vocabulary and its mapping tables
- [ ] Decide the multi-view strategy: CrashCar101 synthetic groups or single-view projection
- [ ] Confirm AGPL-3.0 implications before any use of Carparts-Seg
- [ ] Set the minimum support threshold for cost-band abstention empirically (§14.4)
- [ ] Validate the surveyor journey in §7 with a practising motor surveyor, or record that it is based on documented workflow only
- [ ] Agree the fixed dismissal-reason list in §8.3 across the team before the workbench is built
- [ ] Confirm the three in-scope screens in §8.10 against the demonstration plan

---

## References

- Wang, X., Li, W. and Wu, Z. *CarDD: A New Dataset for Vision-Based Car Damage Detection.* IEEE Transactions on Intelligent Transportation Systems 24(7), 2023. https://cardd-ustc.github.io/
- Huynh et al. *VehiDE Dataset: New dataset for Automatic vehicle damage detection in Car insurance.* IEEE, 2023; and *Powering AI-driven car damage identification based on VeHIDE dataset.* Journal of Information and Telecommunication 9(1), 2025.
- Humans in the Loop. *Car Parts and Car Damages Dataset* (CC0 1.0). https://humansintheloop.org/resources/datasets/car-parts-and-car-damages-dataset/
- Pasupa, K., Kittiworapanya, P., Hongngern, N. and Woraratpanya, K. *Evaluation of deep learning algorithms for semantic segmentation of car parts.* Complex & Intelligent Systems, 2021. https://github.com/dsmlr/Car-Parts-Segmentation
- Parslov, J., Riise, E. and Papadopoulos, D. P. *CrashCar101: Procedural Generation for Damage Assessment.* WACV 2024. https://crashcar.compute.dtu.dk/
- Šimsa, Š. et al. *DocILE Benchmark for Document Information Localization and Extraction.* ICDAR 2023. https://docile.rossum.ai/
- Park, S. et al. *CORD: A Consolidated Receipt Dataset for Post-OCR Parsing.* 2019.
- Huang, Z. et al. *ICDAR 2019 Competition on Scanned Receipt OCR and Information Extraction (SROIE).* ICDAR 2019.
- Jaume, G., Ekenel, H. K. and Thiran, J.-P. *FUNSD: A Dataset for Form Understanding in Noisy Scanned Documents.* 2019.
- van Ruitenbeek, R. E. and Bhulai, S. *Multi-view damage inspection using single-view damage projection.* Machine Vision and Applications 33(46), 2022.
- Hasan et al. *Vehicle Damage Detection Using Artificial Intelligence: A Systematic Literature Review.* WIREs Data Mining and Knowledge Discovery, 2025.
- Xie, E. et al. *SegFormer: Simple and Efficient Design for Semantic Segmentation with Transformers.* NeurIPS 2021.
- Huang, Y. et al. *LayoutLMv3: Pre-training for Document AI with Unified Text and Image Masking.* 2022.
