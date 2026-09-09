# M03 - Multi-view aggregation and coverage

Owner: Lane 2, supported by Lane 1. Runtime: image worker. Code: `src/claim_cmev/vision/multiview/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), Module 3 and sections 12.3, 14.1. Status: specified; method selection remains open.

## Purpose and scope

Produce one vehicle damage summary without double-counting repeated views, and separately state which supported parts can be assessed. A damage-only list is insufficient because it omits adequately visible undamaged parts.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Same-vehicle photo group, M01 part masks, M02 per-image observations, image-quality/visibility measures, optional pose/camera metadata |
| Output | Merged `DamageObservation` records with duplicate/member IDs and all evidence; `PartCoverage` for every supported part/side slot |
| Consumers | M08 comparison, M09 vehicle overview |
| Dependencies | M01-M02, taxonomy, [data contracts](data_contracts.md), quality and aggregation configuration |

Coverage states: `adequate / inadequate / not_visible / unresolved`. The output retains covering photo IDs and quality/visibility reasons even when no damage is detected.

## Processing specification

1. Verify all observations belong to the same claim/input revision and compatible image model bundle.
2. Assess per-view visibility and quality, including blur, lighting, obstruction, resolution and part/side certainty.
3. Investigate mapping single-view detections onto a common 3D vehicle representation, as proposed; document pose/alignment assumptions.
4. Merge only observations with sufficient evidence of the same physical damage. Preserve group membership and all photo/mask references.
5. Retain distinct damage instances on the same part. Do not merge solely on part name and damage type.
6. Build coverage independently of damage presence. Adequacy requires a calibrated visibility/quality policy; photo count alone is not sufficient.
7. Publish aggregation and coverage policy versions with summary results.

Use controlled CrashCar101 groups for development and RQ3. Record whether real grouped vehicle data exists. If the projection method is not feasible, record an ADR and evaluate a conservative alternative against a no-merge baseline; it must not claim duplicate removal that was not measured.

## Failure and uncertainty handling

Unresolved side, conflicting identity, uncertain pose or low-quality coverage prevents a confident negative damage judgement. Missing camera metadata does not justify fabricating a projection. Preserve uncertain observations separately. A single usable photograph may support coverage, but cannot establish multi-view performance.

Do not infer adequate coverage from a low damage score or deduplicate separate left/right panels. Store area by documented representative/aggregation policy rather than summing repeated masks.

## Acceptance criteria

- Initial duplicate reduction >= 90% on labelled grouped data.
- Report vehicle-summary F1, false merges and lost distinct damage alongside duplicate reduction.
- Adequate undamaged, inadequate and unseen parts remain distinguishable.
- Every merged observation can be expanded to its source observations and artifacts.
- Synthetic and real group results are reported separately; thresholds have validation evidence.

## Implementation tasks

- [ ] Register grouped datasets, available camera metadata, and split by vehicle/base scene.
- [ ] Define ground-truth duplicate groups and vehicle-summary matching metrics.
- [ ] Implement quality/visibility measurements and part/side coverage slots.
- [ ] Evaluate projection feasibility and record the selected aggregation method.
- [ ] Implement conservative merging, member lineage and area policy.
- [ ] Calibrate quality/coverage/merge thresholds on held-out validation data.
- [ ] Test separate damage on one panel, repeated images, unknown sides and absent views.
- [ ] Integrate coverage/summary outputs with M08-M09.
- [ ] Report RQ3 and the [evaluation plan](evaluation_plan.md) metrics.

## Open decisions

Projection method, pose estimation, real grouped test data, per-part coverage adequacy, and duplicate matching tolerances. These must be resolved or explicitly limited before final evaluation.
