---
name: cmev-refresh-cost-reference
description: >-
  Build and validate CLAIM-CMEV synthetic reference-cost tables, or execute a
  requested approval-refresh stretch workflow, preserving independent support,
  lineage and pinned tables. Use for reference builds, not checking one amount.
---

# Refresh a CLAIM-CMEV cost reference

Produce an evaluated candidate reference build with traceable membership. Keep candidate creation and changing the serving default as distinct outcomes.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), [M7 reference cost ranges](../../../docs/specs/module-07-reference-cost-ranges.md), the cost sections of the [model training specification](../../../docs/specs/model_training_specification.md), [data contracts](../../../docs/specs/data_contracts.md), and [evaluation plan](../../../docs/specs/evaluation_plan.md). Use [M8 consolidation](../../../docs/specs/module-08-consolidation-checks.md) for consuming/pinning the table and the [technical specification](../../../docs/specs/technical_specification.md) for artifact publication. For approval refresh, also read the [platform specification](../../../docs/specs/application_platform.md) and stretch S3 in proposal section 12.3.

Use the current specifications below for implementation details and acceptance criteria. [Proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope; [ADR 0002](../../../docs/adr/0002-containerised-event-runtime.md) records the later runtime change. Read the sections relevant to the task, preserve proposed versus accepted status, and reconcile concrete conflicts with current user decisions. A specification is not evidence that its behaviour is implemented.

## Establish the requested operation

Core work builds immutable tables from the v2 synthetic generator. Importing final approvals and demonstrating refresh is optional S3; do not make an approval importer or the unsupplied v1 price file a core dependency. For S3, check its documented capacity gate; the core report may describe it without executable implementation. Determine whether the task requests planning/validation, a candidate build, or promotion/rollback. Use the existing implementation, current table pointer and configured eligibility/calibration policy. Do not invent dataset rows, a supported combination, a minimum-support threshold or an accepted interval method.

If inputs or the pipeline are unavailable, validate available metadata and report the specific prerequisite. Do not present a dry run or fixture as a refreshed reference.

## Validate source eligibility

For core synthetic seeds, identify generator version/seed, base-case and quote/workshop IDs, synthetic dates and provenance. For an explicitly requested approval import/refresh, additionally identify source/import IDs, approved-entry lineage, final status/amount, approval availability time and supersession. Validate part/operation/vehicle-class/currency and the fixed cost basis for either source.

Exclude unapproved declarations, surveyor-agreed-only costs, pending/rejected records, duplicates, superseded approvals and incompatible/incomplete rows. Explicit synthetic seed records are a separate documented source, not evidence of real approval.

Deduplicate by source/lineage and synthetic base case as applicable. Count unique eligible support accurately; reshaped workshop quotes must not inflate independent counts. Record inclusion/exclusion reasons and cutoff. Keep an assessed claim's eventual outcome out of its own earlier reference and held-out evaluation.

## Build and evaluate

Freeze source/member IDs, hashes, taxonomy, code/configuration, split/calibration membership and model versions. Generator, model features, percentile groups and lookup share part/operation/vehicle-class/currency under the versioned single-part SGD pre-tax/no-discount basis. Replacement is part supply, repair is labour, and paint is materials plus labour. Exclude side, damage type and model year from pricing features/grouping. Missing quantity is not one; bundles, other currencies and unknown classes receive no range. Synthetic dates/cutoffs do not establish market drift. Build immutable ranges with bounds, coverage, width, record counts, date and provenance.

Use only compatible comparison groups; unsupported combinations have no range. Validate finite ordered bounds, exact money semantics and sparse support handling. Compare empirical percentiles with bounded LightGBM 5th/95th quantiles using v2 M7/RQ4. Choose method and independent-support threshold on validation; use a separate calibration partition if conformal adjustment is selected, then evaluate untouched final tests. Report coverage, width, availability/withholding and support by key. Keep ordinary-price calibration separate from injected-anomaly experiments; an ordinary tail price is not a true anomaly merely because it exceeds the interval. A failed or incomplete candidate cannot replace the active table.

## Promotion, replay and rollback

Change the serving default only when promotion is within the user's requested scope or existing specific authorisation; otherwise deliver the reviewable candidate and results. This skill does not require asking again when promotion is already authorised.

For an authorised promotion, atomically update the future default and record the prior pointer. Existing assessments retain their pinned table. Reassessment creates a new revision. Retried imports/build publication must not double-count membership or overwrite an existing immutable version.

For requested rollback, restore the intended default pointer after checking artifact compatibility; do not delete historical tables, reviews or approvals. Stop repeated publication retries if the state cannot be determined safely; inspect recorded state and report the uncertainty.

## Deliver

Report build/table IDs, cutoff, source/eligible/excluded counts, actual calibration results, synthetic limits, promotion state and historical-assessment checks. Update [CONTEXT.md](../../../CONTEXT.md) and relevant task evidence without claiming real-world price validation.
