---
name: cmev-prepare-dataset
description: >-
  Inspect and prepare CLAIM-CMEV image, document, or repair-cost data for
  reproducible experiments, including provenance, label mapping and grouped
  splits. Use for dataset onboarding or conversion, not model fitting.
---

# Prepare CLAIM-CMEV data

Produce a documented dataset manifest, conversion output and reproducible split membership appropriate to the requested dataset.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), the [data workspace policy](../../../data/README.md), and the data lifecycle in the [technical specification](../../../docs/specs/technical_specification.md). Read [contracts](../../../docs/specs/data_contracts.md) and only the relevant module/data sections of the [proposal](../../../docs/CLAIM-CMEV_project_proposal_v1.md).

## Inspect before transforming

Confirm the provided source/path and requested use. Register the source release, original access/licence evidence, acquisition date, hashes, annotation format, usable counts and real/synthetic provenance. The proposal's recorded counts and licence descriptions are not verified source facts.

Use existing local inputs when supplied. Download only within the task's scope and permitted access; do not bypass licence/access requirements. If source files or permission evidence are missing, record the gap and prepare independent mapping/manifest work without claiming the dataset was processed.

Inspect representative examples and source grouping identifiers. Preserve raw originals and write derived output to the documented data directories.

## Normalise by data type

- **Images:** map part/damage labels to versioned canonical codes; retain unmapped classes and unknown sides. Validate dimensions, orientation, mask alignment and overlapping labels. Do not invent left/right labels from an unsided class.
- **Documents:** preserve original text, page IDs, boxes, rotations and row grouping. Separate survey reports from public business-document benchmarks. Group related synthetic templates together.
- **Costs:** inspect actual row and quote structure, operations, damage labels, money basis, currency, quantities and source status. Do not assume the planned price file exists or that all combinations occur. Preserve declared, agreed, approved and explicit synthetic-seed provenance; missing amounts/labels stay missing.

Use existing converters where available. Introduce deterministic scripts when the requested transformation benefits from reuse, and run them on representative inputs.

## Split without leakage

Identify exact/near duplicates and vehicle, scene, template, claim or synthetic base-case groups before fitting. Keep related samples in one partition. Correlated workshop quotes are not independent base cases.

Create train/validation/calibration where needed/test membership with opaque IDs, grouping policy, seeds and hashes. Honour official benchmark partitions where required; document conflicts or unknown grouping rather than silently claiming guaranteed separation. Fit preprocessing parameters only on eligible training data.

## Verify and deliver

Check manifest totals against outputs, schema/label coverage, excluded records, sample artifact alignment, group disjointness and reproducibility. Commit only safe manifests/configuration and tiny synthetic fixtures; keep raw claim material, large outputs and identifying metadata out of Git.

Report usable counts, exclusions, unresolved mappings, licence/access limitations and resulting paths. This workflow does not establish model accuracy. Update [CONTEXT.md](../../../CONTEXT.md) with the source/split versions and next handoff, excluding credentials and sensitive claim data.
