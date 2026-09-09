# Evaluation and demonstration plan

Owner: Lane 5 coordinates the harness; each module owner supplies results. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), section 14. Targets below are initial project targets, not measured results.

## Test data and experiment controls

Freeze data manifests, group-aware splits, code/configuration versions, model hashes, mappings, and cost-table versions before evaluation. Keep vehicle groups, related synthetic views, report templates, and synthetic base price cases out of multiple partitions. Use separate calibration data where the interval method requires it. Record limitations when original grouping identifiers are absent.

Report real photographs, synthetic views, public business documents, synthetic survey reports, and any real survey reports separately. Synthetic costs demonstrate logic/calibration only. No experiment establishes real fraud detection, workshop price accuracy, or financial savings.

Run deterministic comparison cases on known structured inputs first; report full-pipeline results separately because extraction/vision errors affect the outcome.

## Module metrics

| Owner | Metric | Initial acceptance target |
| --- | --- | --- |
| M01 | mIoU and per-class IoU | mIoU >= 0.60; no panel class below 0.40 |
| M02 | mIoU, per-class IoU, AP | mIoU >= 0.45; separate dent, scratch, cracked results |
| M03 | Duplicate reduction and vehicle-summary F1 | At least 90% duplicate reduction; report incorrect merges and lost damage |
| M04 | Text/field quality and page/source localisation | Establish baseline; no numeric proposal target |
| M05 | Complete-entry F1 and source-location accuracy | F1 >= 0.75 on DocILE line-item evaluation; survey results separate |
| M06 | Nominal 90% interval coverage and average width | Coverage within 5 percentage points of nominal (85%-95%) |
| M07-M08 | Controlled discrepancy metrics below | Use proposal targets and ablation results |
| M09 | Proposed-list edits, usability, persistence, evidence navigation | Record observations and pass functional/recovery scenarios |

Specify supported panel classes before applying the M01 floor. Document AP convention, matching tolerances, class averaging, and confidence thresholds before measurement. Do not silently replace DocILE's evaluator with a custom repair-row metric.

For M03, measure redundant observations removed relative to labelled duplicate observations, and separately count distinct damage instances lost or incorrectly merged. Freeze the duplicate-matching definition before reporting reduction. Record vehicle-summary F1 as well as duplicate reduction to prevent an over-merging system looking successful.

## Comparison scenarios and targets

| Scenario group | Cases | Metric/expected outcome |
| --- | --- | --- |
| Clean declarations | Supported damage with compatible in-range costs | Mean false flags <= 0.05 per clean case |
| Unsupported parts | Adequate coverage with no visible damage, including adjacent panels | Flag precision >= 0.70 across discrepancy flags; recall by type |
| Cost changes | Amounts below/above bounds at multiple deviation sizes | Recall versus change size; equality at bounds is within range |
| Missing repairs | Visible damage absent from declarations | Recall/precision separately; candidate has no invented amount |
| Unphotographed parts | Declared part absent from all views | Correct withholding >= 0.95 |
| Weak evidence | Blur, obstruction, uncertain side/part/amount, unreadable pages | Insufficient evidence with the right reason; no confident unsupported result |
| Weak reference | Missing range, sparse counts, incompatible unit/currency/operation | Withheld cost check; successful photo check remains visible |
| Explicit parts grade | Grade fields present in a constructed example | Test incompatibility; a price change alone is not grade-substitution evidence |

Publish counts, denominators, class/source breakdowns and uncertainty estimates where sample sizes permit. Define a flag as an `unsupported` or `cost_outlier` entry; report proposed additions separately. Missing-evidence informational results are not discrepancy flags.

## Research questions and comparisons

| Question | Experiment | Required evidence |
| --- | --- | --- |
| RQ1 | Part/damage and summary performance by image quality and coverage | Per-class metrics and degradation by quality/coverage |
| RQ2 | Comparable training runs with and without synthetic joint part/damage labels | Damage-to-part matching results on held-out real photos; synthetic results separate |
| RQ3 | Aggregation versus unmerged/per-image baseline | Duplicate reduction, false merges, lost damage, summary F1 |
| RQ4 | Reduce eligible comparable cost records progressively | Flag precision, interval coverage/width, chosen minimum-support threshold |

Compare the structured combined pipeline with image-only and document-only checks on the same eligible cases. Keep joint image-text and shared-representation alignment experiments separate from the required structured pipeline. Record implementation/data limits if a comparison cannot be supported; do not infer superiority without measured results.

## Service and review checks

- Upload -> matching-revision branch results -> assessment -> evidence -> saved review -> structured export.
- Photo-only preparation followed by report or structured declarations.
- Worker failure/retry with no duplicate records; stale results cannot replace current results.
- Review timeout after commit, reconnect/replay, and concurrent-edit conflict.
- Original image/mask and report-location coordinate correctness.
- Dismissal survives reload on the same assessment; changed evidence creates a new assessment.
- Agreed-only costs excluded; duplicate and superseded approvals not double-counted.
- Historical assessment reproduced using stored inputs and pinned versions.
- Private deployment starts on recorded hardware and restores database plus artifacts.

## Preliminary usability protocol

Use comparable manual and assisted cases, alternate order, and record reviewer familiarity. Prefer practising surveyors; otherwise use team members outside the implementing lane and disclose the limitation. Capture review time, edits to the proposed list, finding comprehension, actions to dismiss with a reason, and original evidence access. Measure timeouts/retry behaviour as well as the happy path.

## Demonstration script

1. Freeze and display the run manifest, including synthetic cost provenance.
2. Process an unseen held-out claim; show image summary and complete comparison.
3. Inspect originals, overlays, and report locations; correct an entry and save a review.
4. Demonstrate missing evidence and one failed/retried job without a false clean result.
5. Reconnect an interrupted review save and export the exact reviewed revision.
6. Import a separate synthetic final approval; repeat the import to show deduplication.
7. Build/evaluate/promote a new reference version; prove the old assessment is unchanged.

## Tasks

- [ ] Freeze supported classes, definitions, matching rules, splits, and eligible case cohorts.
- [ ] Create small synthetic contract fixtures and held-out discrepancy generators.
- [ ] Build separate structured-input and full-pipeline runners.
- [ ] Collect module baselines and document any target revision before final testing.
- [ ] Run quality, joint-label, multi-view, and reference-sparsity experiments.
- [ ] Run combined/single-branch and research comparator evaluations.
- [ ] Execute platform, evidence, review, and approval-refresh checks.
- [ ] Conduct usability sessions and record participant limitations.
- [ ] Archive run manifests and safe aggregate reports; record shortfalls and final acceptance.
