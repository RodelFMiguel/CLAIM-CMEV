# Evaluation plan

Status: measurement plan for proposal v2. No experiment has been run. Every number below is a target, that is a hypothesis, not a result.
Owner: Lane 5 coordinates the harness and the service checks. Each module owner produces their own module results. Lane 4 owns the consolidation and cost experiments. Named members are unassigned.
Source: [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) sections 3.4, 11.5, 11.8, 12.2, 13.1 to 13.6. Module targets are copied unchanged from v2 section 13.1. Runtime checks follow [ADR 0002](../adr/0002-containerised-event-runtime.md).

Related documents: [product specification](product_specification.md) for requirement identifiers and safety invariants, [integration contracts](integration_contracts.md) and [data contracts](data_contracts.md) for the records under test, [model training specification](model_training_specification.md) for training and split mechanics, [application platform](application_platform.md) and [technical specification](technical_specification.md) for the runtime under test.

---

## 0. Standing notes

### 0.1 What this plan can and cannot establish

| It can establish | It cannot establish |
| --- | --- |
| Module accuracy on reserved held-out data | Performance on arbitrary workshop documents or real claim photographs |
| That the decision rules behave as specified on structured inputs | That the rules are correct for a real insurer's policy |
| Interval calibration under the documented synthetic price generator | Real repair-price accuracy |
| Anomaly precision and recall against injected labelled deviations | Fraud detection. The project has no fraud labels |
| Assisted review effort on a small pilot | Deployment savings or productivity gains |

