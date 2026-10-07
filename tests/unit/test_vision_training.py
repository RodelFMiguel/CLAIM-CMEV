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


def test_class_weights_follow_training_frequency_and_refuse_heldout():
    records = [{"split": "train", "pixel_counts": [9000, 90, 0, 1]},
               {"split": "train", "pixel_counts": [1000, 10, 0, 0]}]
    weights = t.class_loss_weights(records, 4, "sqrt_inverse", cap=50)
    assert weights[0] == 1.0
    assert weights[1] == pytest.approx(10.0)  # sqrt(10000 / 100)
    assert weights[2] == 1.0  # absent from training: left unweighted
    assert weights[3] == 50.0  # sqrt(10000 / 1) = 100, capped
    assert t.class_loss_weights(records, 4, "none") is None
    with pytest.raises(ValueError, match="training records only"):
        t.class_loss_weights([{"split": "val", "pixel_counts": [1, 1, 1, 1]}], 4)


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_weighted_loss_matches_manual_cross_entropy_and_ignores_void():
    import torch
    import torch.nn.functional as F
    logits = torch.randn(1, 3, 2, 2, requires_grad=True)
    labels = torch.tensor([[[0, 1], [2, 255]]])
    weights = torch.tensor([1.0, 4.0, 2.0])
    loss = t.segmentation_loss(logits, labels, ce_weight=1.0, dice_weight=0.0, class_weights=weights)
    expected = F.cross_entropy(logits, labels, weight=weights, ignore_index=255)
    assert loss.item() == pytest.approx(expected.item())
    loss.backward()
    assert torch.all(logits.grad[0, :, 1, 1] == 0)


def test_new_regularisation_controls_are_validated():
    with pytest.raises(ValueError, match="class_weighting"):
        t.TrainingConfig(class_weighting="inverse")
    with pytest.raises(ValueError, match="drop_path_rate"):
        t.TrainingConfig(drop_path_rate=1.2)
    with pytest.raises(ValueError, match="only applied to SegFormer"):
        t.TrainingConfig(architecture="resnet50", classifier_dropout=0.2)
    with pytest.raises(ValueError, match="ema_decay"):
        t.TrainingConfig(ema_decay=1.0)


@pytest.mark.skipif(importlib.util.find_spec("transformers") is None, reason="transformers not installed")
def test_segformer_dropout_and_drop_path_overrides_are_applied():
    from transformers import SegformerConfig
    small = SegformerConfig(depths=[1, 1, 1, 1], hidden_sizes=[8, 16, 32, 64], decoder_hidden_size=16,
                            num_attention_heads=[1, 1, 1, 1]).to_dict()
    config = t.TrainingConfig(task="damage", drop_path_rate=0.3, classifier_dropout=0.25)
    model = t.build_model(config, ["background", "dent"], saved_config=small, load_pretrained=False)
    assert model.network.config.drop_path_rate == 0.3
    assert model.network.decode_head.dropout.p == 0.25


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_ema_weighted_training_saves_averaged_weights_and_records_binary_history(tmp_path):
    import torch
    manifest = _fixture_manifest(tmp_path)
    config = t.TrainingConfig(epochs=2, image_size=32, batch_size=1, ema_decay=0.5, class_weighting="sqrt_inverse",
                              artifacts_root=str(tmp_path / "artifacts"), run_id="ema", device="cpu", pretrained=False)
    with patch.object(t, "build_model", side_effect=_tiny_model):
        record = t.train(manifest, config)
        evaluated = t.evaluate_run(record["run_dir"], manifest, split="val", device="cpu")
    assert record["ema"]["decay"] == 0.5
    assert record["class_weights"]["background"] == 1.0 and record["class_weights"]["part"] >= 1.0
    state = torch.load(Path(record["run_dir"], "best.pt"), weights_only=True)
    assert set(state["model"]) == set(state["raw_model"])
    assert any(not torch.equal(state["model"][k], state["raw_model"][k]) for k in state["model"])
    history = json.loads(Path(record["run_dir"], "history.json").read_text())
    assert all({"val_binary_iou", "val_binary_recall", "val_binary_precision"} <= set(h) for h in history)
    assert evaluated["background_offset"] == 0.0


