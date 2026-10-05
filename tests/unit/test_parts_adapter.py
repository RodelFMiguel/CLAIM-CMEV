"""Unit tests for M1 vehicle part segmentation adapter, config, and serving handler.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image
import pytest
import torch
from transformers import SegformerConfig, SegformerForSemanticSegmentation

from claim_cmev.contracts.common import (
    Provenance,
    Versions,
    deterministic_id,
)
from claim_cmev.contracts.events import Envelope
from claim_cmev.contracts.events.registry import validate_message
from claim_cmev.messaging.consumer import Context, PermanentError
from claim_cmev.persistence.store import load_records
from claim_cmev.runtime import utcnow
from claim_cmev.storage import Storage
from claim_cmev.vision.palette import ID_TO_PART_CODE, PART_CODE_TO_ID, build_palette
from claim_cmev.vision.parts.adapter import (
    PartsSegmentRequest,
    PartsSegmenter,
    PhotoFileInput,
    make_parts_handler,
    run_parts_segmentation,
)
from claim_cmev.vision.parts.config import PartsConfig, load_parts_config


@pytest.fixture
def dummy_model_dir(tmp_path: Path) -> Path:
    """Create a lightweight SegFormer checkpoint on disk for fast tests."""
    model_dir = tmp_path / "dummy_model"
    model_dir.mkdir(parents=True, exist_ok=True)
    cfg = SegformerConfig(
        num_labels=22,
        id2label={str(i): ID_TO_PART_CODE[i] for i in range(22)},
        label2id={ID_TO_PART_CODE[i]: i for i in range(22)},
        depths=[1, 1, 1, 1],
        hidden_sizes=[16, 32, 64, 128],
        decoder_hidden_size=32,
    )
    model = SegformerForSemanticSegmentation(cfg)
    cfg.save_pretrained(model_dir)
    model.save_pretrained(model_dir)
    return model_dir


@pytest.fixture
def sample_image_bytes() -> bytes:
    """Create a sample 640x480 RGB image with distinct colors."""
    arr = np.zeros((480, 640, 3), dtype=np.uint8)
    arr[50:200, 100:300] = [200, 50, 50]  # bumper/hood like box
    arr[200:400, 300:500] = [50, 200, 50]  # door like box
    img = Image.fromarray(arr, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_parts_config_defaults():
    cfg = PartsConfig()
    assert cfg.model_id == "parts"
    assert cfg.model_version == "parts/0.1.0"
    assert cfg.input_size == 512
    assert cfg.resize_policy == "longest_edge_pad"
    assert cfg.min_part_pixels == 512
    assert cfg.min_part_confidence == 0.50
    assert cfg.device in ("auto", "cuda", "cpu")


def test_parts_config_load_yaml(tmp_path: Path):
    yaml_file = tmp_path / "test_parts.yaml"
    yaml_file.write_text(
        """
