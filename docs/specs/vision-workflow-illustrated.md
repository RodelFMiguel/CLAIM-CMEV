# Illustrated M1 → M2 → M3 workflow

Reading companion to the module specifications, checked against repository code on 2026-10-06. This documents data transformations and existing deterministic rules; it does not establish that trained models are integrated into the application. No model, contract or threshold decision is changed by these drawings.

The drawings are editable Graphviz sources. Generated exports are local, ignored files under `artifacts/exports/vision-workflow/`. After cloning, generate them from the repository root with:

```sh
python3 docs/diagrams/render_vision_workflow.py
```

The renderer requires Graphviz (`dot`) and Python's standard library. It runs no models and reads no datasets. Open the generated HTML in a browser for diagram selection, zoom and scrolling; all images are embedded and no network access is needed.

| Drawing | Viewer / exports | Editable source |
| --- | --- | --- |
| Full model and module workflow | [Zoomable viewer](../../artifacts/exports/vision-workflow/index.html), [SVG](../../artifacts/exports/vision-workflow/vision-tensor-workflow.svg), [PDF](../../artifacts/exports/vision-workflow/vision-tensor-workflow.pdf), [PNG](../../artifacts/exports/vision-workflow/vision-tensor-workflow.png) | [vision-tensor-workflow.dot](../diagrams/vision-tensor-workflow.dot) |
| Two-photo worked example | [SVG](../../artifacts/exports/vision-workflow/vision-summary-example.svg), [PDF](../../artifacts/exports/vision-workflow/vision-summary-example.pdf), [PNG](../../artifacts/exports/vision-workflow/vision-summary-example.png) | [vision-summary-example.dot](../diagrams/vision-summary-example.dot) |

## Reading the full drawing

Read downward. Blue is M1, orange is M2, green is M3, and yellow represents human confirmations. Arrows describe evidence dependencies; the specified orchestration runs M1, then the complete M2 stage, then M3. The two model branches do not imply that both workers currently run concurrently.

1. Original evidence is retained. RGB images are oriented, resized, padded, normalized and stacked into `[B, 3, H, W]`. `B` is photo count in a batch, not the number of vehicle views merged together. The illustrated 512 frame is an example; model/config selection determines resolution.
2. Separate SegFormer models predict part and damage logits. Native spatial dimensions are shown as `h₁,w₁` and `h₂,w₂` because they depend on the network. Bilinear interpolation restores the input-frame dimensions before class selection. The notebooks' `LogitsAdapter` already includes this interpolation.
3. M1 has 22 score channels. CarDD M2 has seven; the separate HITL M2 experiment has nine. Softmax acts across classes at each pixel. Argmax produces one class ID per pixel. M2 also retains the winning probability for confidence filtering.
4. M2's deterministic postprocessing forms connected damage regions and matches each against M1's part mask by containment. The M1 mask is not an input to the damage neural network. Matching requires the same photo, revision and coordinate grid, with recorded model/config versions. An unresolved assignment retains its candidates and reason.
5. M3 uses structured observations, M1 predictions, numeric quality signals, processing status and confirmations. It creates damage groups and coverage records separately. Its pure core does not load model weights or decode images; `compute_view_signals` is a separate image/mask helper whose measurements must be supplied.
6. A summary retains all distinct source observation IDs and damage types. The representative area fraction is the largest member value; maximum confidence is the highest member confidence, possibly from another member. Neither value is a newly calibrated group probability or a physical area estimate. M3 does not count unique physical dents or add areas across photos.
7. Coverage can be adequate only with resolved identity, suitable passing views and a positive human coverage confirmation naming a passing view. Failure and unresolved identity withhold coverage. M8 applies the repair-comparison rules afterward; M3 does not choose repairs or costs.

## Worked example

The second drawing follows a scratch in photo A and a dent in photo B. Both are assigned to the front-door category by M2. Before review, M3 groups them as `part_only` with side unknown. Once identity confirms the left front door, and suitable coverage is separately confirmed, the result is a resolved part summary plus adequate coverage.

The example keeps both observation IDs. The representative fraction is `1500 / 262144 = 0.005722` from B, while `max_confidence = 0.82` comes from A. These are illustrative values, not predictions from trained models. Separate review actions may create successive revisions; only the completed state after both confirmations is illustrated. A right-side confirmation for B would produce separate physical-part groups.

## Current implementation boundaries

- **M1 adapter:** `PartsSegmenter.segment_photo()` implements model inference, interpolation, softmax, argmax and part-record construction. Its current right/bottom black padding differs from the notebooks' centred mean-colour padding. Match serving preprocessing to the selected checkpoint and reconcile the contract's padding-exclusion requirement before acceptance. The drawing does not silently select a new padding policy. The adapter's image-quality record currently has only basic resolution screening, not the full M3 measurements.
- **Shared notebooks:** `LogitsAdapter.forward()` returns upsampled logits. `_predict()` normally takes argmax directly: softmax preserves the winning class, so probabilities are unnecessary for class-only evaluation. An optional validation-selected background offset changes the selection rule; the full drawing deliberately shows the standard zero-offset path. Confidence semantics for an offset-based serving path still require explicit integration.
- **M2:** region extraction and assignment code exist. The live inference/artifact-persistence adapter remains pending. The shared serving observation schema accepts CarDD damage classes; the eight-class HITL experiment cannot be plugged into it without an explicit contract change.
- **M3:** deterministic grouping, screening and coverage logic exist, with confirmation reassessment connected to fixture evidence. End-to-end live model integration is not demonstrated by this diagram. The M3 specification allows pixel-based screening, while integration §8.4 describes a records-only adapter; the runtime owner of measurement computation needs reconciliation.
- **Thresholds:** numeric region/assignment thresholds shown are proposed configuration values. They are not calibrated results and cannot be assumed transferable unchanged between 512 and 640 frames. M3 quality thresholds also remain uncalibrated.

## Source map

| Behaviour | Code / specification |
| --- | --- |
| Model adapter and resizing | [training.py](../../pipelines/vision/training.py): `build_model`, `LogitsAdapter.forward`, `_predict` |
| M1 runtime inference | [parts/adapter.py](../../src/claim_cmev/vision/parts/adapter.py): `segment_photo` |
| M2 component extraction | [damage/regions.py](../../src/claim_cmev/vision/damage/regions.py): `extract_regions` |
| M2 overlap and records | [damage/assignment.py](../../src/claim_cmev/vision/damage/assignment.py): `assign_damage_to_part` |
| M3 grouping | [multiview/grouping.py](../../src/claim_cmev/vision/multiview/grouping.py): `group_key`, `build_groups` |
| M3 view measurements / coverage | [screening.py](../../src/claim_cmev/vision/multiview/screening.py), [coverage.py](../../src/claim_cmev/vision/multiview/coverage.py) |
| M3 input validation / orchestration | [summary.py](../../src/claim_cmev/vision/multiview/summary.py), [confirmations.py](../../src/claim_cmev/vision/multiview/confirmations.py), [review_summary.py](../../src/claim_cmev/orchestration/review_summary.py) |
| Module processing specifications | [M1](module-01-vehicle-part-segmentation.md#processing-specification), [M2](module-02-damage-segmentation.md#processing-specification), [M3](module-03-part-summary-coverage.md#processing-specification) |
| Exact input/output boundaries | [Integration contracts §8](integration_contracts.md#8-model-input-and-output-contracts), [image records](data_contracts.md#6-image-branch-records) |
| Experimental model choices | [Notebook training guide](../training-hitl-notebooks.md) |
