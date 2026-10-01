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


def test_lr_factor_warmup_then_decay():
    assert t.lr_factor(0, 100, 10) == pytest.approx(0.1)
    assert t.lr_factor(9, 100, 10) == pytest.approx(1.0)
    assert t.lr_factor(10, 100, 10, "poly") == pytest.approx(1.0)
    assert t.lr_factor(55, 100, 10, "poly") == pytest.approx(0.5)
    assert t.lr_factor(55, 100, 10, "cosine") == pytest.approx(0.5)
    assert t.lr_factor(100, 100, 10, "poly") == 0
    assert t.lr_factor(0, 100, 0, "cosine") == pytest.approx(1.0)


def test_config_rejects_invalid_schedule_and_augmentation():
    with pytest.raises(ValueError, match="per-step schedule"):
        t.TrainingConfig(warmup_epochs=1)  # legacy cosine_epoch has no warmup
    with pytest.raises(ValueError, match="lr_schedule"):
        t.TrainingConfig(lr_schedule="step")
    with pytest.raises(ValueError, match="scale_range"):
        t.TrainingConfig(scale_range=(2.0, 0.5))
    with pytest.raises(ValueError, match="rotation"):
        t.TrainingConfig(rotation_degrees=90)
    # JSON round trips store tuples as lists; reloaded configs must still validate.
    assert t.TrainingConfig(scale_range=[0.5, 2.0], lr_schedule="poly", warmup_epochs=1).scale_range == [0.5, 2.0]


def test_random_scale_crop_keeps_label_ids_and_ignores_padding():
    import random as stdlib_random
    image = Image.new("RGB", (40, 20), "white")
    labels = np.zeros((20, 40), dtype=np.uint8)
    labels[:, 20:] = 3
    mask = Image.fromarray(labels)
    small, small_mask = t.random_scale_crop(image, mask, 32, 0.5, rng=stdlib_random.Random(1))
    small_labels = np.asarray(small_mask)
    assert small.size == small_mask.size == (32, 32)
    assert set(np.unique(small_labels)) == {0, 3, 255}
    assert np.count_nonzero(small_labels != 255) == 16 * 8  # 40x20 -> 32x16 fit, halved
    large, large_mask = t.random_scale_crop(image, mask, 32, 2.0, rng=stdlib_random.Random(1))
    assert large.size == (32, 32)
    assert not np.any(np.asarray(large_mask) == 255)  # 64x32 covers the whole window
    assert set(np.unique(np.asarray(large_mask))) <= {0, 3}


