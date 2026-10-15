"""Training data for the M5 line-item model: labels for M4 text boxes and the tensors built from them.

M4 returns located text *lines*, not words, so the unit this model classifies is one M4 text
box. This module gives every box of a cached M4 page one of three outcomes, by comparing it
with the dataset's ground-truth field boxes in M4's corrected-page frame:

- a field label (``B-``/``I-`` plus the dataset region label), when the box significantly
  overlaps exactly one field region and no surveyor handwriting;
- ``O``, when it overlaps no field region (static form text, column headers, handwriting);
- masked (label ``-100``), when the assignment is ambiguous: two or more field regions, or
  a printed field together with handwritten text. A masked box is left out of the loss and
  out of the scores, and it is counted.

A ground-truth field that no box covers is reported as missed by OCR; it is never invented.
The overlap rule and its thresholds live in ``LabelPolicy``. They were chosen from the
training split only and are proposed, not accepted.

The page encoding itself is ``page_words``/``encode_page`` of the serving adapter
(``claim_cmev.documents.line_items.layoutlmv3``), so training and serving tensors are built
by the same code. Nothing here reads a model, and nothing here is a model result.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from claim_cmev.documents.line_items.layoutlmv3 import PageWords, encode_page, page_words
from pipelines.documents.m4_cache import CachedPage, source_to_corrected_boxes

POLICY_VERSION = "m5-box-label-alignment/0.1.0"
IGNORE_INDEX = -100
OUTSIDE = "O"
ROW_FIELD = re.compile(r"^part_(\d+)_(description|action|quantity|amount|remarks)$")
ROW_FIELD_TYPES = ("quantity", "description", "action", "amount", "remarks")
PAGE_FIELD = "page_field"  # every labelled field that is not a cell of the parts table
SCORED_SPLITS = ("train", "validation")  # the test split is opened only by the final evaluation


@dataclass(frozen=True)
class LabelPolicy:
    """How an M4 text box gets its training label. Proposed values, chosen on the training split."""

    version: str = POLICY_VERSION
    overlap_measure: str = "intersection_over_smaller_area"
    field_overlap_min: float = 0.5  # box and field region overlap "significantly" at or above this
    handwriting_overlap_min: float = 0.5  # share of one handwritten word's box lying inside the OCR box
    handwriting_source: str = "handwritten_words"
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- dataset files

def read_annotations(dataset_dir: str | Path, *, splits: Sequence[str] | None = None) -> dict[str, dict[str, Any]]:
    """``annotations.jsonl`` as ``{image_id: record}``."""
    records: dict[str, dict[str, Any]] = {}
    with (Path(dataset_dir) / "annotations.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                record = json.loads(line)
                if splits is None or record["split"] in splits:
                    records[record["image_id"]] = record
    return records


def read_claims(dataset_dir: str | Path, *, claim_ids: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """``claims.jsonl`` as ``{dataset claim id: record}``: the structured ground truth of each claim."""
    claims: dict[str, dict[str, Any]] = {}
    with (Path(dataset_dir) / "claims.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                record = json.loads(line)
                if claim_ids is None or record["claim_id"] in claim_ids:
                    claims[record["claim_id"]] = record
    return claims


def region_label_names(annotations: Mapping[str, Mapping[str, Any]],
                       splits: Sequence[str] = SCORED_SPLITS) -> list[str]:
    """Sorted names of the field labels used on pages of ``splits``. Names are schema, not page content."""
    return sorted({region["label"] for record in annotations.values() if record["split"] in splits
                   for region in record["regions"]})


def bio_labels(names: Iterable[str]) -> list[str]:
    """``O`` followed by ``B-``/``I-`` for each field name, in the order given."""
    labels = [OUTSIDE]
    for name in names:
        labels += [f"B-{name}", f"I-{name}"]
    return labels


def field_name(label: str | None) -> str | None:
    """``B-part_2_amount`` to ``part_2_amount``; ``O`` and ``None`` to ``None``."""
    return label[2:] if label and label[:2] in ("B-", "I-") else None


def field_type(name: str | None) -> str:
    """The kind of field a dataset label names: a parts-table column, ``page_field`` or ``O``."""
    if name is None:
        return OUTSIDE
    match = ROW_FIELD.fullmatch(name)
    return match[2] if match else PAGE_FIELD


def row_slot(name: str | None) -> int | None:
    """The row slot ``N`` of ``part_N_<field>``, or ``None`` for any other label."""
    match = ROW_FIELD.fullmatch(name) if name else None
    return int(match[1]) if match else None


def assert_claims_in_one_split(pages: Iterable[CachedPage]) -> dict[str, int]:
    """Every dataset claim lies in exactly one split; returns the number of claims per split."""
    seen: dict[str, str] = {}
    for page in pages:
        if seen.setdefault(page.dataset_claim_id, page.split) != page.split:
            raise ValueError(f"claim {page.dataset_claim_id} has pages in more than one split")
    return dict(sorted(Counter(seen.values()).items()))


# --------------------------------------------------------------------------- geometry

def _areas(boxes: np.ndarray) -> np.ndarray:
    return np.clip(boxes[:, 2] - boxes[:, 0], 0, None) * np.clip(boxes[:, 3] - boxes[:, 1], 0, None)


def _intersections(boxes: np.ndarray, regions: np.ndarray) -> np.ndarray:
    width = np.minimum(boxes[:, None, 2], regions[None, :, 2]) - np.maximum(boxes[:, None, 0], regions[None, :, 0])
    height = np.minimum(boxes[:, None, 3], regions[None, :, 3]) - np.maximum(boxes[:, None, 1], regions[None, :, 1])
    return np.clip(width, 0, None) * np.clip(height, 0, None)


def overlap_of_smaller(boxes: Any, regions: Any) -> np.ndarray:
    """``[N, M]`` intersection area over the area of the smaller of the two boxes.

    It is 1 when either box lies inside the other, whatever their sizes: an OCR line that is
    a little larger than its field, a fragment of a field, and a merged line that swallowed a
    small field all score high. A neighbour that only brushes the field scores low.
    """
    a, b = np.asarray(boxes, dtype=float).reshape(-1, 4), np.asarray(regions, dtype=float).reshape(-1, 4)
    smaller = np.minimum(_areas(a)[:, None], _areas(b)[None, :])
    return np.divide(_intersections(a, b), smaller, out=np.zeros((len(a), len(b))), where=smaller > 0)


def share_inside(boxes: Any, regions: Any) -> np.ndarray:
    """``[N, M]`` share of each region's area that lies inside each box."""
    a, b = np.asarray(boxes, dtype=float).reshape(-1, 4), np.asarray(regions, dtype=float).reshape(-1, 4)
    area = np.broadcast_to(_areas(b)[None, :], (len(a), len(b)))
    return np.divide(_intersections(a, b), area, out=np.zeros((len(a), len(b))), where=area > 0)


