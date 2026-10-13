"""The serving-side pen-mark detector: registry-entry verification, loading and ``detect``.

Every entry here holds RANDOM weights written to a temporary folder (no network, no
pretrained checkpoint). These tests check the hand-over mechanics: what is refused, that a
reloaded model reproduces the saved one, and which frame the boxes are in. They say
nothing about detection accuracy.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")
pytest.importorskip("safetensors")

from safetensors.torch import load_file, save_file  # noqa: E402

from claim_cmev.documents.pen_marks import (  # noqa: E402
    Detection,
    PenMarkDetector,
    RowBox,
    TorchFasterRCNNDetector,
    link_detections,
    load_penmark_detector,
)
from claim_cmev.documents.pen_marks import detector as d  # noqa: E402
from claim_cmev.vision.registry import ModelUnavailable  # noqa: E402
from m6_support import CLAIM, CONFIG, PROV  # noqa: E402

VERSION = "penmarks/9.9.9-test"
SETTINGS = d.DetectorSettings(min_size=128, max_size=192, max_detections_per_page=20)
HEIGHT, WIDTH = 384, 256  # twice the internal size, so the model really resizes


def _image(seed: int = 0, height: int = HEIGHT, width: int = WIDTH) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _random_model(settings: d.DetectorSettings = SETTINGS, seed: int = 7):
    """The served architecture with random weights and random frozen batch-norm statistics."""
    from torchvision.ops.misc import FrozenBatchNorm2d

    torch.manual_seed(seed)
    model = d.build_model(settings)
    for module in model.modules():
        # Fresh statistics are variance 1, where eps cannot matter. A trained network has
        # channels with a variance near zero; give a quarter of the channels one. Their
        # scale weight / sqrt(variance + eps) stays 1 with eps 0 and drops to 0.03 with 1e-5.
        if isinstance(module, FrozenBatchNorm2d):
            quarter = module.weight.shape[0] // 4
            module.running_var[:quarter] = 1e-8
            module.weight[:quarter] = 1e-4
    return model.eval()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _write_entry(folder: Path, model, settings: d.DetectorSettings = SETTINGS) -> Path:
    folder.mkdir(parents=True)
    save_file({k: v.contiguous() for k, v in model.state_dict().items()}, str(folder / d.WEIGHTS_FILE))
    _write_json(folder / "label_schema.json", d.label_schema_record())
    _write_json(folder / "preprocessing.json", settings.to_record())
    _write_json(folder / "manifest.json", {
        "model_id": d.MODEL_ID, "version": VERSION, "status": "candidate", "task": "object_detection",
        "architecture": settings.architecture, "weights_sha256": _sha(folder / d.WEIGHTS_FILE),
        "label_schema_version": d.LABEL_SCHEMA_VERSION, "preprocessing_version": d.PREPROCESSING_VERSION,
        "preprocessing_sha256": _sha(folder / "preprocessing.json"), "provenance": "synthetic"})
    return folder


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    """One random model, its registry entry and its own detections on a fixed image."""
    model = _random_model()
    entry = _write_entry(tmp_path_factory.mktemp("registry") / "penmarks" / "9.9.9-test", model)
    pixels = torch.from_numpy(_image()).permute(2, 0, 1).float().div(255.0)
    with torch.no_grad():
        reference = {k: v.numpy() for k, v in model([pixels])[0].items()}
    yield entry, model, reference
    (entry / d.WEIGHTS_FILE).unlink(missing_ok=True)  # 160 MB of random numbers


@pytest.fixture
def entry(saved, tmp_path) -> Path:
    """A private copy of the entry whose records a test may damage; the weights are a hard link."""
    source, copy = saved[0], tmp_path / "penmarks" / "9.9.9-test"
    copy.mkdir(parents=True)
    for path in source.iterdir():
        if path.name == d.WEIGHTS_FILE:
            os.link(path, copy / path.name)
        else:
            shutil.copy2(path, copy / path.name)
    return copy


def _edit(entry: Path, name: str, **changes) -> None:
    record = json.loads((entry / name).read_text(encoding="utf-8"))
    record.update(changes)
    _write_json(entry / name, record)


def _edit_preprocessing(entry: Path, **changes) -> None:
    """Change the record AND its hash in the manifest, as a wrong export would."""
    _edit(entry, "preprocessing.json", **changes)
    _edit(entry, "manifest.json", preprocessing_sha256=_sha(entry / "preprocessing.json"))


def _refused(entry: Path, reason_code: str, **kwargs) -> ModelUnavailable:
    with pytest.raises(ModelUnavailable) as caught:
        load_penmark_detector(entry, **kwargs)
    assert caught.value.reason_code == reason_code, caught.value
    return caught.value


# --------------------------------------------------------------------------- importing

def test_the_package_imports_without_loading_torch():
    code = ("import sys\nimport claim_cmev.documents.pen_marks as p\n"
            "assert p.load_penmark_detector and p.PenMarkDetector and p.TorchFasterRCNNDetector\n"
            "loaded = sorted(m for m in ('torch', 'torchvision', 'safetensors') if m in sys.modules)\n"
            "assert not loaded, loaded\n")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("missing", ["torch", "torchvision", "safetensors.torch"])
def test_loading_without_the_model_libraries_is_a_refusal_not_a_bare_import_error(saved, monkeypatch, missing):
    """A lean container may verify an entry but cannot load it: the loader says so in its own words."""
    monkeypatch.setitem(sys.modules, missing, None)  # ``import`` of this name now raises ImportError
    manifest, settings = d.verify_penmark_entry(saved[0])  # verification needs no model library
    assert manifest["version"] == VERSION and settings == SETTINGS
    error = _refused(saved[0], "model_runtime_missing")
    assert "torch" in error.reason_text and "torchvision" in error.reason_text and "safetensors" in error.reason_text
    assert isinstance(error.__cause__, ImportError)


# --------------------------------------------------------------------------- loading and detecting

def test_a_valid_entry_loads_and_returns_detection_records(saved):
    detector = load_penmark_detector(saved[0])
    assert isinstance(detector, TorchFasterRCNNDetector) and isinstance(detector, PenMarkDetector)
    assert detector.model_version == VERSION and detector.status == "candidate"
    assert detector.versions == {"penmark_model": VERSION, "penmark_preprocessing": d.PREPROCESSING_VERSION}
    detections = detector.detect(_image(), page_id="dp1")
    assert detections and all(isinstance(item, Detection) for item in detections)
    assert len(detections) <= SETTINGS.max_detections_per_page
    for item in detections:
        assert item.page_id == "dp1" and item.mark_type in {"exclusion", "price_change"}
        assert SETTINGS.score_floor <= item.score <= 1.0
        x0, y0, x1, y1 = item.box_norm
        assert 0.0 <= x0 <= x1 <= 1.0 and 0.0 <= y0 <= y1 <= 1.0
    keys = [(-item.score, item.box_norm[1], item.box_norm[0]) for item in detections]
    assert keys == sorted(keys), "detections are ordered by descending score, then position"
    assert detections == detector.detect(_image(), page_id="dp1"), "the same page gives the same records"


def test_the_reloaded_model_gives_the_detections_of_the_model_that_was_saved(saved):
    entry, _, reference = saved
    detector = load_penmark_detector(entry)
    boxes, labels, scores = detector.predict(_image())
    assert len(scores) == len(reference["scores"]) > 0
    order = np.lexsort((reference["boxes"][:, 0], reference["boxes"][:, 1], -reference["scores"]))
    np.testing.assert_allclose(scores, reference["scores"][order], atol=1e-6)
    np.testing.assert_allclose(boxes, reference["boxes"][order], atol=1e-3)
    np.testing.assert_array_equal(labels, reference["labels"][order])


def test_frozen_batch_norm_eps_comes_from_the_record_not_from_the_library_default(saved):
    """``eps`` is not in the state dict: a strict load succeeds with the wrong value and the boxes move."""
    from torchvision.ops.misc import FrozenBatchNorm2d

    entry, _, reference = saved
    detector = load_penmark_detector(entry)
    layers = [m for m in detector.model.modules() if isinstance(m, FrozenBatchNorm2d)]
    assert layers and {m.eps for m in layers} == {0.0}

    careless = d.build_model(SETTINGS)
    for module in careless.modules():
        if isinstance(module, FrozenBatchNorm2d):
            module.eps = 1e-5  # the constructor default
    careless.load_state_dict(load_file(str(entry / d.WEIGHTS_FILE)), strict=True)  # passes
    pixels = torch.from_numpy(_image()).permute(2, 0, 1).float().div(255.0)
    with torch.no_grad():
        wrong = careless.eval()([pixels])[0]
    same_count = len(wrong["scores"]) == len(reference["scores"])
    assert not (same_count and np.allclose(wrong["boxes"].numpy(), reference["boxes"], atol=1e-3)), \
        "the fixture must make a wrong eps visible"


class _FixedHeads(torch.nn.Module):
    """Stands in for the RoI heads: one box, given in the model's INTERNAL resized frame."""

    def __init__(self, box):
        super().__init__()
        self.box = box

    def forward(self, features, proposals, image_shapes, targets=None):
        self.image_shapes = list(image_shapes)
        return [{"boxes": torch.tensor([self.box], dtype=torch.float32), "labels": torch.tensor([2]),
                 "scores": torch.tensor([0.9])}], {}