def test_background_offset_prediction_trades_background_for_damage():
    import torch
    logits = torch.tensor([[[[2.0]], [[1.5]], [[0.5]]]])  # background 2.0, best damage class 1 at 1.5
    assert t._predict(logits).item() == 0
    assert t._predict(logits, 0.4).item() == 0
    assert t._predict(logits, 0.6).item() == 1
    assert t._predict(logits, -1.0).item() == 0


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_tune_background_offset_uses_validation_and_writes_separate_reports(tmp_path):
    manifest = _fixture_manifest(tmp_path)
    config = t.TrainingConfig(epochs=1, image_size=32, batch_size=1,
                              artifacts_root=str(tmp_path / "artifacts"), run_id="offset", device="cpu", pretrained=False)
    with patch.object(t, "build_model", side_effect=_tiny_model):
        record = t.train(manifest, config)
        selected, table = t.tune_background_offset(record["run_dir"], manifest, offsets=[0, 0.5, 1.0], device="cpu")
        assert selected in {0.0, 0.5, 1.0}
        assert table["background_offset"].tolist() == [0.0, 0.5, 1.0]
        assert {"miou_foreground", "binary_iou", "binary_recall", "iou_part"} <= set(table.columns)
        saved = json.loads(Path(record["evaluation_dir"], "val_background_offset.json").read_text())
        assert saved["selected_offset"] == selected and saved["split"] == "val"
        tuned = t.evaluate_run(record["run_dir"], manifest, split="val", device="cpu", background_offset=0.5)
        assert tuned["background_offset"] == 0.5
        assert Path(record["evaluation_dir"], "val_bg0.5_metrics.json").is_file()
        assert Path(record["evaluation_dir"], "val_metrics.json").is_file()  # untuned report preserved
        with pytest.raises(ValueError, match="include 0"):
            t.tune_background_offset(record["run_dir"], manifest, offsets=[0.5, 1.0], device="cpu")


def test_ema_decay_ramps_up_before_reaching_its_target():
    assert t.ema_decay_at(0, 0.995) == pytest.approx(0.1)
    assert t.ema_decay_at(10, 0.995) == pytest.approx(11 / 20)
    assert t.ema_decay_at(5000, 0.995) == 0.995


# transformers 5 tensor name -> the transformers 4 name found in the saved notebook runs.
_TRANSFORMERS_4_NAMES = (
    (r"decode_head\.linear_projections\.", "decode_head.linear_c."),
    (r"segformer\.stages\.(\d+)\.patch_embeddings\.", r"segformer.encoder.patch_embeddings.\1."),
    (r"segformer\.stages\.(\d+)\.blocks\.", r"segformer.encoder.block.\1."),
    (r"segformer\.stages\.(\d+)\.layer_norm\.", r"segformer.encoder.layer_norm.\1."),
    (r"attention\.sequence_reduction\.sequence_reduction\.", "attention.self.sr."),
    (r"attention\.sequence_reduction\.layer_norm\.", "attention.self.layer_norm."),
    (r"attention\.q_proj\.", "attention.self.query."),
    (r"attention\.k_proj\.", "attention.self.key."),
    (r"attention\.v_proj\.", "attention.self.value."),
    (r"attention\.o_proj\.", "attention.output.dense."),
    (r"mlp\.fc(\d)\.", r"mlp.dense\1."),
    (r"layernorm_before\.", "layer_norm_1."),
    (r"layernorm_after\.", "layer_norm_2."),
)


def _transformers_4_name(key):
    import re
    for pattern, replacement in _TRANSFORMERS_4_NAMES:
        key = re.sub(pattern, replacement, key)
    return key


_BUILD_MODEL = t.build_model


def _tiny_segformer(config, classes, **kwargs):
    from transformers import SegformerConfig
    if kwargs.get("saved_config") is None:
        kwargs["saved_config"] = SegformerConfig(depths=[1, 1, 1, 1], hidden_sizes=[8, 16, 32, 64],
                                                 num_attention_heads=[1, 2, 4, 8], decoder_hidden_size=16).to_dict()
    return _BUILD_MODEL(config, classes, **{**kwargs, "load_pretrained": False})


@pytest.mark.skipif(importlib.util.find_spec("transformers") is None, reason="transformers not installed")
def test_load_run_reads_a_checkpoint_saved_with_transformers_4_tensor_names(tmp_path, monkeypatch):
    import torch
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    manifest = _fixture_manifest(tmp_path)
    config = t.TrainingConfig(epochs=1, image_size=32, batch_size=1, artifacts_root=str(tmp_path / "artifacts"),
                              run_id="legacy", device="cpu", pretrained=False)
    with patch.object(t, "build_model", side_effect=_tiny_segformer):
        run_dir = Path(t.train(manifest, config)["run_dir"])
    pixels = torch.rand(1, 3, 32, 32, generator=torch.Generator().manual_seed(0))
    current = t.load_run(run_dir, device="cpu")[0]
    with torch.no_grad():
        expected = current(pixels)

    state = torch.load(run_dir / "best.pt", map_location="cpu", weights_only=True)
    state["model"] = {_transformers_4_name(key): value for key, value in state["model"].items()}
    assert "network.decode_head.linear_c.0.proj.weight" in state["model"]
    assert not set(state["model"]) & {key for key in current.state_dict() if "stages" in key}
    torch.save(state, run_dir / "best.pt")
    record = json.loads((run_dir / "manifest.json").read_text())
    record["checkpoint_sha256"] = t._file_hash(run_dir / "best.pt")
    (run_dir / "manifest.json").write_text(json.dumps(record))

    reloaded = t.load_run(run_dir, device="cpu")[0]
    with torch.no_grad():
        assert torch.equal(reloaded(pixels), expected)


