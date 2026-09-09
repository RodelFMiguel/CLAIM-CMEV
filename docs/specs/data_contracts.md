# Shared data contracts

Status: proposed contract baseline for team review, intended initial schema version `0.1.0`. Owner: Lane 5 coordinates; Lane 4 owns taxonomy/cost semantics. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), sections 9-10. All modules depend on these definitions.

These are field specifications to implement in shared schemas and database migrations. They are not yet executable validators. Required means present; fields explicitly described as nullable must carry a reason when absent.

## Identity, versions, and common types

| Field/type | Definition |
| --- | --- |
| `claim_id` | Opaque immutable claim identifier; external claim reference is a separate field |
| `input_revision` | Monotonically increasing claim-local revision of files, vehicle details, and declared input |
| `assessment_revision` | Claim-local revision identifying immutable comparison results and pinned dependencies |
| `review_revision` | Claim-local revision of saved human actions referencing one assessment |
| `schema_version` | Contract version, validated at every producer/consumer boundary |
| `processing_status` | `pending / running / succeeded / failed` for a processing-result envelope |
| `versions` | Relevant task model IDs, taxonomy, preprocessing/configuration, code revision, and cost table where used |
| `provenance` | Source kind `real / synthetic / fixture`, source dataset or import ID, and derivation references |
| Timestamp | UTC timestamp with timezone; preserve source dates separately |
| Money | Decimal string in interchange, exact database decimal, explicit currency and cost basis |
| Confidence | Number in [0, 1] with documented scoring method; nullable when unavailable, not invented |
| Artifact reference | Opaque artifact/file ID, SHA-256, media type and storage reference; access via backend |
| Reason | Stable reason code plus readable explanation; neither a free-form error nor a numerical score alone |

Claim-scoped processing results carry `claim_id`, `input_revision`, `schema_version`, `processing_status`, relevant `versions`, timestamps, and provenance. Offline datasets, model manifests, and cost-table builds instead carry dataset/build identity, schema, status, versions, and provenance; they have no artificial claim ID.

Stable IDs are allocated/persisted by the producing system. Replaying the same job reuses its result identity, using uniqueness or deterministic IDs. Do not derive persistent entry IDs from mutable row position or part name alone.

## Canonical vocabulary

Part identity consists of `part_code` and `side`: `left / right / centre / not_applicable / unknown`. Left/right is from the vehicle occupant's perspective facing forward. Unresolved side remains unknown; a dataset's unsided label does not justify assigning a side.

Initial part codes preserve the proposal's 21 HITL categories: windshield, back-windshield, front-window, back-window, front-door, back-door, front-wheel, back-wheel, front-bumper, back-bumper, headlight, tail-light, hood, trunk, licence-plate, mirror, roof, grille, rocker-panel, quarter-panel, fender. Background is a segmentation label, not a repairable part. Aliases such as “rear door” map only through a versioned, reviewed mapping.

Initial damage codes: dent, cracked, scratch, flaking, broken-part, paint-chip, missing-part, corrosion. Source spellings and unmappable classes are retained. Operations include `repair / replace / refinish / other / unknown`; exact supported scope and aliases need agreement. Unknown or other operations do not inherit a repair/replace cost range.

Store original text/source label, mapped code, mapping version, mapping status `resolved / ambiguous / unmapped`, and side confidence or source where applicable. Extensions must explicitly state whether image, report, and price sources support them.

## Spatial coordinates

For interchange, use bounding boxes `[x_min, y_min, x_max, y_max]` normalised to [0, 1], origin top-left, with ordered bounds. Report page numbers are one-based. Store page width, height, rotation, rendering scale and any transform back to the original PDF.

Masks reference raster artifacts with height, width, class encoding and source photo ID. Preserve resize/padding/orientation transforms. Affected area carries pixel count, part pixel count and/or a fraction with its denominator definition. These are image measures, not physical square centimetres or automatic repair severity. Multi-view aggregation must not sum repeated projected areas as if they were separate damage.

## Claim input and file records

`ClaimInput`: claim ID, input revision, external reference, make/model/year, optional class/trim/ADAS features, currency, file IDs, declaration source `none / report / structured`, source record IDs, creation time and previous revision.

`ClaimFile`: file ID, claim/revision membership, original name, media type, hash, byte count, storage reference, upload status, photo dimensions/orientation or report page count, provenance. A file may be explicitly referenced by later revisions without rewriting its bytes. Do not silently combine a report and structured list; corrections establish an explicit revised declaration.

## Image branch records

| Record | Required fields and meaning |
| --- | --- |
| `PartPrediction` | Photo ID, part identity, mask reference, confidence, class/mapping versions, transform metadata |
| `ImageQuality` | Photo ID, quality state, blur/obstruction/lighting/size limitations, reason codes and configuration version |
| `PartCoverage` | Part identity, state `adequate / inadequate / not_visible / unresolved`, covering photo IDs, part-mask refs, per-view quality/visibility, reasons |
| `ImageDamageObservation` | Observation ID, photo ID, damage type, part identity or unresolved candidates, matching status/score, model confidence, area and mask references |
| `DamageObservation` | Merged observation ID, part/side, damage type, confidence and aggregation method, affected-area definition, all supporting photo/mask refs, duplicate-group ID, member observation IDs |