def test_torchvision_already_returns_boxes_in_the_input_frame_and_detect_does_not_rescale_again(saved):
    detector = load_penmark_detector(saved[0])
    heads = _FixedHeads([10.0, 30.0, 60.0, 45.0])
    detector.model.roi_heads = heads
    boxes, labels, scores = detector.predict(_image())
    # A 256 x 384 page is resized to 128 x 192 inside the model: scale 0.5.
    assert heads.image_shapes == [(192, 128)]
    np.testing.assert_allclose(boxes, [[20.0, 60.0, 120.0, 90.0]], atol=1e-4)  # input-frame pixels
    (record,) = detector.detect(_image(), page_id="dp1")
    assert record.mark_type == "price_change" and record.score == pytest.approx(0.9)
    # 20/256, 60/384, 120/256, 90/384. Undoing the resize a second time would double every value.
    assert record.box_norm == pytest.approx((0.078125, 0.15625, 0.46875, 0.234375), abs=1e-6)


def test_boxes_are_clipped_to_the_page_and_rounded_like_m4_box_norm():
    records = d.detections_from_arrays(
        "dp1", np.array([[-4.0, 10.0, 300.0, 50.0], [10.0, 10.0, 20.0, 20.0]]), np.array([1, 2]),
        np.array([0.5, 0.75]), width=256, height=384)
    assert [r.mark_type for r in records] == ["price_change", "exclusion"]  # highest score first
    assert records[1].box_norm == (0.0, 0.026042, 1.0, 0.130208)
    with pytest.raises(ValueError, match="label"):
        d.detections_from_arrays("dp1", np.array([[0.0, 0.0, 1.0, 1.0]]), np.array([0]), np.array([0.5]),
                                 width=10, height=10)


