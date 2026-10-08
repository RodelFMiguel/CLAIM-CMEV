"""The M2 worker's checkpoint verification and its serving adapter.

The checkpoints here are tiny random-weight SegFormers and the photographs are synthetic, so
these check loading, the shared model frame, records and artifacts. They say nothing about
model accuracy.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")
from transformers import SegformerConfig, SegformerForSemanticSegmentation  # noqa: E402

from claim_cmev.contracts.common import HITL_DAMAGE_CODES, Provenance, version_signature  # noqa: E402
from claim_cmev.contracts.imaging import MaskRef  # noqa: E402
from claim_cmev.vision.damage import adapter as damage_adapter  # noqa: E402
from claim_cmev.vision.damage.config import load_assignment_config  # noqa: E402
from claim_cmev.vision.damage.model_config import DamageModelConfig, load_damage_model_config  # noqa: E402
from claim_cmev.vision.frame import build_model_frame  # noqa: E402
from claim_cmev.vision.palette import ID_TO_PART_CODE  # noqa: E402
from claim_cmev.vision.parts.adapter import (  # noqa: E402
    PartsSegmenter,
    PartsSegmentRequest,
    PhotoFileInput,
    run_parts_segmentation,
)
from claim_cmev.vision.parts.config import PartsConfig  # noqa: E402
from claim_cmev.vision.registry import ModelUnavailable  # noqa: E402

CLAIM_ID = "01JAX7Q0VN4Z3K9F2M8R6T1C5D"
PHOTO_ID = "ph_01"
PART_ID = {code: class_id for class_id, code in ID_TO_PART_CODE.items()}
# The numbering of the served checkpoint's trainer (pipelines/vision/convert_damage.py), not the taxonomy file's order.
CLASSES = {0: "background", 1: "missing-part", 2: "broken-part", 3: "scratch", 4: "cracked", 5: "dent", 6: "flaking",
           7: "paint-chip", 8: "corrosion"}
DAMAGE_ID = {code: class_id for class_id, code in CLASSES.items()}
PREPROCESSING = {"preprocessing_version": "1.0.0", "input_size": 128, "resize_policy": "longest_edge_pad",
                 "color_space": "RGB", "pixel_mean": [0.485, 0.456, 0.406], "pixel_std": [0.229, 0.224, 0.225],
                 "pad_value": 0}
PROVENANCE = Provenance(source_kind="real", runtime_profile="lean", producer_service="cmev-worker-damage")


def _tiny(num_labels: int, id2label: dict[int, str] | None = None) -> SegformerForSemanticSegmentation:
    torch.manual_seed(0)
    names = {"id2label": {str(i): name for i, name in id2label.items()},
             "label2id": {name: i for i, name in id2label.items()}} if id2label else {}
    return SegformerForSemanticSegmentation(SegformerConfig(
        num_labels=num_labels, depths=[1, 1, 1, 1], hidden_sizes=[16, 32, 64, 128], num_attention_heads=[1, 2, 4, 8],
        decoder_hidden_size=32, **names))


def write_damage_entry(registry: Path, config: DamageModelConfig, classes: dict[int, str] | None = None,
                       always: str | None = None) -> Path:
    """A registry entry as ``adopt_damage_run`` leaves it: default ``id2label``, the numbering in ``label_schema.json``.

    ``always`` biases the classifier so that every pixel is that damage class.
    """
    classes = classes or CLASSES
    model_dir = registry / config.model_version
    model_dir.mkdir(parents=True)
    model = _tiny(len(classes))
    if always:
        with torch.no_grad():
            model.decode_head.classifier.bias[{c: i for i, c in classes.items()}[always]] = 1000.0
    model.save_pretrained(model_dir)
    weights = (model_dir / "model.safetensors").read_bytes()
    (model_dir / "manifest.json").write_text(json.dumps({
        "model_id": config.model_id, "version": config.model_version, "taxonomy_version": config.taxonomy_version,
        "weights_sha256": hashlib.sha256(weights).hexdigest(), "status": "candidate"}), encoding="utf-8")
    (model_dir / "label_schema.json").write_text(json.dumps({
        "taxonomy_version": config.taxonomy_version, "id_to_code": {str(i): code for i, code in classes.items()}}),
        encoding="utf-8")
    (model_dir / "preprocessing.json").write_text(json.dumps(PREPROCESSING), encoding="utf-8")
    return model_dir


@pytest.fixture
def config() -> DamageModelConfig:
    return DamageModelConfig(model_id="damage-hitl", model_version="damage-hitl/9.9.9-test",
                             taxonomy_version="damage-hitl-1.0.0", config_version="damage-cfg-test", input_size=128,
                             device="cpu")


@pytest.fixture
def registry(tmp_path: Path, config: DamageModelConfig) -> Path:
    write_damage_entry(tmp_path / "registry", config, always="corrosion")
    return tmp_path / "registry"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_the_served_damage_configuration_names_the_hitl_model_and_its_taxonomy():
    served = load_damage_model_config()
    assert served.model_version == "damage-hitl/0.1.0-b3-compound"
    assert served.taxonomy_version == "damage-hitl-1.0.0"
    assert (served.input_size, served.resize_policy) == (512, "longest_edge_pad")


def test_damage_stage_versions_pin_both_models_the_assignment_rules_and_the_vocabulary(config):
    versions = config.stage_versions("0.2.0", parts_model="parts/1.0.0", assignment_config="m2-assignment/0.2.0")
    assert versions == {"damage_model": "damage-hitl/9.9.9-test", "damage_config": "damage-cfg-test",
                        "parts_model": "parts/1.0.0", "assignment_config": "m2-assignment/0.2.0",
                        "taxonomy": "damage-hitl-1.0.0", "code": "0.2.0"}


def test_a_damage_configuration_must_name_a_known_damage_taxonomy():
    with pytest.raises(ValueError, match="damage taxonomy"):
        DamageModelConfig(model_version="damage/1", taxonomy_version="parts-1.0.0", config_version="c")


# ---------------------------------------------------------------------------
# Loading and verifying the checkpoint
# ---------------------------------------------------------------------------

def test_load_damage_segmenter_serves_the_verified_entry_with_its_own_class_numbering(registry, config):
    segmenter = damage_adapter.load_damage_segmenter(config, registry)
    assert segmenter.model_dir == registry / "damage-hitl" / "9.9.9-test"
    assert segmenter.damage_classes == {i: code for i, code in CLASSES.items() if i}


def _edit(name: str, **changes):
    def apply(model_dir: Path) -> None:
        path = model_dir / name
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **changes}), encoding="utf-8")
    return apply


def _append_to_weights(model_dir: Path) -> None:
    with (model_dir / "model.safetensors").open("ab") as handle:
        handle.write(b"tampered")


CARDD_NUMBERING = {str(i): code for i, code in enumerate(
    ["background", "dent", "scratch", "crack", "glass-shatter", "lamp-broken", "tire-flat", "dent", "scratch"])}


@pytest.mark.parametrize(("damage", "reason_code"), [
    pytest.param(lambda d: (d / "manifest.json").unlink(), "model_manifest_missing", id="no-manifest"),
    pytest.param(_edit("manifest.json", version="damage-hitl/0.0.1"), "model_version_mismatch", id="another-version"),
    pytest.param(_edit("manifest.json", taxonomy_version="hitl-damage-1.0.0"), "taxonomy_version_mismatch",
                 id="the-trainers-own-taxonomy-name"),
    pytest.param(_append_to_weights, "model_weights_hash_mismatch", id="changed-weights"),
    pytest.param(_edit("label_schema.json", id_to_code=CARDD_NUMBERING), "label_map_mismatch",
                 id="classes-of-another-vocabulary"),
    pytest.param(_edit("label_schema.json", id_to_code={str(i): c for i, c in CLASSES.items() if i != 8}),
                 "label_map_mismatch", id="a-class-without-a-code"),
    pytest.param(_edit("label_schema.json", id_to_code={**{str(i): c for i, c in CLASSES.items()}, "0": "dent"}),
                 "label_map_mismatch", id="class-zero-is-not-background"),
    pytest.param(lambda d: (d / "preprocessing.json").unlink(), "preprocessing_missing", id="no-frame-record"),
    pytest.param(_edit("preprocessing.json", input_size=640), "preprocessing_mismatch", id="another-size"),
])
def test_load_damage_segmenter_refuses_an_entry_it_cannot_verify(registry, config, damage, reason_code):
    damage(registry / "damage-hitl" / "9.9.9-test")
    with pytest.raises(ModelUnavailable) as refused:
        damage_adapter.load_damage_segmenter(config, registry)
    assert refused.value.reason_code == reason_code


def test_load_damage_segmenter_refuses_a_checkpoint_without_names_for_its_classes(tmp_path, config):
    """``LABEL_0`` to ``LABEL_8`` is what the trainer left in the checkpoint; without a recorded numbering it is refused."""
    model_dir = write_damage_entry(tmp_path / "registry", config)
    (model_dir / "label_schema.json").unlink()
    with pytest.raises(ModelUnavailable) as refused:
        damage_adapter.load_damage_segmenter(config, tmp_path / "registry")
    assert refused.value.reason_code == "label_map_mismatch"


def test_load_damage_segmenter_refuses_weights_that_do_not_all_load(tmp_path, config):
    from safetensors.torch import load_file, save_file

    model_dir = write_damage_entry(tmp_path / "registry", config)
    weights = model_dir / "model.safetensors"
    state = load_file(weights)
    state["decode_head.renamed_by_another_version.weight"] = state.pop("decode_head.classifier.weight")
    save_file(state, weights, metadata={"format": "pt"})
    # the file is intact; its names are not
    _edit("manifest.json", weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest())(model_dir)
    with pytest.raises(ModelUnavailable) as refused:
        damage_adapter.load_damage_segmenter(config, tmp_path / "registry")
    assert refused.value.reason_code == "model_weights_incomplete"


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

def _photo(width: int = 640, height: int = 480, seed: int = 1) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(seed).integers(0, 255, (height, width, 3), dtype=np.uint8)).save(
        buf, format="PNG")
    return buf.getvalue()


def _part_mask(size: int = 128, rows: int = 96) -> np.ndarray:
    """Front-door on the left half and fender on the right half of the photograph's rows; padding is background."""
    mask = np.zeros((size, size), dtype=np.uint8)
    mask[:rows, :size // 2] = PART_ID["front-door"]
    mask[:rows, size // 2:] = PART_ID["fender"]
    return mask


def _png(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, format="PNG")
    return buf.getvalue()


def _versions(config: DamageModelConfig) -> dict[str, str]:
    return config.stage_versions("0.2.0", parts_model="parts/9.9.9-test",
                                 assignment_config=load_assignment_config().config_version)


def _request(config: DamageModelConfig, photo: bytes, part_mask: np.ndarray | None, /, **changes):
    """A request for ``photo`` and ``part_mask``; ``changes`` then replaces fields without fixing up the others."""
    frame = build_model_frame(photo, config.input_size, config.resize_policy)
    mask_bytes = None if part_mask is None else _png(part_mask)
    ref = None if part_mask is None else MaskRef(
        artifact_id="pm_test", object_uri="s3://cmev-evidence/test/parts/mask.png",
        sha256=hashlib.sha256(mask_bytes).hexdigest(), width=config.input_size, height=config.input_size,
        encoding="class_index_png", source_photo_id=PHOTO_ID)
    values = dict(claim_id=CLAIM_ID, input_revision=1, job_key="job-damage-1", photo_id=PHOTO_ID, photo=photo,
                  photo_sha256=hashlib.sha256(photo).hexdigest(), part_mask=mask_bytes, part_mask_ref=ref,
                  transform=frame.transform, accepted_parts=("front-door", "fender"), versions=_versions(config),
                  provenance=PROVENANCE, object_uri_prefix="s3://cmev-evidence/")
    return damage_adapter.DamageSegmentRequest(**{**values, **changes})


@pytest.fixture
def segmenter(registry, config):
    return damage_adapter.load_damage_segmenter(config, registry)


def _run(segmenter, request, **overrides):
    rules = load_assignment_config()
    return damage_adapter.run_damage_segmentation(request, segmenter, rules.with_overrides(overrides) if overrides else rules)


def test_damage_on_one_part_becomes_an_assigned_observation_in_the_models_vocabulary(segmenter, config):
    part_mask = _part_mask()
    part_mask[:96, :] = PART_ID["front-door"]  # the whole photograph is one door
    result = _run(segmenter, _request(config, _photo(), part_mask))

    [observation] = result.observations
    assert (observation.damage_code, observation.part_code, observation.assignment_status) == \
        ("corrosion", "front-door", "assigned")
    assert observation.side == "unknown" and observation.photo_id == PHOTO_ID
    assert observation.versions == _versions(config) and observation.versions["taxonomy"] == "damage-hitl-1.0.0"
    assert observation.provenance.source_kind == "real"
    assert observation.area_pixels == 96 * 128 and observation.area_denominator_pixels == 128 * 128
    assert observation.part_mask_ref.artifact_id == "pm_test"


def test_damage_that_straddles_two_parts_stays_one_unresolved_observation(segmenter, config):
    result = _run(segmenter, _request(config, _photo(), _part_mask()))
    [observation] = result.observations
    assert (observation.part_code, observation.part_reason) == (None, "ambiguous_between_parts")
    assert [c.part_code for c in observation.candidates] == ["front-door", "fender"]


def test_padding_is_never_damage(segmenter, config):
    """The checkpoint calls every pixel corrosion; the 32 padding rows below a 4:3 photograph are not the vehicle."""
    result = _run(segmenter, _request(config, _photo(), _part_mask()))
    mask = np.array(Image.open(io.BytesIO(result.artifact("mask.png").data)))
    assert mask.shape == (128, 128)
    assert np.all(mask[:96] == DAMAGE_ID["corrosion"]) and np.all(mask[96:] == 0)
    assert result.observations[0].area_pixels == 96 * 128


def test_artifacts_are_the_class_mask_and_the_component_raster_under_the_version_signature(segmenter, config):
    request = _request(config, _photo(), _part_mask())
    result = _run(segmenter, request)
    folder = f"claims/{CLAIM_ID}/1/damage/{PHOTO_ID}/{version_signature(request.versions)}"
    assert [a.key for a in result.artifacts] == [f"{folder}/mask.png", f"{folder}/components.png"]
    components = np.array(Image.open(io.BytesIO(result.artifact("components.png").data)))
    assert components.dtype == np.uint16 and set(np.unique(components)) == {0, 1}
    assert result.observations[0].damage_mask_ref.component_index == 1
    ref = result.damage_mask_ref
    assert ref.object_uri == f"s3://cmev-evidence/{folder}/mask.png"
    assert ref.sha256 == hashlib.sha256(result.artifact("mask.png").data).hexdigest()
    payload = result.event_payload()
    assert payload["photo_id"] == PHOTO_ID and payload["observation_ids"] == [result.observations[0].observation_id]


def test_small_or_uncertain_regions_are_dropped_by_the_assignment_rules_not_by_the_adapter(segmenter, config):
    result = _run(segmenter, _request(config, _photo(), _part_mask()), regions={"min_damage_pixels": 128 * 128})
    assert result.observations == () and result.assignment.below_min_pixels_count == 1
    assert result.event_payload()["empty_result"] is True


def test_the_damage_model_is_fed_the_same_tensor_as_the_parts_model(segmenter, config, tmp_path):
    photo = _photo(400, 300, seed=7)
    parts_dir = tmp_path / "parts"
    _tiny(22, ID_TO_PART_CODE).save_pretrained(parts_dir)
    parts = PartsSegmenter(model_dir=parts_dir, config=PartsConfig(input_size=128, device="cpu", write_overlay=False))
    seen: dict[str, torch.Tensor] = {}
    parts.model.register_forward_pre_hook(lambda _m, _a, kw: seen.__setitem__("parts", kw["pixel_values"]),
                                          with_kwargs=True)
    segmenter.model.register_forward_pre_hook(lambda _m, _a, kw: seen.__setitem__("damage", kw["pixel_values"]),
                                              with_kwargs=True)

    outcome = run_parts_segmentation(PartsSegmentRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key="job-parts-1", versions={"code": "test"},
        provenance={"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-worker-parts"},
        photos=(PhotoFileInput(file_id=PHOTO_ID, media_type="image/png",
                               sha256=hashlib.sha256(photo).hexdigest(), data=photo),)), parts).photo_outcomes[0]
    mask = np.array(Image.open(io.BytesIO(outcome.artifacts[0].data)))
    _run(segmenter, _request(config, photo, mask, transform=outcome.transform))

    assert torch.equal(seen["parts"], seen["damage"])


def test_a_part_mask_made_in_another_frame_is_refused(segmenter, config):
    request = _request(config, _photo(), _part_mask())
    shifted = request.transform.model_copy(update={"pad_top": 16.0})
    with pytest.raises(damage_adapter.DamageInputError) as refused:
        _run(segmenter, _request(config, _photo(), _part_mask(), transform=shifted))
    assert refused.value.reason_code == "transform_mismatch"


def test_a_missing_part_mask_keeps_the_damage_with_unknown_part(segmenter, config):
    result = _run(segmenter, _request(config, _photo(), None))
    [observation] = result.observations
    assert (observation.part_code, observation.part_reason, observation.part_mask_ref) == \
        (None, "part_masks_missing", None)
    assert observation.damage_code == "corrosion"


@pytest.mark.parametrize(("changes", "reason_code"), [
    pytest.param({"photo_sha256": "0" * 64}, "artifact_hash_mismatch", id="photo-hash"),
    pytest.param({"photo": b"not an image", "photo_sha256": hashlib.sha256(b"not an image").hexdigest()},
                 "corrupt_photo", id="undecodable-photo"),
    pytest.param({"part_mask": b"other bytes"}, "artifact_hash_mismatch", id="part-mask-hash"),
])
def test_inputs_that_are_not_what_the_command_named_are_refused(segmenter, config, changes, reason_code):
    with pytest.raises(damage_adapter.DamageInputError) as refused:
        _run(segmenter, _request(config, _photo(), _part_mask(), **changes))
    assert refused.value.reason_code == reason_code


def test_the_vocabulary_is_the_whole_hitl_set(registry, config):
    assert set(damage_adapter.load_damage_segmenter(config, registry).damage_classes.values()) == set(HITL_DAMAGE_CODES)


# ---------------------------------------------------------------------------
# The vocabulary is the served entry's: a CarDD checkpoint gives CarDD observations
# ---------------------------------------------------------------------------

def test_a_cardd_entry_is_served_with_cardd_codes_under_the_cardd_taxonomy(tmp_path):
    cardd = {0: "background", 1: "dent", 2: "scratch", 3: "crack", 4: "glass-shatter", 5: "lamp-broken", 6: "tire-flat"}
    config = DamageModelConfig(model_id="damage-cardd", model_version="damage-cardd/9.9.9-test",
                               taxonomy_version="damage-cardd-1.0.0", config_version="damage-cfg-test",
                               input_size=128, device="cpu")
    write_damage_entry(tmp_path / "registry", config, classes=cardd, always="glass-shatter")
    segmenter = damage_adapter.load_damage_segmenter(config, tmp_path / "registry")
    part_mask = _part_mask()
    part_mask[:96, :] = PART_ID["windshield"]

    result = _run(segmenter, _request(config, _photo(), part_mask, accepted_parts=("windshield",)))

    [observation] = result.observations
    assert (observation.damage_code, observation.part_code) == ("glass-shatter", "windshield")
    assert observation.versions["taxonomy"] == "damage-cardd-1.0.0"
    # The same folder under the HITL taxonomy is refused: its classes are not that vocabulary's.
    with pytest.raises(ModelUnavailable) as refused:
        damage_adapter.load_damage_segmenter(config.model_copy(update={"taxonomy_version": "damage-hitl-1.0.0"}),
                                             tmp_path / "registry")
    assert refused.value.reason_code == "taxonomy_version_mismatch"
