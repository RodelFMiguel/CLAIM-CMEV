"""PyTorch Dataset for HITL vehicle damage segmentation (M2).

Loads photos and semantic damage mask PNGs, applies EXIF orientation and letterbox
transformation (512x512 longest_edge_pad), and executes data augmentations for training.

Specification: docs/specs/module-02-damage-segmentation.md
               docs/specs/model_training_specification.md section 7.2
"""
from __future__ import annotations

import json
from pathlib import Path
import random
import sys
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance
import torch
from torch.utils.data import Dataset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claim_cmev.vision.transforms import decode_oriented, letterbox, plan_model_frame
from pipelines.vision.convert_damage import DAMAGE_CLASSES, DAMAGE_CODE_TO_ID, ID_TO_DAMAGE_CODE

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class HitlDamageDataset(Dataset):
    """Dataset for training and evaluating segmentation models on HITL damage masks."""

    def __init__(
        self,
        split_path: Path | str,
        image_size: int = 512,
        is_train: bool = False,
        root_dir: Path | str | None = None,
        augment_mode: str = "standard",
    ) -> None:
        super().__init__()
        self.split_path = Path(split_path)
        self.image_size = image_size
        self.is_train = is_train
        self.root_dir = Path(root_dir) if root_dir else PROJECT_ROOT
        self.augment_mode = augment_mode

        if not self.split_path.exists():
            raise FileNotFoundError(f"Split file not found: {self.split_path}")

        self.records = [
            json.loads(line)
            for line in self.split_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def __len__(self) -> int:
        return len(self.records)

    def _apply_augmentations(
        self,
        image: np.ndarray,
        mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Apply safe augmentations: random horizontal flip and photometric jitter."""
        # 1. Random horizontal flip (damage has no left/right orientation)
        if random.random() > 0.5:
            image = np.ascontiguousarray(image[:, ::-1])
            mask = np.ascontiguousarray(mask[:, ::-1])

        # 2. Photometric jitter on brightness, contrast, and saturation
        pil_img = Image.fromarray(image)
        if random.random() > 0.3:
            factor = random.uniform(0.8, 1.2)
            pil_img = ImageEnhance.Brightness(pil_img).enhance(factor)
        if random.random() > 0.3:
            factor = random.uniform(0.8, 1.2)
            pil_img = ImageEnhance.Contrast(pil_img).enhance(factor)
        if random.random() > 0.3:
            factor = random.uniform(0.8, 1.2)
            pil_img = ImageEnhance.Color(pil_img).enhance(factor)

        return np.array(pil_img), mask

    def __getitem__(self, idx: int) -> dict[str, Any]:
        rec = self.records[idx]

        # Resolve paths
        img_path = Path(rec["source_path"])
        if not img_path.is_absolute():
            img_path = self.root_dir / img_path

        mask_path = Path(rec["mask_path"])
        if not mask_path.is_absolute():
            mask_path = self.root_dir / mask_path

        # 1. Read and decode image with EXIF orientation
        img_bytes = img_path.read_bytes()
        oriented_img, orientation, (sw, sh) = decode_oriented(img_bytes)

        # 2. Plan model frame transform (longest_edge_pad)
        transform = plan_model_frame(
            stored_width=sw,
            stored_height=sh,
            exif_orientation=orientation,
            frame_size=self.image_size,
            policy="longest_edge_pad",
        )

        # 3. Resize and pad image
        letterboxed_img = letterbox(oriented_img, transform, pad_value=0)

        # 4. Load and letterbox mask
        mask_raw = np.array(Image.open(mask_path))
        resized_mask = cv2.resize(
            mask_raw,
            (transform.resized_width, transform.resized_height),
            interpolation=cv2.INTER_NEAREST,
        )

        letterboxed_mask = np.zeros((self.image_size, self.image_size), dtype=np.uint8)
        x0, y0, x1, y1 = transform.content_box()
        letterboxed_mask[y0:y1, x0:x1] = resized_mask

        # 5. Apply augmentations if training
        if self.is_train:
            letterboxed_img, letterboxed_mask = self._apply_augmentations(letterboxed_img, letterboxed_mask)

        # 6. Normalize image to PyTorch CHW tensor
        img_float = letterboxed_img.astype(np.float32) / 255.0
        normalized_img = (img_float - IMAGENET_MEAN) / IMAGENET_STD
        img_tensor = torch.from_numpy(normalized_img).permute(2, 0, 1).float()

        # 7. Mask to long tensor
        label_tensor = torch.from_numpy(letterboxed_mask).long()

        return {
            "pixel_values": img_tensor,
            "labels": label_tensor,
            "example_id": rec["example_id"],
        }
