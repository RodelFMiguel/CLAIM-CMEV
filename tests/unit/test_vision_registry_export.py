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