@pytest.mark.skipif(importlib.util.find_spec("transformers") is None, reason="transformers not installed")
def test_load_run_still_refuses_a_checkpoint_with_missing_tensors(tmp_path, monkeypatch):
    import torch
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    manifest = _fixture_manifest(tmp_path)
    config = t.TrainingConfig(epochs=1, image_size=32, batch_size=1, artifacts_root=str(tmp_path / "artifacts"),
                              run_id="incomplete", device="cpu", pretrained=False)
    with patch.object(t, "build_model", side_effect=_tiny_segformer):
        run_dir = Path(t.train(manifest, config)["run_dir"])
    state = torch.load(run_dir / "best.pt", map_location="cpu", weights_only=True)
    state["model"] = {_transformers_4_name(key): value for key, value in state["model"].items()
                      if "classifier" not in key}
    torch.save(state, run_dir / "best.pt")
    record = json.loads((run_dir / "manifest.json").read_text())
    record["checkpoint_sha256"] = t._file_hash(run_dir / "best.pt")
    (run_dir / "manifest.json").write_text(json.dumps(record))

    with pytest.raises(RuntimeError, match="classifier"):
        t.load_run(run_dir, device="cpu")


# ---------------------------------------------------------------------------
# The serving frame: the frame the M1 worker builds (photo top-left, black padding)
# ---------------------------------------------------------------------------

def _wide_photo():
    """48x32 photo with a gradient and a mask whose right half is class 1."""
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 256, size=(32, 48, 3), dtype=np.uint8)
    labels = np.zeros((32, 48), dtype=np.uint8)
    labels[:, 24:] = 1
    return Image.fromarray(pixels), Image.fromarray(labels)


def test_serving_frame_places_the_photo_top_left_on_black_and_ignores_padding():
    image, mask = _wide_photo()
    frame, target = t.letterbox(image, mask, 32, frame="serving")
    pixels, labels = np.asarray(frame), np.asarray(target)
    assert pixels.shape == (32, 32, 3) and labels.shape == (32, 32)
    assert np.all(pixels[21:] == 0)  # 48x32 -> 32x21 at the top; black below
    assert np.all(labels[21:] == 255)
    assert set(np.unique(labels[:21])) == {0, 1}
    assert np.all(labels[:21, :16] == 0) and np.all(labels[:21, 16:] == 1)


def test_centred_frame_is_unchanged_by_default():
    image, mask = _wide_photo()
    frame, target = t.letterbox(image, mask, 32)
    labels = np.asarray(target)
    assert np.all(labels[:5] == 255) and np.all(labels[26:] == 255)  # padding above and below
    assert tuple(np.asarray(frame)[0, 0]) == (124, 116, 104)  # the normalisation mean
    assert t.TrainingConfig().frame == "centred"


def test_training_config_refuses_an_unknown_frame():
    with pytest.raises(ValueError, match="frame"):
        t.TrainingConfig(frame="middle")


@pytest.mark.skipif(not TORCH, reason="Optional training dependencies not installed")
def test_serving_frame_dataset_pads_pixels_with_normalised_black(tmp_path):
    image, mask = _wide_photo()
    image.save(tmp_path / "image.png")
    mask.save(tmp_path / "mask.png")
    record = {"sample_id": "a", "image_path": str(tmp_path / "image.png"), "mask_path": str(tmp_path / "mask.png")}
    config = t.TrainingConfig(image_size=32, frame="serving")
    item = t.SegmentationDataset([record], config, training=False, num_classes=2)[0]
    black = (0 - np.asarray(config.mean)) / np.asarray(config.std)
    assert np.allclose(item["pixel_values"][:, 21:, :].numpy(), black[:, None, None], atol=1e-6)
    assert bool((item["labels"][21:] == 255).all())


