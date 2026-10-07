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
    dedup_key,
    deterministic_id,
    version_signature,
)
from claim_cmev.contracts.events import Envelope
from claim_cmev.contracts.events.registry import validate_message
from claim_cmev.messaging.consumer import Context, PermanentError, TransientError
from claim_cmev.persistence.store import load_records
from claim_cmev.review.overlays import render_photo_overlay
from claim_cmev.runtime import utcnow
from claim_cmev.storage import Storage
from claim_cmev.vision.palette import ID_TO_PART_CODE, PART_CODE_TO_ID, build_palette
from claim_cmev.vision.parts import adapter as parts_adapter
from claim_cmev.vision.transforms import apply_exif_orientation
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
        # Each hidden size must be a multiple of its stage's head count; the default heads are [1, 2, 5, 8].
        num_attention_heads=[1, 2, 4, 8],
        decoder_hidden_size=32,
    )
    torch.manual_seed(0)  # random weights, but the same ones on every run
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
    handler = make_parts_handler(segmenter, storage, {"parts_model": "parts/0.1.0", "taxonomy": "parts-1.0.0"})

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

    # Storage artifact check: the mask is where the event says it is
    saved_mask = storage.read(part_mask_ref["object_uri"].removeprefix("file://local-evidence/"))
    assert hashlib.sha256(saved_mask).hexdigest() == part_mask_ref["sha256"]
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
    handler = make_parts_handler(segmenter, storage, {"parts_model": "parts/0.1.0"})

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
    handler = make_parts_handler(segmenter, storage, {"parts_model": "parts/0.1.0"})

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


# ---------------------------------------------------------------------------
# Mask geometry and record identity (module-01 acceptance: "Masks align on the
# original photograph"; one part_prediction row per class per photo).
# ---------------------------------------------------------------------------

CLAIM_ID = "01JAX7Q0VN4Z3K9F2M8R6T1C5D"
JOB_KEY = f"{CLAIM_ID}:1:parts_segment:all:deadbeef"
VERSIONS = {"parts_model": "parts/0.1.0", "taxonomy": "parts-1.0.0"}
PROVENANCE = {"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-worker-parts"}
EXIF_ORIENTATION_TAG = 0x0112

# Stored size and EXIF orientation. Orientation 6 is displayed rotated 90 degrees clockwise,
# so the 640x480 file is a 480x640 portrait once oriented.
PHOTO_CASES = [
    pytest.param((640, 480), 1, id="landscape-padded-below"),
    pytest.param((480, 640), 1, id="portrait-padded-right"),
    pytest.param((640, 480), 6, id="exif-rotated"),
]


def _marker_photo(stored_size: tuple[int, int], exif_orientation: int = 1):
    """A black photo with one white rectangle.

    Returns the encoded bytes, the photo as displayed (after EXIF orientation) and the
    rectangle's (x0, y0, x1, y1) box in that displayed frame. The rectangle is off-centre on
    both axes, so a shifted, centred or mirrored mapping cannot land on it by accident.
    """
    stored_w, stored_h = stored_size
    width, height = (stored_h, stored_w) if exif_orientation == 6 else (stored_w, stored_h)
    box = (round(0.55 * width), round(0.60 * height), round(0.80 * width), round(0.85 * height))
    displayed = np.zeros((height, width, 3), dtype=np.uint8)
    displayed[box[1]:box[3], box[0]:box[2]] = 255
    stored = np.ascontiguousarray(np.rot90(displayed) if exif_orientation == 6 else displayed)
    assert np.array_equal(apply_exif_orientation(stored, exif_orientation), displayed)
    exif = Image.Exif()
    exif[EXIF_ORIENTATION_TAG] = exif_orientation
    buf = io.BytesIO()
    Image.fromarray(stored).save(buf, format="PNG", exif=exif)
    return buf.getvalue(), displayed, box


def _box_of(flags: np.ndarray) -> tuple[int, int, int, int]:
    rows, cols = np.where(flags.any(axis=1))[0], np.where(flags.any(axis=0))[0]
    return (int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1)


def _run_and_locate_marker(segmenter: PartsSegmenter, run):
    """Call ``run()`` and return its result with the marker's box in the tensor the network received."""
    seen: dict[str, np.ndarray] = {}

    def hook(_module, args, kwargs, _output):
        pixels = kwargs["pixel_values"] if "pixel_values" in kwargs else args[0]
        seen["pixels"] = pixels.detach().cpu().numpy()

    handle = segmenter.model.register_forward_hook(hook, with_kwargs=True)
    try:
        result = run()
    finally:
        handle.remove()
    # After normalisation white is about +2.2; black photo pixels and padding are about -2.1.
    return result, _box_of(seen["pixels"][0, 0] > 0)


