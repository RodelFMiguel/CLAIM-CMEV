"""Integration test for PartsSegmenter with real trained SegFormer-B0 checkpoint.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest

from claim_cmev.storage import Storage
from claim_cmev.vision.parts.adapter import PartsSegmenter
from claim_cmev.vision.parts.config import PartsConfig


def test_segmenter_inference_on_real_photo(tmp_path: Path):
    model_dir = Path("artifacts/models/parts/0.1.0")
    if not (model_dir / "model.safetensors").exists():
        pytest.skip("Trained model checkpoint not found")

    # Read a test image from test split
    test_split_path = Path("data/splits/parts/0.1.0/test.jsonl")
    first_record = json.loads(test_split_path.read_text(encoding="utf-8").splitlines()[0])
    img_path = Path(first_record["source_path"])
    img_bytes = img_path.read_bytes()

    storage = Storage(directory=tmp_path / "evidence")
    cfg = PartsConfig(min_part_pixels=100, min_part_confidence=0.30)
    segmenter = PartsSegmenter(model_dir=model_dir, config=cfg)

    claim_id = "01HZ4ABCDEFGHJKMNPQRSTVWXZ"
    input_rev = 1
    job_key = f"{claim_id}:{input_rev}:parts_segment:ph_01:deadbeef"
    versions = {
        "parts_model": "parts/0.1.0",
        "taxonomy": "parts-1.0.0",
        "parts_config": "parts-cfg-0.1.0",
        "code": "1.0.0",
    }
    provenance = {
        "source_kind": "real",
        "runtime_profile": "lean",
        "producer_service": "cmev-worker-parts",
    }

    predictions, quality, mask_ref, transform = segmenter.segment_photo(
        photo_bytes=img_bytes,
        photo_id="ph_01",
        claim_id=claim_id,
        input_revision=input_rev,
        job_key=job_key,
        versions=versions,
        provenance=provenance,
        storage=storage,
    )

    # Invariants checks
    assert len(predictions) > 0, "Model should predict at least one part"
    for pred in predictions:
        assert pred.side == "unknown", "M1 invariant: side must always be unknown"
        assert pred.claim_id == claim_id
        assert pred.photo_id == "ph_01"
        assert pred.mask_ref.artifact_id == mask_ref.artifact_id
        assert pred.pixel_count > 0
        assert 0.0 <= pred.mean_confidence <= 1.0

    # Mask artifact checks
    mask_key = f"claims/{claim_id}/{input_rev}/parts/ph_01/mask.png"
    saved_mask_bytes = storage.read(mask_key)
    assert len(saved_mask_bytes) > 0
    mask_img = Image.open(tmp_path / "evidence" / mask_key)
    assert mask_img.size == (512, 512)
    assert mask_img.mode == "P"

    # Transform checks
    assert transform.model_width == 512
    assert transform.model_height == 512
    assert transform.mask_frame == "model"

    # Quality check
    assert quality.state in ("acceptable", "limited", "unusable")
    assert quality.photo_id == "ph_01"
