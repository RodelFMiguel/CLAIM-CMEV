"""Unit tests for SegFormer training and evaluation pipeline components.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
               docs/specs/model_training_specification.md section 7.1
"""
from __future__ import annotations

import torch
import pytest

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
