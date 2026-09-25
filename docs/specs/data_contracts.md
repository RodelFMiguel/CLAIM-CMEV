# Shared data contracts

Status: **proposed** contract baseline for v2, target schema version `0.2.0`. Nothing here is implemented. These are field specifications to build into shared schemas and database migrations; they are not yet executable validators. Owner: Lane 5 coordinates; Lane 4 owns taxonomy, cost and finding semantics; each module owner owns its own records. Source: [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) sections 2.2 and 2.3 (marks and amounts), 8 (decision rules), 9.3 (records), 9.6 (failure handling), 10 (modules), 11.6 (cost data) and 16 (transition).

Transport, topic payloads, adapter signatures and the HTTP surface are in [integration contracts](integration_contracts.md). Runtime mechanics are in the [technical specification](technical_specification.md) under [ADR 0002](../adr/0002-containerised-event-runtime.md).

Required means present. A field described as nullable must carry a **reason** when it is absent. A null without a reason is a validation failure, not a missing value.

## 1. What changed from v1

The v1 contract file was `schema_version 0.1.0`. This is `0.2.0`. Identity, versions, common types, spatial coordinates and money are kept and updated. The rest changes with v2.

| Area | v1 (`0.1.0`) | v2 (`0.2.0`) |
|---|---|---|
| Damage vocabulary | 8 HITL labels: dent, cracked, scratch, flaking, broken-part, paint-chip, missing-part, corrosion | **6 CarDD labels**: dent, scratch, crack, glass shatter, lamp broken, tire flat. The HITL eight become a named contingency taxonomy with its own version |
| Declared rows | `DeclaredRepairEntry` with one declared amount | `LineItem` with **printed price and effective price held separately** |
| Pen marks | Did not exist | `PenMark` with `pending`, `confirmed`, `rejected`, candidate entry IDs and a human-entered amount held apart from any TrOCR suggestion |
| Declaration state | Implicit | `DeclarationCompleteness`: `complete`, `partial`, `unreadable`, `explicitly_empty` |
| Cost key | Part, side, operation, damage type, vehicle class or a make/model/year grouping, currency | **Part, operation, vehicle class, currency** under one fixed cost basis. No year, no side, no damage type |
| Human confirmation | Generic review events | Explicit `IdentityConfirmation` and `CoverageConfirmation` records with their own provenance |
| Transport | Records moved through a database jobs table | Records move through Kafka topics and PostgreSQL. Every record maps to a table and, where applicable, a topic. See section 12 |

**The compatibility rule from v2 section 16 is binding.** A `0.1.0` payload, fixture or model bundle is **rejected** at every boundary with reason `schema_unsupported`. It is never silently reinterpreted. The two damage taxonomies are never merged, mapped implicitly or pooled. A v1 cost table is not loadable by a v2 runtime, and the build manifest's `cost_key_fields` is the check. The application is a scaffold with no code, so no executable migration is claimed here; the rule exists so that pre-transition fixtures cannot leak into v2 results.

## 2. Identity, versions and common types

| Field or type | Definition |
|---|---|
| `claim_id` | Opaque immutable claim identifier, ULID. The insurer's external reference is a separate field. Also the Kafka message key |
| `input_revision` | Monotonically increasing claim-local revision of files, vehicle details and declared input |
| `assessment_revision` | Claim-local revision identifying immutable comparison results and their pinned dependencies |
| `review_revision` | Claim-local revision of saved human actions referencing one assessment |
| `schema_version` | Contract version, validated at every producer and consumer boundary. `0.2.0` for v2 |
| `processing_status` | `pending`, `running`, `succeeded`, `failed` for a processing-result envelope |
| `versions` | Map of the model, taxonomy, preprocessing, configuration, code and cost-table versions relevant to the record. An empty map is invalid |
| `provenance` | `{source_kind: real \| synthetic \| fixture \| explainer, runtime_profile: lean \| full, producer_service, source_dataset_id?, derivation_refs[]?}` |
| `job_key` | Unique work identity: claim, input revision, task, target and version signature. See [integration contracts](integration_contracts.md) section 6.6 |
| Timestamp | UTC with timezone, RFC 3339. Source dates from a document are preserved separately from system times |
| Money | Decimal string in interchange, exact database `numeric` in storage, always with explicit `currency` and `cost_basis`. **Never a JSON number** |
| Confidence | Number in `[0, 1]` with a documented scoring method. Nullable when the producer supplies none. Never invented |
| Artifact reference | `{artifact_id, object_uri, sha256, media_type, byte_count}`. Bytes are reached only through the backend, never by a presigned URL handed to the browser |
| Reason | Stable machine reason code plus a readable explanation. Not a free-form error string, and not a bare number |

