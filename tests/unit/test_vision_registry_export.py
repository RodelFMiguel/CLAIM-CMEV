"""Exporting a notebook training run to the registry the M1 worker serves from.

The runs here are tiny random-weight fixtures: they check the hand-over, not model quality.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

from claim_cmev.vision.palette import ID_TO_PART_CODE  # noqa: E402
from claim_cmev.vision.parts import adapter as parts_adapter  # noqa: E402
from claim_cmev.vision.parts.config import PartsConfig  # noqa: E402
from pipelines.vision import training as t  # noqa: E402
from pipelines.vision.registry import export_parts_run  # noqa: E402

PART_CLASSES = [ID_TO_PART_CODE[i] for i in sorted(ID_TO_PART_CODE)]
VERSION = "parts/9.9.9-notebook"
_BUILD_MODEL = t.build_model


def _tiny_segformer(config, classes, **kwargs):
    from transformers import SegformerConfig
    if kwargs.get("saved_config") is None:
        kwargs["saved_config"] = SegformerConfig(depths=[1, 1, 1, 1], hidden_sizes=[8, 16, 32, 64],
                                                 num_attention_heads=[1, 2, 4, 8], decoder_hidden_size=16).to_dict()
    return _BUILD_MODEL(config, classes, **{**kwargs, "load_pretrained": False})


def _manifest(tmp_path: Path, task: str = "parts", classes: list[str] | None = None) -> dict:
    records = []
    for i, split in enumerate(("train", "train", "val", "test")):
        pixels = np.zeros((32, 48, 3), dtype=np.uint8)
        pixels[:, 20:, 0] = 255
        labels = np.zeros((32, 48), dtype=np.uint8)
        labels[:, 20:] = 1
        image_path, mask_path = tmp_path / f"image{i}.png", tmp_path / f"mask{i}.png"
        Image.fromarray(pixels).save(image_path)
        Image.fromarray(labels).save(mask_path)
        records.append({"task": task, "sample_id": str(i), "group_id": str(i), "split": split,
                        "image_path": str(image_path), "mask_path": str(mask_path)})
    return {"records": records, "class_names": classes or PART_CLASSES, "manifest_hash": "fixture-manifest"}


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: dict | None = None, **settings) -> dict:
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    config = t.TrainingConfig(**{"epochs": 1, "image_size": 32, "batch_size": 1, "frame": "serving",
                                 "artifacts_root": str(tmp_path / "artifacts"), "run_id": "run", "device": "cpu",
                                 "pretrained": False, **settings})
    with patch.object(t, "build_model", side_effect=_tiny_segformer):
        return t.train(manifest or _manifest(tmp_path), config)


@pytest.fixture
def serving() -> PartsConfig:
    """A serving configuration at the fixture's size; the real one serves 512."""
    return PartsConfig(model_version="parts/0.0.0-other", input_size=32, device="cpu", write_overlay=False)


def test_a_serving_frame_run_records_the_frame_it_was_trained_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    record = _run(tmp_path, monkeypatch)
    assert {key: record["preprocessing"][key] for key in ("input_size", "resize_policy", "pixel_mean", "pixel_std",
                                                         "pad_value")} == {
        "input_size": 32, "resize_policy": "longest_edge_pad", "pixel_mean": [0.485, 0.456, 0.406],
        "pixel_std": [0.229, 0.224, 0.225], "pad_value": 0}


def test_a_centred_run_keeps_the_preprocessing_record_of_earlier_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    record = _run(tmp_path, monkeypatch, frame="centred")
    assert record["preprocessing"] == {
        "resize": "aspect-preserving centered letterbox", "mask_resampling": "nearest", "ignore_index": 255,
        "background": 0, "reduce_labels": False,
        "evaluation_frame": "resized letterboxed model frame; padding excluded"}