def test_detect_refuses_an_image_that_is_not_rgb_uint8(saved):
    detector = load_penmark_detector(saved[0])
    for bad in (_image().astype(np.float32) / 255.0, _image()[:, :, 0], np.zeros((8, 8, 4), dtype=np.uint8)):
        with pytest.raises(ValueError, match="RGB uint8"):
            detector.detect(bad, page_id="dp1")


def test_detections_feed_the_existing_linker_and_every_mark_is_pending(saved):
    detector = load_penmark_detector(saved[0])
    detections = detector.detect(_image(), page_id="dp1")
    rows = [RowBox(entry_id="e1", page_id="dp1", row_box_norm=(0.0, 0.0, 1.0, 1.0))]
    config = CONFIG.with_overrides({"detection": {"score_threshold": {"exclusion": 0.05, "price_change": 0.05}}})
    versions = {**detector.versions, "code": "test"}
    result = link_detections(detections, rows, config=config, job_key=f"{CLAIM}:1:pen_marks_detect:all:test",
                             claim_id=CLAIM, input_revision=1, provenance=PROV, versions=versions)
    assert result.marks and {m.state for m in result.marks} == {"pending"}
    assert {m.versions["penmark_model"] for m in result.marks} == {VERSION}
    assert all(m.confirmed_amount is None for m in result.marks)


# --------------------------------------------------------------------------- refusals

def test_a_missing_entry_manifest_or_weight_file_is_refused(entry, tmp_path):
    _refused(tmp_path / "absent", "model_not_found")
    (entry / d.WEIGHTS_FILE).unlink()
    _refused(entry, "model_weights_missing")
    (entry / "manifest.json").unlink()
    _refused(entry, "model_manifest_missing")


def test_weights_that_do_not_match_the_manifest_hash_are_refused(entry):
    _edit(entry, "manifest.json", weights_sha256="0" * 64)
    assert "manifest" in _refused(entry, "model_weights_hash_mismatch").reason_text


def test_another_model_or_an_unknown_architecture_is_refused(entry):
    _edit(entry, "manifest.json", model_id="parts")
    _refused(entry, "model_id_mismatch")
    _edit(entry, "manifest.json", model_id=d.MODEL_ID, architecture="fasterrcnn_mobilenet_v3_large_fpn")
    assert "fasterrcnn_resnet50_fpn" in _refused(entry, "architecture_unsupported").reason_text


@pytest.mark.parametrize("labels", [
    {"0": "background", "1": "price_change", "2": "exclusion"},  # swapped
    {"0": "background", "1": "exclusion"},  # a class missing
    {"0": "background", "1": "exclusion", "2": "price_change", "3": "tick"},  # an extra class
    {"1": "exclusion", "2": "price_change"},  # no background
])
def test_a_label_schema_that_is_not_exactly_the_three_classes_is_refused(entry, labels):
    _edit(entry, "label_schema.json", id_to_code=labels)
    _refused(entry, "label_map_mismatch")


