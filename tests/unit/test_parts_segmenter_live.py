"""Check of the serving entry point with the trained checkpoint and a real HITL photograph.

Skipped unless the configured registry entry and the HITL part photographs are present on
this workstation. It checks that inference runs and honours the M1 invariants; it does not
measure accuracy.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
"""
from __future__ import annotations

import hashlib
import io

from PIL import Image
import pytest

from claim_cmev.vision.parts.adapter import (
    ModelUnavailable,
    PartsSegmentRequest,
    PhotoFileInput,
    load_parts_segmenter,
    run_parts_segmentation,
)
from claim_cmev.vision.parts.config import load_parts_config
from pipelines.vision.splits import find_hitl_folder

CLAIM_ID = "01HZ4ABCDEFGHJKMNPQRSTVWXZ"


def test_segmenter_inference_on_real_photo():
    config = load_parts_config()
    try:
        segmenter = load_parts_segmenter(config)
    except ModelUnavailable as unavailable:
        pytest.skip(f"Trained model checkpoint not available: {unavailable.reason_code}")
    try:
        photo_path = sorted((find_hitl_folder("parts") / "File1" / "img").iterdir())[0]
    except FileNotFoundError:
        pytest.skip("HITL part photographs are not on this workstation")
    photo = photo_path.read_bytes()
    versions = config.stage_versions("0.2.0")
    request = PartsSegmentRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=f"{CLAIM_ID}:1:parts_segment:ph_01:deadbeef",
        photos=(PhotoFileInput(file_id="ph_01", media_type="image/png",
                               sha256=hashlib.sha256(photo).hexdigest(), data=photo),),
        versions=versions,
        provenance={"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-worker-parts"})

    result = run_parts_segmentation(request, segmenter)

    assert result.processing_status == "succeeded"
    [outcome] = result.photo_outcomes
    assert outcome.predictions, "Model should predict at least one part"
    for pred in outcome.predictions:
        assert pred.side == "unknown", "M1 invariant: side must always be unknown"
        assert (pred.claim_id, pred.photo_id) == (CLAIM_ID, "ph_01")
        assert pred.mask_ref.artifact_id == outcome.mask_ref.artifact_id
        assert pred.pixel_count > 0 and 0.0 <= pred.mean_confidence <= 1.0
        assert pred.versions == versions

    mask = next(a for a in outcome.artifacts if a.key.endswith("/mask.png"))
    mask_img = Image.open(io.BytesIO(mask.data))
    assert (mask_img.size, mask_img.mode) == ((config.input_size, config.input_size), "P")
    assert outcome.transform.mask_frame == "model"
    assert outcome.quality.state in ("acceptable", "limited", "unusable", "not_assessed")