def test_export_writes_a_registry_entry_the_parts_worker_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              serving: PartsConfig):
    record = _run(tmp_path, monkeypatch)
    registry = tmp_path / "registry"

    entry = export_parts_run(record["run_dir"], VERSION, registry_root=registry, parts_config=serving)

    assert entry == registry / "parts" / "9.9.9-notebook"
    served = parts_adapter.load_parts_segmenter(serving.model_copy(update={"model_version": VERSION}), registry)
    trained = t.load_run(record["run_dir"], device="cpu")[0]
    pixels = torch.rand(1, 3, 32, 32, generator=torch.Generator().manual_seed(0))
    with torch.no_grad():
        assert torch.equal(served.model(pixel_values=pixels).logits, trained.network(pixels).logits)

    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION and manifest["model_id"] == "parts" and manifest["status"] == "candidate"
    assert manifest["taxonomy_version"] == serving.taxonomy_version
    assert manifest["weights_sha256"] == hashlib.sha256((entry / "model.safetensors").read_bytes()).hexdigest()
    saved = json.loads(Path(record["run_dir"], "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_run"]["run_id"] == "run"
    assert manifest["source_run"]["checkpoint_sha256"] == saved["checkpoint_sha256"]
    assert manifest["source_run"]["manifest_hash"] == "fixture-manifest"
    assert manifest["source_run"]["split_hash"] == saved["split_hash"]
    assert manifest["metrics"]["val_mIoU_foreground"] == saved["best_val_miou_foreground"]
    assert manifest["dependency_versions"]["transformers"]
    preprocessing = json.loads((entry / "preprocessing.json").read_text(encoding="utf-8"))
    assert preprocessing["resize_policy"] == "longest_edge_pad" and preprocessing["pad_value"] == 0
    assert preprocessing["input_size"] == 32


def test_export_refuses_a_run_trained_in_the_centred_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           serving: PartsConfig):
    record = _run(tmp_path, monkeypatch, frame="centred")
    with pytest.raises(ValueError, match="frame"):
        export_parts_run(record["run_dir"], VERSION, registry_root=tmp_path / "registry", parts_config=serving)
    assert not (tmp_path / "registry" / "parts").exists()


def test_export_refuses_a_size_the_worker_does_not_serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    record = _run(tmp_path, monkeypatch)
    with pytest.raises(parts_adapter.ModelUnavailable) as refused:
        export_parts_run(record["run_dir"], VERSION, registry_root=tmp_path / "registry",
                         parts_config=PartsConfig(input_size=512, device="cpu"))
    assert refused.value.reason_code == "preprocessing_mismatch"
    assert not any((tmp_path / "registry").rglob("*.safetensors"))
    assert not (tmp_path / "registry" / "parts" / "9.9.9-notebook").exists()


def test_export_refuses_to_replace_a_registry_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    serving: PartsConfig):
    record = _run(tmp_path, monkeypatch)
    registry = tmp_path / "registry"
    export_parts_run(record["run_dir"], VERSION, registry_root=registry, parts_config=serving)
    before = (registry / "parts" / "9.9.9-notebook" / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        export_parts_run(record["run_dir"], VERSION, registry_root=registry, parts_config=serving)
    assert (registry / "parts" / "9.9.9-notebook" / "manifest.json").read_bytes() == before


def test_export_refuses_a_damage_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, serving: PartsConfig):
    record = _run(tmp_path, monkeypatch, manifest=_manifest(tmp_path, task="damage", classes=["background", "dent"]),
                  task="damage")
    with pytest.raises(ValueError, match="parts"):
        export_parts_run(record["run_dir"], VERSION, registry_root=tmp_path / "registry", parts_config=serving)


def test_export_refuses_classes_that_are_not_the_parts_taxonomy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               serving: PartsConfig):
    swapped = [PART_CLASSES[0], PART_CLASSES[2], PART_CLASSES[1], *PART_CLASSES[3:]]
    record = _run(tmp_path, monkeypatch, manifest=_manifest(tmp_path, classes=swapped))
    with pytest.raises(ValueError, match="taxonomy"):
        export_parts_run(record["run_dir"], VERSION, registry_root=tmp_path / "registry", parts_config=serving)


@pytest.mark.parametrize("version", ["damage/1.0.0", "parts/../escape", "parts", "parts/a/b", "/abs/parts/1"])
def test_export_refuses_a_version_outside_the_parts_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            serving: PartsConfig, version: str):
    record = _run(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="parts/<name>"):
        export_parts_run(record["run_dir"], version, registry_root=tmp_path / "registry", parts_config=serving)


# ---------------------------------------------------------------------------
# Adopting a damage trainer's output folder as a registry entry
# ---------------------------------------------------------------------------