Coverage is recorded for every supported part/side slot in the vehicle vocabulary, including parts with no detected damage. Adequate coverage and absence of damage are distinct facts. Unknown vehicle features or unresolved sides may make a slot unresolved; do not fabricate left/right coverage. M08 derives photographic support using both coverage and observations.

## Document and declaration records

`DocumentPage`: report file ID, page number, rendered artifact reference, dimensions/rotation, extraction route `embedded_text / ocr / direct_image`, tokens/regions with original text, box, reading order and confidence, page-level quality and errors.

`DeclaredRepairEntry`: stable entry ID, original part/operation text, canonical part/side/operation and mapping status, declared amount (nullable), currency, amount basis, field and overall extraction confidence, missing/uncertain fields, source reference, optional quantity/labour hours/rate/parts grade/tax basis, and lineage to prior entry when corrected.

For report entries, source reference includes file ID, page number and one or more boxes/token IDs for wrapped or continued rows. For structured entry input it includes actor, timestamp and request/action ID; there is no invented PDF location. Totals and tax summaries are not repair entries. Declared amount is the line total unless an explicitly agreed alternative basis is recorded.

An extraction failure retains an uncertain row or page status. An explicit confirmed empty declared list is distinguishable from missing declarations or a parser that extracted nothing.

## Cost records

`ReferenceCostRange`: table version, range ID, comparison key, lower/upper amounts, currency, amount basis, unique eligible record count, nominal coverage (initially 0.90), observed calibration metrics/reference, as-of/cutoff date, synthetic marker/provenance, applicable quantity/grade/labour/tax constraints and support status.

The comparison key includes part, side where relevant, operation, damage type, vehicle class or a versioned make/model/year grouping, currency and cost basis. Any fallback grouping must be explicit, versioned, calibrated, and exposed as comparison criteria. Do not pool repair and replacement by default. Missing range is null with a reason, never [0, 0]. Unsupported combinations receive no range.

`CostCheck`: entry ID, result `within_range / outside_range / insufficient_support / not_evaluated`, bounds and range reference when applied, direction `below / above` if outside, absolute deviation as money, optional defined normalised score, support count, reason and policy version.

Do not calculate deviations for incompatible amounts/units. For eligible amount a and bounds [L, U], equality is within range; absolute deviation is L-a below L, a-U above U, and zero inside. A zero-width interval is valid only if the reference build permits it; never divide by U-L without handling zero.

## Assessment findings and proposed additions

`AssessmentFinding`: finding ID, assessment revision, declared entry ID, individual documentary/photographic/cost checks, overall result `ok / unsupported / cost_outlier / insufficient_evidence`, reasons, photo/mask/report evidence refs, applied cost range or null, all dependency versions, and creation time.

Individual checks use `passed / failed / insufficient / not_evaluated`, with domain-specific detail. Documentary check includes schema readability/mapping; photographic check describes adequate support, absent visible support, or inability to assess; cost detail uses `CostCheck`. A skipped cost check is not a successful check. Overall ordering is specified by [M08](module-08-evidence-comparison.md).

`ProposedRepairAddition` is separate: candidate ID, assessment revision, merged observation ID, part/side, reason, supporting evidence, and review state. It has no declared entry ID or declared amount until a surveyor explicitly adds one. An unresolved declared part that could match the observation must produce an ambiguity for review, not a confident omission.

## Human review, approvals, and reference membership

`ReviewEvent`: event/action ID, claim, assessment revision, expected/current review revision, actor, timestamp, target finding/entry/candidate, action, reason code/note, original value references, edited values or agreed amount, and client idempotency key. Actions include confirm, accept finding, dismiss finding, edit entry, add/remove entry, record agreed amount, and record manual assessment.

Machine records remain immutable. Corrections to declared inputs create a new input/assessment revision; review-only actions create a review revision. A dismissal applies to the original finding and assessment, survives reload/retry, and is not blindly carried into changed evidence.

`ApprovalRecord`: approval ID, source import/external record ID, claim and reviewed entry lineage, status, final approved amount and cost attributes, currency/basis, approver/source, approval/effective timestamps, synthetic marker, and optional superseded approval ID. Rejected, pending, superseded and unapproved estimates are ineligible for cost training.

`ReferenceBuildMember`: build ID/table version, eligible approval or synthetic seed record ID, source hash, inclusion/exclusion reason, cutoff, split assignment, and weight. Deduplicate by source/lineage; multiple workshop quotes of the same synthetic base case must not inflate independent support counts.

## Invariants and tasks

- [ ] Implement validators and representative valid/invalid examples for each record.
- [ ] Agree taxonomy aliases, supported sides/operations, and unresolved-value policy.
- [ ] Freeze money basis, tax/quantity/labour/grade semantics and currency handling.
- [ ] Implement coordinate round-trip checks against original photos and report pages.
- [ ] Enforce revision compatibility, foreign keys, stable IDs, and uniqueness on retries.
- [ ] Define deterministic review-action replay and conflict behaviour.
- [ ] Validate absent ranges, uncertain amounts, explicit empty lists, and proposed additions.
- [ ] Implement immutable approval lineage and reference-build eligibility.
- [ ] Add compatibility checks and a migration policy before changing released schemas.
