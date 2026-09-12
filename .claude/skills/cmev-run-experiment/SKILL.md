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

## Establish a bounded experiment

Identify the hypothesis/RQ, task, baseline/comparator, dataset/split manifests, checkpoint, configuration, intended metric and available hardware. Use the requested run budget or a documented bounded configuration. An experiment request is not an instruction to run an indefinite parameter search.

Check input availability, split/group separation and required dependencies. If execution is unavailable, produce the experiment definition and exact missing prerequisite; label it unrun. Do not silently substitute fixtures for model output, synthetic data for real data, or a different benchmark.

## Execute and preserve provenance

Record run ID, code revision and dirty-worktree status, effective config, environment/dependency versions, hardware, seed, source/split hashes, model/base-checkpoint identity and taxonomy/threshold/cost-table versions where relevant.

Use separate fitting, tuning/calibration and final test data. Compare methods on the same eligible cohort and documented matching/metric definitions. Report image quality, class, document domain and real/synthetic breakdowns where the question requires them.

Write generated outputs under the appropriate ignored artifact directories. Capture failure state and completed intermediate results. Resume a run only when inputs/configuration are compatible, and do not hide failed attempts from the experiment record.

## Interpret the result

Use proposal targets as initial targets, not guarantees. Do not retune thresholds on the final test set or lower a target silently to label a run successful.

- RQ1: show part/damage quality and coverage effects, including per-class failures.
- RQ2: compare joint-label synthetic augmentation with a controlled baseline and separate real-image transfer.
- RQ3: report duplicate reduction together with false merges, lost damage and summary F1.
- RQ4: vary comparable record support and report interval coverage/width and flag precision.

For document evaluation, retain the defined DocILE benchmark and report survey-domain results separately. For fusion, separate known-structured-input tests from full-pipeline results. Cost experiments on synthetic prices cannot support claims about real repair-price accuracy, savings or fraud.

## Deliver

Provide the run manifest/location, commands and outcomes, metric denominators, baseline comparison, failures/limitations and next justified experiment. Publish/promote a serving artifact only when that outcome is within the requested task; a successful experiment alone does not authorise changing the serving default.

Update [CONTEXT.md](../../../CONTEXT.md) with measured evidence and relevant specification TODOs. Keep checkpoints, detailed claim-level results and sensitive data outside committed documentation.
