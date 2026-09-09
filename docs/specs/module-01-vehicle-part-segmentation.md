# M01 - Vehicle part segmentation

Owner: Lane 1. Runtime: image worker. Code: `src/claim_cmev/vision/parts/`; training: `pipelines/vision/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), sections 11 (Module 1), 12.1, 14.1. Status: specified; implementation pending.

## Purpose and scope

Label supported vehicle-part pixels in each photograph so downstream modules can assign damage and assess visibility. This module detects parts, not damage or repair necessity. An unsided dataset label does not establish left/right identity.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Validated claim/photo references and dimensions, input revision, model/preprocessing manifest, taxonomy version |
| Output | `PartPrediction` per detected part with mask, confidence, original-photo reference and transforms; quality/preprocessing metadata |
| Consumers | M02 damage matching; M03 coverage and aggregation |
| Shared dependency | [Data contracts](data_contracts.md), taxonomy, image loading/preprocessing, artifact storage |

Initial classes are the 21 HITL categories enumerated in the data contracts. Background has no repair identity. Store `side=unknown` unless a documented mapping or evaluated supplementary side method resolves it.

## Processing specification

1. Validate the photo and preserve its original hash/orientation.
2. Decode and preprocess through the versioned shared vision pipeline; retain resize, padding, and rotation transforms.
3. Run the pinned fine-tuned SegFormer checkpoint.
4. Convert class predictions into referenced masks and documented confidence summaries.
5. Map source labels to canonical parts without forcing ambiguous side or part matches.
6. Publish schema-validated records/artifacts and actual checkpoint/config versions.

Use HITL as the initial part dataset. Evaluate supplementary DSMLR labels for side information; reserve datasets require licence/access review. Share conversion/training infrastructure with M02 while keeping task labels, checkpoints, and results separate.

## Failure and uncertainty handling

Corrupt or unsupported images produce explicit file/job errors. No vehicle/part prediction produces an empty detection result with quality/status context, not proof that every part is unphotographed or undamaged. M03 owns coverage judgement. Preserve small/uncertain predictions for configured downstream handling; do not silently invent a canonical class. A missing/incompatible model fails readiness.

## Acceptance criteria

- Initial mIoU >= 0.60; no agreed supported panel class below 0.40; report all class scores and support.
- Image-to-mask overlays align on original images including rotated/padded examples.
- Unresolved side is preserved across the worker boundary.
- Reprocessing the same job does not create duplicate prediction records.
- Published evaluation distinguishes source data and model/configuration versions; results are not presented as achieved until measured.

## Implementation tasks

- [ ] Verify HITL/supplementary access, original licences, annotation counts and usable classes.
- [ ] Agree source-to-canonical labels, panel classes, background policy and side handling.
- [ ] Build shared conversion/preprocessing with M02 and group-aware split manifests.
- [ ] Establish SegFormer baseline, then fine-tune and record configuration/seeds.
- [ ] Export per-class metrics and original-coordinate mask artifacts.
- [ ] Package the model manifest and worker inference adapter.
- [ ] Validate overlays, corrupt images, unknown sides, and retry identity.
- [ ] Run the [evaluation plan](evaluation_plan.md) and document shortfalls.

## Open decisions

Checkpoint size and hardware budget; confidence/quality thresholds; supplementary side labels. Select using validation evidence and record in versioned configuration.
