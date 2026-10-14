# M4 pretrained OCR worker

M4 uses pretrained English PaddleOCR **2.10.0** with PaddlePaddle **2.6.2** on CPU.
There is no CLAIM-CMEV OCR training step. The selected models are English PP-OCRv3
text detection, English PP-OCRv4 recognition and mobile v2 angle classification.
The served output remains **line regions**. The M5_Document_Worker notebook export declares line-box preprocessing and accepts these regions. The older word-only checkpoint still requires true word geometry.

## Provision the reference release

The repository uses `artifacts/models` (plural), consistently with the image model
registry. This release is `artifacts/models/ocr/paddleocr-2.10.0-en-v1/`.

```sh
python scripts/download_ocr_models.py
python scripts/download_ocr_models.py --verify-only
```

The downloader checks the archive and all nine inference artifact SHA-256 values
against `configs/models/ocr-manifest.json`. It keeps original downloaded archives,
installed `det`, `rec`, `cls` files and the manifest. The hashes were calculated from
official HTTPS downloads on 2026-10-11; they are reproducibility pins, not upstream
signed checksums. Weights stay ignored in Git. The registry verifier compares the
entire manifest and every file before native model loading. Runtime never downloads
missing weights. A corrupt or missing entry prevents worker startup.

Upstream [model list](https://www.paddleocr.ai/v2.10.0/en/ppocr/model_list.html),
[English inference example](https://www.paddleocr.ai/v2.10.0/en/infer_deploy/paddle2onnx.html)
and [Apache-2.0 license](https://github.com/PaddlePaddle/PaddleOCR/blob/v2.10.0/LICENSE)
are retained in the manifest. The recognition character dictionary is supplied by
the pinned PaddleOCR package; no local dictionary override is enabled.

## Container and ownership

```sh
docker compose --env-file infra/compose/.env \
  -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.ocr.yml --profile lean up --build -d
```

The OCR overlay switches page reading ownership together across API/orchestration/
fixture/consolidation services and starts `cmev-worker-ocr`. It can be combined with
`docker-compose.image.yml`. In this OCR-only overlay M5 and M6 remain fixtures; use the complete document overlay below to run their models. A live OCR stage alone does not make the full assessment real.

`infra/Dockerfile.ocr` installs the OCR stack separately from vision and LayoutLMv3.
Paddle2.x needs NumPy1.x: `infra/requirements-ocr.txt` pins NumPy1.26.4 and compatible
OpenCV packages, plus setuptools because Paddle imports it at runtime. Models are
mounted read-only. `CMEV_OCR_MODEL_CONFIG`, `CMEV_OCR_MANIFEST`, `CMEV_M4_CONFIG`
and `CMEV_MODEL_REGISTRY_PATH` select the pinned startup inputs.

All original upload bytes, corrected geometry and OCR uncertainty remain evidence.
This model can recognize handwritten-like text but its outputs cannot confirm an
exclusion or a revised amount; M6/human review own those decisions. Synthetic smoke
checks validate execution and persistence, not held-out real-page OCR accuracy.

## Verification on 2026-10-11

- All nine downloaded inference files and the installed manifest pass the pinned
  integrity verifier. Five model selection/version/missing-file tests pass.
- Linux ARM64 Docker build succeeds with Python3.12, Paddle2.6.2 and NumPy1.26.4.
  The final image includes migration files required by worker startup checks.
- An isolated container with **network disabled** loads the read-only release and
  recognizes `FRT BUMPER 980.00` with confidence0.9415 and a line quadrilateral.
- Raw engine inference on `SYN-00214_P2.jpg` yields 56 regions on the original and
  60 on the surveyor-annotated counterpart. Inputs were mounted read-only;
  `artifacts/evaluation/m4-live-ocr-smoke/results.json` retains actual text,
  quadrilaterals, scores and granularity. This inspects existing synthetic test
  examples for execution only; no thresholds/model selection were tuned here.
- Compose parsing passes without starting the application stack. This does not
  prove a broker/database/object-store live deployment or production throughput.

A native macOS ARM64 smoke loaded the models but did not complete inference during
verification. The supported serving evidence above is the Linux container. There
is no fallback to a different OCR engine on failure. Real permitted estimates,
word alignment and held-out OCR character/amount accuracy remain acceptance work.

The complete `run_page_reading` adapter also succeeds on both pages in the isolated
Linux container: one page/56 text boxes for the original, one page/60 boxes for the
marked image. `artifacts/evaluation/m4-live-ocr-smoke/adapter-results.json` preserves
shared DocumentPage records, artifact references/hashes, quality, transforms and
metrics. This adapter check performs no database/Kafka writes. Combined model-config
and independent registry tests pass: **16 passed**.

An isolated real-worker smoke also exercises committed synthetic upload → orchestrator
command → pretrained OCR → artifact storage + database records + transactional outbox
completion. It recognizes `FRT BUMPER` and `980.00`, persists the page, page reading
and two text-box rows, verifies all artifact hashes, and redelivers the command
without a second OCR call or event. Evidence is `runtime-results.json` beside the
adapter report. It uses SQLite/local storage/in-memory messaging in a network-disabled
Linux container; this is not a Kafka/PostgreSQL/MinIO deployment check.

The worker's configured retry limit is honored and obsolete queued input revisions
are stopped before any read. Existing job/DLQ failure states record unusable pages;
no successful empty result is emitted. Failure artifacts are not committed as
DocumentPage records. Historical successful page records remain readable.

## Complete document branch, 2026-10-14

The [document overlay](document-worker-live.md) now adds live M5/M6 handlers after this worker. The OCR-only overlay still leaves M5/M6 as fixtures. The earlier verification section records historical checks; current live integration evidence is in the 2026-10-14 CONTEXT handoff.