Claim-scoped records carry `claim_id`, `input_revision`, `schema_version`, `processing_status`, the relevant `versions`, timestamps and `provenance`. Offline datasets, model manifests and cost-table builds instead carry a build or dataset identity; they have no artificial claim ID.

Stable IDs are allocated by the producing service and are **deterministic from the job key plus a stable index**, so a replay reuses the same identity. Never derive a persistent entry ID from a mutable row position or from a part name alone.

## 3. Canonical vocabulary

### 3.1 Part identity

Part identity is `part_code` plus `side`. Sides are `left`, `right`, `centre`, `not_applicable`, `unknown`. Left and right are from the vehicle occupant's perspective facing forward.

The 21 HITL part categories are unchanged from v1:

`windshield`, `back-windshield`, `front-window`, `back-window`, `front-door`, `back-door`, `front-wheel`, `back-wheel`, `front-bumper`, `back-bumper`, `headlight`, `tail-light`, `hood`, `trunk`, `licence-plate`, `mirror`, `roof`, `grille`, `rocker-panel`, `quarter-panel`, `fender`.

Background is a segmentation label, not a repairable part. Aliases such as "rear door" resolve only through a versioned, reviewed mapping.

**Side is never produced by a model.** HITL part labels carry no left or right information, so M1 and M2 always emit `side = unknown`. Only a recorded `IdentityConfirmation` attaches a side. A dataset's unsided label never justifies assigning one.

**Source-mapping note, verified in the repository on 2026-09-22.** Under `data/raw/` the two HITL folders are named the wrong way round relative to their contents: the folder named `Car damages dataset/` holds the **21 part classes** with 998 images, and the folder named `Car parts dataset/` holds the **8 damage classes** with 814 images. Annotations are Supervisely-format polygon JSON under `File1/ann` with images under `File1/img`, plus `masks_human` and `masks_machine`. Any loader must map by **inspected class names**, never by folder name. The acquisition manifest records the swap; do not rename the folders on disk, because the download tooling and its tests reference the published names. `data/raw/Car-Parts-Segmentation` (DSMLR) and `data/raw/carparts-seg` (Ultralytics) are present but outside v2 core scope.

### 3.2 Damage vocabulary: six CarDD categories

v2 M2 uses the six CarDD categories and nothing else.

| `damage_code` | CarDD category |
|---|---|
| `dent` | dent |
| `scratch` | scratch |
| `crack` | crack |
| `glass-shatter` | glass shatter |
| `lamp-broken` | lamp broken |
| `tire-flat` | tire flat |

Taxonomy version `damage-cardd-<x.y.z>`. Background is a distinct label and is not the same as unsupported or unannotated damage. CarDD instance annotations are converted to semantic masks under a recorded overlap and background policy; the results are semantic segmentation metrics and must not be reported as instance segmentation.

### 3.3 Damage vocabulary: the HITL contingency taxonomy

The eight HITL damage labels are the **named access contingency only** (v2 sections 11.2 and 12.4), used if CarDD consent or files do not arrive by the day-2 checkpoint:

`dent`, `cracked`, `scratch`, `flaking`, `broken-part`, `paint-chip`, `missing-part`, `corrosion`.

Taxonomy version `damage-hitl-<x.y.z>`, **distinct** from the CarDD version.

Hard rules:

- **Do not merge the two vocabularies.** They are different taxonomies with different versions and different label definitions.
- `dent` and `scratch` appear in both lists. They are still **not** the same label, because the annotation guidelines differ. Mapping one to the other requires a recorded, reviewed decision, not an assumption based on the shared spelling.
- Selecting the contingency taxonomy means recording its split, its targets and every affected proposal and specification change first. Do not combine sources to rescue sample counts.
- The CarDD model does not cover HITL-only types such as corrosion or flaking. An observation of a type outside the active taxonomy is out of scope for support and constrains a negative finding; it is not evidence of absence.

### 3.4 Operations, vehicle class and mapping status

| Vocabulary | Values |
|---|---|
| `operation` | `repair`, `replace`, `paint`, `other`, `unknown`. Only `repair`, `replace` and `paint` are eligible for a cost range |
| `vehicle_class` | Four documented classes, frozen by day 2, plus `unknown`. `unknown` receives no cost comparison |
| `mapping_status` | `resolved`, `ambiguous`, `unmapped` |

`other` and `unknown` operations never inherit a repair, replace or paint range. Repair, replacement and paint never share a range.

Every mapped value stores the original source text, the mapped code, the mapping version and the mapping status. Extensions must state explicitly whether the image, document and price sources all support them.

## 4. Spatial coordinates

