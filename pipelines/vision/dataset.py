"""PyTorch Dataset for HITL vehicle part segmentation.

Loads photos and semantic mask PNGs, applies EXIF orientation and letterbox
transformation (512x512 longest_edge_pad), and executes data augmentations
for training.

Specification: docs/specs/model_training_specification.md section 7.1.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance
import torch
from torch.utils.data import Dataset

from claim_cmev.vision.transforms import decode_oriented, letterbox, plan_model_frame
from pipelines.vision.splits import HITL_FOLDERS, LABELS_ENV, find_hitl_folder, labels_dir, mask_pixel_sha256, read_split

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class HitlPartsDataset(Dataset):
    """Dataset for training and evaluating SegFormer on HITL part masks."""

    def __init__(
        self,
        split_path: Path | str,
        image_size: int = 512,
        is_train: bool = False,
        root_dir: Path | str | None = None,
        augment_mode: str = "standard",
        images_root: Path | str | None = None,
        labels_root: Path | str | None = None,
        verify: bool = False,
    ) -> None:
        """``images_root`` is the HITL parts export and ``labels_root`` the converter's output folder.

        Both default to the environment or the repository locations (``pipelines.vision.splits``).
        ``root_dir`` only serves the repository-relative paths of split 0.1.0. ``verify`` checks
        every mask against the pixel hash in its split record.
        """
        super().__init__()
        self.split_path = Path(split_path)
        self.image_size = image_size
        self.is_train = is_train
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        self.augment_mode = augment_mode

        if not self.split_path.exists():
            raise FileNotFoundError(f"Split file not found: {self.split_path}")

        self.records = read_split(self.split_path)
        self.paths = self._resolve_paths(images_root, labels_root)
        if verify:
            self._verify_masks()

    def _resolve_paths(self, images_root: Path | str | None, labels_root: Path | str | None) -> list[tuple[Path, Path]]:
        images = labels = None
        if any("image_relative_path" in rec for rec in self.records):
            images, labels = find_hitl_folder("parts", images_root), labels_dir(labels_root)
        paths = []
        for rec in self.records:
            if "image_relative_path" in rec:
                paths.append((images / rec["image_relative_path"], labels / rec["mask_relative_path"]))
            else:  # split 0.1.0: repository-relative paths, used as written
                paths.append(tuple(path if path.is_absolute() else self.root_dir / path
                                   for path in (Path(rec["source_path"]), Path(rec["mask_path"]))))
        no_image = sum(1 for image, _ in paths if not image.is_file())
        no_mask = sum(1 for _, mask in paths if not mask.is_file())
        if no_image or no_mask:
            raise FileNotFoundError(
                f"{self.split_path.name}: {no_image} of {len(paths)} images are missing"
                f" (the HITL parts export: {images or self.root_dir}; set {HITL_FOLDERS['parts'][1]})"
                f" and {no_mask} masks (the converted labels: {labels or self.root_dir};"
                f" run `python pipelines/vision/convert_hitl.py` or set {LABELS_ENV})")
        return paths

    def _verify_masks(self) -> None:
        for rec, (_, mask_path) in zip(self.records, self.paths):
            expected = rec.get("mask_pixel_sha256")
            if expected and mask_pixel_sha256(np.array(Image.open(mask_path))) != expected:
                raise ValueError(f"the mask of {rec['example_id']} at {mask_path} does not have the pixels this split "
                                 "was built from; rerun pipelines/vision/convert_hitl.py")

    def __len__(self) -> int:
        return len(self.records)

    def _apply_augmentations(
        self,
        image: np.ndarray,
        mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Apply safe augmentations: random horizontal flip, seam contrast, perspective, and color jitter."""
        # 1. Random horizontal flip (safe for HITL because labels have no left/right side)
        if random.random() > 0.5:
            image = np.ascontiguousarray(image[:, ::-1])
            mask = np.ascontiguousarray(mask[:, ::-1])

        # 2. Advanced augmentations: subtle perspective shifts & CLAHE seam contrast
        if self.augment_mode == "advanced":
            # 2a. Subtle bounded perspective warp (< 4% edge perturbation)
            if random.random() > 0.5:
                h, w = image.shape[:2]
                max_shift = 0.04 * min(h, w)
                src_pts = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
                dst_pts = np.float32([
                    [random.uniform(-max_shift, max_shift), random.uniform(-max_shift, max_shift)],
                    [w - 1 + random.uniform(-max_shift, max_shift), random.uniform(-max_shift, max_shift)],
                    [w - 1 + random.uniform(-max_shift, max_shift), h - 1 + random.uniform(-max_shift, max_shift)],
                    [random.uniform(-max_shift, max_shift), h - 1 + random.uniform(-max_shift, max_shift)],
                ])
                M = cv2.getPerspectiveTransform(src_pts, dst_pts)
                image = cv2.warpPerspective(image, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                mask = cv2.warpPerspective(mask, M, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

            # 2b. CLAHE local contrast enhancement for door seams & panel boundaries
            if random.random() > 0.4:
                lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
                l_channel, a_channel, b_channel = cv2.split(lab)
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                cl = clahe.apply(l_channel)
                limg = cv2.merge((cl, a_channel, b_channel))
                image = cv2.cvtColor(limg, cv2.COLOR_LAB2RGB)

        # 3. Photometric jitter on brightness, contrast, and saturation
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
        img_path, mask_path = self.paths[idx]

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
        shrinking = transform.resized_width < transform.oriented_width
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
