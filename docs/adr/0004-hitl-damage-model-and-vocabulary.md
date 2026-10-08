# ADR 0004 - HITL damage model served, HITL vocabulary on the `.v1` damage topics

Status: accepted by the user, 2026-10-08. What was run against it is recorded in [CONTEXT.md](../../CONTEXT.md).

Owner: lane 1 (Vision) for the served model and its vocabulary. The message contracts have no named owner yet.

## Context

The [M2 specification](../specs/module-02-damage-segmentation.md) names a SegFormer-B0 trained on CarDD, with six damage codes. Two trained damage models existed on 2026-10-08, and neither is that model:

| Model | Trained on | Vocabulary | Frame it was trained in | Registry entry |
| --- | --- | --- | --- | --- |
| SegFormer-B3, `damage-hitl/0.1.0-b3-compound` (script pipeline, lane 1) | HITL damage subset | Eight HITL labels | The serving frame at 512, the one M1 uses | Yes |
| SegFormer-B2, notebook run `damage_cardd-segformer-20261002T043059Z-7981a76f` | CarDD | Six CarDD codes | Centred letterbox at 640 | No |

M2 lays each damage region over the M1 part mask of the same photograph, so the M2 worker serves only a model trained in the M1 frame. The CarDD run cannot be served as it is; it has to be trained again.

Three questions followed from serving the HITL model:

1. Which model does the M2 worker serve?
2. [Integration contracts](../specs/integration_contracts.md) section 6.2 calls a widened enum and a new required field breaking changes that need `.v2` topics. Do the two damage topics move to `.v2`?
3. M8 may report a declared repair as `unsupported` when adequate views of the right part show no damage of a supported type. Does a model with low recall get to support that finding?

## Decision

**1. Serve the HITL model now; keep the path to CarDD open.** `configs/models/damage.yaml` names `damage-hitl/0.1.0-b3-compound` and taxonomy `damage-hitl-1.0.0`. A CarDD model can replace it by changing that file's `model_id`, `model_version` and `taxonomy_version`, with no code change:

- The CarDD notebook has a recipe `serving_512` that trains in the serving frame, and an export section that calls `pipelines.vision.registry.export_damage_run`.
- The export refuses a run trained in another frame or at another size, and names the entry after the run's vocabulary (`damage-cardd/<name>` or `damage-hitl/<name>`).

**2. Keep the `.v1` topics, as a narrow exception to section 6.2.** `cmev.cmd.damage-segment.v1` and `cmev.evt.damage-segmented.v1` now accept the eight HITL codes and require `versions.taxonomy`. The exception holds because of three conditions, and only while all three hold:

- The schemas bind a code to its vocabulary. Under `damage-cardd-*` a message accepts exactly the six CarDD codes, as before. The HITL codes are accepted only under `damage-hitl-*`.
- Every producer and consumer of the two topics is in this repository and changed in the same commit. M3 and M8 read the vocabulary from the taxonomy version; neither guesses from the spelling of a code.
- No message of either topic is retained from before the change that a current consumer must still read. The stacks are development stacks.

A consumer outside this repository, or a retained topic history, ends the exception and needs `.v2`.

**3. All eight HITL labels count as supported in M8.** `damage.supported_types_hitl` lists all eight. Under the HITL model M8 may therefore report a declared repair as `unsupported`. The finding goes to the surveyor, who sees the photographs and decides; it is not a statement that the damage is absent. No rule was added to withhold it.

The two vocabularies are never merged and never mapped to each other. `dent` and `scratch` are spelt alike in both and are still different labels. M3 refuses to summarise observations of both vocabularies together.

## Alternatives considered

- **Serve the CarDD notebook run.** It matches the specified vocabulary and scores far higher on CarDD photographs. It was trained in another frame, so its mask does not align with the M1 mask. Rejected for now; decision 1 keeps it reachable.
- **Map HITL labels to CarDD codes.** This would keep the six-code contract. The annotation guidelines differ and five HITL labels have no CarDD counterpart. Rejected: [data contracts](../specs/data_contracts.md) section 3.3 forbids an implicit mapping.
- **`.v2` topics.** The rule as written. It costs a second pair of topics, schemas and examples, and a migration window, to protect consumers that do not exist. Rejected while the three conditions hold.
- **Withhold negative findings under the HITL model**, or allow them only for the labels the model finds reliably. This was the implementer's recommendation, given the miss rates below. The user chose to keep the finding and leave the judgement to the surveyor.