Bounding boxes in interchange are `[x_min, y_min, x_max, y_max]`, normalised to `[0, 1]`, origin top-left, bounds ordered. Report page numbers are one-based.

For pages, store page width, height, rotation, rendering scale, whether perspective correction was applied, and the inverse transform back to the uploaded page and to the original PDF. A box is normalised on the **corrected render**; navigating back to the original requires the stored transform. New PDF page records carry optional `render_to_pdf`, a 3x3 matrix from source-render pixels to original PDF user-space points (bottom-left origin), including crop offsets and page rotation. Compose it after `homography_inverse`. Scale alone is insufficient. Image uploads use null. Legacy PDF records lacking the matrix remain readable, but PDF-point conversion refuses until regenerated; their old coordinates are not reinterpreted.

Masks reference raster artifacts with height, width, class encoding and source photo ID. Preserve resize, padding and orientation transforms. A part mask and a damage mask for the same photo must be reconcilable to one grid; if they cannot be, the job fails with `mask_geometry_mismatch` rather than resampling a class map with interpolation that invents values.

Affected area carries a pixel count, a part pixel count where available, and a fraction with an explicit denominator definition. **These are image measures.** They are not square centimetres, not severity and not a count of physical damage instances. Multi-view aggregation must never sum repeated projected areas as if they were separate damage.

## 5. Claim input and file records

`ClaimInput`: claim ID, input revision, external reference (nullable with reason), make, model, year, optional trim and ADAS features, resolved `vehicle_class` or `unknown` with a reason, currency, file IDs, creation time, previous input revision, and references to any explicitly reused prior-revision artifacts with their lineage.

`ClaimFile`: file ID, claim and revision membership, original name, media type, SHA-256, byte count, object reference, upload status, photo dimensions and EXIF orientation or page count, and provenance. A file may be referenced by a later revision without rewriting its bytes. Object keys are generated; no user-supplied string ever forms a storage path.

Model year is claim metadata. It is display only and is excluded from cost generation, cost model features and cost lookup (v2 section 8.3).

## 6. Image branch records

| Record | Required fields and meaning |
|---|---|
| `PartPrediction` | Photo ID, `part_code`, `side = unknown`, mask reference, mean confidence, pixel count, taxonomy and model versions, transform metadata |
| `ImageQuality` | Photo ID, quality state, blur, obstruction, lighting and size limitations, reason codes, configuration version |
| `ImageDamageObservation` | Observation ID, photo ID, `damage_code`, `damage_confidence`, `assignment_status`, `part_code` or null with `part_reason`, `side = unknown` with `side_reason`, ranked `candidates[]` with `containment`, `primary_containment`, `runner_up_containment`, `background_containment`, `area_pixels`, `area_fraction` with its recorded denominator, damage and part mask references, `bbox_norm` on the original photo, `assignment_config_version`, versions |
| `PartSummary` | Summary ID, resolved `part_code` and `side`, member observation IDs, supporting photo and mask references, aggregation method. **No inferred count of physical damage instances** |
| `PartCoverage` | `part_code`, `side`, state `adequate \| inadequate \| not_visible \| unresolved`, covering photo IDs, per-view quality and visibility, reason codes, the coverage-confirmation reference when the state is `adequate` |
| `IdentityConfirmation` | Confirmation ID, photo ID, confirmed `part_code` and `side`, actor, recorded time, review revision, note. Provenance `kind = real`, source `human` |
| `CoverageConfirmation` | Confirmation ID, `part_code`, `side`, covering photo IDs, `covers_enough` boolean, reason, actor, recorded time, review revision |

Rules:

- `part_reason` is required whenever `part_code` is null: `no_part_overlap`, `below_containment_threshold`, `ambiguous_between_parts`, `part_not_accepted`, `mostly_background` or `part_masks_missing`. The candidate list is retained in every case.
- Coverage is recorded for **every** supported part and side slot in the vocabulary, including parts with no detected damage. Adequate coverage and absence of damage are distinct facts.
- An `adequate` state requires a recorded `CoverageConfirmation`. Mask area, sharpness and lighting are screening signals stored as such, never as proof of whole-panel visibility.
- Unknown vehicle features or unresolved sides make a slot `unresolved`. Never fabricate left or right coverage.
- A failed model run is a processing failure, never `not_visible` and never an empty successful result.
- A confirmation never rewrites a model row. It is a separate record with its own actor and provenance, and M8 reads both.

## 7. Document and declaration records

### 7.1 `DocumentPage`

Page file ID, page number (one-based), corrected render reference, page-reading JSON reference, dimensions and rotation, whether perspective correction was applied and why not if it was not, extraction route (`ocr` for photographed pages; `embedded_text` only for a digital PDF), tokens or regions with original text, box, reading order and confidence, actual `text_granularity` (`word`, `line` or `block`), page-level quality state and reason codes.

