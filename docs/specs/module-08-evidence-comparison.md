# M08 - Evidence comparison

Owner: Lane 4. Runtime: backend. Code: `src/claim_cmev/comparison/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), sections 9-10, Module 8, and 14.2. Status: specified; implementation pending.

## Purpose and scope

Combine independently produced photographic evidence, declared repair entries and cost checks using explicit ordered rules. Preserve evidence and each individual check result. This is decision-level fusion; it does not infer hidden structural damage or a technically required repair operation.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Matching-revision M03 coverage/observations, M05 or structured declarations with completeness, M06 pinned table, M07 check interface, taxonomy and policy versions |
| Output | Immutable `AssessmentFinding` per declared entry and separate `ProposedRepairAddition` candidates |
| Consumers | M09 review, structured export and historical audit records |
| Dependencies | M03, M05, M06-M07, coordinator and [data contracts](data_contracts.md) |

The coordinator must supply successful required branch outputs. Failed/incomplete required processing is an application state, not a clean assessment.

## Ordered decision table

Apply the first matching row. Store documentary, photographic and cost subchecks plus the overall result.

| Order | Condition | Overall result | Required explanation |
| --- | --- | --- | --- |
| 1 | Unreadable/uncertain required entry fields or unresolved/unsupported part, side or operation | `insufficient_evidence` | Identify documentary/mapping correction needed |
| 2 | Part lacks adequate coverage, or relevant image evidence is ambiguous | `insufficient_evidence` | Missing/poor view, unresolved identity, or uncertain damage evidence |
| 3 | Adequate assessable coverage and confidently no supporting visible damage | `unsupported` | Link covering original photos and masks; do not claim repair is fraudulent |
| 4 | Supporting damage exists but compatible sufficient cost reference is unavailable | `insufficient_evidence` | Preserve successful photographic check; explain withheld cost |
| 5 | Supporting damage exists and M07 returns outside-range | `cost_outlier` | Show direction, declared amount, bounds, criteria and support |
| 6 | Supporting damage exists and M07 returns within-range | `ok` | No discrepancy found in available checks |

Do not mark a skipped check passed. For rows 1-3, cost is `not_evaluated` under the proposal's ordered flow. A model/API error is never evidence of no visible damage. Confidence/quality thresholds must be calibrated and versioned.

## Reverse comparison: possible missing repairs

Match each eligible merged damage observation against the declared list by supported part/side identity. Multiple repair operations can legitimately refer to the same part; operation choice remains with the surveyor.

A supported observation without a matching declaration becomes a proposed addition with evidence and no invented entry ID or price. Deduplicate candidates by observation/part policy without dropping distinct damaged parts. Unresolved declared names/sides, incomplete extraction or uncertain observations may conceal a match; surface the uncertainty rather than a confident omission. Only an explicitly confirmed empty declaration is an empty repair scope.

## Revision and explanation requirements

Freeze model, mapping, coverage/matching policy and cost-table versions in every assessment. Reject mixed input revisions or incompatible label schemas. Stable finding IDs support idempotent replay. Human actions reference findings separately and never rewrite machine results.

Adding evidence or correcting declarations produces a new input/assessment revision. Reassessment with a different cost/model bundle produces a new assessment revision. A dismissal is retained on its original assessment; new evidence must not silently inherit a previous dismissal as if unchanged.

## Acceptance criteria

- Controlled discrepancy flag precision >= 0.70; recall reported separately for unsupported parts, cost changes and missing repairs.
- Mean false flags <= 0.05 per clean case.
- Correct withholding >= 0.95 for parts absent from all photographs.
- Each outcome follows the ordered table and exposes subchecks/evidence.
- Structured-input rule evaluation and full-pipeline evaluation are reported separately.
- Missing evidence, ranges, uncertain amounts and unresolved identities never become confident discrepancies by default.

## Implementation tasks

- [ ] Freeze matching/ordering/reason-code policy with M03, M05 and M07.
- [ ] Implement contract/revision/version validation before comparison.
- [ ] Implement ordered checks and explicit skipped-check records.
- [ ] Implement reverse matching with completeness and ambiguity safeguards.
- [ ] Generate stable findings/candidates and store source evidence references.
- [ ] Build controlled clean, unsupported, outlier, omitted and insufficient cases.
- [ ] Integrate fixed-version execution and reassessment with the coordinator.
- [ ] Evaluate combined versus single-branch outputs and research comparators.
- [ ] Publish targets, denominators, error analysis and limitations.

## Open decisions

Matching tolerances, entry completeness/uncertainty thresholds, candidate grouping, and evidence templates. Resolve with held-out validation and retain policy versions.
