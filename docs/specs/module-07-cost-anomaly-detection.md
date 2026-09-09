# M07 - Cost anomaly detection

Owner: Lane 4. Runtime: backend. Code: `src/claim_cmev/costs/anomaly/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), Module 7 and sections 4, 10.3, 14.4. Status: specified; calibration policy pending.

## Purpose and scope

Check a declared amount against a compatible, sufficiently supported reference range and explain any deviation. No fraud labels, fraud probability or automatic settlement decision are produced. Absence of fraud labels alone does not establish that this method meets an unsupervised-learning course requirement.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Valid normalised declared entry, resolved comparison attributes, pinned M06 table/range, minimum-support and comparability policy |
| Output | `CostCheck`: within-range, outside-range or insufficient-support; amount/bounds, direction, deviation, counts, reasons and versions |
| Consumer | M08 overall finding; M09 via the assessment |
| Dependencies | M06, taxonomy and [data contracts](data_contracts.md) |

M08 determines whether this check is reached under the ordered evidence rules. If earlier checks block it, M08 records cost as `not_evaluated`; it must not synthesise a successful M07 result.

## Decision specification

1. Validate amount, currency, operation, part/side, damage/vehicle grouping and applicable quantity/grade/labour/tax basis.
2. Resolve the compatible range from the assessment's pinned table.
3. Withhold when required fields/range are absent, units are incompatible, or unique supporting records fall below the versioned threshold.
4. For eligible amount a and [L, U], return within-range when L <= a <= U.
5. Return outside-range below L or above U, with direction and absolute deviation.
6. Preserve criteria, support count, cutoff and synthetic marker for explanation.

Absolute deviation is L-a below the lower bound and a-U above the upper bound. It is zero within range. If a normalised score is introduced, document its denominator and behaviour for zero bounds/zero-width ranges; do not divide by zero. Exact monetary comparison uses decimal arithmetic.

## Failure and uncertainty handling

Unknown ranges are absent, not zero-price bands. Currency conversion, repair/replacement pooling and fallback vehicle classes require explicit evaluated policies; otherwise withhold. A missing model/table artifact is an operational error, distinct from a valid table containing no matching record.

Both unusually low and high amounts can be outside range. A high cost may be reasonable due to hidden damage, ADAS, specialist procedures or price changes. The output describes deviation, not its cause. Price alone cannot establish grade substitution.

## Acceptance criteria

- Deterministic boundary cases below, equal to, inside and above bounds return the specified results.
- Sparse, absent, incompatible and uncertain inputs withhold rather than flag.
- Evidence includes exact applied table/range and unique record count.
- RQ4 reports flag precision, interval coverage and width against reference support.
- Controlled discrepancy precision contributes to the >= 0.70 M07-M08 target; recall by cost-change size is reported separately.

## Implementation tasks

- [ ] Agree comparability key, money basis and threshold configuration with M06.
- [ ] Implement version-pinned lookup and structured withheld reasons.
- [ ] Implement exact bounds/deviation logic and optional score definition.
- [ ] Validate boundaries, zero-width ranges, nulls, currency/unit mismatches and sparse counts.
- [ ] Select minimum support through RQ4 validation without test-set tuning.
- [ ] Integrate results/explanations with M08 and workbench range details.
- [ ] Confirm anomaly-method course classification and record any required method change.
- [ ] Publish controlled cost-change and clean-case results.

## Open decisions

Minimum unique support, any normalised score, and course classification. No numeric support threshold is validated by this scaffold.