An exceedance of a reference interval means an unusual **synthetic** price. It is not proof of a wrong price and never proof of fraud. This distinction is load-bearing and is repeated in [experiment C](#63-experiment-c-ordinary-price-calibration-and-injected-anomalies).

### 0.2 Where a large language model sits

No large language model sits in the decision path, so nothing in this plan measures one.

- Part and damage integration is deterministic mask overlap in M2, measured in [section 4](#4-module-metrics) as assignment accuracy.
- Vision and document integration is the deterministic rule set of v2 section 8 applied in M8, measured in [experiment A](#61-experiment-a-deterministic-rule-tests-on-structured-inputs).
- The optional stretch service `cmev-explainer` may turn an already-computed finding into a readable sentence. It is off by default. It never changes a result. Check SVC-17 exists only to prove that.

### 0.3 The runtime change and what it adds here

Proposal v2 section 9.1 planned one API process and one worker with a polled jobs table and no message broker. The team has directed a containerised, event-driven runtime instead, with Kafka on `cmev-kafka` as the transport. That is a change from v2 section 9.1.

It adds no domain metric. It adds the service checks SVC-09 to SVC-17 in [section 7.2](#72-transport-and-container-checks), and it moves that measurement effort into Lane 5's reserved integration and evaluation days.

[ADR 0002](../adr/0002-containerised-event-runtime.md) estimates about 11 extra person-days for the full runtime, which exceeds both Lane 5's 5 implementation days and the team's 5 contingency days. If that overrun happens it is reported against [SC-8](#14-success-criteria), not absorbed silently.

---

## 1. Research questions and the evidence that answers each

| RQ | Question | Evidence that answers it | Where |
| --- | --- | --- | --- |
| RQ1 | How accurate are part segmentation, damage segmentation and damage-to-part assignment? How do photograph quality and coverage affect decisions and withholding? | M1 and M2 held-out metrics with per-class results, the M2 assignment sample, and a bounded comparison of usable against degraded or cropped views grouped with their originals | [Section 4](#4-module-metrics), [section 5.4](#54-quality-and-coverage-comparison) |
| RQ2 | How accurately are exclusion and price-change marks detected and linked to rows, and how many corrections are needed to reconstruct the scope? | M6 detection and linking precision and recall before human correction on held-out team-marked pages, plus the count of corrections needed to reach the surveyor's intended scope | [Section 4](#4-module-metrics), [section 5.5](#55-correction-effort) |
| RQ3 | On a small labelled vehicle-group set, how useful is the conservative part summary compared with separate per-image observations? What opposite-side errors, incorrect groupings or lost observations remain? | M3 exploratory counts and named cases against a per-image baseline on held-out team vehicle groups. Reported as a case study with raw counts and examples | [Section 4](#4-module-metrics) |
| RQ4 | Under the documented synthetic price generator, how do interval coverage, width, anomaly-flag precision and withholding change with fewer independent reference cases? | Repeated M7 builds at decreasing independent base-case counts per key, with coverage, width, ordinary exceedance, injected-anomaly precision and recall, and withheld fraction | [Section 8](#8-rq4-fewer-reference-cases) |

RQ2 measures mark detection and linking. It does not require automatic handwriting recognition. Automatic amount-reading accuracy is measured only if stretch S2 is attempted, and is then reported separately.

---

## 2. Reporting rules

These rules apply to every table in this document. Breaking one invalidates the result.

| ID | Rule |
| --- | --- |
| RR-01 | Every percentage is published with its numerator and its denominator. A bare percentage is not a result |
| RR-02 | Automatic model and parser output is measured **before** any human correction. The reviewed outcome and the correction effort are reported separately, never merged into the automatic number |
| RR-03 | A shortfall is reported against the **original** target. Explaining a shortfall does not make it met. A revised target is recorded with its date and reason, and the original stays visible |
| RR-04 | Classes, test cases and cohorts are frozen before measurement. They are never narrowed after errors are seen |
| RR-05 | Fixture output is never reported as a model result (SI-09). Every result record names the artifact versions that produced it |
| RR-06 | For small samples, publish raw errors and example cases rather than claiming broad reliability. State the sample size in the same sentence as the claim |
| RR-07 | A discrepancy flag means an `unsupported` or a `cost_outlier` result. `insufficient_evidence` is informational and is never counted as a flag. Possible additions are counted separately |
| RR-08 | Withholding metrics are always published beside decision-rate metrics. An always-withhold system can pass a withholding target while helping nobody (v2 R12) |
| RR-09 | Thresholds and policies are selected on validation data, frozen, and only then applied to the untouched final test set. Final-test errors never drive retuning |
| RR-10 | Human corrections used in the final workflow demonstration never enter model training or threshold selection |

---

## 3. Experiment controls

Before any measurement run, freeze and record: data manifests and file hashes, group-aware split membership, code and configuration versions, model checkpoint hashes, taxonomy and vocabulary versions, threshold values, and the cost-table version. Record them in the run manifest described in [section 11](#11-evidence-artifacts).

Report these sources separately and never pool them: held-out public dataset images, team-photographed images, generated document pages, team-marked physical pages, and generated price records.

Run the deterministic rule tests first. Report the full-pipeline results separately, because extraction and vision errors move those numbers for reasons that have nothing to do with the rules.

---

## 4. Module metrics

Targets are copied unchanged from proposal v2 section 13.1. The denominator column is added here so that two people computing the same metric get the same number.

| Module | Metric | Target or reporting commitment | Evaluation data | Denominator rule | Owner |
| --- | --- | --- | --- | --- | --- |
| M1 parts | mIoU and per-class IoU | mIoU at least 0.60. No agreed supported panel class below 0.40 | Held-out HITL part split | Pixel intersection over union per class, averaged over the frozen supported panel class list. Background excluded from the mean. Class list frozen before measurement (RR-04) | Lane 1 |
| M2 damage | Semantic mIoU and per-class IoU | mIoU at least 0.45. All six CarDD categories reported, including dent, scratch and crack | Held-out CarDD split | Pixel intersection over union per damage category, averaged over all six. Instance annotations converted to semantic masks by the recorded overlap policy. Reported as semantic segmentation, never as instance segmentation | Lane 1 |
| M2 assignment | Correct part assignment, unknown or withheld rate, and errors by part and side | Report counts and errors. Do not infer this quality from the separate mask IoUs | Jointly labelled damage-to-part sample, about 30 to 50 photographs | Denominator is labelled damage regions in the sample. Three outcomes counted separately: correct part, wrong part, withheld as unknown. Side errors counted separately again | Lane 1 |
| M3 summary and coverage | Part-summary precision and recall, incorrect identity groupings, lost observations, decision and withholding rate | Exploratory counts and named cases against a per-image baseline. No universal duplicate-reduction claim | Held-out team vehicle groups, about 5 to 10 vehicles | Denominator is labelled physical parts per vehicle group for summary precision and recall, and labelled observations for lost observations. All views of one vehicle stay in one partition | Lane 1 |
| M4 OCR | Printed-text word error rate and exact printed-amount accuracy | Report by image quality and by whether the print is obscured | Team-marked pages | Word error rate over labelled printed tokens. Amount accuracy over labelled printed amount fields, exact decimal string match including currency. Obscured fields counted in their own bucket, not dropped | Lane 2 |
| M5 parser | Complete-entry F1, row association and source-box accuracy | F1 at least 0.75 on held-out variants within the supported layout families. Unsupported layouts and physical pages reported separately | Generated pages and team-photographed pages | A complete entry requires correct part and side where applicable, operation, quantity, currency and basis, and line amount. All of them, or the entry is wrong. Box matching tolerance fixed on validation | Lane 2 |
| M6 exclusion marks | Per-row detection and linking precision and recall, before human correction | Precision and recall at least 0.90. Raw counts by writer and template | Held-out team-marked pages | Denominator is labelled rows on held-out pages. A true positive needs the correct mark type **and** the correct row. An unlinked detection is not a true positive | Lane 3 |
| M6 price changes | Detection precision and recall, and correct row association, before correction | Detection precision and recall at least 0.85. Linking and correction counts reported | Held-out team-marked pages | Denominator is labelled price-change marks. Detection and linking scored separately so a detected but unlinked mark is visible in the numbers | Lane 3 |
| M7 cost ranges | Coverage, width, available-range rate and independent support | Nominal 90 percent coverage, initial target 85 to 95 percent on eligible final-test records. Per-support-group results reported | Held-out generated price records | Coverage denominator is eligible final-test records that received a range. The available-range rate has all eligible records as its denominator, so withholding stays visible. Width is reported as a distribution, not one mean | Lane 4 |
| Stretch S1 | Same metrics as M5 | Report only if attempted, separately from core results and from human correction | The same frozen document test cases as the parser | Identical to the M5 rule, so the comparison is paired | Lane 2 |
| Stretch S2 | Exact amount-read accuracy | Report only if attempted, separately from core results and from human correction | Held-out team-marked pages | Exact decimal string match on isolated price-change crops. An unreadable output is its own bucket, not a miss and not a hit | Lane 3 |

Notes that bind the whole table:

- Counts and denominators accompany every percentage (RR-01).
- Automatic performance is measured before the surveyor corrects anything (RR-02).
- A target is a hypothesis. A shortfall is reported against the original target (RR-03).
- Do not narrow reported classes or test cases after observing errors (RR-04).

---

## 5. Supporting measurements

### 5.1 Complete-entry scoring

Frozen before measurement: the required field list, the string normalisation used for comparison, the box overlap tolerance, and the currency and basis comparison rule. Money compares as an exact decimal string with its currency and cost basis. `980.00` SGD on the replacement basis does not equal `980.00` SGD on the repair basis.

### 5.2 Per-class reporting

M1 reports every supported panel class. M2 reports all six CarDD categories. Neither collapses a weak class into a mean and neither drops a class after seeing its score.

### 5.3 Domain checks

Team photographs are a separately reported domain check for M1 and M2. They are never pooled with the held-out public split, and a team-photograph result never substitutes for the held-out result.

### 5.4 Quality and coverage comparison

RQ1 includes a bounded comparison between usable views and degraded or cropped views. Degraded versions are grouped with their originals in the same partition, so the comparison does not leak. Report the decision rate and the withholding rate for each group, not only accuracy.

### 5.5 Correction effort

For RQ2, count the review actions needed to move from the automatic output to the surveyor's intended scope: mark confirmations, mark rejections, manually added marks, link corrections, amount entries and field corrections. Report the count per page and per claim, with denominators.

---

## 6. Three separate consolidation experiments

These three are separate on purpose. Their numbers are not comparable with each other and must never be averaged together.

### 6.1 Experiment A: deterministic rule tests on structured inputs

Build known observations, coverage states, rows, mark states and prices by hand. No models run. Every case has one specified outcome, and the outcome is required, not aspirational.

| Case family | Cases that must be covered |
| --- | --- |
| Evidence | Resolved adequate coverage with supporting damage. Resolved adequate coverage without supporting damage. Opposite-side evidence only. Uncertain identity. Uncertain damage. Sharp but cropped view. Missing photograph. Failed inference |
| Marks | Pending. Confirmed. Rejected. Missed and manually added. Unlinked with candidate rows. Unreadable revised amount. A pending price change that must not use the printed amount. Conflicting marks on one row |
| Extraction | Incomplete extraction. No rows parsed. Explicitly confirmed empty scope. An ambiguous row that could hide a match. A confirmed exclusion on the same part. A confirmed exclusion on the opposite side. Legitimate multiple operations on one part |
| Cost | Amount exactly on the lower bound. Amount exactly on the upper bound. Incompatible quantity. Incompatible currency. Incompatible cost basis. Absent range. Sparse range below the support minimum. Unknown vehicle class |
| Revisions | Corrected amount creating a new assessment. Corrected identity creating a new assessment. Corrected coverage creating a new assessment. Original findings preserved. Finalization against the current revision only |

Required outcomes:

- Every specified case returns its specified result. No exceptions and no partial credit.
- Withholding and exclusion handling is correct in every structured case.
- Clean structured cases with explicitly supported damage and compatible in-range amounts produce **zero** discrepancy flags.
- Each safety invariant SI-01 to SI-12 from the [product specification](product_specification.md#2-safety-invariants) maps to at least one case here.

These tests establish rule correctness. They establish nothing about model accuracy.

### 6.2 Experiment B: full pipeline on controlled claim cases

Pass real photographs and real document pages through the actual models and parser. Include clean cases and cases with labelled introduced changes: unsupported parts, omitted repairs, price changes and surveyor mark decisions.

A legitimate exclusion or a legitimate repricing is an expected workflow event. It is not automatically an anomaly. Keep every expected outcome independent of what the models predict.

| Measure | Target or reporting commitment | Denominator |
| --- | --- | --- |
| Precision of discrepancy flags on controlled cases | At least 0.70. `unsupported` and `cost_outlier` flags reported separately as well as together | All raised discrepancy flags |
| Recall by introduced discrepancy type and magnitude | Report separately with counts. No single pooled recall number | Introduced discrepancies of that type |
| False flags on constructed clean cases with known compatible in-range prices | Initial target at most 0.05 per case. Report the actual result and the case sizes | Constructed clean cases |
| Correct withholding for unphotographed parts | At least 0.95. Opposite-side, cropped and ambiguous cases also reported | Declared parts absent from all views |
| Decision rate on assessable entries | Report the fraction receiving a substantive check, with both the eligible and the total denominator (RR-08) | Eligible entries, and separately all entries |
| Possible additions | Separate precision and recall, plus the count of withheld ambiguities. Never folded into the discrepancy-flag counts (RR-07) | Labelled additions that should have been raised |
| Photo-check and cost-check completion | Reported separately, with counts withheld for identity, coverage, document and reference reasons | Entries reaching each check |
| Human assistance | Corrections and outcomes before and after review, reported separately (RR-02) | Review actions per case |

Both safety cases and assessable positive and negative cases are required. A test set of only safety cases proves nothing about usefulness.

### 6.3 Experiment C: ordinary price calibration and injected anomalies

Two populations, measured separately.

**Ordinary prices.** Sample ordinary records from the documented generator. Measure empirical interval coverage and the exceedance rate. A calibrated 90 percent interval leaves about 10 percent of ordinary prices outside it. Eight eligible ordinary-price rows therefore give about 0.8 expected interval exceedances per case. The "at most 0.05 false flags per case" target of experiment B **does not apply** to this population. Mixing the two is the single easiest way to misreport this project.

**Injected anomalies.** Separately inject labelled deviations into independent ordinary base cases, at several change magnitudes. Report anomaly precision and recall at a stated anomaly prevalence. Keep ordinary tail values in the analysis. Do not define every price outside the learned interval as a true anomaly.

| Measure | Reporting commitment |
| --- | --- |
| Empirical coverage of the nominal 90 percent interval on ordinary records | Report with counts, overall and per support group |
| Ordinary exceedance rate per case | Report as a distribution, with the expected value stated for comparison |
| Interval width | Report as a distribution per key group. A narrow interval that loses coverage is a failure, not a success |
| Injected-anomaly precision and recall by magnitude | Report at the stated prevalence, with the prevalence in the same table |
| Withheld keys and entries | Report the fraction with no available range and the reason |

---

## 7. End-to-end and service checks

### 7.1 Workflow and record checks

From proposal v2 section 13.3, unchanged in substance.

| ID | Check | Required evidence |
| --- | --- | --- |
| SVC-01 | Unseen case: photographs and a marked estimate through the overview, corrections and PDF | Actual core model and parser outputs, with the amount of human assistance recorded |
| SVC-02 | Evidence links | Correct original photograph, mask, page and mark or row box. Coordinate transforms preserved |
| SVC-03 | Job failure and retry | Visible incomplete state, no duplicate rows, no false clean assessment |
| SVC-04 | New inputs or a decision-changing confirmation | New input and assessment revision, explicitly reused artifacts with lineage, recomputed findings, original results retained |
| SVC-05 | Idempotent review and stale edit | A repeated save returns the same action and result. A stale conflicting write cannot silently overwrite a newer review |
| SVC-06 | Pending marks and finalization | No premature exclusion, no printed-price fallback for pending repricing, no print of stale findings |
| SVC-07 | Version pinning and historical rendering | Stored versions and the exact review snapshot explain the printed result. A later cost table never changes it |
| SVC-08 | Stretch S3, only if attempted | Synthetic final approvals eligible for a new build. Unapproved, duplicate and superseded rows excluded. The old assessment unchanged |

### 7.2 Transport and container checks

New checks created by the runtime change of [section 0.3](#03-the-runtime-change-and-what-it-adds-here). They do not exist in proposal v2.

| ID | Check | Method | Required evidence |
| --- | --- | --- | --- |
| SVC-09 | Duplicate message delivery | Republish an identical work message to the same topic | Row counts unchanged. One result, not two. The consumer is idempotent on the job key (FR-53) |
| SVC-10 | Consumer restart mid-processing | Stop and restart `cmev-worker-ocr`, and separately `cmev-consolidator`, while a claim is in flight | Exactly one complete result. No lost claim and no duplicated committed result (FR-57) |
| SVC-11 | Dead-letter routing | Feed a message that fails deterministically until retries are exhausted | The message lands on the dead-letter topic. The claim shows incomplete. It never shows clean (FR-55, SI-05) |
| SVC-12 | Broker unavailability | Stop `cmev-kafka`, submit an upload, then restart the broker | `cmev-api` reports a clear state rather than silently dropping work. Accepted claims resume and none is lost (FR-56) |
| SVC-13 | Message for a superseded input revision | Hold a revision-1 completion message, create revision 2, then release the held message | The revision-1 result is stored against revision 1. The current assessment is untouched (FR-54, SI-06) |
| SVC-14 | Compose profiles | Start the `lean` profile, then the `full` profile, on the recorded demonstration machine | Both start. `lean` completes the demonstration. `full` runs every module container (FR-58) |
| SVC-15 | Profile equivalence | Run the same claim under `lean`, where `cmev-worker-combined` registers every handler in one process, and under `full` | Identical findings, states and amounts under both profiles. A difference is a defect, not a profile characteristic (ADR 0002) |
| SVC-16 | Out-of-order branch completion | Deliver the document-branch completion before the image-branch completion, then reverse the order | The same assessment results either way. Consolidation waits for both branches or records the missing branch explicitly |
| SVC-17 | Explainer neutrality, only if the stretch service is attempted | Run the same claim with `cmev-explainer` off and on | Findings, states and amounts are identical. Only the readable sentence differs (FR-59) |

Record the measured startup time, peak memory and per-stage latency for the `lean` profile on the demonstration machine. No latency target is assumed before that measurement (NFR-07).

### 7.3 Course evidence: combined against single branch

On the same controlled cases, compare the combined pipeline with checks that use only one branch's information. State which questions each branch can answer alone. When an input branch is absent, withhold the cross-modal decision. Absence of a branch is not evidence.

Report the incremental checks and the incremental errors. Do not invent a standalone document-only damage judgement in order to have something to compare against.

---

## 8. RQ4: fewer reference cases

Reduce the number of independent training base cases per eligible key, in documented steps. Keep the validation, calibration and test groups separate at every step. Compare empirical percentiles against the bounded LightGBM method.

| Reported at each step | Note |
| --- | --- |
| Interval coverage | Against the nominal 90 percent |
| Interval width | As a distribution |
| Ordinary-price exceedance rate | From the ordinary population of experiment C |
| Injected-anomaly precision and recall | From the injected population of experiment C, at the stated prevalence |
| Fraction of keys with no available range | Withholding must stay visible |
| Fraction of entries receiving no cost check | The user-visible consequence of withholding |

Select the minimum independent support threshold on validation data, freeze it, and only then report final-test results (RR-09). If the LightGBM comparator is dropped under contingency, name the missing comparison and limit the conclusion to the empirical method. Do not invent model results.

---

## 9. Usability

Two members from outside the implementing lane review three cases each. Practising surveyors are used if available. Otherwise the team members' familiarity with the system is disclosed together with the small sample size.

| Measured | How |
| --- | --- |
| Review time per case | Wall clock, recorded per case |
| Edits to the proposed list | Counted by action type |
| Mark-link corrections | Counted separately from other edits |
| Amount entry | Count and time |
| Finding comprehension | The reviewer states what a finding means before seeing the explanation. Agreement recorded |
| Evidence access | Taps or clicks to reach the original photograph and the source page box |

Honest limits, stated in the report every time these numbers appear:

- Team members are not practising surveyors. Their familiarity with the system inflates speed and comprehension.
- Six reviewed cases is a pilot, not a sample.
- A reduced-effort claim requires comparable manual cases with alternated manual and assisted order. Without that baseline, report assisted-review effort only and make no comparison.
- Nothing here supports a deployment saving estimate.

---

## 10. Splits and leakage discipline

From proposal v2 section 11.8. Reserve final-test membership before fitting any model, tuning any parser rule or selecting any threshold, ideally by day 2. Freezing release versions later is not permission to choose test examples after seeing results.

| Data | Grouping rule | Leakage risk to check |
| --- | --- | --- |
| HITL and CarDD | Record train, validation and test manifests with hashes. Inspect publisher partitions where available. Group related vehicle or source images where identifiers exist | Cross-source overlap and near duplicates. Image hashes alone do not exclude related-view leakage. Disclose missing group metadata |
| Damage-to-part sample | No overlap with either segmentation training set. Separate pilot development examples from final cases | An image used for training cannot score assignment |
| Vehicle groups | All views of one vehicle stay in one partition | One vehicle split across partitions makes summary results meaningless |
| Generated documents | Group the writer, the physical page, the generated base page and related template variants | A template variant in both partitions makes the parser look better than it is |
| Team-marked pages | Allocate writer and template groups to development, validation and final test **before** collection. With three writers, one supplies each group, and that limited writer diversity is reported | Retuning the parser or detector on final pages |
| CORD, only if S1 starts | Use publisher splits. Reserve the project's final document tests for the same comparison the parser uses | An unpaired comparison |
| Prices | Keep every quote from one base case together. Disjoint training, validation, calibration and final-test partitions with documented cutoffs | Repeated quotes from one base case inflating the independent support count |

Report supported-layout-family performance separately from unseen families. A known layout family is not evidence of performance on arbitrary workshop documents.

---

## 11. Evidence artifacts

Every run writes to `artifacts/evaluation/<run_id>/`, where `run_id` is `<yyyy-mm-dd>-<experiment>-<short-commit>`.

| Path | Contents |
| --- | --- |
| `artifacts/evaluation/<run_id>/manifest.json` | Run identity, git commit, dataset manifest hashes, split membership hashes, model and cost-table versions, threshold values, seeds, hardware, container image tags |
| `artifacts/evaluation/<run_id>/metrics.json` | Every metric with its numerator, denominator and target, and a met or unmet flag |
| `artifacts/evaluation/<run_id>/per_case/` | One record per test case: inputs referenced by identifier, expected outcome, actual outcome, pass or fail |
| `artifacts/evaluation/<run_id>/errors/` | Named failure examples with references back to the evidence. No raw claim material is committed |
| `artifacts/evaluation/<run_id>/service_checks.json` | SVC-01 to SVC-17 results with timestamps and container image tags |
| `artifacts/evaluation/<run_id>/notes.md` | Deviations from this plan, limitations and any target revision with its date and reason |
| `artifacts/models/<model_id>/<version>/` | Model manifest with its own evaluation section, referenced by the run manifest |
| `artifacts/cost_tables/<version>/` | Build membership, exclusions, independent support counts and calibration results |

Only manifests and aggregate results are tracked in version control. Datasets, checkpoints, raw outputs and claim material stay in the ignored locations (NFR-09).

---

## 12. Where each class of check lives

Matches the existing scaffold directories. No new top-level test directory is proposed.

| Directory | Class of check | Examples | Owner |
| --- | --- | --- | --- |
| `tests/unit` | One function or class, no I/O, no container | Mask overlap thresholding, decimal string parsing, header alias matching, coverage state transitions | Module owners |
| `tests/contracts` | Record shape and field discipline against the [data contracts](data_contracts.md) and [integration contracts](integration_contracts.md) | Required fields present, enum values valid, every null carries a reason, money carries currency and cost basis, version fields populated, schema compatibility across a change | Lane 5 with every producer |
| `tests/integration` | Two or more modules over the real transport, using fixtures | A worker consumes its topic and writes its result, `cmev-consolidator` recomputes after a correction, determinism check for NFR-06 | Lane 5 with module owners |
| `tests/e2e` | The whole system under compose, through `cmev-web` and `cmev-api` | SVC-01 to SVC-17, the unseen case to PDF, the offline `lean` rehearsal | Lane 5 |
| `tests/evaluation` | Metric computation and experiment runners | Experiment A runner, experiment B and C runners, RQ1 to RQ4 scripts, metric definitions with their denominators | Lane 4 with module owners |
| `tests/fixtures/synthetic` | Labelled fixture inputs and expected outputs, all marked as fixtures | Experiment A structured rule cases, generated estimate pages, generated marked pages, generated price records | Lanes 3 and 4 |

Experiment A case data lives in `tests/fixtures/synthetic/rule_cases/` and its runner in `tests/evaluation/`. A fast subset covering the safety invariants also runs in `tests/integration` so that a regression is caught before an evaluation day.

---

## 13. Evaluation schedule

| Day | Evaluation work | Gate |
| --- | --- | --- |
| 1 to 2 | Reserve final-test membership. Freeze vocabulary, cost basis and records. Write the first experiment A cases against the fixtures | Test membership reserved before any tuning (RR-09) |
| 3 to 4 | Run experiment A continuously as rules land. Record candidate model quality, clearly labelled as candidate, not final | Early inference is not final evaluated performance |
| 5 | Core feature cutoff. Review validation results and remaining effort before any stretch goal starts | A stretch experiment never becomes a baseline dependency |
| 6 | Complete integration and recovery checks, validation and any separate calibration. Freeze release configurations, thresholds and artifacts | Nothing is tuned after this point |
| 7 | Run the untouched module and pipeline test sets, the exploratory grouped-view cases, SVC-01 to SVC-17 and the usability sessions. Record denominators, failures and corrections | Final test data is touched exactly once |
| 8 | Contingency. Record any repeated test exposure and any changed version | A repeated exposure is disclosed, not hidden |
| 9 to 10 | Write up results, shortfalls, limitations and omitted stretch work | Shortfalls reported against original targets (RR-03) |

---

## 14. Success criteria

Repeated from the [product specification](product_specification.md#16-success-criteria) so the measurement owner sees them beside the metrics. SC-2 includes the transport checks SVC-09 to SVC-17.

| ID | Criterion | Evidence that closes it |
| --- | --- | --- |
| SC-1 | Unseen case completes upload to PDF with real model output | SVC-01 result plus the recorded human assistance count |
| SC-2 | Required deterministic safety and service checks pass | Experiment A full pass, SVC-01 to SVC-17 results |
| SC-3 | Module and full-pipeline targets measured on reserved test data, each reported met or unmet | [Section 4](#4-module-metrics) and [experiment B](#62-experiment-b-full-pipeline-on-controlled-claim-cases) tables with denominators |
| SC-4 | Withholding on unphotographed parts reaches at least 0.95, with decision rates also reported | Experiment B withholding and decision-rate rows |
| SC-5 | At least three required course aspects demonstrated under the confirmed rubric mapping | Rubric mapping record plus the referenced results |
| SC-6 | RQ1, RQ2 and RQ4 measured. RQ3 delivered as a labelled exploratory case study with stated gaps | [Section 1](#1-research-questions-and-the-evidence-that-answers-each) evidence column |
| SC-7 | Original extractions, reviewed values and final approvals stay separate, with versions retained | SVC-04, SVC-07 and the contract tests |
| SC-8 | Work fits the 50-person-day budget, or the overrun and resulting scope reductions are reported | Recorded actual effort per lane |

Functional completion, measured model performance and budget adherence are reported separately. None of them implies production readiness or real-price validation.

---

## 15. Evaluation tasks

- [ ] Reserve final-test membership for every dataset before any tuning, and record the manifests and hashes.
- [ ] Freeze the supported panel class list for the M1 floor, and the six damage categories for M2.
- [ ] Freeze complete-entry scoring, box tolerances and money comparison before any M5 measurement.
- [ ] Write the experiment A case set and map each safety invariant SI-01 to SI-12 to at least one case.
- [ ] Build the experiment B controlled claim cases with outcomes labelled independently of model predictions.
- [ ] Build the experiment C ordinary and injected populations as two separate generators, and document the prevalence used.
- [ ] Implement SVC-01 to SVC-17, including the transport checks SVC-09 to SVC-17 added by the runtime change.
- [ ] Record the `lean` profile startup time, peak memory and per-stage latency on the demonstration machine.
- [ ] Select every threshold on validation data and record the frozen value with its date.
- [ ] Define the run manifest schema and confirm every experiment writes one.
- [ ] Recruit usability reviewers from outside the implementing lane and record their familiarity.
- [ ] Record results, shortfalls against original targets, denominators and limitations in `artifacts/evaluation/`.