def _recorded_transform(segmenter: PartsSegmenter, photo: bytes):
    request = PartsSegmentRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=JOB_KEY, versions=VERSIONS, provenance=PROVENANCE,
        photos=(PhotoFileInput(file_id="ph_01", media_type="image/png",
                               sha256=hashlib.sha256(photo).hexdigest(), data=photo),),
    )
    return run_parts_segmentation(request, segmenter).photo_outcomes[0].transform


@pytest.fixture
def geometry_segmenter(dummy_model_dir: Path) -> PartsSegmenter:
    cfg = PartsConfig(input_size=512, min_part_pixels=1, min_part_confidence=0.0, write_overlay=False)
    return PartsSegmenter(model_dir=dummy_model_dir, config=cfg)


@pytest.mark.parametrize(("stored_size", "exif_orientation"), PHOTO_CASES)
def test_recorded_transform_maps_the_model_frame_back_to_the_photo(
        geometry_segmenter: PartsSegmenter, stored_size, exif_orientation):
    photo, displayed, marker = _marker_photo(stored_size, exif_orientation)
    height, width = displayed.shape[:2]

    transform, model_box = _run_and_locate_marker(
        geometry_segmenter, lambda: _recorded_transform(geometry_segmenter, photo))

    x0, y0, x1, y1 = transform.model_box_to_original_norm(tuple(float(v) for v in model_box))
    # Resizing blurs the rectangle's edge by at most one model pixel, 1.25 photo pixels here.
    assert (x0 * width, y0 * height, x1 * width, y1 * height) == pytest.approx(marker, abs=2.5)


@pytest.mark.parametrize(("stored_size", "exif_orientation"), PHOTO_CASES)
def test_overlay_drawn_with_the_recorded_transform_lands_on_the_photo_region(
        geometry_segmenter: PartsSegmenter, stored_size, exif_orientation):
    photo, displayed, marker = _marker_photo(stored_size, exif_orientation)
    transform, (x0, y0, x1, y1) = _run_and_locate_marker(
        geometry_segmenter, lambda: _recorded_transform(geometry_segmenter, photo))
    # A model-frame mask covering exactly what the network saw of the rectangle.
    mask = np.zeros((512, 512), dtype=np.uint8)
    mask[y0:y1, x0:x1] = 255
    mask_png = io.BytesIO()
    Image.fromarray(mask).save(mask_png, format="PNG")

    overlay = render_photo_overlay(photo, photo_id="ph_01", part_masks={"marker": mask_png.getvalue()},
                                   transform=transform, show_damage=False)

    drawn = np.asarray(Image.open(io.BytesIO(overlay)).convert("RGB")) != displayed
    assert _box_of(drawn.any(axis=2)) == pytest.approx(marker, abs=3)


def test_run_parts_segmentation_gives_each_photo_its_own_prediction_ids(
        geometry_segmenter: PartsSegmenter, sample_image_bytes: bytes):
    # The same picture uploaded twice: both photos show the same parts.
    sha = hashlib.sha256(sample_image_bytes).hexdigest()
    request = PartsSegmentRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=JOB_KEY, versions=VERSIONS, provenance=PROVENANCE,
        photos=tuple(PhotoFileInput(file_id=file_id, media_type="image/jpeg", sha256=sha, data=sample_image_bytes)
                     for file_id in ("ph_01", "ph_02")),
    )

    result = run_parts_segmentation(request, geometry_segmenter)

    first, second = ({p.part_code for p in outcome.predictions} for outcome in result.photo_outcomes)
    assert first and first == second
    prediction_ids = [p.prediction_id for p in result.predictions]
    assert len(set(prediction_ids)) == len(prediction_ids)


# ---------------------------------------------------------------------------
# Padding, overlays, artifact keys, storage failures and the completion event
# ---------------------------------------------------------------------------

DOOR_ID = PART_CODE_TO_ID["front-door"]


def _request(photos: dict[str, bytes], versions: dict[str, str] = VERSIONS) -> PartsSegmentRequest:
    return PartsSegmentRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=JOB_KEY, versions=versions, provenance=PROVENANCE,
        photos=tuple(PhotoFileInput(file_id=file_id, media_type="image/png",
                                    sha256=hashlib.sha256(data).hexdigest(), data=data)
                     for file_id, data in photos.items()))


