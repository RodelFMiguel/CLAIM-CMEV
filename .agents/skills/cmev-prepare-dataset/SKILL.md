---
name: cmev-prepare-dataset
description: >-
  Inspect and prepare CLAIM-CMEV image, document, or repair-cost data for
  reproducible experiments, including provenance, label mapping and grouped
  splits. Use for dataset onboarding or conversion, not model fitting.
---

# Prepare CLAIM-CMEV data

Produce a documented dataset manifest, conversion output and reproducible split membership appropriate to the requested dataset.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), the [model training specification](../../../docs/specs/model_training_specification.md) for source roles, conversions, labels and splits, and the [data workspace policy](../../../data/README.md). Open the selected module specification from the [index](../../../docs/specs/README.md). Use the [data contracts](../../../docs/specs/data_contracts.md) for output records, the [technical specification](../../../docs/specs/technical_specification.md) for data lifecycle/storage and the [evaluation plan](../../../docs/specs/evaluation_plan.md) for reserved evaluation cohorts. Consult proposal sections 11 and 12.3-12.4 for scope and contingency rationale.

Use the current specifications below for implementation details and acceptance criteria. [Proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope; [ADR 0002](../../../docs/adr/0002-containerised-event-runtime.md) records the later runtime change. Read the sections relevant to the task, preserve proposed versus accepted status, and reconcile concrete conflicts with current user decisions. A specification is not evidence that its behaviour is implemented.

## Select the scoped data

Core data are HITL parts, CarDD damage subject to consent/files, generated estimates/marks, team-marked physical pages, independently labelled damage-to-part/vehicle-group samples and generated prices. The existing [acquisition catalogue](../../../docs/dataset-downloads.md) covers older scope: choose explicit eligible sources instead of treating its default set as v2 requirements. Check the current tool's selectors before executing it.

CORD preparation and OCR/training-label alignment belong only to stretch S1; TrOCR crop evaluation is S2. DocILE, CrashCar101, VehiDE, DSMLR, SROIE, FUNSD and IAM are not core commitments. HITL damage is the documented access contingency, with a distinct taxonomy, split and revised targets, not an additional dataset beside CarDD. A submitted CarDD request is not consent or acquired files.

## Inspect before transforming

Confirm the provided source/path and requested use. Register the source release, original access/licence evidence, acquisition date, hashes, annotation format, usable counts and real/synthetic provenance. The proposal's recorded counts and licence descriptions are not verified source facts.

Use existing local inputs when supplied. Download only within the task's scope and permitted access; do not bypass licence/access requirements. If source files or permission evidence are missing, record the gap and prepare independent mapping/manifest work without claiming the dataset was processed.

Inspect representative examples and source grouping identifiers. Preserve raw originals and write derived output to the documented data directories.

## Normalise by data type

- **Images:** preserve the 21 HITL part categories and unresolved side. Convert CarDD instance annotations to semantic masks for dent, scratch, crack, glass shatter, lamp broken and tire flat, recording overlap/background handling; unannotated or unsupported damage is not a confident negative. Validate orientation, dimensions and mask alignment. Keep assignment labels and physical-part/coverage labels for the team pilot separate from segmentation targets; separate mask datasets do not establish matching accuracy.
- **Documents:** retain printed text/amounts, page/row/mark IDs, boxes, transforms, mark-to-row associations and intended decisions/amounts independent of predictions. Preserve uncertain/unlinked/conflicting examples. Develop the parser for 2-3 documented layout families; reserve held-out variants and report unseen families separately. Record the actual OCR box granularity. For S1 only, follow v2 section 12.3: preserve CORD splits, map available fields without inventing repair-operation labels, and mask/flag ambiguous OCR token alignment before two-stage training.
- **Costs:** use the v2 section 11.6 reproducible generator; the unsupplied v1 price file is no longer a dependency. Record seed/version, eligible part/operation/class/currency keys, fixed single-part SGD basis, base-case/workshop/quote IDs and synthetic dates. Year, side and damage type are not price-generation features. Separate ordinary prices from injected deviations with independent labels; repeated quotes do not add independent support. Preserve declared/agreed/approved/synthetic provenance and missing values.

Use existing converters where available. Introduce deterministic scripts when the requested transformation benefits from reuse, and run them on representative inputs.

## Split without leakage

Reserve final-test membership before tuning models, parser rules or thresholds. Identify exact/near duplicates and vehicle, source, writer, physical page, generated base page, related template, claim or synthetic base-case groups before fitting. All photographs/augmentations of a page or vehicle stay together; assignment/vehicle evaluation samples stay out of both segmentation training sets. Report missing grouping metadata and limited writer/vehicle diversity. Keep related samples in one partition. Correlated workshop quotes are not independent base cases.

Create train/validation/calibration where needed/test membership with opaque IDs, grouping policy, seeds and hashes. Honour official benchmark partitions where required; document conflicts or unknown grouping rather than silently claiming guaranteed separation. Fit preprocessing parameters only on eligible training data.

## Verify and deliver

Check manifest totals against outputs, schema/label coverage, excluded records, sample artifact alignment, group disjointness and reproducibility. Commit only safe manifests/configuration and tiny synthetic fixtures; keep raw claim material, large outputs and identifying metadata out of Git.

Report usable counts, exclusions, unresolved mappings, licence/access limitations and resulting paths. This workflow does not establish model accuracy. Update [CONTEXT.md](../../../CONTEXT.md) with the source/split versions and next handoff, excluding credentials and sensitive claim data.