from claim_cmev.contracts.common import HITL_DAMAGE_CODES  # noqa: E402
from claim_cmev.vision.damage import adapter as damage_adapter  # noqa: E402
from claim_cmev.vision.damage.model_config import DamageModelConfig  # noqa: E402
from claim_cmev.vision.registry import ModelUnavailable  # noqa: E402
from pipelines.vision.registry import adopt_damage_run  # noqa: E402

TRAINER_ORDER = ["background", "missing-part", "broken-part", "scratch", "cracked", "dent", "flaking", "paint-chip",
                 "corrosion"]


def _trainer_output(folder: Path, *, input_size: int = 128, classes: list[str] | None = None) -> Path:
    """What pipelines/vision/train_damage.py leaves: default class names, its own version and taxonomy name."""
    from transformers import SegformerConfig, SegformerForSemanticSegmentation
    classes = classes or TRAINER_ORDER
    folder.mkdir(parents=True)
    torch.manual_seed(0)
    SegformerForSemanticSegmentation(SegformerConfig(
        num_labels=9, depths=[1, 1, 1, 1], hidden_sizes=[16, 32, 64, 128], num_attention_heads=[1, 2, 4, 8],
        decoder_hidden_size=32)).save_pretrained(folder)
    (folder / "config.yaml").write_text(f"input_size: {input_size}\nresize_policy: longest_edge_pad\n", encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({
        "model_id": "damage", "model_version": "damage/9.9.9-test", "taxonomy_version": "hitl-damage-1.0.0",
        "best_val_miou_foreground": 0.2, "per_class_validation": {
            name: {"class_id": index, "iou": 0.1} for index, name in enumerate(classes)}}), encoding="utf-8")
    return folder


@pytest.fixture
def damage_config() -> DamageModelConfig:
    return DamageModelConfig(model_id="damage-hitl", model_version="damage-hitl/9.9.9-test",
                             taxonomy_version="damage-hitl-1.0.0", config_version="damage-cfg-test", input_size=128,
                             device="cpu")


def test_adopting_a_damage_run_in_place_makes_an_entry_the_damage_worker_loads(tmp_path: Path, damage_config):
    registry = tmp_path / "registry"
    run = _trainer_output(registry / "damage-hitl" / "9.9.9-test")
    trainer_manifest = (run / "manifest.json").read_bytes()

    entry = adopt_damage_run(run, registry_root=registry, damage_config=damage_config)

    assert entry == run.resolve()
    segmenter = damage_adapter.load_damage_segmenter(damage_config, registry)
    assert segmenter.damage_classes == dict(enumerate(TRAINER_ORDER[1:], start=1))
    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "damage-hitl/9.9.9-test" and manifest["taxonomy_version"] == "damage-hitl-1.0.0"
    assert manifest["weights_sha256"] == hashlib.sha256((entry / "model.safetensors").read_bytes()).hexdigest()
    assert manifest["best_val_miou_foreground"] == 0.2  # what the trainer recorded is kept
    assert manifest["trainer"] == {"model_version": "damage/9.9.9-test", "taxonomy_version": "hitl-damage-1.0.0"}
    assert (entry / "manifest.trainer.json").read_bytes() == trainer_manifest
    preprocessing = json.loads((entry / "preprocessing.json").read_text(encoding="utf-8"))
    assert (preprocessing["input_size"], preprocessing["resize_policy"], preprocessing["pad_value"]) == \
        (128, "longest_edge_pad", 0)

    again = adopt_damage_run(run, registry_root=registry, damage_config=damage_config)  # a second run changes nothing
    assert again == entry and (entry / "manifest.trainer.json").read_bytes() == trainer_manifest
    assert json.loads((entry / "manifest.json").read_text(encoding="utf-8"))["weights_sha256"] == manifest["weights_sha256"]


def test_adopting_a_damage_run_from_elsewhere_copies_it_and_never_replaces_an_entry(tmp_path: Path, damage_config):
    run = _trainer_output(tmp_path / "training-output")
    entry = adopt_damage_run(run, registry_root=tmp_path / "registry", damage_config=damage_config)
    assert entry == (tmp_path / "registry" / "damage-hitl" / "9.9.9-test").resolve()
    assert not (run / "label_schema.json").exists()  # the trainer's folder is left as it was
    assert damage_adapter.load_damage_segmenter(damage_config, tmp_path / "registry").damage_classes[8] == "corrosion"
    with pytest.raises(FileExistsError):
        adopt_damage_run(_trainer_output(tmp_path / "another-output"), registry_root=tmp_path / "registry",
                         damage_config=damage_config)


