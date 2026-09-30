"""Offline harness checks with synthetic masks; these are not measured model results."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from pipelines.vision import training as t


def test_confusion_ignores_void_and_includes_false_positive_only_class():
    counts = t.confusion_counts(np.array([0, 1, 1, 255]), np.array([2, 1, 0, 2]), 4)
    metrics = t.metrics_from_confusion(counts, ["background", "dent", "crack", "absent"])
    assert metrics["valid_pixels"] == 3
    assert metrics["per_class"][1]["iou"] == .5
    assert metrics["per_class"][2]["iou"] == 0
    assert metrics["per_class"][3]["iou"] is None
    assert metrics["macro_class_count"] == 2
    assert metrics["miou_foreground"] == .25
    assert metrics["per_class"][3]["iou_reason"] == "absent_in_truth_and_prediction"
    assert metrics["per_class"][1]["recall"] == .5
    assert metrics["per_class"][1]["precision"] == 1


def test_letterbox_labels_preserved_and_padding_ignored():
    image = Image.new("RGB", (4, 2), "white")
    mask = Image.fromarray(np.array([[0, 1, 2, 255], [0, 1, 2, 255]], dtype=np.uint8))
    resized, target = t.letterbox(image, mask, 8)
    labels = np.asarray(target)
    assert resized.size == (8, 8)
    assert np.all(labels[:2] == 255) and np.all(labels[6:] == 255)
    assert set(np.unique(labels)) == {0, 1, 2, 255}
    assert labels[3].tolist() == [0, 0, 1, 1, 2, 2, 255, 255]


def test_manifest_refuses_cross_task_group_leakage():
    manifest = {"class_names": {"parts": ["background", "door"], "damage": ["background", "dent"]},
                "records": [{"task": "parts", "sample_id": "p", "group_id": "g", "split": "train"},
                            {"task": "damage", "sample_id": "d", "group_id": "g", "split": "test"}]}
    with pytest.raises(ValueError, match="Leakage"):
        t.task_manifest(manifest, "parts")


def test_config_rejects_path_traversal_and_invalid_loss():
    with pytest.raises(ValueError, match="single directory"):
        t.TrainingConfig(run_id="../old-run")
    with pytest.raises(ValueError, match="loss weight"):
        t.TrainingConfig(ce_weight=0, dice_weight=0)


TORCH = importlib.util.find_spec("torch") is not None


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_loss_void_pixels_have_zero_gradient():
    import torch
    logits = torch.randn(1, 3, 2, 2, requires_grad=True)
    labels = torch.tensor([[[0, 1], [255, 2]]])
    loss = t.segmentation_loss(logits, labels)
    loss.backward()
    assert torch.isfinite(loss)
    assert torch.all(logits.grad[0, :, 1, 0] == 0)
    logits = torch.randn(1, 3, 2, 2, requires_grad=True)
    t.segmentation_loss(logits, torch.full((1, 2, 2), 255)).backward()
    assert torch.all(logits.grad == 0)


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_dataset_paired_flip_and_palette_ids(tmp_path):
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[:, :16] = 255
    labels = np.zeros((32, 32), dtype=np.uint8)
    labels[:, :16] = 1
    Image.fromarray(image).save(tmp_path / "image.png")
    mask = Image.fromarray(labels).convert("P")
    mask.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    mask.save(tmp_path / "mask.png")
    config = t.TrainingConfig(image_size=32, horizontal_flip=1, brightness=0, contrast=0)
    dataset = t.SegmentationDataset([{"image_path": tmp_path / "image.png", "mask_path": tmp_path / "mask.png", "sample_id": "x"}], config, training=True, num_classes=2)
    result = dataset[0]
    assert np.all(result["labels"].numpy()[:, :16] == 0)
    assert np.all(result["labels"].numpy()[:, 16:] == 1)
    assert result["pixel_values"][0, 0, 0] < result["pixel_values"][0, 0, -1]


def _fixture_manifest(tmp_path):
    records = []
    for i, split in enumerate(("train", "train", "val", "test")):
        pixels = np.zeros((32, 48, 3), dtype=np.uint8)
        pixels[:, 20:, 0] = 255
        labels = np.zeros((32, 48), dtype=np.uint8)
        labels[:, 20:] = 1
        image_path, mask_path = tmp_path / f"image{i}.png", tmp_path / f"mask{i}.png"
        Image.fromarray(pixels).save(image_path)
        Image.fromarray(labels).save(mask_path)
        records.append({"task": "parts", "sample_id": str(i), "group_id": str(i), "split": split,
                        "image_path": str(image_path), "mask_path": str(mask_path)})
    return {"records": records, "class_names": ["background", "part"], "manifest_hash": "fixture-manifest"}


def _tiny_model(config, classes, **kwargs):
    import torch
    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Conv2d(3, 4, 1)
            self.head = torch.nn.Conv2d(4, len(classes), 1)
            self.architecture_config = {"fixture": True}
        def encoder_parameters(self):
            return list(self.network.parameters())
        def head_parameters(self):
            return list(self.head.parameters())
        def forward(self, pixels):
            return self.head(self.network(pixels).relu())
    return Tiny()


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_train_reload_evaluate_and_artifact_isolation(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    config = t.TrainingConfig(epochs=2, image_size=32, batch_size=1, accumulation_steps=3,
                              artifacts_root=str(tmp_path / "artifacts"), run_id="fixture", device="cpu", pretrained=False)
    with patch.object(t, "build_model", side_effect=_tiny_model):
        record = t.train(manifest, config)
        assert record["status"] == "completed"
        assert record["registry_status"] == "candidate"
        assert Path(record["run_dir"], "best.pt").is_file()
        assert not Path(record["evaluation_dir"], "test_metrics.json").exists()
        with pytest.raises(FileExistsError):
            t.train(manifest, config)
        evaluated = t.evaluate_run(record["run_dir"], manifest, split="val", device="cpu")
        assert evaluated["image_count"] == 1
        assert evaluated["valid_pixels"] == 32 * 21  # 48x32 -> 32x21, padded pixels ignored
        changed = {**manifest, "manifest_hash": "different-data"}
        with pytest.raises(ValueError, match="differs"):
            t.evaluate_run(record["run_dir"], changed, device="cpu")
        loaded, _, saved, _ = t.load_run(record["run_dir"], device="cpu")
        assert saved["checkpoint_sha256"]
        assert all(p.device.type == "cpu" for p in loaded.parameters())
        assert len(t.compare_runs([record["run_dir"]])) == 1
        import matplotlib
        matplotlib.use("Agg")
        t.plot_history(record["run_dir"])
        t.plot_confusion(record["run_dir"])
        t.show_predictions(record["run_dir"], manifest, count=1, device="cpu")
        assert Path(record["evaluation_dir"], "val_overlays.png").is_file()


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_failure_manifest_preserves_failure(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    config = t.TrainingConfig(image_size=32, artifacts_root=str(tmp_path / "artifacts"), run_id="failed", device="cpu")
    with patch.object(t, "build_model", side_effect=RuntimeError("fixture failure")):
        with pytest.raises(RuntimeError, match="fixture failure"):
            t.train(manifest, config)
    record = json.loads((tmp_path / "artifacts/models/parts/failed/manifest.json").read_text())
    assert record["status"] == "failed"
    assert record["failure"] == "RuntimeError: fixture failure"


def test_moved_run_rebinds_evaluation_location(tmp_path):
    directory = tmp_path / "artifacts/models/parts/moved"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"run_dir": "/old/run", "evaluation_dir": "/old/evaluation"}))
    record = t._read_run(directory)
    assert Path(record["evaluation_dir"]) == tmp_path / "artifacts/evaluation/moved"


def test_record_hash_verification_detects_mutation(tmp_path):
    artifact = tmp_path / "mask.png"
    artifact.write_bytes(b"original")
    expected = t._file_hash(artifact)
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        t._verify_records([{"sample_id": "x", "mask_path": str(artifact), "mask_sha256": expected}])