## Consequences

- Observations, summaries and assessments made with the served model carry HITL codes. The CarDD target of 0.45 foreground mIoU does not apply to them. Targets for the HITL vocabulary are not recorded yet.
- The specified model is still not trained. Proposal v2 sections 11.2 and 12.4 describe HITL damage as an access contingency; those sections are not rewritten here.
- An `unsupported` finding made with this model is weak evidence. The served model leaves about one labelled damage region in three without any detection (see below). The review screen and the printed report list the pinned versions, the damage model among them; they carry no warning about its miss rate.
- Switching to a CarDD model later changes the pinned versions, so claims are reassessed and not silently reinterpreted. Stored assessments keep the vocabulary they were made with.
- The files `configs/taxonomy/damage_hitl.yaml` and `damage_cardd.yaml` were not edited. `damage_hitl.yaml` still says `active: false`. The hash of each taxonomy file is part of the identity of the notebooks' prepared training data, so an edit, even to a comment, stops `data/processed/hitl/v1` to `v3` and `data/processed/cardd/v1` from reloading. Which vocabulary is served is decided by `configs/models/damage.yaml`, and which vocabularies a record may carry by `DAMAGE_VOCABULARIES` in `src/claim_cmev/contracts/common.py`. A test now pins the three file hashes.

## Validation evidence

Measured on 2026-10-08 on validation photographs only, on one workstation (Apple GPU). Each model ran in the frame and at the size it was trained in. The comparison is "any damage" against "no damage", so the two vocabularies need no mapping. A prediction counts when its confidence is at least 0.5. A labelled region counts as found when a kept predicted region covers at least 10% of it; regions and predictions smaller than 0.1% of the frame are left out. The script and its output are in the ignored folder `artifacts/evaluation/damage-model-choice-20261008/`.

| Photographs | Model | Any-damage IoU | Recall | Labelled regions found | Photographs with damage and no detection |
| --- | --- | ---: | ---: | ---: | ---: |
| HITL validation, 71 | HITL B3 (served) | 0.389 | 0.501 | 141 of 210 (67%) | 2 of 71 |
| HITL validation, 71 | CarDD B2 (notebook) | 0.229 | 0.268 | 70 of 210 (33%) | 10 of 71 |
| CarDD validation, 810 | HITL B3 (served) | 0.253 | 0.303 | 1,022 of 1,411 (72%) | 55 of 810 |
| CarDD validation, 810 | CarDD B2 (notebook) | 0.809 | 0.920 | 1,313 of 1,407 (93%) | 0 of 810 |

The 71 HITL photographs are those in the validation split of both HITL pipelines, so neither HITL-trained model saw them. The CarDD region counts differ by four between the two rows because each model's frame resizes the labels differently.

Share of each labelled HITL type's pixels that the served model marks as any damage, on the same 71 photographs: flaking 0.15, broken-part 0.40, paint-chip 0.43, missing-part 0.52, dent 0.61, scratch 0.68, corrosion 0.69, cracked 0.76. The CarDD model on the same photographs finds 0.09 of broken-part, 0.07 of missing-part, 0.07 of corrosion and 0.03 of flaking.

Limits of this evidence:

- These are validation figures. The CarDD model's validation set was also used to select its checkpoint, so its own-data row is optimistic.
- No photograph of a real claim was used. Neither dataset says how either model behaves on the team's photographs.
- The CarDD model was measured in its own centred frame. A CarDD model trained in the serving frame at 512 does not exist yet and may score differently.

The export path and the switch were checked by tests on small random-weight models, not by a trained CarDD export: `tests/unit/test_vision_registry_export.py` and `tests/integration/test_image_branch.py::test_a_cardd_model_is_served_by_changing_only_the_damage_configuration`.

## Affected specifications

- [Integration contracts](../specs/integration_contracts.md) sections 6.2 and 6.3: the exception and its conditions.
- [Data contracts](../specs/data_contracts.md) section 3.3: the HITL vocabulary on records.
- [M2](../specs/module-02-damage-segmentation.md), [M3](../specs/module-03-part-summary-coverage.md) and [M8](../specs/module-08-consolidation-checks.md): dated serving notes.
- Not changed: [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) sections 11.2 and 12.4, and the M2 specification's tables, which still describe the CarDD model.