@pytest.mark.parametrize("classes", [
    pytest.param(TRAINER_ORDER[:-1] + ["rust"], id="a-class-outside-the-taxonomy"),
    pytest.param(["dent", *TRAINER_ORDER[1:]], id="class-zero-is-not-background"),
])
def test_adopting_refuses_a_run_whose_classes_are_not_the_taxonomy(tmp_path: Path, damage_config, classes):
    run = _trainer_output(tmp_path / "registry" / "damage-hitl" / "9.9.9-test", classes=classes)
    before = sorted(p.name for p in run.iterdir())
    with pytest.raises(ModelUnavailable) as refused:
        adopt_damage_run(run, registry_root=tmp_path / "registry", damage_config=damage_config)
    assert refused.value.reason_code == "label_map_mismatch"
    assert sorted(p.name for p in run.iterdir()) == before  # nothing was written


def test_adopting_refuses_a_run_trained_at_a_size_the_worker_does_not_serve(tmp_path: Path, damage_config):
    run = _trainer_output(tmp_path / "registry" / "damage-hitl" / "9.9.9-test", input_size=640)
    with pytest.raises(ModelUnavailable) as refused:
        adopt_damage_run(run, registry_root=tmp_path / "registry", damage_config=damage_config)
    assert refused.value.reason_code == "preprocessing_mismatch"
    assert not (run / "preprocessing.json").exists()


def test_the_hitl_vocabulary_is_what_the_trainer_numbers():
    assert set(TRAINER_ORDER[1:]) == set(HITL_DAMAGE_CODES)


# ---------------------------------------------------------------------------
# Exporting a notebook damage run for the M2 worker
# ---------------------------------------------------------------------------

from pipelines.vision.registry import export_damage_run  # noqa: E402

CARDD_CLASSES = ["background", "dent", "scratch", "crack", "glass-shatter", "lamp-broken", "tire-flat"]
HITL_NOTEBOOK_CLASSES = ["background", "dent", "cracked", "scratch", "flaking", "broken-part", "paint-chip",
                         "missing-part", "corrosion"]  # the notebook's numbering, not the script trainer's
CARDD_VERSION = "damage-cardd/9.9.9-notebook"


def _damage_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str = "damage_cardd",
                classes: list[str] | None = None, **settings) -> dict:
    classes = classes or CARDD_CLASSES
    return _run(tmp_path, monkeypatch, manifest=_manifest(tmp_path, task=task, classes=classes), task=task, **settings)


@pytest.fixture
def worker_frame() -> DamageModelConfig:
    """The frame the damage worker builds, at the fixture's size; the real one serves 512."""
    return DamageModelConfig(model_version="damage-hitl/0.0.0-served", taxonomy_version="damage-hitl-1.0.0",
                             config_version="damage-cfg-test", input_size=32, device="cpu")


def _served(version: str, taxonomy: str) -> DamageModelConfig:
    return DamageModelConfig(model_id=version.split("/")[0], model_version=version, taxonomy_version=taxonomy,
                             config_version="damage-cfg-test", input_size=32, device="cpu")