model_id: "parts"
model_version: "parts/0.2.0"
input_size: 256
min_part_pixels: 100
min_part_confidence: 0.65
device: "cpu"
""",
        encoding="utf-8",
    )
    cfg = load_parts_config(yaml_file)
    assert cfg.model_version == "parts/0.2.0"
    assert cfg.input_size == 256
    assert cfg.min_part_pixels == 100
    assert cfg.min_part_confidence == 0.65
    assert cfg.device == "cpu"


def test_palette_structure():
    palette = build_palette()
    assert len(palette) == 768
    # Class 0 is background: black (0, 0, 0)
    assert palette[0] == 0
    assert palette[1] == 0
    assert palette[2] == 0


def test_part_codes_mapping():
    assert ID_TO_PART_CODE[0] == "background"
    assert len(ID_TO_PART_CODE) == 22
    assert len(PART_CODE_TO_ID) == 21
    for i in range(1, 22):
        code = ID_TO_PART_CODE[i]
        assert PART_CODE_TO_ID[code] == i


def test_parts_segmenter_cpu_fallback(dummy_model_dir: Path):
    # If cuda is requested but torch.cuda.is_available() is False, fall back cleanly
    with patch("torch.cuda.is_available", return_value=False):
        cfg = PartsConfig(device="cuda")
        segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=cfg)
        assert segmenter.device.type == "cpu"


def test_run_parts_segmentation_batch_and_failures(dummy_model_dir: Path, sample_image_bytes: bytes):
    cfg = PartsConfig(input_size=512, min_part_pixels=10, min_part_confidence=0.01, write_overlay=True)
    segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=cfg)

    good_sha = hashlib.sha256(sample_image_bytes).hexdigest()
    photo_good = PhotoFileInput(
        file_id="ph_01",
        media_type="image/jpeg",
        sha256=good_sha,
        data=sample_image_bytes,
        exif_orientation=1,
    )
    photo_mismatch = PhotoFileInput(
        file_id="ph_02",
        media_type="image/jpeg",
        sha256="0" * 64,  # wrong hash
        data=sample_image_bytes,
        exif_orientation=1,
    )
    corrupt_bytes = b"corrupt_not_an_image"
    photo_corrupt = PhotoFileInput(
        file_id="ph_03",
        media_type="image/jpeg",
        sha256=hashlib.sha256(corrupt_bytes).hexdigest(),
        data=corrupt_bytes,
        exif_orientation=1,
    )

    request = PartsSegmentRequest(
        claim_id="01JAX7Q0VN4Z3K9F2M8R6T1C5D",
        input_revision=1,
        job_key="01JAX7Q0VN4Z3K9F2M8R6T1C5D:1:parts_segment:all:deadbeef",
        photos=(photo_good, photo_mismatch, photo_corrupt),
        versions={"parts_model": "parts/0.1.0", "taxonomy": "parts-1.0.0"},
        provenance={"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-worker-parts"},
    )

    result = run_parts_segmentation(request, segmenter, cfg)
    assert result.processing_status == "partial"
    assert result.metrics["photos_processed"] == 3
    assert result.metrics["photos_failed"] == 2

    # Check outcomes
    good_out = result.photo_outcomes[0]
    assert good_out.status == "succeeded"
    assert good_out.photo_id == "ph_01"
    assert good_out.mask_ref is not None
    assert good_out.transform is not None
    assert good_out.quality is not None
    assert good_out.quality.state == "not_assessed"
    assert good_out.quality.exposure_state == "not_assessed"
    assert "quality_not_assessed" in good_out.quality.reasons
    assert len(good_out.artifacts) == 2  # mask and overlay
    for pred in good_out.predictions:
        assert pred.side == "unknown"

    mismatch_out = result.photo_outcomes[1]
    assert mismatch_out.status == "failed"
    assert mismatch_out.reasons[0].code == "artifact_hash_mismatch"

    corrupt_out = result.photo_outcomes[2]
    assert corrupt_out.status == "failed"
    assert corrupt_out.reasons[0].code == "corrupt_photo"


def test_make_parts_handler_emits_valid_schema(dummy_model_dir: Path, sample_image_bytes: bytes, tmp_path: Path):
    storage = Storage(directory=tmp_path / "evidence")
    photo_key = "claims/test_claim/1/photos/ph_01.jpg"
    storage.write(photo_key, sample_image_bytes, "image/jpeg")
    photo_sha = hashlib.sha256(sample_image_bytes).hexdigest()

    cfg = PartsConfig(input_size=512, min_part_pixels=10, min_part_confidence=0.01)
    segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=cfg)
    handler = make_parts_handler(segmenter, storage)

    emitted: list[tuple[str, dict]] = []

    envelope = Envelope.build(
        topic="cmev.cmd.parts-segment.v1",
        claim_id="01JAX7Q0VN4Z3K9F2M8R6T1C5D",
        input_revision=1,
        task="parts_segment",
        versions={"parts_model": "parts/0.1.0", "taxonomy": "parts-1.0.0"},
        provenance={"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-api"},
        trace_id="trace-12345",
        occurred_at=utcnow(),
        target="ph_01",
    )
    payload = {
        "photo": {
            "file_id": "ph_01",
            "object_uri": storage.uri(photo_key),
            "sha256": photo_sha,
            "media_type": "image/jpeg",
            "byte_count": len(sample_image_bytes),
            "width": 640,
            "height": 480,
            "page_number": None,
            "exif_orientation": 1,
        },
        "model_id": "parts",
        "model_version": "parts/0.1.0",
        "preprocess_config_version": "vision-pre-1.0.0",
        "taxonomy_version": "parts-1.0.0",
    }

    # Mock database session
    mock_session = MagicMock()

    message = envelope.message(payload)
    ctx = Context(
        session=mock_session,
        topic="cmev.cmd.parts-segment.v1",
        message=message,
        envelope=envelope,
        group="cmev-worker-parts",
        service="cmev-worker-parts",
        profile="lean",
        source_kind="real",
        now=utcnow(),
        job={"job_key": envelope.job_key, "task": "parts_segment", "target": "ph_01", "attempt_epoch": 0},
    )
    ctx.emit = lambda topic, message, **kwargs: emitted.append((topic, message))

    result = handler(ctx)
    assert result["photo_id"] == "ph_01"
    assert len(emitted) == 1

    topic, event_message = emitted[0]
    assert topic == "cmev.evt.parts-segmented.v1"

    # Schema validation against cmev.evt.parts-segmented.v1 schema
    validated = validate_message(topic, event_message)
    assert validated == event_message

    # Strict invariant assertions
    event_payload = event_message["payload"]
    part_mask_ref = event_payload["part_mask_ref"]
    assert "source_photo_id" not in part_mask_ref, "maskRef wire schema must not leak source_photo_id"
    assert part_mask_ref["encoding"] == "class_index_png"
    assert part_mask_ref["width"] == 512
    assert part_mask_ref["height"] == 512

    # ImageQuality checks
    iq = event_payload["image_quality"]
    assert iq["state"] == "not_assessed"
    assert iq["exposure_state"] == "not_assessed"
    assert iq["reasons"] == ["quality_not_assessed"]

    # Storage artifact check
    saved_mask = storage.read("claims/01JAX7Q0VN4Z3K9F2M8R6T1C5D/1/parts/ph_01/mask.png")
    assert len(saved_mask) > 0
    mask_img = Image.open(io.BytesIO(saved_mask))
    assert mask_img.size == (512, 512)
    assert mask_img.mode == "P"


def test_make_parts_handler_hash_mismatch(dummy_model_dir: Path, sample_image_bytes: bytes, tmp_path: Path):
    storage = Storage(directory=tmp_path / "evidence")
    photo_key = "claims/test_claim/1/photos/ph_01.jpg"
    storage.write(photo_key, sample_image_bytes, "image/jpeg")

    cfg = PartsConfig(input_size=512)
    segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=cfg)
    handler = make_parts_handler(segmenter, storage)

    envelope = Envelope.build(
        topic="cmev.cmd.parts-segment.v1",
        claim_id="01JAX7Q0VN4Z3K9F2M8R6T1C5D",
        input_revision=1,
        task="parts_segment",
        versions={"parts_model": "parts/0.1.0"},
        provenance={"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-api"},
        trace_id="trace-12345",
        occurred_at=utcnow(),
        target="ph_01",
    )
    payload = {
        "photo": {
            "file_id": "ph_01",
            "object_uri": storage.uri(photo_key),
            "sha256": "wrong_hash" * 4,
            "media_type": "image/jpeg",
            "byte_count": len(sample_image_bytes),
            "width": 640,
            "height": 480,
            "page_number": None,
            "exif_orientation": 1,
        },
        "model_id": "parts",
        "model_version": "parts/0.1.0",
        "preprocess_config_version": "vision-pre-1.0.0",
        "taxonomy_version": "parts-1.0.0",
    }
    message = envelope.message(payload)
    ctx = Context(
        session=MagicMock(),
        topic="cmev.cmd.parts-segment.v1",
        message=message,
        envelope=envelope,
        group="cmev-worker-parts",
        service="cmev-worker-parts",
        profile="lean",
        source_kind="real",
        now=utcnow(),
        job={"job_key": envelope.job_key, "task": "parts_segment", "target": "ph_01", "attempt_epoch": 0},
    )

    with pytest.raises(PermanentError) as exc_info:
        handler(ctx)
    assert exc_info.value.reason_code == "artifact_hash_mismatch"


def test_make_parts_handler_corrupt_photo(dummy_model_dir: Path, tmp_path: Path):
    storage = Storage(directory=tmp_path / "evidence")
    photo_key = "claims/test_claim/1/photos/corrupt.jpg"
    storage.write(photo_key, b"corrupted_jpeg_data", "image/jpeg")

    cfg = PartsConfig(input_size=512)
    segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=cfg)
    handler = make_parts_handler(segmenter, storage)

    envelope = Envelope.build(
        topic="cmev.cmd.parts-segment.v1",
        claim_id="01JAX7Q0VN4Z3K9F2M8R6T1C5D",
        input_revision=1,
        task="parts_segment",
        versions={"parts_model": "parts/0.1.0"},
        provenance={"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-api"},
        trace_id="trace-12345",
        occurred_at=utcnow(),
        target="ph_01",
    )
    payload = {
        "photo": {
            "file_id": "ph_01",
            "object_uri": storage.uri(photo_key),
            "sha256": hashlib.sha256(b"corrupted_jpeg_data").hexdigest(),
            "media_type": "image/jpeg",
            "byte_count": len(b"corrupted_jpeg_data"),
            "width": 640,
            "height": 480,
            "page_number": None,
            "exif_orientation": 1,
        },
        "model_id": "parts",
        "model_version": "parts/0.1.0",
        "preprocess_config_version": "vision-pre-1.0.0",
        "taxonomy_version": "parts-1.0.0",
    }
    message = envelope.message(payload)
    ctx = Context(
        session=MagicMock(),
        topic="cmev.cmd.parts-segment.v1",
        message=message,
        envelope=envelope,
        group="cmev-worker-parts",
        service="cmev-worker-parts",
        profile="lean",
        source_kind="real",
        now=utcnow(),
        job={"job_key": envelope.job_key, "task": "parts_segment", "target": "ph_01", "attempt_epoch": 0},
    )

    with pytest.raises(PermanentError) as exc_info:
        handler(ctx)
    assert exc_info.value.reason_code == "corrupt_photo"