def _grey_photo(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.full((height, width, 3), 128, dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _artifact(outcome, name: str):
    return next((a for a in outcome.artifacts if a.key.endswith("/" + name)), None)


def _door_everywhere(dummy_model_dir: Path, **config) -> PartsSegmenter:
    """The dummy checkpoint with its classifier biased so every pixel, padding included, is a front door."""
    model = SegformerForSemanticSegmentation.from_pretrained(dummy_model_dir)
    with torch.no_grad():
        model.decode_head.classifier.bias[DOOR_ID] = 1000.0
    model.save_pretrained(dummy_model_dir)
    settings = {"input_size": 512, "min_part_pixels": 1, "min_part_confidence": 0.0, "write_overlay": True, **config}
    return PartsSegmenter(model_dir=dummy_model_dir, config=PartsConfig(**settings))


def test_padding_is_background_in_the_mask_and_is_not_counted(dummy_model_dir: Path):
    segmenter = _door_everywhere(dummy_model_dir)

    outcome = run_parts_segmentation(_request({"ph_01": _grey_photo(640, 480)}), segmenter).photo_outcomes[0]

    mask = np.asarray(Image.open(io.BytesIO(_artifact(outcome, "mask.png").data)))
    assert set(np.unique(mask[:384])) == {DOOR_ID}  # the 640x480 photo fills model rows 0 to 383
    assert not mask[384:].any()
    [door] = outcome.predictions
    assert (door.part_code, door.pixel_count) == ("front-door", 512 * 384)


def test_overlay_outlines_each_accepted_part_and_no_rejected_one():
    """Three vertical bands; the first two are accepted parts and the third is not."""
    first, second, rejected = PART_CODE_TO_ID["front-door"], PART_CODE_TO_ID["back-door"], PART_CODE_TO_ID["fender"]
    class_mask = np.zeros((512, 512), dtype=np.uint8)
    class_mask[:, :170], class_mask[:, 170:340], class_mask[:, 340:] = first, second, rejected
    photo = _grey_photo(512, 512)
    transform = parts_adapter.ImageTransform(stored_width=512, stored_height=512, model_width=512, model_height=512,
                                             scale=1.0)

    overlay = parts_adapter.render_parts_overlay(photo, "ph_01", class_mask, [first, second], transform)

    drawn = (np.asarray(Image.open(io.BytesIO(overlay)).convert("RGB")) != 128).any(axis=2)
    assert drawn[200:300, 165:175].any(), "the seam between two accepted parts is outlined"
    assert not drawn[:, 345:].any(), "a rejected part gets no outline"


def test_overlay_is_written_only_when_a_part_is_accepted(dummy_model_dir: Path):
    photo = {"ph_01": _grey_photo(640, 480)}

    accepted = run_parts_segmentation(_request(photo), _door_everywhere(dummy_model_dir)).photo_outcomes[0]
    rejected = run_parts_segmentation(
        _request(photo), _door_everywhere(dummy_model_dir, min_part_pixels=10**7)).photo_outcomes[0]

    assert Image.open(io.BytesIO(_artifact(accepted, "overlay.png").data)).size == (640, 480)  # the photo's frame
    assert [p.accepted for p in rejected.predictions] == [False]
    assert _artifact(rejected, "overlay.png") is None and _artifact(rejected, "mask.png") is not None


def test_mask_and_overlay_are_stored_under_the_version_signature(dummy_model_dir: Path):
    outcome = run_parts_segmentation(
        _request({"ph_01": _grey_photo(640, 480)}), _door_everywhere(dummy_model_dir)).photo_outcomes[0]

    folder = f"claims/{CLAIM_ID}/1/parts/ph_01/{version_signature(VERSIONS)}"
    assert sorted(a.key for a in outcome.artifacts) == [f"{folder}/mask.png", f"{folder}/overlay.png"]
    assert outcome.mask_ref.object_uri.endswith(f"/{folder}/mask.png")


def test_a_hash_mismatch_or_corrupt_photo_is_not_worth_retrying(dummy_model_dir: Path):
    segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=PartsConfig(write_overlay=False))
    request = PartsSegmentRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=JOB_KEY, versions=VERSIONS, provenance=PROVENANCE,
        photos=(PhotoFileInput(file_id="ph_01", media_type="image/png", sha256="0" * 64, data=_grey_photo(64, 48)),
                PhotoFileInput(file_id="ph_02", media_type="image/png",
                               sha256=hashlib.sha256(b"not an image").hexdigest(), data=b"not an image")))

    result = run_parts_segmentation(request, segmenter)

    assert [r.code for r in result.reasons] == ["artifact_hash_mismatch", "corrupt_photo"]
    assert result.retryable is False


