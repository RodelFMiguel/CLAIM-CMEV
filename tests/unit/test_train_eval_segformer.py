"""Unit tests for SegFormer training and evaluation pipeline components.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
               docs/specs/model_training_specification.md section 7.1
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import torch
import pytest

from pipelines.vision import splits, train_segformer
from pipelines.vision.train_segformer import compute_confusion_matrix, PANEL_CLASSES
from pipelines.vision.convert_hitl import PART_CODES, ID_TO_PART_CODE


def test_confusion_matrix_computation():
    """Verify confusion matrix correctly tallies TP, FP, FN."""
    num_classes = 5
    # Ground truth: [0, 1, 2, 3, 4]
    # Predictions:  [0, 1, 1, 3, 0]
    # Class 0: gt=0, pred=0 -> TP
    # Class 1: gt=1, pred=1 -> TP; gt=2, pred=1 -> FP for 1, FN for 2
    # Class 2: gt=2, pred=1 -> FN for 2
    # Class 3: gt=3, pred=3 -> TP
    # Class 4: gt=4, pred=0 -> FP for 0, FN for 4
    targets = torch.tensor([0, 1, 2, 3, 4], dtype=torch.long)
    preds = torch.tensor([0, 1, 1, 3, 0], dtype=torch.long)

    cm = compute_confusion_matrix(preds, targets, num_classes=num_classes)

    assert cm.shape == (5, 5)
    assert cm[0, 0] == 1  # gt 0 -> pred 0
    assert cm[1, 1] == 1  # gt 1 -> pred 1
    assert cm[2, 1] == 1  # gt 2 -> pred 1
    assert cm[3, 3] == 1  # gt 3 -> pred 3
    assert cm[4, 0] == 1  # gt 4 -> pred 0

    intersection = cm.diag().float()
    gt_sum = cm.sum(dim=1).float()
    pred_sum = cm.sum(dim=0).float()
    union = gt_sum + pred_sum - intersection

    # IoU for class 1: TP=1, union = gt(1) + pred(2) - TP(1) = 2. IoU = 0.5
    iou_1 = intersection[1] / union[1]
    assert pytest.approx(iou_1.item()) == 0.5


def test_panel_classes_validity():
    """Verify all defined panel classes are valid part codes."""
    for panel in PANEL_CLASSES:
        assert panel in PART_CODES, f"Panel class '{panel}' not in canonical PART_CODES"


# ---------------------------------------------------------------------------
# Device selection: configs/models/parts.yaml is shared by the trainer and the
# serving adapter, so the trainer must accept the same "device" vocabulary.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARTS_CONFIG = REPO_ROOT / "configs" / "models" / "parts.yaml"


def test_trainer_gets_past_device_setup_with_the_default_parts_config(tmp_path: Path):
    """The default config says ``device: "auto"``; training must not stop there.

    An empty split directory stops the run at its next step, before any model is built.
    """
    with pytest.raises(FileNotFoundError, match="Split file not found"):
        train_segformer.train(config_path=DEFAULT_PARTS_CONFIG, split_dir=tmp_path, output_dir=tmp_path / "out")


@pytest.mark.parametrize(
    ("cuda", "mps", "expected"),
    [(True, True, "cuda"), (True, False, "cuda"), (False, True, "mps"), (False, False, "cpu")],
)
def test_auto_device_prefers_cuda_then_apple_gpu_then_cpu(cuda: bool, mps: bool, expected: str):
    with patch("torch.cuda.is_available", return_value=cuda), \
            patch("torch.backends.mps.is_available", return_value=mps):
        assert train_segformer.resolve_device("auto").type == expected


@pytest.mark.parametrize("name", ["cpu", "cuda", "mps"])
def test_explicit_device_is_used_as_given(name: str):
    assert train_segformer.resolve_device(name).type == name


# ---------------------------------------------------------------------------
# What the trainer records about a run comes from the run, not from literals
# ---------------------------------------------------------------------------

PUBLISHED_TRAIN_SPLIT = REPO_ROOT / "data" / "splits" / "parts" / "0.1.1" / "train.jsonl"
# The counts train_segformer.py used to carry as a literal: background, then the 21 parts in taxonomy order.
FORMERLY_HARDCODED_PIXEL_COUNTS = [
    130262704, 8251968, 3206149, 4020753, 3803488, 13796839, 9407824,
    7837884, 5422967, 17626360, 9164679, 4012362, 2249440, 13617414,
    5147621, 982040, 1363353, 2133505, 2841408, 1835137, 7772521, 9917856,
]


def _split_file(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "train.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_training_pixel_counts_are_summed_from_the_split_records(tmp_path: Path):
    first, second = PART_CODES[0], PART_CODES[1]
    split = _split_file(tmp_path, [
        {"width": 10, "height": 10, "class_pixel_counts": {first: 30, second: 20}},
        {"width": 20, "height": 5, "class_pixel_counts": {first: 10}},
    ])

    counts = train_segformer.training_pixel_counts(split)

    assert counts.shape == (22,)
    assert counts[:3].tolist() == [200 - 60, 40, 20]  # background is every pixel that is not a part
    assert counts[3:].sum() == 0


def test_published_split_gives_the_pixel_counts_the_trainer_used_to_hardcode():
    assert train_segformer.training_pixel_counts(PUBLISHED_TRAIN_SPLIT).tolist() == FORMERLY_HARDCODED_PIXEL_COUNTS


def test_inverse_sqrt_weights_follow_the_split(tmp_path: Path):
    counts = {code: 100 * (i + 1) ** 2 for i, code in enumerate(PART_CODES)}
    split = _split_file(tmp_path, [{"width": 2000, "height": 2000, "class_pixel_counts": counts}])

    weights = train_segformer.compute_class_weights(split, weighting_type="inverse_sqrt_freq", bg_weight=0.25)

    assert weights[0].item() == pytest.approx(0.25)
    assert weights[1:].mean().item() == pytest.approx(1.0)
    assert (weights[1] / weights[2]).item() == pytest.approx(2.0)  # a part with a quarter of the pixels weighs double


def test_recorded_library_versions_are_the_installed_ones():
    import transformers

    assert train_segformer.dependency_versions() == {"torch": torch.__version__,
                                                     "transformers": transformers.__version__}


def test_notice_names_the_checkpoint_that_was_actually_trained():
    notice = train_segformer.notice_text({"architecture": "nvidia/mit-b2", "model_version": "parts/0.5.0-b2"})
    assert "nvidia/mit-b2" in notice and "parts/0.5.0-b2" in notice
    assert "B0" not in notice and "mit-b0" not in notice


def test_split_hashes_and_version_are_read_from_the_split_manifest(tmp_path: Path):
    (tmp_path / "split_manifest.json").write_text(json.dumps(
        {"split_version": "9.9.9", "file_hashes": {"train.jsonl": "abc"}}), encoding="utf-8")

    assert splits.split_file_hashes(tmp_path) == {"train.jsonl": "abc"}
    assert splits.split_version(tmp_path) == "9.9.9"