def test_export_damage_run_writes_an_entry_the_damage_worker_loads_under_the_runs_vocabulary(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_frame):
    record = _damage_run(tmp_path, monkeypatch)
    registry = tmp_path / "registry"

    entry = export_damage_run(record["run_dir"], CARDD_VERSION, registry_root=registry, damage_config=worker_frame)

    assert entry == registry / "damage-cardd" / "9.9.9-notebook"
    served = damage_adapter.load_damage_segmenter(_served(CARDD_VERSION, "damage-cardd-1.0.0"), registry)
    assert served.damage_classes == dict(enumerate(CARDD_CLASSES[1:], start=1))
    trained = t.load_run(record["run_dir"], device="cpu")[0]
    pixels = torch.rand(1, 3, 32, 32, generator=torch.Generator().manual_seed(0))
    with torch.no_grad():
        assert torch.equal(served.model(pixel_values=pixels).logits, trained.network(pixels).logits)
    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    saved = json.loads(Path(record["run_dir"], "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["model_id"], manifest["version"], manifest["status"]) == ("damage-cardd", CARDD_VERSION, "candidate")
    assert manifest["taxonomy_version"] == "damage-cardd-1.0.0"
    assert manifest["weights_sha256"] == hashlib.sha256((entry / "model.safetensors").read_bytes()).hexdigest()
    assert manifest["source_run"]["checkpoint_sha256"] == saved["checkpoint_sha256"]
    assert manifest["metrics"]["val_mIoU_foreground"] == saved["best_val_miou_foreground"]
    preprocessing = json.loads((entry / "preprocessing.json").read_text(encoding="utf-8"))
    assert (preprocessing["input_size"], preprocessing["resize_policy"], preprocessing["pad_value"]) == \
        (32, "longest_edge_pad", 0)


def test_export_damage_run_keeps_a_hitl_runs_own_class_numbering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                worker_frame):
    record = _damage_run(tmp_path, monkeypatch, task="damage", classes=HITL_NOTEBOOK_CLASSES)
    version = "damage-hitl/9.9.9-notebook"
    export_damage_run(record["run_dir"], version, registry_root=tmp_path / "registry", damage_config=worker_frame)
    served = damage_adapter.load_damage_segmenter(_served(version, "damage-hitl-1.0.0"), tmp_path / "registry")
    assert served.damage_classes == dict(enumerate(HITL_NOTEBOOK_CLASSES[1:], start=1))


def test_export_damage_run_refuses_a_run_trained_in_the_centred_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                    worker_frame):
    record = _damage_run(tmp_path, monkeypatch, frame="centred")
    with pytest.raises(ValueError, match="frame"):
        export_damage_run(record["run_dir"], CARDD_VERSION, registry_root=tmp_path / "registry",
                          damage_config=worker_frame)
    assert not (tmp_path / "registry" / "damage-cardd").exists()


def test_export_damage_run_refuses_a_size_the_worker_does_not_serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    record = _damage_run(tmp_path, monkeypatch)
    at_512 = DamageModelConfig(model_version="damage-hitl/0.0.0-served", taxonomy_version="damage-hitl-1.0.0",
                               config_version="c", input_size=512)
    with pytest.raises(ModelUnavailable) as refused:
        export_damage_run(record["run_dir"], CARDD_VERSION, registry_root=tmp_path / "registry", damage_config=at_512)
    assert refused.value.reason_code == "preprocessing_mismatch"
    assert not (tmp_path / "registry" / "damage-cardd" / "9.9.9-notebook").exists()


def test_export_damage_run_refuses_a_parts_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_frame):
    record = _run(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="damage"):
        export_damage_run(record["run_dir"], CARDD_VERSION, registry_root=tmp_path / "registry",
                          damage_config=worker_frame)


@pytest.mark.parametrize("version", ["damage-hitl/9.9.9-notebook", "damage/9.9.9", "parts/9.9.9", "damage-cardd",
                                     "damage-cardd/a/b", "damage-cardd/.."])
def test_export_damage_run_names_the_entry_after_the_runs_vocabulary(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_frame, version: str):
    """A CarDD run is ``damage-cardd/<name>``: the folder says which vocabulary its observations carry."""
    record = _damage_run(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="damage-cardd/<name>"):
        export_damage_run(record["run_dir"], version, registry_root=tmp_path / "registry", damage_config=worker_frame)


def test_export_damage_run_refuses_classes_of_no_known_vocabulary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                worker_frame):
    record = _damage_run(tmp_path, monkeypatch, classes=["background", "dent", "rust"])
    with pytest.raises(ValueError, match="vocabulary"):
        export_damage_run(record["run_dir"], CARDD_VERSION, registry_root=tmp_path / "registry",
                          damage_config=worker_frame)


def test_export_damage_run_refuses_to_replace_a_registry_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              worker_frame):
    record = _damage_run(tmp_path, monkeypatch)
    export_damage_run(record["run_dir"], CARDD_VERSION, registry_root=tmp_path / "registry", damage_config=worker_frame)
    with pytest.raises(FileExistsError):
        export_damage_run(record["run_dir"], CARDD_VERSION, registry_root=tmp_path / "registry",
                          damage_config=worker_frame)