**Do not invent word-level boxes when the engine supplies only line boxes.** The granularity field states what the engine actually gave. Failed correction, failed OCR or obscured print marks the affected page and rows incomplete.

### 7.2 `LineItem`, replacing v1 `DeclaredRepairEntry`

| Field | Required | Meaning |
|---|---|---|
| `entry_id` | yes | Stable, deterministic from the job key and row index |
| `page_id`, `page_number`, `row_box_norm` | yes | Source location on the corrected render |
| `amount_box_norm` | nullable | The printed amount's own box, used for mark linking |
| `original_part_text`, `original_operation_text` | yes | Exactly as read. Never normalised away |
| `part_code`, `part_mapping_status` | yes, `part_code` nullable | Null with a reason when unmapped or ambiguous |
| `side`, `side_source` | yes | `unknown` unless printed text, a correction, or an explicitly unsided taxonomy part resolves it. Unsided parts with no side text use `not_applicable` and `side_source=absent`; absent text never resolves a sided part. `side_source` is `document_text`, `human_correction` or `absent` |
| `operation`, `operation_mapping_status` | yes, `operation` nullable | Null with a reason |
| `quantity` | nullable | Decimal string. **A missing quantity is never silently set to one** |
| `unit_price` | nullable | Decimal string with a reason when absent |
| `printed_line_amount` | nullable | Decimal string. Current reading of the workshop's printed price, including an explicit OCR correction |
| `original_printed_line_amount`, `printed_amount_corrected` | optional | First extracted numeric amount (nullable) and correction flag (default false). On the first printed-amount correction preserve that original; later corrections cannot replace it. `original_amount_text` remains unchanged. Show the original and corrected reading in review and frozen reports |
| `effective_price` | nullable | **Held separately from `printed_line_amount`.** See the rule below |
| `effective_price_source` | yes | `printed`, `surveyor_entry` or `unresolved`, with a reason when `unresolved` |
| `currency`, `cost_basis` | yes | From explicit document or claim information, or a recorded correction |
| `field_uncertainty[]` | yes, may be empty | `{field, reason}` per uncertain field |
| `field_confidence`, `entry_confidence` | nullable | Null when the extractor supplies none |
| `lineage` | nullable | Prior entry ID when this row is a correction |
| `versions` | yes | Parser or model version, taxonomy version, code version |

**The effective-price rule** (v2 section 2.2). The effective price is the revised amount entered and confirmed by the surveyor, **or** the readable printed amount when no price change is pending or confirmed. A **pending** price change leaves `effective_price` null with reason `price_change_pending` and `effective_price_source = "unresolved"`. It never falls back to the printed amount. This is a safety rule, not a convenience default.

Totals, subtotals, headings and tax lines are identified and excluded from line items. An uncertain row is kept and flagged, never dropped. Values are parsed exactly; zero is never substituted for missing text.

### 7.3 `DeclarationCompleteness`

| Field | Required | Meaning |
|---|---|---|
| `state` | yes | `complete`, `partial`, `unreadable`, `explicitly_empty` |
| `reasons[]` | yes | May be empty only when `complete` |
| `unparsed_region_count` | yes | Visible regions not turned into rows |
| `layout_family` | nullable | Null with a reason when no supported family matched |
| `confirmed_by`, `confirmed_at`, `review_revision` | nullable | Present when a human set the state |
| `source` | yes | `parser` or `human_confirmation` |

**A parser returning zero rows is `unreadable` or `partial`, never `explicitly_empty`.** Only an explicit surveyor confirmation produces `explicitly_empty`. This distinction gates possible additions (v2 section 8.2).

### 7.4 `PenMark`

| Field | Required | Meaning |
|---|---|---|
| `mark_id` | yes | Stable |
| `page_id`, `box_norm` | yes | Location on the corrected render |
| `mark_type` | yes | `exclusion` or `price_change` |
| `detection_confidence` | nullable | Null for a human-added mark |
| `entry_id` | nullable | Null when linking was ambiguous, with a `link_reason` |
| `candidate_entry_ids[]` | yes, may be empty | Retained rows the mark might belong to. Always populated when `entry_id` is null |
| `link_reason` | yes | `unambiguous_row_overlap`, `mark_between_rows`, `no_candidate_row`, `human_link`, and similar stable codes |
| `state` | yes | `pending`, `confirmed`, `rejected` |
| `confirmed_amount`, `confirmed_currency`, `confirmed_cost_basis` | nullable | **The human-entered amount.** Required when a `price_change` mark is confirmed |
| `trocr_suggestion` | nullable | `{text, confidence}` from stretch S2 only. **Held separately from `confirmed_amount` and never copied into it** |
| `decision_action_id`, `decided_by`, `decided_at`, `review_revision` | nullable | Present once the state leaves `pending` |
| `origin` | yes | `detector` or `human_added` |
| `versions` | yes | Detector version, link configuration version |