def test_dataset_rotation_pads_labels_with_ignore(tmp_path):
    Image.new("RGB", (32, 32), "white").save(tmp_path / "image.png")
    Image.fromarray(np.ones((32, 32), dtype=np.uint8)).save(tmp_path / "mask.png")
    config = t.TrainingConfig(image_size=32, horizontal_flip=0, brightness=0, contrast=0, saturation=0.2,
                              rotation_degrees=30, scale_range=(0.9, 1.1))
    record = {"image_path": tmp_path / "image.png", "mask_path": tmp_path / "mask.png", "sample_id": "x"}
    t.random.seed(3)
    labels = t.SegmentationDataset([record], config, training=True, num_classes=2)[0]["labels"].numpy()
    assert set(np.unique(labels)) <= {1, 255}
    assert np.any(labels == 255)
    evaluation = t.SegmentationDataset([record], config, training=False, num_classes=2)[0]["labels"].numpy()
    assert np.all(evaluation == 1)  # evaluation never augments


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_per_step_poly_schedule_warms_up_and_decays(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    config = t.TrainingConfig(epochs=3, image_size=32, batch_size=1, accumulation_steps=1, head_lr=1e-3,
                              encoder_lr=1e-4, lr_schedule="poly", warmup_epochs=1, patience=10,
                              scale_range=(0.5, 2.0), rotation_degrees=10,
                              artifacts_root=str(tmp_path / "artifacts"), run_id="poly", device="cpu", pretrained=False)
    with patch.object(t, "build_model", side_effect=_tiny_model):
        record = t.train(manifest, config)
    assert record["status"] == "completed"
    assert record["schedule"] == {"updates_per_epoch": 2, "total_updates": 6, "warmup_updates": 2}
    history = json.loads(Path(record["run_dir"], "history.json").read_text())
    # Learning rate after each epoch's updates: end of warmup, then linear decay to zero.
    assert [round(h["head_lr"] / 1e-3, 4) for h in history] == [1.0, 0.5, 0.0]


def test_show_targets_samples_rare_classes_and_refuses_test(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    manifest = _fixture_manifest(tmp_path)
    for record in manifest["records"]:
        record["pixel_counts"] = [32 * 20, 32 * 28]
    figure = t.show_targets(manifest, "parts", split="train", count=2)
    assert len(figure.axes) == 6
    with pytest.raises(ValueError, match="test unseen"):
        t.show_targets(manifest, "parts", split="test")


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_model_summary_counts_encoder_and_head_parameters():
    import torch
    class Adapter(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = torch.nn.Sequential(torch.nn.Conv2d(3, 4, 1), torch.nn.Conv2d(4, 2, 1))
        def encoder_parameters(self):
            return list(self.network[0].parameters())
    summary = t.model_summary(Adapter(), depth=1)
    assert summary["module"].tolist() == ["0", "1"]
    assert summary["parameters"].tolist() == [16, 10]
    assert summary["part"].tolist() == ["encoder", "head/decoder"]


def test_damage_crop_retains_small_preferred_target_and_aligned_pixels():
    import random
    labels = np.zeros((96, 128), dtype=np.uint8)
    labels[4:6, 120:122] = 2  # tiny preferred target close to the image edge
    labels[40:60, 40:60] = 1
    pixels = np.zeros((96, 128, 3), dtype=np.uint8)
    pixels[labels == 2, 0] = 255
    cfg = t.TrainingConfig(image_size=32, crop_mode="damage_aware",
        full_image_probability=0, focused_crop_probability=1, crop_focus_class_ids=(2,))
    for seed in range(20):
        rgb, mask = t.damage_aware_crop(Image.fromarray(pixels), Image.fromarray(labels), cfg, random.Random(seed))
        output = np.asarray(mask)
        assert np.any(output == 2)
        assert set(np.unique(output)) <= {0, 1, 2, 255}
        assert np.asarray(rgb)[output == 2, 0].max() > 0


def test_damage_crop_full_image_and_negative_branches():
    import random
    image, mask = Image.new("RGB", (40, 20)), Image.new("L", (40, 20))
    cfg = t.TrainingConfig(image_size=32, crop_mode="damage_aware",
                          full_image_probability=1, focused_crop_probability=0)
    result = t.damage_aware_crop(image, mask, cfg, random.Random(1))
    expected = t.letterbox(image, mask, 32)
    assert np.array_equal(np.asarray(result[1]), np.asarray(expected[1]))
    cfg.full_image_probability, cfg.focused_crop_probability = 0, 1
    result = t.damage_aware_crop(image, mask, cfg, random.Random(1))
    assert set(np.unique(result[1])) <= {0, 255}  # no invented target on negative images
    assert np.any(np.asarray(result[1]) == 255)  # small source is padded with ignore


def test_damage_aware_dataset_does_not_crop_validation(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    cfg = t.TrainingConfig(image_size=32, crop_mode="damage_aware")
    with patch.object(t, "damage_aware_crop", side_effect=AssertionError("training only")):
        ds = t.SegmentationDataset(manifest["records"], cfg, training=False, num_classes=2)
        assert ds[0]["labels"].shape == (32, 32)


def test_repeat_weights_are_capped_and_refuse_heldout_records():
    rows = [{"split": "train", "pixel_counts": [100, 5, int(i == 0)]} for i in range(10)]
    weights = t.repeat_sampling_weights(rows, 3, threshold=0.9, max_repeat=2)
    assert weights == [2] + [1] * 9
    with pytest.raises(ValueError, match="training records"):
        t.repeat_sampling_weights([{**rows[0], "split": "val"}], 3, .2)
    with pytest.raises(ValueError, match="taxonomy"):
        t.repeat_sampling_weights(rows, 4, .2)


def test_binary_metrics_count_wrong_damage_type_as_detected_damage():
    matrix = np.array([[10, 2, 1], [3, 4, 5], [1, 2, 6]])
    result = t.metrics_from_confusion(matrix, ["background", "dent", "scratch"])
    binary = result["binary_foreground"]
    assert binary["true_positive"] == 17  # includes dent<->scratch confusions
    assert binary["false_positive"] == 3 and binary["false_negative"] == 4
    assert binary["iou"] == pytest.approx(17 / 24)
    assert binary["recall"] == pytest.approx(17 / 21)
    assert t.metrics_from_confusion(np.zeros((2, 2), dtype=int), ["background", "dent"])["binary_foreground"]["iou"] is None


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_plateau_reduces_learning_rate_and_allows_grace(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    cfg = t.TrainingConfig(epochs=12, image_size=32, batch_size=1, device="cpu", pretrained=False,
        lr_schedule="plateau", warmup_epochs=1, patience=5, plateau_patience=1,
        plateau_grace_epochs=1, plateau_factor=.5,
        artifacts_root=str(tmp_path / "artifacts"), run_id="plateau")
    metrics = t.metrics_from_confusion(np.array([[10, 1], [1, 10]]), manifest["class_names"])
    metrics.update(loss=1.0, image_count=1)
    with patch.object(t, "build_model", side_effect=_tiny_model), patch.object(t, "_evaluate", return_value=metrics):
        record = t.train(manifest, cfg)
    h = json.loads(Path(record["run_dir"], "history.json").read_text())
    assert record["completed_epochs"] < cfg.epochs
    drops = [i for i, row in enumerate(h) if row["next_head_lr"] < row["head_lr"]]
    assert len(drops) >= 2
    assert len(h) - 1 - drops[-1] >= cfg.plateau_grace_epochs
    assert h[0]["head_lr"] == pytest.approx(cfg.head_lr)
    assert h[-1]["head_lr"] < cfg.head_lr


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_batchnorm_adapts_only_when_enabled(tmp_path):
    import torch
    manifest = _fixture_manifest(tmp_path)
    def factory(config, classes, **kwargs):
        model = _tiny_model(config, classes)
        model.network = torch.nn.Sequential(model.network, torch.nn.BatchNorm2d(4))
        return model
    for frozen in (False, True):
        cfg = t.TrainingConfig(epochs=1, image_size=32, batch_size=2, device="cpu", pretrained=False,
            freeze_batchnorm=frozen, artifacts_root=str(tmp_path / "artifacts"), run_id=f"bn-{frozen}")
        with patch.object(t, "build_model", side_effect=factory):
            run = t.train(manifest, cfg)
        state = torch.load(Path(run["run_dir"]) / "last.pt", weights_only=True)
        updates = state["model"]["network.1.num_batches_tracked"].item()
        assert updates == (0 if frozen else 1)


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_common_resolution_evaluation_preserves_original_metrics(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    cfg = t.TrainingConfig(epochs=1, image_size=32, batch_size=2, device="cpu", pretrained=False,
        artifacts_root=str(tmp_path / "artifacts"), run_id="common-size")
    with patch.object(t, "build_model", side_effect=_tiny_model):
        run = t.train(manifest, cfg)
        path = Path(run["evaluation_dir"]) / "val_metrics.json"
        original = path.read_bytes()
        result = t.evaluate_run(run["run_dir"], manifest, image_size=64, device="cpu")
        assert result["evaluation_image_size"] == 64
        assert path.read_bytes() == original
        compared = t.compare_runs([run["run_dir"]], evaluation_image_size=64)
        assert compared.iloc[0]["training_image_size"] == 32
        assert compared.iloc[0]["evaluation_image_size"] == 64
        t.plot_confusion(run["run_dir"], image_size=64)
        t.show_predictions(run["run_dir"], manifest, image_size=64, count=1, device="cpu")
        assert Path(run["evaluation_dir"], "val_size64_overlays.png").exists()
        result["checkpoint_sha256"] = "wrong"
        t._json(Path(run["evaluation_dir"]) / "val_size64_metrics.json", result)
        with pytest.raises(ValueError, match="checkpoint"):
            t.compare_runs([run["run_dir"]], evaluation_image_size=64)


def test_rejects_invalid_m2_controls():
    with pytest.raises(ValueError, match="Crop probabilities"):
        t.TrainingConfig(full_image_probability=.6, focused_crop_probability=.7)
    with pytest.raises(ValueError, match="repeat-factor"):
        t.TrainingConfig(repeat_factor_threshold=2)
    with pytest.raises(ValueError, match="grace epochs"):
        t.TrainingConfig(lr_schedule="plateau", patience=3, plateau_patience=2, plateau_grace_epochs=2)