def boxes_in_pixels(page: CachedPage) -> np.ndarray:
    """The page's text boxes in reading order as corrected-page pixel boxes ``[N, 4]``."""
    record = page.page
    if record is None or not record.text_boxes:
        return np.zeros((0, 4))
    size = np.array([record.transform.corrected_width, record.transform.corrected_height] * 2, dtype=float)
    ordered = sorted(record.text_boxes, key=lambda box: box.order_index)
    return np.array([box.box_norm for box in ordered], dtype=float) * size


def regions_in_corrected_frame(page: CachedPage, boxes_xyxy: Sequence[Sequence[float]]) -> np.ndarray:
    """Dataset pixel boxes mapped into M4's corrected-page frame (the identity only on flat scans)."""
    if not len(boxes_xyxy):
        return np.zeros((0, 4))
    return source_to_corrected_boxes(page.page, boxes_xyxy)


# --------------------------------------------------------------------------- alignment

@dataclass(frozen=True)
class BoxLabel:
    """The training label of one M4 text box, and why."""

    box_id: str
    order_index: int
    text: str
    confidence: float | None
    box_px: tuple[float, float, float, float]  # corrected-page pixels
    outcome: str  # "field" | "other" | "masked"
    label: str | None  # "B-part_1_amount", "O", or None when masked
    field: str | None  # dataset field label for "field"; the single candidate of a masked box
    reason: str  # single_field | static_text | handwriting | merged_fields | print_and_handwriting
    field_overlap: float  # best overlap with any field region
    handwriting_overlap: float  # best share of a handwritten word inside the box


@dataclass(frozen=True)
class FieldMatch:
    """What M4 delivered for one ground-truth field."""

    label: str
    text: str
    box_px: tuple[float, float, float, float]  # corrected-page pixels
    status: str  # "matched" | "ambiguous" | "missed" | "page_unreadable"
    box_ids: tuple[str, ...]  # labelled boxes when matched; the masked boxes when ambiguous


@dataclass(frozen=True)
class PageAlignment:
    image_id: str
    dataset_claim_id: str
    split: str
    page_type: str
    page_status: str
    boxes: tuple[BoxLabel, ...]  # reading order
    fields: tuple[FieldMatch, ...]  # annotation order

    def label_ids(self, label2id: Mapping[str, int]) -> list[int]:
        """One class id per box in reading order; ``-100`` for a masked box."""
        return [IGNORE_INDEX if box.label is None else label2id[box.label] for box in self.boxes]

    def field(self, label: str) -> FieldMatch | None:
        return next((match for match in self.fields if match.label == label), None)