Rules:

- Every detector-produced mark is created `pending`. The detector never confirms anything.
- A detected price-change box is **not a read amount**. A TrOCR suggestion is a suggestion and never an effective price.
- Confirming one mark never silently resolves another pending mark.
- Conflicting marks on one row stay unresolved and block that row's checks until corrected.
- The surveyor can add a missed mark. It is stored with `origin = human_added`, `state = confirmed` and human provenance.
- A rejected mark is retained. Rejection dismisses a false detection; it does not delete the record.

## 8. Cost records

### 8.1 The v2 cost key

The comparison key is exactly four fields:

**`part_code` + `operation` + `vehicle_class` + `currency`**, under one versioned, fixed `cost_basis`.

Excluded from the key, deliberately (v2 section 8.3):

| Excluded | Why |
|---|---|
| Model year | Display metadata only. Excluded from generation, model features and lookup |
| Side | Left and right price pooling is a synthetic convention. It does not resolve photographic side identity, and physical identity is still required for the photo check |
| Damage type | Cost depends on part and operation, not on how the damage looks |

The generator, the LightGBM features, the percentile groups and the published lookup all use these same four dimensions. Any fallback grouping must be explicit, versioned, calibrated and exposed as comparison criteria. Repair, replacement and paint never pool.

### 8.2 `ReferenceCostRange`

| Field | Required | Meaning |
|---|---|---|
| `table_version` | yes | Pinned into every assessment that used it |
| `range_id` | yes | Stable within the table |
| `part_code`, `operation`, `vehicle_class`, `currency` | yes | The complete key |
| `cost_basis` | yes | The single fixed basis, for example `single_part_pre_tax_no_discount_v1` |
| `lower_amount`, `upper_amount` | yes | Decimal strings. Crossed bounds fail the build and can never be served |
| `independent_base_case_count` | yes | **Independent eligible base cases**, not repeated quotes from one case |
| `nominal_coverage` | yes | Initially `0.90` |
| `observed_calibration` | nullable | Validation coverage and width, or a reference to the report |
| `as_of_date`, `cutoff_date` | yes | Reproducible synthetic dates. They do not demonstrate real market drift |
| `method` | yes | `empirical_percentile` or `lightgbm_quantile` |
| `synthetic` | yes | Always `true` in this project |
| `provenance` | yes | Generator reference, version and seed |
| `support_status` | yes | `supported` or `withheld` with a reason |

A missing range is **null with a reason**, never `[0, 0]`. An unsupported combination receives no range. Keys below the validation-selected minimum independent support receive no range.

### 8.3 `CostCheck`

| Field | Required | Meaning |
|---|---|---|
| `entry_id` | yes | The line item checked |
| `result` | yes | `within_range`, `outside_range`, `insufficient_support`, `not_evaluated` |
| `lower_amount`, `upper_amount`, `range_id` | nullable | Present only when a range was applied |
| `direction` | nullable | `below` or `above`, present only when `outside_range` |
| `absolute_deviation` | nullable | Decimal string money |
| `normalised_score` | nullable | Only if a defined normalisation exists |
| `independent_base_case_count` | nullable | Copied from the applied range |
| `reason_code` | yes | Required for every result that is not `within_range` or `outside_range` |
| `policy_version`, `cost_table_version` | yes | Pinned |

Arithmetic, fixed: for an eligible amount `a` and bounds `[L, U]`, equality at a bound is **within range**; absolute deviation is `L - a` below `L`, `a - U` above `U`, and zero inside. Never divide by `U - L` without handling a zero width. A zero-width interval is valid only if the build manifest permits it.

Do not compute a deviation for incompatible amounts or units. Bundled operations, unspecified bases, quantities other than one, currencies other than SGD, unknown vehicle classes and unsupported part and operation combinations receive **no** cost comparison, with a reason. Exceeding a reference interval indicates an unusual synthetic price, not proof of an incorrect price and not fraud.

### 8.4 `ApprovalRecord` and `ReferenceBuildMember`

`ApprovalRecord`: approval ID, source import or external record ID, claim and reviewed entry lineage, status, final approved amount with currency and cost basis, approver or source, approval and effective timestamps, synthetic marker, optional superseded approval ID. **The final approved amount is never used in the assessment of the same claim** (v2 section 2.3). Rejected, pending, superseded and unapproved estimates are ineligible for cost training. Approval import is stretch S3; in the core scope the record exists in the contract and stays empty.