@pytest.mark.skipif(importlib.util.find_spec("transformers") is None, reason="transformers not installed")
def test_serving_frame_tensor_is_the_tensor_the_parts_worker_feeds_its_model(tmp_path):
    """The contract: a validation tensor of a serving-frame run equals the worker's tensor for that photo."""
    import hashlib
    import io
    import torch
    adapter = pytest.importorskip("claim_cmev.vision.parts.adapter")
    from claim_cmev.vision.palette import ID_TO_PART_CODE
    from claim_cmev.vision.parts.config import PartsConfig
    from transformers import SegformerConfig, SegformerForSemanticSegmentation

    rng = np.random.default_rng(3)
    photo = Image.fromarray(rng.integers(0, 256, size=(300, 400, 3), dtype=np.uint8))
    photo.save(tmp_path / "image.png")
    Image.new("L", photo.size).save(tmp_path / "mask.png")
    record = {"sample_id": "a", "image_path": str(tmp_path / "image.png"), "mask_path": str(tmp_path / "mask.png")}
    trained = t.SegmentationDataset([record], t.TrainingConfig(image_size=64, frame="serving"),
                                    training=False, num_classes=22)[0]["pixel_values"]

    model_dir = tmp_path / "model"
    SegformerForSemanticSegmentation(SegformerConfig(
        num_labels=22, id2label={str(i): ID_TO_PART_CODE[i] for i in range(22)},
        label2id={ID_TO_PART_CODE[i]: i for i in range(22)}, depths=[1, 1, 1, 1], hidden_sizes=[16, 32, 64, 128],
        num_attention_heads=[1, 2, 4, 8], decoder_hidden_size=32)).save_pretrained(model_dir)
    segmenter = adapter.PartsSegmenter(model_dir=model_dir, config=PartsConfig(input_size=64, device="cpu",
                                                                                 write_overlay=False))
    served = []
    segmenter.model.register_forward_pre_hook(lambda module, args, kwargs: served.append(kwargs["pixel_values"]),
                                              with_kwargs=True)
    data = (tmp_path / "image.png").read_bytes()
    adapter.run_parts_segmentation(adapter.PartsSegmentRequest(
        claim_id="01JAX7Q0VN4Z3K9F2M8R6T1C5D", input_revision=1, job_key="job", versions={"code": "test"},
        provenance={"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-worker-parts"},
        photos=(adapter.PhotoFileInput(file_id="ph_01", media_type="image/png",
                                       sha256=hashlib.sha256(data).hexdigest(), data=data),)), segmenter)
    assert len(served) == 1
    assert torch.equal(served[0][0].cpu(), trained)


def test_serving_frame_augmentations_fill_with_black(tmp_path):
    import random as stdlib_random
    image, mask = Image.new("RGB", (40, 20), "white"), Image.new("L", (40, 20))
    config = t.TrainingConfig(image_size=32, frame="serving")
    small, small_mask = t.random_scale_crop(image, mask, 32, 0.5, config.padding_colour, rng=stdlib_random.Random(1))
    padding = np.asarray(small_mask) == 255
    assert padding.any() and np.all(np.asarray(small)[padding] == 0)
    assert t.TrainingConfig(image_size=32).padding_colour == (0.485, 0.456, 0.406)

    crop_config = t.TrainingConfig(image_size=32, frame="serving", crop_mode="damage_aware",
                                   full_image_probability=0, focused_crop_probability=0)
    cropped, cropped_mask = t.damage_aware_crop(image, mask, crop_config, stdlib_random.Random(1))
    padding = np.asarray(cropped_mask) == 255
    assert padding.any() and np.all(np.asarray(cropped)[padding] == 0)

    full_config = t.TrainingConfig(image_size=32, frame="serving", crop_mode="damage_aware",
                                   full_image_probability=1, focused_crop_probability=0)
    _, full_mask = t.damage_aware_crop(image, mask, full_config, stdlib_random.Random(1))
    assert np.all(np.asarray(full_mask)[:16] == 0) and np.all(np.asarray(full_mask)[16:] == 255)

    # A photo that fills the frame: after a rotation the only padding is the rotation's own fill.
    Image.new("RGB", (32, 32), "white").save(tmp_path / "image.png")
    Image.new("L", (32, 32)).save(tmp_path / "mask.png")
    record = {"sample_id": "a", "image_path": str(tmp_path / "image.png"), "mask_path": str(tmp_path / "mask.png")}
    rotating = t.TrainingConfig(image_size=32, frame="serving", rotation_degrees=45, horizontal_flip=0,
                                brightness=0, contrast=0)
    if TORCH:
        stdlib_random.seed(1)  # the first draw is the angle: about -33 degrees
        item = t.SegmentationDataset([record], rotating, training=True, num_classes=2)[0]
        black = (0 - np.asarray(rotating.mean)) / np.asarray(rotating.std)
        assert item["labels"][0, 0] == 255
        assert np.allclose(item["pixel_values"][:, 0, 0].numpy(), black, atol=1e-6)
