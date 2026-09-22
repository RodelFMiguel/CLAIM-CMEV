---
name: cmev-run-experiment
description: >-
  Run or reproduce a defined CLAIM-CMEV training, evaluation, calibration, or
  ablation experiment with versioned inputs and measured results. Use for model
  experiments and RQ1-RQ4 evidence, not routine unit tests or open-ended tuning.
---

# Run a reproducible CLAIM-CMEV experiment

Produce a reproducible run record and an evidence-based interpretation of the requested experiment.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), the [evaluation plan](../../../docs/specs/evaluation_plan.md), the relevant module specification from the [index](../../../docs/specs/README.md), and [pipeline guidance](../../../pipelines/README.md). Use the current implementation and accepted configuration, not a guessed command.

Use [proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) for current scope and the [workflow guide](../../../docs/agent-workflows.md#scope-and-specification-transition) for module mapping and specification-transition limits. Read only the v2 sections relevant to this task; unmigrated v1 content does not override v2. Check newer explicitly recorded user decisions and their affected specifications before applying runtime assumptions.

## Establish a bounded experiment

Identify the hypothesis/RQ, task, baseline/comparator, dataset/split manifests, checkpoint, configuration, intended metric and available hardware. Use the requested run budget or a documented bounded configuration. An experiment request is not an instruction to run an indefinite parameter search.

Use v2 sections 3.4 and 13 for RQs and evaluation, and section 12 for the bounded budget and contingencies. S1 LayoutLMv3/CORD/alignment, S2 TrOCR and S3 approval refresh start only under the section 12.3 stretch gate; protect contingency/reporting allocations and keep the parser baseline available. Check input availability, split/group separation and required dependencies. If execution is unavailable, produce the experiment definition and exact missing prerequisite; label it unrun. Do not silently substitute fixtures for model output, synthetic data for real data, or a different benchmark.

## Execute and preserve provenance

Record run ID, code revision and dirty-worktree status, effective config, environment/dependency versions, hardware, seed, source/split hashes, model/base-checkpoint identity and taxonomy/threshold/cost-table versions where relevant.

Use separate fitting, tuning/calibration and final test data. Compare methods on the same eligible cohort and documented matching/metric definitions. Report image quality, class, document domain and real/synthetic breakdowns where the question requires them.

Write generated outputs under the appropriate ignored artifact directories. Capture failure state and completed intermediate results. Resume a run only when inputs/configuration are compatible, and do not hide failed attempts from the experiment record.

## Interpret the result

Use proposal targets as initial targets, not guarantees. Do not retune thresholds on the final test set or lower a target silently to label a run successful.

- RQ1: report HITL part and CarDD semantic damage metrics separately, then damage-to-part assignment on independently labelled held-out examples. Include usable versus degraded/cropped views, grouping variants with originals.
- RQ2: measure exclusion/price-change detection and row-linking on held-out team-marked physical pages, before human correction; report correction effort separately. Exact amount reading applies only to S2.
- RQ3: compare conservative part summaries with per-image observations on labelled vehicle groups. Report incorrect identity groupings, lost observations, summary precision/recall and decision/withholding rates as an exploratory pilot, with automatic and assisted results separate. Do not promise physical-damage deduplication.
- RQ4: reduce independent training base cases per eligible cost key; compare empirical percentiles with bounded LightGBM. Report interval coverage/width, ordinary-price exceedances, injected-anomaly precision/recall at stated prevalence and range availability/withholding. Freeze support thresholds on validation.

For M4/M5, report printed-text/amount accuracy and complete-entry F1 with row/box matching tolerances frozen on validation. Separate supported-family variants, unseen layouts and photographed pages; DocILE is not a v2 benchmark requirement. For S1, compare the candidate and parser on the same frozen project tests, select serving on validation, and do not attribute gains to CORD without a controlled comparator.

Keep the three v2 section 13.2 experiments distinct:

- Structured rule tests establish deterministic behaviour, including zero discrepancy flags for supported, compatible in-range clean inputs; they are not model results.
- Controlled full-pipeline cases use real models/parser and independent expected outcomes. Report unsupported and cost flags, possible additions, substantive-decision rates and reason-specific withholding separately. Legitimate exclusions/repricing are workflow events, not automatic anomalies.
- Ordinary synthetic-price calibration measures coverage and tails; separately injected deviations measure anomaly detection. A nominal 90% interval permits about 10% ordinary exceedances. The controlled clean-case target does not apply to that ordinary population, and interval exceedance is not itself an anomaly label.

Cost experiments cannot establish real repair-price accuracy, savings or fraud. For usability, follow v2 section 13.5: report assisted time/edits and pilot limits; an effort-reduction claim needs comparable manual cases and alternated order.

## Deliver

Provide the run manifest/location, commands and outcomes, metric denominators, baseline comparison, failures/limitations and next justified experiment. Publish/promote a serving artifact only when that outcome is within the requested task; a successful experiment alone does not authorise changing the serving default.

Update [CONTEXT.md](../../../CONTEXT.md) with measured evidence and relevant specification TODOs. Keep checkpoints, detailed claim-level results and sensitive data outside committed documentation.
