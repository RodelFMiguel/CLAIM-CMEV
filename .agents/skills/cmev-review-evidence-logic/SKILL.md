---
name: cmev-review-evidence-logic
description: >-
  Review CLAIM-CMEV evidence comparison, coverage gating, cost checks and missing
  repair logic for incorrect or unjustified findings. Use for a requested domain
  review or a suspected decision-rule defect; report findings before making fixes.
---

# Review CLAIM-CMEV evidence logic

Return actionable review findings supported by code/specification evidence. This is a focused domain review, not a general style or security audit.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), [M8 consolidation and checks](../../../docs/specs/module-08-consolidation-checks.md), and the relevant [data contracts](../../../docs/specs/data_contracts.md). For the suspected defect, read [M3 coverage](../../../docs/specs/module-03-part-summary-coverage.md), [M5 extraction](../../../docs/specs/module-05-line-item-extraction.md), [M6 marks](../../../docs/specs/module-06-pen-mark-recognition.md), or [M7 ranges](../../../docs/specs/module-07-reference-cost-ranges.md). Use the [evaluation plan](../../../docs/specs/evaluation_plan.md) for regression cases and the [product specification](../../../docs/specs/product_specification.md) for requirement IDs; proposal section 8 supplies the scope-level rule rationale.

Use the current specifications below for implementation details and acceptance criteria. [Proposal v2](../../../docs/CLAIM-CMEV_project_proposal_v2.md) governs project scope; [ADR 0002](../../../docs/adr/0002-containerised-event-runtime.md) records the later runtime change. Read the sections relevant to the task, preserve proposed versus accepted status, and reconcile concrete conflicts with current user decisions. A specification is not evidence that its behaviour is implemented.

## Review the requested scope

Identify the diff/function/behaviour under review and trace the relevant input states through comparison and displayed/exported output. Use existing tests or small non-mutating probes when useful. Review is read-only by default; apply fixes or write a report/context entry only when requested.

Check the ordered flow against v2 section 8.1: pending/conflicting/unlinked marks -> insufficient evidence; confirmed exclusion -> excluded and not checked; unresolved required fields/physical identity -> insufficient evidence; inadequate views or uncertain/out-of-scope damage -> insufficient evidence; confirmed adequate coverage with confidently no supported damage -> unsupported; supported damage with no compatible sufficient range -> insufficient evidence; eligible effective price outside/inside inclusive bounds -> cost outlier/no discrepancy found. Keep individual photo/cost results even when the overall result is withheld.

Examine the applicable failure cases:

- Pending price changes cannot fall back to printed amounts; optional recognition suggestions cannot substitute for human confirmation. A confirmed exclusion is not an `ok` finding, and unresolved conflicting marks take precedence.
- Missing photo/unknown side, blur, cropping, obstruction or failed inference cannot establish an unsupported repair. HITL labels do not resolve side; a sharp large mask cannot prove whole-panel coverage. Negative findings require recorded adequate coverage of the resolved physical part within supported damage scope.
- Empty uncertain or partially unreadable extraction is not an explicitly empty declaration.
- Skipped cost checks and failed processing cannot appear as passed checks or a completed clean assessment.
- Cost comparison uses part/operation/vehicle-class/currency, fixed SGD quantity-one pre-tax/no-discount basis and independent base-case support. Replacement is part supply, repair is labour, and paint is materials plus labour. Bundles, missing/non-unit quantity, other currencies, unknown classes or absent/sparse ranges withhold cost checks. Side/damage/year are not cost grouping dimensions; side still matters to photo matching.
- Cost flags retain applied range/table evidence; a high amount alone cannot prove fraud or grade substitution.
- Possible additions require confident damage, resolved physical identity and sufficiently complete declarations. Unreadable rows/pages, uncertain identity or pending/unlinked marks can hide a match. A confirmed exclusion suppresses an addition only for that same physical part and retains an informational damage note. Preserve legitimate multiple operations; invent neither operation nor amount.
- Decision-changing corrections/confirmations create new input/assessment revisions and recompute affected findings; explicit artifact reuse retains lineage. Original outputs remain immutable. Notes/dismissals reference their findings; finalization rejects pending/unlinked marks, failed/unfinished recomputation and stale assessment/review pairs. Final approval remains separate.

Do not infer a defect solely from an initial metric target being unmet; distinguish algorithm bugs, measured model limitations and unimplemented specification work.

## Evidence and output

For each material finding, give severity, file/location, concrete triggering input, observed versus required behaviour, likely user impact and a minimal regression scenario. Order by impact and avoid speculative findings unsupported by the inspected code.

If no defect is found, state the scope checked and any untested paths; do not imply model accuracy or full-system correctness. If code is absent, identify missing implementation rather than inventing a code review.

Do not run full model training or costly evaluations for a rule review. Follow the [evaluation plan](../../../docs/specs/evaluation_plan.md) only for relevant existing scenarios. When fixes are explicitly requested, verify the changed behaviour and update the appropriate TODO/context evidence.