def align_page(page: CachedPage, annotation: Mapping[str, Any], policy: LabelPolicy = LabelPolicy()) -> PageAlignment:
    """Label every M4 box of one page against the page's ground-truth field regions."""
    if annotation["image_id"] != page.image_id:
        raise ValueError(f"annotation {annotation['image_id']} does not describe page {page.image_id}")
    regions = annotation["regions"]
    record = page.page
    ordered = sorted(record.text_boxes, key=lambda box: box.order_index) if record is not None else []
    field_px = regions_in_corrected_frame(page, [region["bbox"] for region in regions]) if record is not None \
        else np.asarray([region["bbox"] for region in regions], dtype=float)
    if not ordered:
        # M4 read nothing: the fields exist on paper, but there is no box to label.
        status = "page_unreadable" if page.page_status == "unreadable" else "missed"
        return PageAlignment(page.image_id, page.dataset_claim_id, page.split, page.page_type, page.page_status, (),
                             tuple(FieldMatch(region["label"], region["text"], tuple(map(float, box)), status, ())
                                   for region, box in zip(regions, field_px)))
    box_px = boxes_in_pixels(page)
    overlap = overlap_of_smaller(box_px, field_px)
    ink = annotation.get(policy.handwriting_source) or []
    ink_share = share_inside(box_px, regions_in_corrected_frame(page, [word["bbox"] for word in ink])).max(axis=1) \
        if ink else np.zeros(len(ordered))

    decided: list[tuple[str, str | None, str]] = []  # outcome, field, reason
    for index in range(len(ordered)):
        hits = np.nonzero(overlap[index] >= policy.field_overlap_min)[0]
        handwritten = bool(ink_share[index] >= policy.handwriting_overlap_min)
        if len(hits) >= 2:
            decided.append(("masked", None, "merged_fields"))
        elif len(hits) == 1 and handwritten:
            decided.append(("masked", regions[hits[0]]["label"], "print_and_handwriting"))
        elif len(hits) == 1:
            decided.append(("field", regions[hits[0]]["label"], "single_field"))
        else:
            decided.append(("other", None, "handwriting" if handwritten else "static_text"))

    # Several boxes inside one field (a wrapped or broken line): first in reading order begins it.
    started: set[str] = set()
    boxes: list[BoxLabel] = []
    for index, (box, (outcome, name, reason)) in enumerate(zip(ordered, decided)):
        label = None
        if outcome == "field":
            label = ("I-" if name in started else "B-") + name
            started.add(name)
        elif outcome == "other":
            label = OUTSIDE
        boxes.append(BoxLabel(box.box_id, box.order_index, box.text, box.confidence,
                              tuple(float(value) for value in box_px[index]), outcome, label, name, reason,
                              float(overlap[index].max()) if len(regions) else 0.0, float(ink_share[index])))

    fields: list[FieldMatch] = []
    for column, region in enumerate(regions):
        touching = np.nonzero(overlap[:, column] >= policy.field_overlap_min)[0]
        labelled = tuple(boxes[i].box_id for i in touching if boxes[i].outcome == "field")
        masked = tuple(boxes[i].box_id for i in touching if boxes[i].outcome == "masked")
        status = "matched" if labelled else ("ambiguous" if masked else "missed")
        fields.append(FieldMatch(region["label"], region["text"], tuple(float(v) for v in field_px[column]), status,
                                 labelled or masked))
    return PageAlignment(page.image_id, page.dataset_claim_id, page.split, page.page_type, page.page_status,
                         tuple(boxes), tuple(fields))


def align_pages(pages: Iterable[CachedPage], annotations: Mapping[str, Mapping[str, Any]],
                policy: LabelPolicy = LabelPolicy()) -> dict[str, PageAlignment]:
    """``{image_id: PageAlignment}`` for the given pages."""
    return {page.image_id: align_page(page, annotations[page.image_id], policy) for page in pages}


