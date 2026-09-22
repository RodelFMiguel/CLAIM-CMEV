# Image worker

Owners: Lanes 1-2; Lane 5 supplies job/storage integration.

Entrypoint for [M1](../../docs/specs/module-01-vehicle-part-segmentation.md), [M2](../../docs/specs/module-02-damage-segmentation.md) and [M3](../../docs/specs/module-03-part-summary-coverage.md). Load a verified model/config bundle, lease a revision-scoped job, run domain modules, and publish artifacts/results atomically.

Image quality and coverage remain separate from damage detections. Never turn missing checkpoints, failed inference or unknown sides into clean evidence. Keep fitting and training in `pipelines/vision/`. This directory currently reserves the runtime boundary.
