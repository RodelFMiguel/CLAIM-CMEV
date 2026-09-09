# M05 - Repair line-item recognition

Owner: Lane 3; Lane 4 supports taxonomy. Runtime: document worker. Code: `src/claim_cmev/documents/line_items/`; training: `pipelines/documents/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), Module 5 and sections 12.5, 14.1. Status: specified; model choice open.

## Purpose and scope

Group report fields into complete declared repair entries with exact source locations. Preserve the surveyor's declared values; recognition does not select a repair operation, amend a cost, or record final approval.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | M04 pages/text/layout/images and completeness, extraction checkpoint, part/operation mapping version, claim currency/vehicle context |
| Output | `DeclaredRepairEntry` with stable ID, original and canonical fields, declared amount/currency/basis, field confidence/uncertainty and source references |
| Consumers | M08 comparison, M09 review; M07 via eligible normalised entries |
| Dependencies | M04, shared taxonomy and [data contracts](data_contracts.md) |

Retain quantity, labour hours/rate, parts grade and tax basis when available. Missing required fields remain null/unknown with reasons. Structured surveyor input enters through the platform using the same entry schema and actor provenance, bypassing document inference.

## Processing specification

1. Adapt LayoutLMv3 or Donut using DocILE and synthetic survey-report layouts.
2. Identify repair-table fields and row membership; handle wrapped and continued entries across pages.
3. Distinguish actual repair entries from totals, headings, tax summaries and repeated table headers.
4. Parse exact decimal amounts and explicit currency/cost basis, retaining original text.
5. Map part, side and operation conservatively; retain ambiguous candidates.
6. Publish field-level confidence and uncertain/missing required fields with all source spans.
7. Allocate stable IDs per extracted run and retain lineage when the surveyor corrects entries.

Keep related templates out of independent partitions. DocILE is a benchmark for business document line items; map its fields/evaluator to the repair task explicitly and report domain results separately.

## Failure and uncertainty handling

Unreadable amounts, unresolved sides, contradictory currencies, unsupported parts/operations and uncertain row grouping block confident comparison. Never replace a missing amount with zero. Do not interpret no extracted rows from a low-quality report as an explicitly empty repair scope.

A partially unreadable report marks declared-scope completeness. M08 must qualify/withhold possible missing repairs when the relevant declared row could be unread. Preserve repeated repair rows until evidence establishes duplication; two operations on one part may both be legitimate.

## Acceptance criteria

- Initial complete-entry F1 >= 0.75 on DocILE line-item evaluation; source-location accuracy reported.
- Report synthetic survey and any real survey performance separately from DocILE.
- Every report-derived field/entry links to the original report page/span.
- Uncertain required fields remain reviewable without generating an unsupported/cost-outlier finding.
- Wrapped rows, repeated headers, multiple operations on one part and exact money parsing have representative checks.

## Implementation tasks

- [ ] Register document datasets/licences and related-template split groups.
- [ ] Define repair field schema and explicit DocILE benchmark mapping.
- [ ] Select LayoutLMv3/Donut using baseline extraction and localisation results.
- [ ] Prepare annotated synthetic survey layouts with recorded provenance.
- [ ] Implement grouping, exact money parsing, confidence and taxonomy mapping.
- [ ] Implement stable entry IDs and source-span lineage.
- [ ] Validate uncertain, empty, partial and multi-page reports.
- [ ] Integrate report and structured-entry contracts with M08-M09.
- [ ] Publish benchmark/domain metrics and model manifest.

## Open decisions

Model/checkpoint, survey-layout availability, confidence thresholds, operation vocabulary and cost basis rules. Unsupported data fields must remain explicit limitations.