def test_a_missing_label_schema_or_preprocessing_record_is_refused(entry):
    (entry / "label_schema.json").unlink()
    _refused(entry, "label_schema_missing")
    _write_json(entry / "label_schema.json", d.label_schema_record())
    (entry / "preprocessing.json").unlink()
    _refused(entry, "preprocessing_missing")


@pytest.mark.parametrize("changes", [
    {"color_space": "BGR"},  # OpenCV order: the model would see red and blue swapped
    {"value_range": [0.0, 255.0]},  # the detector feeds 0-1
    {"input_dtype": "float32"},
    {"normalisation": "caller"},
    {"min_size": 0},
    {"min_size": 400, "max_size": 300},
    {"anchor_sizes": [[32], [64]]},  # the FPN has five levels
    {"anchor_sizes": [[32, 64], [64], [128], [256], [512]]},  # one head serves every level
    {"anchor_aspect_ratios": []},
    {"frozen_batchnorm_eps": -1.0},
    {"unknown_key": 1},
    {"preprocessing_version": "penmarks-preprocessing/0.0.1"},
])
def test_a_preprocessing_record_the_detector_does_not_apply_is_refused(entry, changes):
    _edit_preprocessing(entry, **changes)
    _refused(entry, "preprocessing_mismatch")


def test_a_preprocessing_record_edited_after_export_is_refused(entry):
    _edit(entry, "preprocessing.json", min_size=1240, max_size=1754)  # valid values, wrong hash
    _refused(entry, "preprocessing_hash_mismatch")


def test_settings_pinned_by_the_caller_must_equal_the_record(entry):
    assert load_penmark_detector(entry, expected={"min_size": 128, "max_size": 192}).settings == SETTINGS
    error = _refused(entry, "preprocessing_mismatch", expected={"min_size": 800, "nms_iou": 0.5})
    assert "min_size" in error.reason_text and "nms_iou" not in error.reason_text
    with pytest.raises(ValueError, match="not a preprocessing setting"):
        load_penmark_detector(entry, expected={"minimum": 800})


def test_anchors_that_differ_from_the_trained_ones_are_refused_not_loaded_loosely(entry):
    """Five aspect ratios need a wider RPN head than the three the weights were trained with."""
    _edit_preprocessing(entry, anchor_aspect_ratios=[0.125, 0.25, 0.5, 1.0, 2.0])
    assert "rpn.head" in _refused(entry, "model_weights_incomplete").reason_text


def test_weights_with_a_missing_or_an_extra_tensor_are_refused(entry):
    weights = entry / d.WEIGHTS_FILE
    state = load_file(str(weights))
    weights.unlink()  # a hard link: never write through it
    del state["roi_heads.box_predictor.cls_score.bias"]
    save_file(state, str(weights))
    _edit(entry, "manifest.json", weights_sha256=_sha(weights))
    assert "cls_score.bias" in _refused(entry, "model_weights_incomplete").reason_text


def test_a_candidate_is_refused_where_only_approved_models_may_be_served(entry):
    _refused(entry, "model_status_not_approved", allow_candidate=False)
    _edit(entry, "manifest.json", status="approved")
    assert load_penmark_detector(entry, allow_candidate=False).status == "approved"
    _edit(entry, "manifest.json", status="draft")
    _refused(entry, "model_status_invalid")


def test_record_versions_the_loader_does_not_know_are_refused(entry):
    _edit(entry, "manifest.json", label_schema_version="penmarks-labels/2.0.0")
    _refused(entry, "label_schema_version_mismatch")


# --------------------------------------------------------------------------- settings

def test_settings_round_trip_through_the_record_and_describe_torchvision_defaults():
    record = d.DetectorSettings().to_record()
    assert d.DetectorSettings.from_record(record) == d.DetectorSettings()
    assert (record["min_size"], record["max_size"], record["color_space"]) == (800, 1333, "RGB")
    assert record["anchor_sizes"] == [[32], [64], [128], [256], [512]]
    assert record["anchor_aspect_ratios"] == [0.5, 1.0, 2.0] and record["frozen_batchnorm_eps"] == 0.0
    assert json.loads(json.dumps(record)) == record

    model = d.build_model(d.DetectorSettings(anchor_aspect_ratios=(0.25, 0.5, 1.0, 2.0), min_size=640, max_size=960))
    assert model.rpn.head.cls_logits.out_channels == 4 and model.rpn.head.bbox_pred.out_channels == 16
    assert model.transform.min_size == (640,) and model.transform.max_size == 960
    assert model.roi_heads.box_predictor.cls_score.out_features == 3
    assert d.applied_settings(model, d.DetectorSettings()).anchor_aspect_ratios == (0.25, 0.5, 1.0, 2.0)