def overlap_samples(pages: Iterable[CachedPage], annotations: Mapping[str, Mapping[str, Any]]) -> dict[str, np.ndarray]:
    """Every positive overlap value between M4 boxes and field regions / handwritten words.

    This is the measurement the policy thresholds were chosen from, so it accepts training
    pages only and refuses anything else.
    """
    fields: list[np.ndarray] = []
    handwriting: list[np.ndarray] = []
    for page in pages:
        if page.split != "train":
            raise ValueError(f"thresholds are chosen on the training split only; {page.image_id} is {page.split}")
        box_px = boxes_in_pixels(page)
        if not len(box_px):
            continue
        record = annotations[page.image_id]
        values = overlap_of_smaller(box_px, regions_in_corrected_frame(page, [r["bbox"] for r in record["regions"]]))
        fields.append(values[values > 0])
        ink = record.get("handwritten_words") or []
        if ink:
            share = share_inside(box_px, regions_in_corrected_frame(page, [w["bbox"] for w in ink])).max(axis=1)
            handwriting.append(share[share > 0])
    return {"field_overlap": np.concatenate(fields) if fields else np.zeros(0),
            "handwriting_overlap": np.concatenate(handwriting) if handwriting else np.zeros(0)}


def box_rows(alignments: Mapping[str, PageAlignment]) -> list[dict[str, Any]]:
    """One plain row per M4 box, ready for ``pandas.DataFrame``."""
    return [{"image_id": a.image_id, "split": a.split, "page_type": a.page_type, **asdict(box),
             "field_type": field_type(box.field) if box.outcome != "other" else OUTSIDE}
            for a in alignments.values() for box in a.boxes]


def field_rows(alignments: Mapping[str, PageAlignment]) -> list[dict[str, Any]]:
    """One plain row per ground-truth field, ready for ``pandas.DataFrame``."""
    return [{"image_id": a.image_id, "split": a.split, "page_type": a.page_type, "label": match.label,
             "field_type": field_type(match.label), "slot": row_slot(match.label), "text": match.text,
             "status": match.status, "box_count": len(match.box_ids)}
            for a in alignments.values() for match in a.fields]


# --------------------------------------------------------------------------- OCR text quality

def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance: the fewest single-character insertions, deletions and substitutions."""
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def char_error_rate(truth: str, read: str) -> float:
    """Edit distance divided by the length of the true text (0 is a perfect read; it can exceed 1)."""
    return edit_distance(truth, read) / max(1, len(truth))


def row_markings(annotation: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{row id: {"action", "style"}}`` for an estimate page; ``style`` is ``unmarked`` without a mark."""
    marks = {mark["mark_id"]: mark for mark in annotation.get("marks") or []}
    rows: dict[str, dict[str, Any]] = {}
    for decision in annotation.get("row_decisions") or []:
        styles = [marks[mark_id]["style"] for mark_id in decision.get("mark_ids") or [] if mark_id in marks]
        rows[decision["row_id"]] = {"action": decision["synthetic_action"], "style": "+".join(styles) or "unmarked"}
    return rows


def ink_over(box_xyxy: Sequence[float], annotation: Mapping[str, Any]) -> bool:
    """True when any stroke or handwritten component of any surveyor mark crosses the box."""
    components = [component for mark in annotation.get("marks") or [] for component in mark["component_bboxes"]]
    if not components:
        return False
    return bool((_intersections(np.asarray([box_xyxy], dtype=float), np.asarray(components, dtype=float)) > 0).any())


