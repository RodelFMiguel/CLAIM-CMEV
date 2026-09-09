# M06 - Reference cost model

Owner: Lane 4; Lane 5 supplies approval/storage interfaces. Runtime: offline training and refresh jobs. Code: `src/claim_cmev/costs/reference/`, `pipelines/costs/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), Module 6 and sections 10.4, 12.4, 14.4. Status: specified; source schema and interval method open.

## Purpose and scope

Build versioned lower/upper cost bounds for comparable repairs, with evidence of calibration and reference support. Serving assessments look up a fixed published table; training never runs in a surveyor request.

Synthetic prices test range logic and synthetic calibration. They cannot validate real repair prices or savings.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Documented synthetic seed records or eligible later final-approved entries, taxonomy/vehicle mapping, currency/cost basis, split/calibration config and cutoff |
| Output | Immutable `ReferenceCostRange` table, model bundle, build/member manifest, exclusion report, metrics, date and synthetic marker |
| Consumer | M07 range lookup/checks; M08-M09 display/reproducibility |
| Dependencies | [Data contracts](data_contracts.md), approval importer, versioned storage |

The proposal describes a planned `prices_dataset.csv` of 630 rows, 18 parts, 5 models, 7 years and 3 workshops. The file is not supplied here. Confirm whether workshop quotes are columns or rows, actual combinations, operation/damage labels, currencies and units before fitting. Do not assume a Cartesian product or infer missing damage labels silently.

## Processing specification

1. Profile the source and retain real/synthetic provenance and source/base-case IDs.
2. Normalise eligible records by part/side where relevant, operation, damage type, vehicle grouping, currency, quantity/basis and available grade/labour/tax attributes.
3. Exclude incompatible/incomplete/duplicate/superseded rows; distinguish quotes/estimates from final approval or explicitly synthetic seeds.
4. Split by claim/base case and time as appropriate. Keep preprocessing/fitting, calibration, and evaluation separate.
5. Fit a gradient-boosting interval model using quantiles or a documented conformal method; target nominal 90% coverage.
6. Evaluate empirical coverage and average width, including low-support groups.
7. Publish only supported combinations with actual unique eligible record counts and an as-of/cutoff date.
8. Promote an evaluated candidate atomically; retain prior tables and model/configuration versions.

A reference lookup must not extrapolate a range for an unsupported combination without an explicitly evaluated grouping policy. Any recency weighting and support-count definition must be recorded. Correlated workshop quotes do not become independent cases merely by reshaping columns into rows.

## Refresh and uncertainty handling

Only controlled final-approved entries qualify for the approval path. Surveyor agreement or dismissal does not imply approval. Deduplicate using source/lineage IDs, exclude superseded records, record exact build membership, and apply an availability cutoff so a claim's eventual outcome cannot affect its own earlier assessment.

Missing operation/damage/unit support yields no range. Empty or invalid training data fails the build. Non-finite/crossed bounds fail validation; do not publish malformed intervals. Sparse support remains visible for M07 to withhold. A failed candidate never replaces the active table.

## Acceptance criteria

- Empirical coverage lies within 5 percentage points of nominal 90% on held-out synthetic data; report width and subgroup results.
- Every range has criteria, unique support count, cutoff/date, table version, calibration evidence and synthetic provenance.
- Repair/replacement and incompatible currencies/units are not pooled silently.
- Refresh excludes agreed-only, duplicate and superseded entries.
- Rebuilding from a recorded manifest is reproducible within documented numerical limits; historical assessments retain their original table.

## Implementation tasks

- [ ] Inspect/document the actual price file and resolve missing field semantics.
- [ ] Agree eligibility, deduplication, vehicle grouping and cost basis policies.
- [ ] Implement source conversion with exclusions and base-case lineage.
- [ ] Select quantile/conformal method and group/time split strategy.
- [ ] Train baseline intervals and calibrate nominal coverage.
- [ ] Run RQ4 sparsity experiments; select minimum support with M07.
- [ ] Implement immutable table/member manifests, validation, promotion and rollback.
- [ ] Integrate synthetic approval import and a separate offline refresh demonstration.
- [ ] Publish metrics and explicit limits on real-world interpretation.

## Open decisions

Price-source schema, interval method, minimum support, grouping fallback and recency weighting. Decisions must follow data inspection and validation, not assumed dataset completeness.
