---
name: cmev-review-evidence-logic
description: >-
  Review CLAIM-CMEV evidence comparison, coverage gating, cost checks and missing
  repair logic for incorrect or unjustified findings. Use for a requested domain
  review or a suspected decision-rule defect; report findings before making fixes.
---

# Review CLAIM-CMEV evidence logic

Return actionable review findings supported by code/specification evidence. This is a focused domain review, not a general style or security audit.

Read [AGENTS.md](../../../AGENTS.md), [CONTEXT.md](../../../CONTEXT.md), [M08](../../../docs/specs/module-08-evidence-comparison.md), and [data contracts](../../../docs/specs/data_contracts.md). Read [M03](../../../docs/specs/module-03-multiview-coverage.md) for coverage defects, [M07](../../../docs/specs/module-07-cost-anomaly-detection.md) for price checks, or [M05](../../../docs/specs/module-05-repair-line-items.md) for extraction completeness as needed.

## Review the requested scope

Identify the diff/function/behaviour under review and trace the relevant input states through comparison and displayed/exported output. Use existing tests or small non-mutating probes when useful. Review is read-only by default; apply fixes or write a report/context entry only when requested.

Check the ordered flow against M08: uncertain required declarations -> insufficient evidence; inadequate/ambiguous photographic evidence -> insufficient evidence; adequate coverage with no supporting visible damage -> unsupported; supported damage with no compatible sufficient range -> insufficient evidence; eligible out-of-range cost -> cost outlier; eligible in-range cost -> no discrepancy found.

Examine the applicable failure cases:

- Missing photo/unknown side, blur, obstruction or failed inference cannot establish an unsupported repair.
- Empty uncertain or partially unreadable extraction is not an explicitly empty declaration.
- Skipped cost checks and failed processing cannot appear as passed checks or a completed clean assessment.
- Repair/replacement, currencies, quantities, labour/grade/tax bases and sparse reference support must remain comparable or produce a withheld cost check.
- Cost flags retain applied range/table evidence; a high amount alone cannot prove fraud or grade substitution.
- Reverse matching preserves observation evidence without inventing an amount, and accounts for unresolved declarations that could already describe the damage.
- Findings retain matching revisions and original evidence; human dismissals and corrections do not rewrite machine results or final approval.

Do not infer a defect solely from an initial metric target being unmet; distinguish algorithm bugs, measured model limitations and unimplemented specification work.

## Evidence and output

For each material finding, give severity, file/location, concrete triggering input, observed versus required behaviour, likely user impact and a minimal regression scenario. Order by impact and avoid speculative findings unsupported by the inspected code.

If no defect is found, state the scope checked and any untested paths; do not imply model accuracy or full-system correctness. If code is absent, identify missing implementation rather than inventing a code review.

Do not run full model training or costly evaluations for a rule review. Follow the [evaluation plan](../../../docs/specs/evaluation_plan.md) only for relevant existing scenarios. When fixes are explicitly requested, verify the changed behaviour and update the appropriate TODO/context evidence.