`ReferenceBuildMember`: build ID and table version, eligible approval or documented synthetic seed record ID, source hash, inclusion or exclusion reason, cutoff, split assignment, base-case ID and weight. Deduplicate by source and lineage: multiple workshop quotes of one synthetic base case must not inflate the independent support count.

## 9. Assessment findings and possible additions

### 9.1 `AssessmentFinding`

| Field | Required | Meaning |
|---|---|---|
| `finding_id`, `assessment_revision` | yes | Immutable |
| `entry_id` | yes | The line item this finding is about |
| `documentary_check` | yes | Result plus detail: schema readability and mapping |
| `photographic_check` | yes | Result plus detail: supported, absent visible support, or unable to assess |
| `cost_check` | yes | A `CostCheck` |
| `mark_state_check` | yes | Result plus detail: pending, conflicting or unlinked marks on this row |
| `overall_result` | yes | `ok`, `unsupported`, `cost_outlier`, `insufficient_evidence`, `not_evaluated` |
| `row_state` | yes | `active` or `excluded`. A confirmed exclusion is a row state, **not** an `ok` finding |
| `reasons[]` | yes | Stable reason codes with readable text |
| `evidence_refs[]` | yes | Photos, masks, page boxes, mark boxes and the applied range |
| `applied_range_id` | nullable | Null with a reason |
| `pinned_versions` | yes | Every model, parser, taxonomy, configuration and cost-table version used |
| `created_at` | yes | |
| `explanation_sentence` | nullable | Stretch `cmev-explainer` output only, with `provenance.source_kind = "explainer"`. Never a check result |

Per-check results are `passed`, `failed`, `insufficient`, `not_evaluated`. **A skipped check is `not_evaluated`, never `passed`.** Each check is stored separately as well as the overall result: a passed photo check can sit beside a withheld cost check. Overall ordering is specified by [M8](module-08-consolidation-checks.md).

The four displayed results are unchanged from v2 section 7.2. A fifth stored value, `not_evaluated`, carries a row that was deliberately not checked:

| `overall_result` | Display label | Meaning |
|---|---|---|
| `ok` | No discrepancy found | Supported and within range |
| `unsupported` | No supporting damage detected in adequate views | Resolved identity, confirmed adequate coverage, and confidently no supported damage type detected |
| `cost_outlier` | Cost outside reference range | Supported, but the effective price is outside the range |
| `insufficient_evidence` | More information needed | Pending mark, unresolved identity or amount, incomplete extraction, uncertain damage, inadequate views, or no compatible range |
| `not_evaluated` | No result chip. The row carries its excluded treatment instead | The row was deliberately not checked. Currently this means a confirmed exclusion, with `row_state = excluded` and every per-check result `not_evaluated` |

`insufficient_evidence` is informational and must be visually distinct from a discrepancy flag.

**On the fifth value.** V2 section 7.2 lists four results because it is describing what the overview displays for a checked row, and it states separately that exclusion is a row state and not an `ok` finding. Those four remain the only display labels. `not_evaluated` exists so that a deliberately unchecked row has somewhere valid to be stored: without it, an excluded row either has no finding at all or is forced into `ok`, and forcing it into `ok` would break the invariant that exclusion is not a pass. The [interface specification](ui_specification.md) already renders such a row with an "Excluded by surveyor" chip and no result chip, and [M8](module-08-consolidation-checks.md) already writes it. This entry makes the contract agree with both. An excluded row must never be stored or displayed as `ok`.

### 9.2 `ProposedRepairAddition`

Candidate ID, assessment revision, summary or observation IDs, resolved `part_code` and `side`, reason, supporting evidence references, review state. It has **no** `entry_id`, **no** operation and **no** amount until the surveyor supplies them. An unresolved declared row that could match the observation must produce an ambiguity for review, not a confident omission. Suppressed candidates are recorded with their suppression reason, including an existing confirmed exclusion on the same resolved part and side.

## 10. Human review, actions and confirmations

`ReviewEvent`: event and action ID, claim, assessment revision, expected and resulting review revision, actor, timestamp, target finding, entry, mark or candidate ID, action type, reason code and note, original value references, new values, and a client idempotency key.

Action types in v2:

| Action | Creates a new assessment | Note |
|---|---|---|
| `confirm_mark` | Yes | Exclusion or price change. A confirmed price change requires an amount |
| `reject_mark` | Yes | Dismisses a false detection. The record is retained |
| `add_mark` | Yes | A mark the detector missed, stored with human provenance |
| `correct_mark_link` | Yes | Fixes the row association |
| `enter_amount` | Yes | Manual effective amount entry |
| `correct_line_item` | Yes | Part, side, operation, quantity or row link |
| `confirm_identity` | Yes | Creates an `IdentityConfirmation` |
| `confirm_coverage` | Yes | Creates a `CoverageConfirmation` |
| `confirm_declaration_completeness` | Yes | Sets `DeclarationCompleteness` with human provenance |
| `accept_addition` | Yes | Surveyor supplies the operation and any amount |
| `dismiss_addition` | No | Review revision only |
| `dismiss_finding` | No | Review revision only. Reason from v2 section 2.6 |
| `add_note` | No | Review revision only |

Rules:

- Machine records remain **immutable**. A decision-changing correction or confirmation records the actor, the original value, the new value and the source, then creates a new input and assessment revision (v2 section 8.4).
- A dismissal applies to the original finding in the original assessment, survives reload and retry, and is never blindly carried into changed evidence.
- Review saves carry an idempotency key and an expected review revision. A repeat returns the original result; a stale conflicting edit is rejected for reload rather than overwriting newer decisions.
- Dismissal reasons come from the v2 section 2.6 list: hidden damage found after dismantling, ADAS calibration or specialist procedure, parts price change, inadequate photograph, system error, other. The reason stays in the record.
- A surveyor's edit is **not** an approval.

## 11. Finalization and print

`Finalization`: assessment revision, frozen review revision, actor, timestamp, and the precondition results that allowed it. Finalize is refused while any mark is `pending` or unlinked, while a required reassessment is unfinished, or while a required stage has failed. `insufficient_evidence` results may remain in the report with their reasons; finalizing does not turn them into passes.

The print payload is derived from **one** frozen assessment and its matching frozen review revision. It never mixes new values with stale findings. The footer carries the input, assessment and review revisions and every pinned model, parser, configuration and cost-table version. Synthetic costs and the fixed cost basis are labelled. Final approval shows as not recorded unless a stretch S3 import actually happened.

## 12. Record to table to topic map

Schemas are PostgreSQL schemas. This table is the alignment check: contract, database and transport must agree.

| Record | PostgreSQL table | Written by | Kafka topic that carries it |
|---|---|---|---|
| `ClaimInput` | `claim.claims`, `claim.input_revisions` | `cmev-api` | `cmev.evt.input-revision-created.v1` |
| `ClaimFile` | `claim.files` | `cmev-api` | Referenced in `cmev.evt.input-revision-created.v1` |
| `PartPrediction` | `imaging.part_predictions` | `cmev-worker-parts` | Summarised in `cmev.evt.parts-segmented.v1` |
| `ImageQuality` | `imaging.image_quality` | `cmev-worker-parts` | `cmev.evt.parts-segmented.v1` |
| `ImageDamageObservation` | `imaging.damage_observations` | `cmev-worker-damage` | `cmev.evt.damage-segmented.v1` |
| `PartSummary` | `imaging.summary_groups`, `imaging.summary_members` | `cmev-worker-summary` | IDs in `cmev.evt.part-summarised.v1` |
| `PartCoverage` | `imaging.part_coverage` | `cmev-worker-summary` | IDs in `cmev.evt.part-summarised.v1` |
| `IdentityConfirmation` | `review.identity_confirmations` | `cmev-api` | Passed into `cmev.cmd.part-summary.v1` |
| `CoverageConfirmation` | `review.coverage_confirmations` | `cmev-api` | Passed into `cmev.cmd.part-summary.v1` |
| `DocumentPage` | `document.page_readings`, `document.text_boxes` | `cmev-worker-ocr` | `cmev.evt.page-read.v1` |
| `LineItem` | `document.line_items`, `document.line_item_fields` | `cmev-worker-lineitems`, corrected by `cmev-api` | `cmev.evt.line-items-extracted.v1` |
| `DeclarationCompleteness` | `document.declaration_status` | `cmev-worker-lineitems`, confirmed by `cmev-api` | `cmev.evt.line-items-extracted.v1` |
| `PenMark` | `document.pen_marks` | `cmev-worker-penmarks`, decided by `cmev-api` | `cmev.evt.pen-marks-detected.v1` |
| `ReferenceCostRange` | `costs.reference_ranges` | Offline M7 build | None. Read from the database by `cmev-consolidator` and `cmev-api` |
| `ReferenceBuildMember` | `costs.build_members` | Offline M7 build | None |
| `CostCheck` | `assessment.finding_checks` | `cmev-consolidator` | Counted in `cmev.evt.assessment-ready.v1` |
| `AssessmentFinding` | `assessment.findings`, `assessment.finding_checks` | `cmev-consolidator` | Counted in `cmev.evt.assessment-ready.v1` |
| `ProposedRepairAddition` | `assessment.possible_additions` | `cmev-consolidator` | Counted in `cmev.evt.assessment-ready.v1` |
| `ReviewEvent` | `review.review_actions` | `cmev-api` | Triggers `cmev.cmd.consolidate.v1` when decision-changing |
| `Finalization` | `review.finalizations` | `cmev-api` | None |
| `ApprovalRecord` | `costs.approvals` | `cmev-api`, stretch S3 | None |
| Job and failure state | `ops.jobs`, `ops.job_attempts`, `ops.branch_state`, `ops.dead_letters` | `cmev-orchestrator` | `cmev.evt.job-failed.v1`, `cmev.dlq.v1` |
| Idempotency and transport state | `ops.consumed_messages`, `ops.outbox`, `ops.idempotency_keys` | Every service | None. Platform only |

