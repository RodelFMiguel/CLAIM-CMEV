"""Unit tests for HitlPartsDataset."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
import torch

from pipelines.vision.dataset import HitlPartsDataset


def test_hitl_parts_dataset_item(tmp_path: Path):
    # Create mock image
    img_dir = tmp_path / "img"
    mask_dir = tmp_path / "mask"
    img_dir.mkdir()
    mask_dir.mkdir()

    img_path = img_dir / "car_01.png"
    mask_path = mask_dir / "car_01.png"

    # 100x80 RGB image
    pil_img = Image.fromarray(np.full((80, 100, 3), 128, dtype=np.uint8))
    pil_img.save(img_path)

    # 100x80 mask with class 5
    pil_mask = Image.fromarray(np.full((80, 100), 5, dtype=np.uint8))
    pil_mask.save(mask_path)

    split_file = tmp_path / "train.jsonl"
    record = {
        "example_id": "car_01.png",
        "source_path": str(img_path),
        "mask_path": str(mask_path),
        "width": 100,
        "height": 80,
    }
    split_file.write_text(json.dumps(record) + "\n")

    dataset = HitlPartsDataset(split_file, image_size=512, is_train=False, root_dir=tmp_path)
    assert len(dataset) == 1

    sample = dataset[0]
    assert sample["example_id"] == "car_01.png"
    assert sample["pixel_values"].shape == (3, 512, 512)
    assert sample["labels"].shape == (512, 512)
    assert sample["labels"].dtype == torch.long
    assert (sample["labels"] == 5).sum() > 0
    assert (sample["labels"] == 0).sum() > 0  # padding area is background 0