def _command(storage, versions: dict[str, str] = VERSIONS, *, photo: bytes | None = None, attempt_epoch: int = 0):
    """A parts command for ``ph_01`` (stored first when ``photo`` is given) and the list its events land in."""
    key = "originals/ph_01.png"
    if photo is not None:
        storage.write(key, photo, "image/png")
    envelope = Envelope.build(
        topic="cmev.cmd.parts-segment.v1", claim_id=CLAIM_ID, input_revision=1, task="parts_segment",
        versions=versions, trace_id="trace-12345", occurred_at=utcnow(), target="ph_01",
        attempt_epoch=attempt_epoch,
        provenance={"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "cmev-orchestrator"})
    payload = {"photo": {"file_id": "ph_01", "object_uri": storage.uri(key),
                         "sha256": hashlib.sha256(photo or b"").hexdigest(), "media_type": "image/png",
                         "byte_count": len(photo or b"x"), "width": 640, "height": 480, "page_number": None,
                         "exif_orientation": 1},
               "model_id": "parts", "model_version": "0.1.0", "preprocess_config_version": "parts-cfg-0.1.0",
               "taxonomy_version": "parts-1.0.0"}
    context = Context(session=MagicMock(), topic="cmev.cmd.parts-segment.v1", message=envelope.message(payload),
                      envelope=envelope, group="cmev-worker-parts", service="cmev-worker-parts", profile="lean",
                      source_kind="real", now=utcnow(),
                      job={"job_key": envelope.job_key, "task": "parts_segment", "target": "ph_01",
                           "attempt_epoch": attempt_epoch})
    emitted: list[dict] = []
    context.emit = lambda topic, message, **kwargs: emitted.append(message)
    return context, emitted


def test_a_rerun_under_another_model_keeps_the_earlier_mask(dummy_model_dir: Path, tmp_path: Path):
    storage = Storage(directory=tmp_path / "evidence")
    segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=PartsConfig(write_overlay=False))
    photo = _grey_photo(640, 480)
    refs = []
    for versions in ({"parts_model": "parts/0.1.0"}, {"parts_model": "parts/0.2.0"}):
        context, _emitted = _command(storage, versions, photo=photo)
        refs.append(make_parts_handler(segmenter, storage, versions)(context)["part_mask_ref"])

    assert refs[0]["object_uri"] != refs[1]["object_uri"]
    for ref in refs:
        stored = storage.read(ref["object_uri"].removeprefix("file://local-evidence/"))
        assert hashlib.sha256(stored).hexdigest() == ref["sha256"]


class _FailingStorage:
    """A store whose reads fail with ``error``; the handler must not get as far as writing."""

    def __init__(self, error: Exception):
        self.error = error

    def uri(self, key: str) -> str:
        return f"s3://cmev-evidence/{key}"

    def read(self, key: str) -> bytes:
        raise self.error


def _no_such_key() -> Exception:
    error = RuntimeError("An error occurred (NoSuchKey) when calling the GetObject operation")
    error.response = {"Error": {"Code": "NoSuchKey"}}  # the shape of botocore's ClientError
    return error


@pytest.mark.parametrize("storage_error", [FileNotFoundError("no such file"), _no_such_key()],
                         ids=["local-file-absent", "s3-no-such-key"])
def test_a_photo_that_is_not_in_storage_is_refused_at_once(dummy_model_dir: Path, storage_error):
    storage = _FailingStorage(storage_error)
    handler = make_parts_handler(PartsSegmenter(model_dir=dummy_model_dir, config=PartsConfig()), storage, VERSIONS)
    context, emitted = _command(storage)

    with pytest.raises(PermanentError) as refused:
        handler(context)

    assert refused.value.reason_code == "artifact_missing" and emitted == []


def test_an_unreachable_object_store_is_worth_retrying(dummy_model_dir: Path):
    storage = _FailingStorage(ConnectionError("connection refused"))
    handler = make_parts_handler(PartsSegmenter(model_dir=dummy_model_dir, config=PartsConfig()), storage, VERSIONS)
    context, emitted = _command(storage)

    with pytest.raises(TransientError) as failed:
        handler(context)

    assert failed.value.reason_code == "artifact_read_failed" and emitted == []


def test_completion_event_continues_the_command_chain_and_carries_the_retry_epoch(dummy_model_dir: Path, tmp_path: Path):
    storage = Storage(directory=tmp_path / "evidence")
    segmenter = PartsSegmenter(model_dir=dummy_model_dir, config=PartsConfig(write_overlay=False))
    context, emitted = _command(storage, photo=_grey_photo(640, 480), attempt_epoch=2)

    make_parts_handler(segmenter, storage, VERSIONS)(context)

    [event] = emitted
    topic = "cmev.evt.parts-segmented.v1"
    assert validate_message(topic, event) == event
    assert event["job_key"] == context.envelope.job_key
    assert event["causation_id"] == context.envelope.dedup_key
    assert event["dedup_key"] == dedup_key(topic, context.envelope.job_key, 2)
