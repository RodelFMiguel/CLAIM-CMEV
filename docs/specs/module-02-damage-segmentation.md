# M02 - Damage segmentation and part matching

Owner: Lane 2. Runtime: image worker. Code: `src/claim_cmev/vision/damage/`; training: `pipelines/vision/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), Module 2 and sections 12.2, 14.1. Status: specified; implementation pending.

## Purpose and scope

Locate visible damage and associate each region with a vehicle part. Damage area is measured in image pixels/fractions, not physical size or an automatically required repair operation.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Photo references/transforms, M01 part masks from matching input revision, damage model manifest and taxonomy |
| Output | `ImageDamageObservation`: ID, photo, damage class, canonical part/side or unresolved candidates, confidence, area, masks, matching score/status |
| Consumer | M03 aggregation; M08 through the merged observations |
| Dependencies | M01 masks, [data contracts](data_contracts.md), shared vision preprocessing and artifact storage |

Initial classes: dent, cracked, scratch, flaking, broken-part, paint-chip, missing-part, corrosion. Retain source labels and mapping reports; dataset categories need not map one-to-one.

## Processing specification

1. Run the pinned SegFormer damage checkpoint using the shared coordinate pipeline.
2. Extract damage regions and publish their masks/confidence.
3. Intersect each damage mask with canonical part masks; calculate the configured overlap measure.
4. Assign the part only when the matching policy supports it. Split or retain multi-part candidates when a region crosses a panel boundary; document the policy.
5. Record image area and part-relative fraction with an explicit denominator.
6. Persist uncertain/unassigned regions so downstream coverage cannot mistake ambiguity for clean evidence.

Use accessible licensed HITL/VehiDE/CarDD data after verification. For RQ2, compare training with and without synthetic joint part/damage labels from CrashCar101 under otherwise comparable configurations and held-out real-image evaluation.

## Failure and uncertainty handling

Missing part masks do not erase a damage detection; retain an unresolved association. A missing-part damage region may have little overlap with an extant part mask: explicitly handle this case or leave identity unresolved. Different damage classes may coexist on a part. Do not force dents/scratches/cracks into one label solely to simplify matching.

Corrupt input or a failed model is an operational error. A valid run with no accepted damage is distinct from failed inference or ambiguous low-confidence damage. Propagate uncertainty for M03/M08 to withhold judgement where needed.

## Acceptance criteria

- Initial damage mIoU >= 0.45; report per-class IoU/AP and separate dent/scratch/cracked results.
- Matching evaluation reports correct part assignments, unresolved assignments, and boundary-crossing errors.
- A damage region without a reliable part stays observable and unresolved.
- RQ2 results separate real transfer performance from synthetic validation.
- Masks and areas remain traceable to originals and repeatable under the pinned configuration.

## Implementation tasks

- [ ] Verify source access/licences and map the eight damage classes.
- [ ] Build data conversion with M01 and deduplicate/group split sources.
- [ ] Train and evaluate the SegFormer baseline.
- [ ] Define overlap, multi-part, missing-part and uncertainty policies.
- [ ] Implement mask association and original-coordinate area references.
- [ ] Compare joint-label synthetic training for RQ2.
- [ ] Integrate the image worker with actual model/version reporting.
- [ ] Validate overlapping classes, ambiguous parts, missing masks and retries.
- [ ] Publish metrics and limitations under the [evaluation plan](evaluation_plan.md).

## Open decisions

Data access, confidence/matching thresholds, handling overlapping damage classes, and precise region-splitting policy. Calibrate without consulting the final test set.
