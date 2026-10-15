# Live document workers

The live document branch is M4 pretrained OCR → M5 LayoutLMv3 extraction → M6 Faster R-CNN and geometric linking. The orchestrator waits for every M4 page before M5, then supplies the actual M5 row/amount boxes to M6. M8 consumes their persisted records. This is integration of candidate models, not evidence that specification targets or real-estimate accuracy have been met.

## Selected models

`configs/models/lineitems.yaml` selects `lineitems/lineitems-layoutlmv3-20261013T141341Z-ec8bcba-s20260922-serving`.
`configs/models/penmarks.yaml` selects `penmarks/penmarks-fasterrcnn-20261013T142838Z-78d92779-serving`.
Both entries remain candidates. Their serving files, including weights, preprocessing and manifests, are pinned by SHA-256. Model loading is local only, once at startup, and missing or changed files prevent startup. Exporting a notebook run does not automatically update the selection.

The selected M5 checkpoint consumes M4 line boxes. The deployment explicitly selects `family-a-ruled-grid`, the fixed single-part cost basis, and confidence threshold 0.5; claim currency comes from intake. It is intended for pages aligned with the synthetic training layout. It does not establish reliable extraction across different layouts, displaced tables, photographs or real handwriting. Missing quantities and ambiguous identities remain unresolved, and neural declaration completeness remains partial.

The separate `configs/pipeline/m6_linking_serving.yaml` uses the latest export's proposed validation operating point: exclusion 0.89, price change 0.59. The original `m6_linking.yaml` is unchanged. These thresholds are selected for this candidate deployment, not accepted accuracy targets. Every detector mark starts pending; only a human can confirm an exclusion or enter a revised amount. TrOCR is disabled.

## Start with the existing application

Provision the ignored model entries under `artifacts/models/` on the machine running Docker. The OCR release must also be present (`scripts/download_ocr_models.py --verify-only` verifies it). Keep the existing local credentials in `infra/compose/.env`.

```sh
sh scripts/live-stack.sh up --build -d
```

The script always uses base + image + document overlays with lean orchestration, and wraps Compose in `caffeinate -i -s` when available. Use the same script for `ps`, `logs` and `down`; `down` keeps stored volumes. Existing database and object-store volumes are retained. Rebuild/recreate API and orchestration together with workers so that command versions and consumer ownership agree. The overlay disables new fixture seed claims; historical claims remain available.

For a document-only live deployment with fixture photographs, invoke Compose directly with the base and document overlays. The document overlay includes M4; do not also add the OCR-only overlay.

### `model_version_unsupported` after restarting

Starting only the base Compose file resets API/orchestration to fixture versions while Compose leaves previously started real workers running. The real OCR worker can then receive a command pinned to `fixture-ocr/0.0.0` and dead-letter it. This is a deployment mismatch; changing OCR weights or weakening version validation will not fix it.

Restore the complete stack with the live start command above. Failed jobs retain their original version pins, so retrying those jobs sends the same unsupported command. To process their original uploads with the active models, create a new input revision using the existing file IDs via `POST /api/v1/claims/{claim_id}/input-revisions` with `{"file_ids": [...]}` and an idempotency key. The API retains original uploads and failed history; reuse is refused when stage versions change. If switching back to fixtures, stop the live stack with this script before starting the base-only stack.

The document overlay runs three services: `cmev-worker-ocr`, `cmev-worker-lineitems`, `cmev-worker-penmarks`. M5 and M6 use the neural `full` runtime even beside the lean orchestration topology. OCR remains isolated with Paddle/NumPy 1.x. The neural image pins the tested Torch/Torchvision/Transformers stack in `infra/requirements-document.txt`. A combined `document` worker role is also available for M5/M6 in one process; do not start it alongside the separate workers.

`CMEV_OCR_PRODUCER`, `CMEV_LINEITEMS_PRODUCER`, and `CMEV_PENMARKS_PRODUCER` switch ownership explicitly. M5 requires real M4; M6 requires both. API/orchestration read model configuration without importing neural libraries or loading weights. Fixture consumers relinquish real stages. When both image and document branches are real, new runtime provenance is real inference; this describes execution on uploaded evidence, not the model's synthetic training provenance or generalization.

## Evidence and review

Workers check command versions, upstream job lineage, page sets, artifact hashes and corrected dimensions. M6 refuses altered row geometry. Outputs and completion events share the existing transaction/outbox; duplicate jobs do not run inference again. Obsolete input revisions are refused before reading pixels, and storage failures use bounded retries. Failed pages never become successful empty results.

M5 persists line items and completeness plus a JSON extraction artifact containing field/row diagnostics, predictions and unparsed regions. M6 persists pending marks and a JSON artifact containing raw detections, original-coordinate quadrilaterals, candidate scores, duplicate/threshold diagnostics, plus page overlays. Artifact references are retained in the producing job's result record. No database migration or Kafka schema change is required.

Assessment snapshots retain corrected-page references and transforms. The authenticated endpoint `GET /api/v1/claims/{claim}/assessments/{revision}/pages/{page}/corrected` checks claim ownership, assessment membership and artifact integrity. The review panel uses it to display extracted rows and marks in their actual coordinate frame; the original file remains available. Old assessments without corrected references continue to display originals. A missing/corrupt corrected image is an error, not an empty page.

Review decisions use the existing revision flow: pending repricing withholds the printed-price fallback; confirmation/rejection creates a new input and assessment revision, reuses unchanged inference with lineage, and preserves the original assessment. Model output is never rewritten as a human decision.

## Validation boundary

Use synthetic training/validation pages for integration checks, not held-out test pages. Verify the live broker/database/object-store path separately from in-memory fixture tests. A successful deployment, worker heartbeat or model loading check is not an accuracy measurement. The dated `CONTEXT.md` handoff records the checks actually performed for this implementation.