Only `cmev-api` writes to the `claim` and `review` schemas. Only the owning worker writes to its own `imaging` or `document` tables. Only `cmev-consolidator` writes to `assessment`. The offline build is the only writer of `costs.reference_ranges`.

## 13. Invariants

- The three amounts stay separate: workshop printed price, surveyor effective price, final approved amount. None overwrites another.
- A pending price change never falls back to the printed amount.
- Only a confirmed exclusion removes a row from checks, and it is a row state, not an `ok` finding.
- Side is never inferred by a model. Only a recorded identity confirmation attaches one.
- `adequate` coverage requires a recorded human confirmation. Sharpness and mask size are screening signals only.
- Machine results are immutable. Human actions create new revisions and carry their own provenance.
- A skipped check is `not_evaluated`. A failed job is a processing failure, never an empty successful result.
- Every persisted row stores the versions that produced it. A newer cost table never changes an existing assessment.
- Money is an exact decimal with currency and cost basis. A missing amount is null with a reason, never zero.
- A missing range is null with a reason, never `[0, 0]`.
- Independent support counts base cases, not quotes.
- The two damage taxonomies are never merged, and v1 payloads are rejected, never reinterpreted.
- Fixture records carry `provenance.source_kind = "fixture"` and can never be shown as real inference.

## 14. Tasks

- [ ] Implement Pydantic and SQLAlchemy definitions for every record, with valid and invalid examples per record.
- [ ] Freeze the part vocabulary, the six CarDD damage codes, operations, the four vehicle classes and every alias table, with a recorded taxonomy version.
- [ ] Record the HITL contingency damage taxonomy under its own distinct version, with an explicit no-merge test.
- [ ] Record the HITL folder-name swap in the acquisition manifest and add a loader test that maps by inspected class names, not folder names.
- [ ] Freeze the fixed SGD cost basis and the quantity, tax and operation semantics.
- [ ] Implement coordinate round-trip checks against original photos and uploaded pages, including rotated, letterboxed and perspective-corrected cases.
- [ ] Enforce revision compatibility, foreign keys, deterministic IDs and uniqueness on retries.
- [ ] Implement the effective-price rule with a test that a pending price change never yields the printed amount.
- [ ] Implement pen-mark state transitions, including conflicting, unlinked and human-added marks.
- [ ] Implement the declaration-completeness distinction between zero parsed rows and a confirmed empty scope.
- [ ] Validate absent ranges, uncertain amounts, explicit empty lists and suppressed possible additions.
- [ ] Implement immutable approval lineage and reference-build eligibility, including base-case deduplication.
- [ ] Implement deterministic review-action replay and stale-edit conflict behaviour.
- [ ] Add the `0.1.0` rejection test and a compatibility and migration policy before any released schema changes.
- [ ] Verify the section 12 map against the actual migrations and the actual topic payloads.

## 15. Open decisions

| Item | Owner | Where to resolve |
|---|---|---|
| Exact alias tables for part and operation text, and the fuzzy-match policy | Lane 2 with Lane 4 | M5 specification, taxonomy configuration |
| The four vehicle-class definitions and the lookup from make, model and year | Lane 4 | v2 section 11.6, M7 specification |
| Whether `paint` is a separate operation in the document vocabulary or only in the cost catalogue | Lane 4 with Lane 2 | Taxonomy freeze, day 2 |
| Whether `CoverageConfirmation` is required for all `adequate` states or only for a negative finding | Lanes 1 and 4 | M3 and M8 specifications |
| The `part_key` format used in the review API path, given that side may be `unknown` | Lane 5 | [application platform](application_platform.md) |
| Whether CarDD consent arrives, and therefore which damage taxonomy is active | Lane 1 | v2 section 12.4, day-2 checkpoint |
| Retention and anonymisation policy before any real claim material is accepted | All | Not yet decided. Until it exists, real claim content must not enter this prototype |
