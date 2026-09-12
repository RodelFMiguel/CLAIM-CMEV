---
name: cmev-refresh-cost-reference
description: >-
  Prepare, validate, or execute a CLAIM-CMEV reference-cost refresh from documented
  synthetic seeds or eligible final approvals, preserving build lineage and prior
  tables. Use for cost-table builds and refreshes, not checking one declared amount.
---

# Refresh a CLAIM-CMEV cost reference

Produce an evaluated candidate reference build with traceable membership. Keep candidate creation and changing the serving default as distinct outcomes.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), [M06](../../../docs/specs/module-06-reference-cost-model.md), [M07](../../../docs/specs/module-07-cost-anomaly-detection.md), the [cost/approval contracts](../../../docs/specs/data_contracts.md), and refresh/deployment sections of the [technical specification](../../../docs/specs/technical_specification.md).

## Establish the requested operation

Determine whether the task requests planning/validation, a candidate build, or promotion/rollback. Use the existing implementation, current table pointer and configured eligibility/calibration policy. Do not invent dataset rows, a supported combination, a minimum-support threshold or an accepted interval method.

If inputs or the pipeline are unavailable, validate available metadata and report the specific prerequisite. Do not present a dry run or fixture as a refreshed reference.

## Validate source eligibility

Identify source/import IDs, approved-entry lineage, final status and amount, approval availability time, supersession, currency/units, part/side/operation/damage/vehicle attributes and synthetic provenance.

Exclude unapproved declarations, surveyor-agreed-only costs, pending/rejected records, duplicates, superseded approvals and incompatible/incomplete rows. Explicit synthetic seed records are a separate documented source, not evidence of real approval.

Deduplicate by source/lineage and synthetic base case as applicable. Count unique eligible support accurately; reshaped workshop quotes must not inflate independent counts. Record inclusion/exclusion reasons and cutoff. Keep an assessed claim's eventual outcome out of its own earlier reference and held-out evaluation.

## Build and evaluate

Freeze source/member IDs, hashes, taxonomy, cost basis/grouping, recency weighting, code/configuration, split/calibration membership and model versions. Build immutable ranges with bounds, coverage, width, record counts, date and provenance.

Use only compatible comparison groups; unsupported combinations have no range. Validate finite ordered bounds, exact money semantics and sparse support handling. Evaluate nominal interval coverage and subgroup/support performance using the established held-out protocol. A failed or incomplete candidate cannot replace the active table.

## Promotion, replay and rollback

Change the serving default only when promotion is within the user's requested scope or existing specific authorisation; otherwise deliver the reviewable candidate and results. This skill does not require asking again when promotion is already authorised.

For an authorised promotion, atomically update the future default and record the prior pointer. Existing assessments retain their pinned table. Reassessment creates a new revision. Retried imports/build publication must not double-count membership or overwrite an existing immutable version.

For requested rollback, restore the intended default pointer after checking artifact compatibility; do not delete historical tables, reviews or approvals. Stop repeated publication retries if the state cannot be determined safely; inspect recorded state and report the uncertainty.

## Deliver

Report build/table IDs, cutoff, source/eligible/excluded counts, actual calibration results, synthetic limits, promotion state and historical-assessment checks. Update [CONTEXT.md](../../../CONTEXT.md) and relevant task evidence without claiming real-world price validation.
