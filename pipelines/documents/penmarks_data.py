"""Pen-mark detection data for M6: labelled pages on M4's corrected renders.

The detector is trained on what it will be served: the corrected page image M4 produced
(``pipelines.documents.m4_cache``), not the dataset's own image file. Every labelled box
(marks, distractors, rows and printed-amount boxes) is therefore mapped from the dataset
image frame into the corrected-page frame with the page's M4 transform.

Two classes are detector targets, ``exclusion`` and ``price_change``. Acceptance ticks and
review notes are kept as *distractors*: they are not labels, they are used to check that
the detector learned to ignore them. The dataset is synthetic (font-rendered ink on one
page layout), so nothing measured with it is real-page performance.

The test partition is the final held-out split: ``load_penmark_pages`` leaves it out unless
``include_test=True`` is passed.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageFilter
from torchvision.ops import box_iou

from pipelines.documents.m4_cache import source_to_corrected_boxes

TARGET_CLASSES = ("exclusion", "price_change")
DISTRACTOR_CLASSES = ("acceptance_tick", "review_note")
CLASS_TO_LABEL = {"exclusion": 1, "price_change": 2}  # 0 is the detector's background class
LABEL_TO_CLASS = {label: name for name, label in CLASS_TO_LABEL.items()}
CONVERSION_VERSION = "penmarks-m4-corrected-1.0.0"
"""Label source: the dataset's COCO exports, checked against ``annotations.jsonl``; boxes
cover the whole logical mark (stroke plus its remark) and are mapped to the corrected frame."""
HELD_OUT_SPLIT = "test"

# Okabe-Ito colours: distinguishable with the common forms of colour blindness.
PALETTE = {"exclusion": "#D55E00", "price_change": "#0072B2", "acceptance_tick": "#009E73", "review_note": "#CC79A7",
           "row": "#999999", "amount": "#E69F00", "true_positive": "#009E73", "false_positive": "#D55E00",
           "missed": "#CC79A7", "detection": "#56B4E9", "neutral": "#000000", "highlight": "#F0E442"}

Box = tuple[float, float, float, float]


class PenMarkDataError(ValueError):
    """The dataset and the M4 cache do not describe the same labelled pages."""


@dataclass(frozen=True)
class MarkBox:
    """One labelled pen mark in corrected-page pixels (x0, y0, x1, y1)."""

    mark_id: str
    mark_class: str  # a target class or a distractor class
    style: str
    box: Box
    components: tuple[Box, ...]  # the strokes and words the box was drawn around
    row_id: str | None
    entry_id: str | None
    text: str | None


ROWS_CELL_UNION = "oracle_cell_union"
"""Row box = the union of the row's printed field boxes: ``row_box(r)`` of module-06 step 4,
and what the M5 parser stores as ``LineItem.row_box_norm``."""
ROWS_TABLE_BAND = "oracle_table_band"
"""Row box = the full table-row band the dataset draws (``row_bbox``). Rows touch each other.
This is NOT what M5 supplies; it is kept as a contrast for the linking evaluation."""
ROWS_M5 = "m5"
"""Row box = the geometry a trained M5 model produced for the row."""
ROW_FIELDS = ("quantity", "description", "action", "amount", "remarks")


@dataclass(frozen=True)
class RowGeometry:
    """One printed estimate row in corrected-page pixels, from the dataset's ground truth.

    ``box`` is the row box handed to the linker. ``load_penmark_pages`` sets it to the union
    of the row's printed field boxes (``cells``). ``band`` is the dataset's full table-row
    band, which is taller and touches its neighbours; ``with_table_band_rows`` swaps it in.
    """

    row_id: str
    entry_id: str
    box: Box
    amount_box: Box | None  # the printed amount of the row
    action: str  # the generator's intended decision for the row; never a model input
    band: Box | None = None  # the dataset's ``row_bbox``; None when a row is built by hand
    cells: tuple[Box, ...] = ()  # the printed field boxes ``box`` is the union of


@dataclass(frozen=True)
class PenMarkPage:
    image_id: str
    dataset_claim_id: str
    claim_id: str
    split: str
    page_type: str
    page_number: int
    page_id: str  # the M4 DocumentPage id; marks and rows refer to the page by it
    page_status: str  # M4's reading status. An unreadable page still has a corrected image
    image_path: Path  # M4's corrected render
    width: int
    height: int
    writer: str  # the handwriting font of the page: the dataset's stand-in for a writer
    marks: tuple[MarkBox, ...] = ()  # detector targets
    distractors: tuple[MarkBox, ...] = ()
    rows: tuple[RowGeometry, ...] = ()
    rows_source: str = ROWS_CELL_UNION  # which row definition ``rows[i].box`` holds; names every linking result
    boxes: np.ndarray = field(default_factory=lambda: np.zeros((0, 4), dtype=np.float32), compare=False, repr=False)
    labels: np.ndarray = field(default_factory=lambda: np.zeros((0,), dtype=np.int64), compare=False, repr=False)


# --------------------------------------------------------------------------- loading

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_claim_grouped(pages: Iterable[Any]) -> None:
    """Refuse a split in which one claim's pages sit in more than one partition.

    Both pages of a claim share a vehicle, a writer and a layout instance; a claim on both
    sides of a split would leak them. Only ``dataset_claim_id`` and ``split`` are read.
    """
    seen: dict[str, set[str]] = {}
    for page in pages:
        seen.setdefault(page.dataset_claim_id, set()).add(page.split)
    crossing = sorted(claim for claim, splits in seen.items() if len(splits) > 1)
    if crossing:
        raise PenMarkDataError(f"{len(crossing)} claim(s) appear in more than one partition, e.g. {crossing[:3]}")


def _boxes(page: Any, boxes: Sequence[Sequence[float]]) -> list[Box]:
    mapped = source_to_corrected_boxes(page, np.asarray(boxes, dtype=float).reshape(-1, 4))
    return [tuple(float(v) for v in row) for row in mapped]


def union_box(boxes: Sequence[Sequence[float]]) -> Box:
    """The smallest upright box around ``boxes`` (x0, y0, x1, y1); at least one box is required."""
    array = np.asarray(boxes, dtype=float).reshape(-1, 4)
    if not len(array):
        raise ValueError("the union of no boxes is undefined")
    return (float(array[:, 0].min()), float(array[:, 1].min()), float(array[:, 2].max()), float(array[:, 3].max()))


def with_table_band_rows(pages: Iterable[PenMarkPage]) -> list[PenMarkPage]:
    """Copies of ``pages`` whose row boxes are the dataset's full table-row bands.

    A contrast for the linking evaluation: it shows what the linker does when rows are drawn
    as whole table bands that touch each other. M5 does not supply such rows.
    """
    swapped = []
    for page in pages:
        missing = [row.row_id for row in page.rows if row.band is None]
        if missing:
            raise PenMarkDataError(f"page {page.image_id}: rows {missing} carry no table band")
        swapped.append(replace(page, rows=tuple(replace(row, box=row.band) for row in page.rows),
                               rows_source=ROWS_TABLE_BAND))
    return swapped


def _coco_targets(dataset_dir: Path, split: str) -> dict[str, dict[str, tuple[str, Box]]]:
    """``image_id -> mark_id -> (class, x0y0x1y1)`` from one COCO export, in dataset pixels."""
    path = dataset_dir / f"marks_coco_{split}.json"
    if not path.is_file():
        raise PenMarkDataError(f"detection labels missing: {path}")
    coco = json.loads(path.read_text(encoding="utf-8"))
    names = {c["id"]: c["name"] for c in coco["categories"]}
    if set(names.values()) != set(TARGET_CLASSES):
        raise PenMarkDataError(f"{path.name} has categories {sorted(names.values())}, expected {TARGET_CLASSES}")
    images = {image["id"]: Path(image["file_name"]).stem for image in coco["images"]}
    targets: dict[str, dict[str, tuple[str, Box]]] = {image_id: {} for image_id in images.values()}
    for annotation in coco["annotations"]:
        x, y, w, h = (float(v) for v in annotation["bbox"])
        targets[images[annotation["image_id"]]][annotation["mark_id"]] = (names[annotation["category_id"]],
                                                                        (x, y, x + w, y + h))
    return targets


def load_penmark_pages(cache: Any, dataset_dir: str | Path, splits: Sequence[str] = ("train", "validation"), *,
                       include_test: bool = False) -> list[PenMarkPage]:
    """Labelled pages of ``splits`` on the corrected renders of ``cache`` (an ``M4Cache``).

    Detection targets come from ``marks_coco_<split>.json`` and must equal the target marks
    of ``annotations.jsonl``, which also supplies the styles, the distractors and the row
    geometry. Every box is mapped into the corrected-page frame with the page's M4
    transform. Raises ``PenMarkDataError`` when the files disagree or are not the ones the
    cache was built from.
    """
    if HELD_OUT_SPLIT in splits and not include_test:
        raise ValueError("the test split is the final held-out partition; pass include_test=True only for the "
                         "final evaluation, after the model and its thresholds are frozen")
    dataset_dir = Path(dataset_dir)
    annotations_path = dataset_dir / "annotations.jsonl"
    if not annotations_path.is_file():
        raise PenMarkDataError(f"dataset file missing: {annotations_path}")
    recorded = cache.manifest["dataset"]["annotations_sha256"]
    if _sha256(annotations_path) != recorded:
        raise PenMarkDataError(f"{annotations_path} is not the annotations.jsonl this M4 cache was built from "
                               f"(sha256 {recorded})")
    assert_claim_grouped(cache.pages)
    records: dict[str, dict[str, Any]] = {}
    with annotations_path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                record = json.loads(line)
                if record["split"] in splits:  # labels of other partitions are never parsed further
                    records[record["image_id"]] = record

    pages: list[PenMarkPage] = []
    for split in splits:
        coco = _coco_targets(dataset_dir, split)
        cached_pages = cache.split(split)
        if {p.image_id for p in cached_pages} != set(coco):
            raise PenMarkDataError(f"marks_coco_{split}.json and the M4 cache list different pages for {split}")
        for cached in cached_pages:
            image_id, record = cached.image_id, records.get(cached.image_id)
            if record is None or record["split"] != split:
                raise PenMarkDataError(f"page {image_id}: no {split} record in annotations.jsonl")
            if cached.page is None or cached.rectified_path is None:
                raise PenMarkDataError(f"page {image_id}: the M4 cache holds no corrected image for it")
            listed = {m["mark_id"]: (m["class"], tuple(float(v) for v in m["bbox"])) for m in record["marks"]
                      if m["class"] in TARGET_CLASSES}
            if listed != coco[image_id]:
                differing = sorted(set(listed) ^ set(coco[image_id])
                                   | {k for k in set(listed) & set(coco[image_id]) if listed[k] != coco[image_id][k]})
                raise PenMarkDataError(f"page {image_id}: marks_coco_{split}.json and annotations.jsonl disagree on "
                                       f"{differing or 'its target marks'}")
            unknown = sorted({m["class"] for m in record["marks"]} - set(TARGET_CLASSES) - set(DISTRACTOR_CLASSES))
            if unknown:
                raise PenMarkDataError(f"page {image_id}: unknown mark classes {unknown}")
            marks = []
            for mark in record["marks"]:
                box, *components = _boxes(cached.page, [mark["bbox"], *mark["component_bboxes"]])
                marks.append(MarkBox(mark["mark_id"], mark["class"], mark["style"], box, tuple(components),
                                     mark.get("target_row_id"), mark.get("target_entry_id"), mark.get("text")))
            fields = {r["label"]: r["bbox"] for r in record["regions"]}
            rows = []
            for row in record["row_decisions"]:
                # Module-06 step 4: row_box(r) is the union of the row's cell boxes. Here the cells
                # are the row's printed field boxes; the full table band is kept beside it.
                printed = [fields[f"{row['row_id']}_{name}"] for name in ROW_FIELDS
                           if f"{row['row_id']}_{name}" in fields]
                if not printed:
                    raise PenMarkDataError(f"page {image_id}: row {row['row_id']} has no printed field box; "
                                           "its row box cannot be built and the table band is not a substitute")
                amount = fields.get(f"{row['row_id']}_amount")
                band, *cells = _boxes(cached.page, [row["row_bbox"], *printed])
                rows.append(RowGeometry(row["row_id"], f"{image_id}:{row['row_id']}", union_box(cells),
                                        _boxes(cached.page, [amount])[0] if amount else None,
                                        row["synthetic_action"], band=band, cells=tuple(cells)))
            targets = tuple(m for m in marks if m.mark_class in TARGET_CLASSES)
            transform = cached.page.transform
            pages.append(PenMarkPage(
                image_id=image_id, dataset_claim_id=cached.dataset_claim_id, claim_id=cached.claim_id, split=split,
                page_type=cached.page_type, page_number=cached.page_number, page_id=cached.page.page_id,
                page_status=cached.page_status, image_path=Path(cached.rectified_path),
                width=int(transform.corrected_width), height=int(transform.corrected_height),
                writer=Path(str(record.get("font_file") or "unknown")).stem, marks=targets,
                distractors=tuple(m for m in marks if m.mark_class in DISTRACTOR_CLASSES), rows=tuple(rows),
                boxes=np.array([m.box for m in targets], dtype=np.float32).reshape(-1, 4),
                labels=np.array([CLASS_TO_LABEL[m.mark_class] for m in targets], dtype=np.int64)))
    return pages


def load_image(page: PenMarkPage) -> np.ndarray:
    """The corrected page as RGB uint8 ``[height, width, 3]``, the array the detector is served."""
    with Image.open(page.image_path) as image:
        pixels = np.array(image.convert("RGB"))
    if pixels.shape[:2] != (page.height, page.width):
        raise PenMarkDataError(f"page {page.image_id}: the corrected image is {pixels.shape[1]}x{pixels.shape[0]}, "
                               f"its page record says {page.width}x{page.height}")
    return pixels


def split_rows(pages: Sequence[PenMarkPage]) -> list[dict[str, Any]]:
    """One summary row per partition: pages, claims, pages without a target and marks per class."""
    rows = []
    for split in dict.fromkeys(p.split for p in pages):
        members = [p for p in pages if p.split == split]
        counts = Counter(m.mark_class for p in members for m in (*p.marks, *p.distractors))
        rows.append({"split": split, "pages": len(members), "claims": len({p.dataset_claim_id for p in members}),
                     "estimate_pages": sum(p.page_type == "repair_estimate" for p in members),
                     "pages_without_target": sum(not p.marks for p in members),
                     "m4_unreadable_pages": sum(p.page_status == "unreadable" for p in members),
                     **{name: counts.get(name, 0) for name in (*TARGET_CLASSES, *DISTRACTOR_CLASSES)}})
    return rows


def style_rows(pages: Sequence[PenMarkPage]) -> list[dict[str, Any]]:
    """Mark counts per class and style, one column per partition, with the median box size."""
    splits = list(dict.fromkeys(p.split for p in pages))
    groups: dict[tuple[str, str], list[tuple[str, Box]]] = {}
    for page in pages:
        for mark in (*page.marks, *page.distractors):
            groups.setdefault((mark.mark_class, mark.style), []).append((page.split, mark.box))
    rows = []
    for (mark_class, style), members in sorted(groups.items(), key=lambda item: (item[0][0] not in TARGET_CLASSES,
                                                                               item[0])):
        widths = np.array([b[2] - b[0] for _, b in members])
        heights = np.array([b[3] - b[1] for _, b in members])
        rows.append({"mark_class": mark_class, "style": style, "target": mark_class in TARGET_CLASSES,
                     **{split: sum(s == split for s, _ in members) for split in splits},
                     "median_width_px": float(np.median(widths)), "median_height_px": float(np.median(heights)),
                     "min_height_px": float(heights.min()), "median_width_over_height": float(np.median(widths / heights))})
    return rows


# --------------------------------------------------------------------------- ink checks

def ink_mask(pixels: np.ndarray, darkest_below: int = 140) -> np.ndarray:
    """Pixels darker than paper: printed text and pen ink alike (boolean ``[height, width]``).

    A pixel counts as ink when its darkest channel is below ``darkest_below``. The synthetic
    surveyor writes in several blues and in near-black, so colour cannot single out the pen.
    This is a check on the labels, never a model input.
    """
    return pixels.min(axis=2) < darkest_below


def ink_extent(mask: np.ndarray, box: Sequence[float]) -> tuple[int, int, int, int] | None:
    """The tight box (x0, y0, x1, y1) around the ink inside ``box``, or None when it holds none."""
    x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
    x1, y1 = min(mask.shape[1], math.ceil(box[2])), min(mask.shape[0], math.ceil(box[3]))
    ys, xs = np.nonzero(mask[y0:y1, x0:x1])
    if not len(xs):
        return None
    return (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1)


# --------------------------------------------------------------------------- augmentation

@dataclass(frozen=True)
class AugmentConfig:
    """Training-only augmentation. Every transform keeps the boxes exact or recomputes them exactly.

    There is no horizontal flip: a page is not mirror-symmetric, and a mirrored page would
    teach amounts on the left. A value of 0 switches a transform off.
    """

    brightness: float = 0.12  # multiply by a factor in 1 +- brightness
    contrast: float = 0.12  # scale the distance from the page mean by 1 +- contrast
    blur_probability: float = 0.25
    blur_max_radius: float = 1.0  # Gaussian radius in pixels
    jpeg_probability: float = 0.35
    jpeg_quality: tuple[int, int] = (45, 90)
    shadow_probability: float = 0.30  # a smooth lighting ramp across the page
    shadow_strength: float = 0.25  # darkest factor is 1 - strength
    scale: float = 0.05  # zoom about the page centre by a factor in 1 +- scale
    translate_px: float = 20.0  # shift by up to this many pixels on each axis
    rotation_degrees: float = 0.0  # up to this angle either way; boxes become the rotated rectangle's bound
    min_visible: float = 0.5  # a box with less than this share left on the page is dropped


def affine_matrix(width: int, height: int, *, scale: float, degrees: float, translate: tuple[float, float]) -> np.ndarray:
    """The 2 x 3 matrix that zooms and rotates about the page centre, then shifts."""
    angle = math.radians(degrees)
    cos, sin = math.cos(angle) * scale, math.sin(angle) * scale
    cx, cy = width / 2.0, height / 2.0
    return np.array([[cos, -sin, cx - cos * cx + sin * cy + translate[0]],
                     [sin, cos, cy - sin * cx - cos * cy + translate[1]]], dtype=float)


def affine_boxes(boxes: np.ndarray, matrix: np.ndarray, width: int, height: int, *,
                 min_visible: float) -> tuple[np.ndarray, np.ndarray]:
    """Boxes after ``matrix``: the bound of each moved rectangle, clipped to the page.

    Returns the kept boxes and a boolean mask over the input. A box that keeps less than
    ``min_visible`` of its moved area on the page is dropped, never kept as a sliver.
    """
    boxes = np.asarray(boxes, dtype=float).reshape(-1, 4)
    if not len(boxes):
        return boxes.copy(), np.zeros(0, dtype=bool)
    x0, y0, x1, y1 = boxes.T
    corners = np.stack([x0, y0, x1, y0, x1, y1, x0, y1], axis=1).reshape(-1, 4, 2)
    moved = corners @ matrix[:, :2].T + matrix[:, 2]
    bound = np.concatenate([moved.min(axis=1), moved.max(axis=1)], axis=1)
    clipped = np.clip(bound, 0.0, [width, height, width, height])
    area = lambda b: np.clip(b[:, 2] - b[:, 0], 0, None) * np.clip(b[:, 3] - b[:, 1], 0, None)  # noqa: E731
    keep = area(clipped) >= min_visible * np.maximum(area(bound), 1e-9)
    keep &= (clipped[:, 2] - clipped[:, 0] >= 1.0) & (clipped[:, 3] - clipped[:, 1] >= 1.0)
    return clipped[keep], keep


def _paper_colour(pixels: np.ndarray) -> tuple[int, int, int]:
    border = np.concatenate([pixels[:4].reshape(-1, 3), pixels[-4:].reshape(-1, 3)])
    return tuple(int(v) for v in np.median(border, axis=0))


def augment_page(pixels: np.ndarray, boxes: np.ndarray, labels: np.ndarray, rng: np.random.Generator,
                 config: AugmentConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """One augmented copy of an RGB uint8 page and its boxes; ``info`` records what was drawn."""
    height, width = pixels.shape[:2]
    boxes, labels = np.asarray(boxes, dtype=float).reshape(-1, 4), np.asarray(labels)
    info: dict[str, Any] = {"geometric": False, "scale": 1.0, "rotation_degrees": 0.0, "translate_px": (0.0, 0.0),
                            "brightness": 1.0, "contrast": 1.0, "blur_radius": 0.0, "jpeg_quality": None,
                            "shadow": None}
    if config.scale or config.translate_px or config.rotation_degrees:
        scale = 1.0 + float(rng.uniform(-config.scale, config.scale))
        degrees = float(rng.uniform(-config.rotation_degrees, config.rotation_degrees))
        shift = (float(rng.uniform(-config.translate_px, config.translate_px)),
                 float(rng.uniform(-config.translate_px, config.translate_px)))
        matrix = affine_matrix(width, height, scale=scale, degrees=degrees, translate=shift)
        # Pillow asks for the map from output pixels back to input pixels.
        inverse = np.linalg.inv(np.vstack([matrix, [0.0, 0.0, 1.0]]))[:2]
        image = Image.fromarray(pixels).transform((width, height), Image.Transform.AFFINE, tuple(inverse.reshape(-1)),
                                                  resample=Image.Resampling.BILINEAR, fillcolor=_paper_colour(pixels))
        pixels = np.array(image)
        boxes, keep = affine_boxes(boxes, matrix, width, height, min_visible=config.min_visible)
        labels = labels[keep]
        info.update(geometric=True, scale=scale, rotation_degrees=degrees, translate_px=shift)
    work = pixels.astype(np.float32)
    if config.shadow_probability and rng.random() < config.shadow_probability:
        angle, strength = float(rng.uniform(0, 2 * math.pi)), float(rng.uniform(0.3, 1.0)) * config.shadow_strength
        ramp = np.add.outer(np.arange(height, dtype=np.float32) * math.sin(angle),
                            np.arange(width, dtype=np.float32) * math.cos(angle))
        ramp = (ramp - ramp.min()) / max(float(ramp.max() - ramp.min()), 1e-6)
        work *= (1.0 - strength * ramp)[..., None]
        info["shadow"] = {"angle_degrees": math.degrees(angle), "strength": strength}
    if config.contrast:
        info["contrast"] = 1.0 + float(rng.uniform(-config.contrast, config.contrast))
        mean = work.mean()
        work = (work - mean) * info["contrast"] + mean
    if config.brightness:
        info["brightness"] = 1.0 + float(rng.uniform(-config.brightness, config.brightness))
        work *= info["brightness"]
    pixels = np.clip(work + 0.5, 0, 255).astype(np.uint8)
    if config.blur_probability and rng.random() < config.blur_probability:
        info["blur_radius"] = float(rng.uniform(0.3, max(0.3, config.blur_max_radius)))
        pixels = np.array(Image.fromarray(pixels).filter(ImageFilter.GaussianBlur(info["blur_radius"])))
    if config.jpeg_probability and rng.random() < config.jpeg_probability:
        info["jpeg_quality"] = int(rng.integers(config.jpeg_quality[0], config.jpeg_quality[1] + 1))
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="JPEG", quality=info["jpeg_quality"])
        pixels = np.array(Image.open(buffer).convert("RGB"))
    return pixels, boxes, labels, info


# --------------------------------------------------------------------------- torch dataset

class PenMarkDataset(torch.utils.data.Dataset):
    """Pages as torchvision detection samples: an image tensor and a target dictionary.

    The image is float32 ``[3, height, width]`` in 0-1 (RGB); the detector normalises and
    resizes it itself. ``target["boxes"]`` is float32 ``[N, 4]`` in pixels of that image and
    ``target["labels"]`` int64 ``[N]``; a page without marks has ``N = 0``. With ``augment``
    the sample is a training view that depends on ``seed``, the epoch and the index only.
    """

    def __init__(self, pages: Sequence[PenMarkPage], augment: AugmentConfig | None = None, seed: int = 0):
        self.pages, self.augment, self.seed, self.epoch = list(pages), augment, seed, 0

    def set_epoch(self, epoch: int) -> None:
        self.epoch = int(epoch)

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        page = self.pages[index]
        pixels, boxes, labels = load_image(page), page.boxes, page.labels
        if self.augment is not None:
            rng = np.random.default_rng([self.seed, self.epoch, index])
            pixels, boxes, labels, _ = augment_page(pixels, boxes, labels, rng, self.augment)
        image = torch.from_numpy(np.ascontiguousarray(pixels)).permute(2, 0, 1).float().div(255.0)
        target = {"boxes": torch.as_tensor(np.asarray(boxes, dtype=np.float32).reshape(-1, 4)),
                  "labels": torch.as_tensor(np.asarray(labels, dtype=np.int64).reshape(-1)),
                  "image_id": torch.tensor(index)}
        return image, target


def collate(batch: Sequence[tuple[torch.Tensor, dict[str, torch.Tensor]]]) -> tuple[tuple, tuple]:
    """Keep images and targets as tuples: a detector takes lists, never a stacked batch."""
    return tuple(zip(*batch))


# --------------------------------------------------------------------------- resizing and anchors

def resized_size(height: int, width: int, *, min_size: int, max_size: int) -> tuple[float, tuple[int, int],
                                                                                    tuple[int, int]]:
    """What torchvision's detection transform does to a ``height`` x ``width`` page.

    Returns the scale factor, the resized ``(height, width)`` and the padded size (the next
    multiples of 32). The shorter side goes to ``min_size`` unless the longer side would
    then exceed ``max_size``.
    """
    scale = min(min_size / min(height, width), max_size / max(height, width))
    resized = (math.floor(height * scale), math.floor(width * scale))
    padded = (math.ceil(resized[0] / 32) * 32, math.ceil(resized[1] / 32) * 32)
    return scale, resized, padded


def best_anchor_iou(boxes: Any, anchors: torch.Tensor, chunk: int = 64) -> np.ndarray:
    """For each box, the highest IoU any anchor reaches. Boxes and anchors share one frame.

    This is how well the region proposal network can start: it regresses from anchors, and
    an anchor counts as a positive example for a box at IoU 0.7 or above.
    """
    boxes = torch.as_tensor(np.asarray(boxes, dtype=np.float32).reshape(-1, 4))
    best = [box_iou(boxes[start:start + chunk], anchors.float()).max(dim=1).values
            for start in range(0, len(boxes), chunk)]
    return torch.cat(best).numpy() if best else np.zeros(0, dtype=np.float32)


def data_record(cache: Any, dataset_dir: str | Path, pages: Sequence[PenMarkPage]) -> dict[str, Any]:
    """What a training run records about its data: hashes, M4 versions and per-partition counts.

    The held-out partition appears with its page count only. Its label file is hashed like
    every split file but not parsed, so the record holds nothing learned from its labels.
    """
    dataset_dir, manifest = Path(dataset_dir), cache.manifest
    dataset, m4 = manifest["dataset"], manifest.get("m4", {})
    partitions: dict[str, Any] = {}
    for row in split_rows(pages):
        members = [p for p in pages if p.split == row["split"]]
        partitions[row["split"]] = {
            "pages": row["pages"], "claims": row["claims"], "pages_without_target": row["pages_without_target"],
            "m4_unreadable_pages": row["m4_unreadable_pages"],
            "marks": {name: row[name] for name in TARGET_CLASSES},
            "distractors": {name: row[name] for name in DISTRACTOR_CLASSES},
            "styles": dict(sorted(Counter(f"{m.mark_class}/{m.style}" for p in members for m in p.marks).items()))}
    for split in sorted({p.split for p in cache.pages} - set(partitions)):
        members = [p for p in cache.pages if p.split == split]
        partitions[split] = {"pages": len(members), "claims": len({p.dataset_claim_id for p in members}),
                             "marks": None, "marks_reason": "the labels of this partition were not read"}
    split_files = {path.name: _sha256(path) for path in sorted(dataset_dir.glob("marks_coco_*.json"))}
    for name in ("manifest.csv", "annotations.jsonl"):
        if (dataset_dir / name).is_file():
            split_files[name] = _sha256(dataset_dir / name)
    cache_manifest = Path(cache.root) / "manifest.json"
    return {
        "dataset": {"name": dataset.get("name"), "manifest_sha256": dataset.get("manifest_sha256"),
                    "annotations_sha256": dataset.get("annotations_sha256"),
                    "provenance_sha256": dataset.get("provenance_sha256"),
                    "provenance": "fully synthetic: one page layout, surveyor ink rendered with handwriting fonts"},
        "m4_cache": {"schema": manifest.get("schema"),
                     "manifest_sha256": _sha256(cache_manifest) if cache_manifest.is_file() else None,
                     "versions": m4.get("versions"), "m4_source_sha256": m4.get("m4_source_sha256"),
                     "page_config_sha256": m4.get("page_config_sha256"),
                     "ocr_model_version": m4.get("ocr_model_version")},
        "split_version": f"{dataset.get('name')}@manifest-{str(dataset.get('manifest_sha256'))[:12]}",
        "grouping_key": "dataset claim id: both pages of a claim are in one partition",
        "split_files": split_files, "partitions": partitions, "conversion_version": CONVERSION_VERSION,
        "frame": "M4 corrected page (rectified.png); labels mapped with the page transform",
    }


def records_by_id(pages: Sequence[PenMarkPage]) -> Mapping[str, PenMarkPage]:
    return {page.image_id: page for page in pages}


__all__ = ["CLASS_TO_LABEL", "CONVERSION_VERSION", "DISTRACTOR_CLASSES", "LABEL_TO_CLASS", "PALETTE", "ROWS_CELL_UNION",
           "ROWS_M5", "ROWS_TABLE_BAND", "ROW_FIELDS", "TARGET_CLASSES",
           "AugmentConfig", "MarkBox", "PenMarkDataError", "PenMarkDataset", "PenMarkPage", "RowGeometry",
           "affine_boxes", "affine_matrix", "assert_claim_grouped", "augment_page", "best_anchor_iou", "collate",
           "data_record",
           "ink_extent", "ink_mask", "load_image", "load_penmark_pages", "records_by_id", "resized_size",
           "split_rows", "style_rows", "union_box", "with_table_band_rows"]