def text_quality_rows(alignments: Mapping[str, PageAlignment],
                      annotations: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """For every matched field: the true text, what OCR read, and whether surveyor ink crosses the field.

    Several boxes of one field are joined with a space in reading order. Whitespace is
    collapsed on both sides before comparing; nothing else is normalised.
    """
    rows: list[dict[str, Any]] = []
    for alignment in alignments.values():
        annotation = annotations[alignment.image_id]
        region_px = {region["label"]: region["bbox"] for region in annotation["regions"]}
        markings = row_markings(annotation)
        by_id = {box.box_id: box for box in alignment.boxes}
        for match in alignment.fields:
            if match.status != "matched":
                continue
            read_boxes = [by_id[box_id] for box_id in match.box_ids]
            truth = " ".join(match.text.split())
            read = " ".join(" ".join(box.text for box in read_boxes).split())
            slot = row_slot(match.label)
            confidences = [box.confidence for box in read_boxes if box.confidence is not None]
            rows.append({"image_id": alignment.image_id, "split": alignment.split, "label": match.label,
                         "field_type": field_type(match.label), "slot": slot, "truth": truth, "read": read,
                         "exact": truth == read, "cer": char_error_rate(truth, read),
                         "confidence": min(confidences) if confidences else None,
                         "ink_over_field": ink_over(region_px[match.label], annotation),
                         "row_style": markings.get(f"part_{slot}", {}).get("style", "unmarked") if slot else None})
    return rows


# --------------------------------------------------------------------------- tensors

def encode_cached_page(processor: Any, page: CachedPage, label_ids: Sequence[int] | None = None) -> tuple[PageWords, Any]:
    """Encode one cached M4 page exactly as the serving adapter does; ``(words, None)`` if no box has text.

    ``label_ids`` has one entry per box in reading order (the output of
    ``PageAlignment.label_ids``); entries of boxes without visible text are dropped with them.
    """
    from PIL import Image

    words = page_words(page.page)
    if not words.readable:
        return words, None
    if label_ids is not None and len(label_ids) != len(words.boxes):
        raise ValueError(f"{page.image_id}: need one label per text box")
    with Image.open(page.rectified_path) as source:
        image = source.convert("RGB")
    if image.size != (page.page.transform.corrected_width, page.page.transform.corrected_height):
        raise ValueError(f"{page.image_id}: the cached image is not the M4 corrected page")
    word_labels = None if label_ids is None else [label_ids[index] for index in words.readable]
    return words, encode_page(processor, image, words, word_labels)


class PageWindowDataset:
    """Every 512-token window of the given pages, encoded once and kept in memory.

    Item keys: ``input_ids``, ``attention_mask``, ``bbox``, ``pixel_values``, ``labels`` and
    ``word_index``. ``word_index[t]`` is the position (among the page's readable boxes) of
    the box whose first subword is token ``t``, and ``-1`` elsewhere: the positions where a
    label is trained and where a prediction is read. Pages M4 could not read have no window;
    they are listed in ``skipped`` so that a caller counts them instead of losing them.
    """

    def __init__(self, pages: Sequence[CachedPage], processor: Any, *,
                 alignments: Mapping[str, PageAlignment] | None = None, label2id: Mapping[str, int] | None = None,
                 progress: Callable[[int, int], None] | None = None):
        import torch

        if (alignments is None) != (label2id is None):
            raise ValueError("pass alignments and label2id together, or neither for unlabelled pages")
        self.pages: list[CachedPage] = []
        self.words: list[PageWords] = []
        self.page_windows: list[list[int]] = []
        self.windows: list[dict[str, Any]] = []
        self.window_page: list[int] = []
        self.skipped: list[tuple[str, str]] = []
        for done, page in enumerate(pages, start=1):
            ids = alignments[page.image_id].label_ids(label2id) if alignments is not None else None
            words, encoding = encode_cached_page(processor, page, ids) if page.page is not None else (None, None)
            if encoding is None:
                self.skipped.append((page.image_id, page.page_status if page.page_status != "complete" else "no_text"))
            else:
                count = encoding["input_ids"].shape[0]
                starts = encoding["offset_mapping"][..., 0]
                page_index = len(self.pages)
                self.pages.append(page)
                self.words.append(words)
                self.page_windows.append(list(range(len(self.windows), len(self.windows) + count)))
                for window in range(count):
                    word_index = torch.full((starts.shape[1],), -1, dtype=torch.long)
                    for token, word in enumerate(encoding.word_ids(batch_index=window)):
                        if word is not None and int(starts[window, token]) == 0 and word not in word_index:
                            word_index[token] = word
                    labels = encoding["labels"][window] if ids is not None else torch.full_like(word_index, IGNORE_INDEX)
                    self.windows.append({"input_ids": encoding["input_ids"][window],
                                         "attention_mask": encoding["attention_mask"][window],
                                         "bbox": encoding["bbox"][window],
                                         "pixel_values": encoding["pixel_values"][window],
                                         "labels": labels, "word_index": word_index})
                    self.window_page.append(page_index)
            if progress is not None:
                progress(done, len(pages))

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.windows[index]

    def token_counts(self) -> list[int]:
        """Real (unpadded) tokens of each window, special tokens included."""
        return [int(window["attention_mask"].sum()) for window in self.windows]


def collate_windows(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Stack window dictionaries into one batch of tensors."""
    import torch

    return {key: torch.stack([item[key] for item in items]) for key in items[0]}


__all__ = ["BoxLabel", "FieldMatch", "IGNORE_INDEX", "LabelPolicy", "OUTSIDE", "PAGE_FIELD", "POLICY_VERSION",
           "PageAlignment", "PageWindowDataset", "ROW_FIELD_TYPES", "align_page", "align_pages",
           "assert_claims_in_one_split", "bio_labels", "box_rows", "boxes_in_pixels", "char_error_rate",
           "collate_windows", "edit_distance", "encode_cached_page", "field_name", "field_rows", "field_type",
           "ink_over", "overlap_of_smaller", "overlap_samples", "read_annotations", "read_claims",
           "region_label_names", "regions_in_corrected_frame", "row_markings", "row_slot", "share_inside",
           "text_quality_rows"]
