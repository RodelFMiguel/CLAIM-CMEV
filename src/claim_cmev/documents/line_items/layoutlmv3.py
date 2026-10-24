"""Explicit, local-only S1 inference and conservative M5 contract adapter.

OCR must supply real text boxes on the corrected image, of the unit the checkpoint was
trained on (its ``preprocessing.json`` declares it; a checkpoint that says nothing reads
word boxes, with their text exactly as OCR wrote it). Boxes are never split or merged to
fit a model. Model row-slot labels are page-local; they are never used as part codes or
globally unique entry IDs.

A registry entry is loaded only if it is whole: an entry that names its text unit must
declare its complete page encoding, and when the folder has a ``manifest.json`` with
``weights_sha256`` the weights file must have that hash.

``page_words`` and ``encode_page`` are the only place where a page becomes model tensors.
The training dataset in ``pipelines/documents/lineitems_data.py`` imports them, so the
tensors a model is trained on and the tensors it is served with cannot drift apart.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import json
import re
from typing import Any

from PIL import Image

from claim_cmev.contracts.common import ContractError, Provenance
from claim_cmev.contracts.documents import DeclarationCompleteness, DocumentPage, TextBox
from .config import LayoutFamilyConfig
from .parser import ParseResult, PageParse, UnparsedRegion, _Builder, _check_inputs
from .table import Band, BoxAssignment, ColumnBinding, ColumnInterval, RawRow, group_bands, page_boxes, union_box
from .vocabulary import EstimateVocabulary

ALIGNMENT_VERSION = "m5-layoutlmv3-slot-alignment/0.1.0"
LABEL = re.compile(r"^[BI]-part_(\d+)_(description|action|quantity|amount|unit_price|remarks)$")
FIELDS = {"description": "description", "action": "operation", "quantity": "qty",
          "amount": "amount", "unit_price": "unit_price"}

# Encoding settings shared by training and serving. A checkpoint whose preprocessing.json
# records another value was trained on other tensors and is refused, not adapted.
MAX_LENGTH = 512  # tokens per window, special tokens included
STRIDE = 64  # tokens repeated at the start of the next overflow window
BBOX_SCALE = 1000  # LayoutLMv3 layout grid; coordinates are truncated and clipped to 0..999
TEXT_NORMALISATION = "collapse_whitespace"  # applied only for a checkpoint that declares it
LABEL_RULE = "first_subword"
TEXT_UNITS = ("word", "line", "block")
DEFAULT_TEXT_UNIT = "word"  # a checkpoint that declares nothing was trained on word boxes
PREPROCESSING_FILE = "preprocessing.json"
MANIFEST_FILE = "manifest.json"
UNREAD_LABEL = "O"  # reported, with confidence 0, for a box that has no text to tokenise


def serving_preprocessing(text_unit: str) -> dict[str, Any]:
    """What a registry entry's ``preprocessing.json`` must say about how this adapter encodes a page."""
    return {"text_unit_granularity": text_unit, "text_normalisation": TEXT_NORMALISATION,
            "max_length": MAX_LENGTH, "stride": STRIDE, "bbox_scale": BBOX_SCALE, "label_rule": LABEL_RULE}


def declared_preprocessing(model_dir: str | Path) -> dict[str, Any]:
    """The checkpoint's ``preprocessing.json``, or ``{}`` for a checkpoint saved without one."""
    path = Path(model_dir) / PREPROCESSING_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def declared_text_unit(model_dir: str | Path) -> str:
    """The granularity of the text boxes the checkpoint labels: ``word`` unless it declares another."""
    unit = declared_preprocessing(model_dir).get("text_unit_granularity", DEFAULT_TEXT_UNIT)
    if unit not in TEXT_UNITS:
        raise ValueError(f"{PREPROCESSING_FILE}: text_unit_granularity must be one of {TEXT_UNITS}, not {unit!r}")
    return unit


def layout_bbox(box_norm: Sequence[float]) -> tuple[int, int, int, int]:
    """An M4 ``box_norm`` (0 to 1 of the corrected page) on LayoutLMv3's integer 0..999 grid."""
    x0, y0, x1, y1 = (min(BBOX_SCALE - 1, max(0, int(value * BBOX_SCALE))) for value in box_norm)
    return x0, y0, x1, y1


@dataclass(frozen=True)
class PageWords:
    """One page as the model reads it: every text box in reading order, one "word" per box."""

    boxes: tuple[TextBox, ...]  # every box of the page, sorted by ``order_index``
    readable: tuple[int, ...]  # positions in ``boxes`` that are passed to the tokenizer
    texts: tuple[str, ...]  # text of the readable boxes, as the model reads it
    bboxes: tuple[tuple[int, int, int, int], ...]  # their layout boxes on the 0..999 grid


def page_words(page: DocumentPage, text_normalisation: str | None = TEXT_NORMALISATION) -> PageWords:
    """The page's boxes in reading order, with their text as the checkpoint was trained to read it.

    ``collapse_whitespace`` is what training applies and what a registry entry declares:
    runs of whitespace become one space, and a box without visible text gives no token, so
    it is listed apart. ``None`` is for a checkpoint that declares nothing: every box is
    passed with its text exactly as OCR wrote it, as this adapter always did for it.
    """
    boxes = tuple(sorted(page.text_boxes, key=lambda box: box.order_index))
    if text_normalisation is None:
        return PageWords(boxes, tuple(range(len(boxes))), tuple(box.text for box in boxes),
                         tuple(layout_bbox(box.box_norm) for box in boxes))
    if text_normalisation != TEXT_NORMALISATION:
        raise ValueError(f"Unknown text normalisation {text_normalisation!r}")
    texts = [" ".join(box.text.split()) for box in boxes]
    readable = tuple(index for index, text in enumerate(texts) if text)
    return PageWords(boxes, readable, tuple(texts[index] for index in readable),
                     tuple(layout_bbox(boxes[index].box_norm) for index in readable))


def encode_page(processor: Any, image: Image.Image, words: PageWords,
                word_labels: Sequence[int] | None = None) -> Any:
    """Tokenise one page into 512-token windows; the single processor call of training and serving.

    ``word_labels`` holds one class id (or -100) per readable box. Only the first subword of
    a box carries it: transformers 5 also labels the continuation subword an overflow window
    starts with, which serving never reads, so those positions are reset to -100 here.
    """
    import torch

    arguments: dict[str, Any] = dict(
        images=image, text=list(words.texts), boxes=[list(box) for box in words.bboxes],
        padding="max_length", truncation=True, max_length=MAX_LENGTH, stride=STRIDE,
        return_overflowing_tokens=True, return_offsets_mapping=True, return_tensors="pt")
    if word_labels is not None:
        if len(word_labels) != len(words.texts):
            raise ValueError("word_labels needs one entry per readable box")
        arguments["word_labels"] = [int(label) for label in word_labels]
    encoding = processor(**arguments)
    # With overflow windows the processor returns one image per window as a list, on both
    # transformers 4 and 5. Stack it at this documented batch boundary.
    if isinstance(encoding["pixel_values"], list):
        encoding["pixel_values"] = torch.stack([torch.as_tensor(value) for value in encoding["pixel_values"]])
    if word_labels is not None:
        encoding["labels"][encoding["offset_mapping"][..., 0] != 0] = -100
    return encoding


@dataclass(frozen=True)
class WordPrediction:
    box_id: str
    label: str
    confidence: float


class LocalLayoutLMv3:
    """Load once, process overflow windows sequentially, retain first-subword labels."""

    def __init__(self, model_dir: Path, device: str = "cpu"):
        import torch
        from transformers import AutoModelForTokenClassification, AutoProcessor

        model_dir = Path(model_dir)
        self.torch = torch
        self.device = device
        # The checkpoint says which text unit it labels and how its pages were encoded.
        self.preprocessing = declared_preprocessing(model_dir)
        self.text_unit = declared_text_unit(model_dir)
        applied = serving_preprocessing(self.text_unit)
        if "text_unit_granularity" in self.preprocessing:
            # An entry that names its text unit is a registry entry: it states its whole encoding.
            missing = [key for key in applied if key not in self.preprocessing]
            if missing:
                raise ValueError(f"{PREPROCESSING_FILE} names a text unit but does not declare {', '.join(missing)}; "
                                 "nothing about a registry entry's page encoding is defaulted")
        differing = [f"{key}: trained with {self.preprocessing[key]!r}, served with {value!r}"
                     for key, value in applied.items() if key in self.preprocessing and self.preprocessing[key] != value]
        if differing:
            raise ValueError("Checkpoint was trained with another page encoding (" + "; ".join(differing) + ")")
        # Whitespace is collapsed only when the checkpoint declares it. One that declares
        # nothing reads the raw box text, exactly as it did before text units were declared.
        self.text_normalisation = self.preprocessing.get("text_normalisation")
        # Hash the actual weights, rather than trusting the directory name, and fail closed
        # when the entry's own manifest records other weights.
        digest = hashlib.sha256()
        with (model_dir / "model.safetensors").open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        weights_sha256 = digest.hexdigest()
        manifest_path = model_dir / MANIFEST_FILE
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError(f"{MANIFEST_FILE} is not a JSON object; the registry entry is damaged and is not served")
            recorded = manifest.get("weights_sha256")
            if recorded and recorded != weights_sha256:
                raise ValueError(f"{MANIFEST_FILE} records weights {str(recorded)[:12]}… but model.safetensors is "
                                 f"{weights_sha256[:12]}…; the registry entry is damaged and is not served")
        self.processor = AutoProcessor.from_pretrained(
            str(model_dir), apply_ocr=False, local_files_only=True)
        self.model = AutoModelForTokenClassification.from_pretrained(
            str(model_dir), local_files_only=True).to(device).eval()
        if not self.processor.tokenizer.is_fast:
            raise ValueError("A fast tokenizer is required for source word alignment")
        if not any(LABEL.fullmatch(label) for label in self.model.config.id2label.values()):
            raise ValueError("Checkpoint does not contain the notebook's part_N field labels")
        self.model_version = "sha256:" + weights_sha256

    def encode(self, page: DocumentPage, image_path: Path) -> tuple[PageWords, Any]:
        """The page's boxes and the tensors the model reads for them (``None`` when no box has text)."""
        words = page_words(page, self.text_normalisation)
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        if image.size != (page.transform.corrected_width, page.transform.corrected_height):
            raise ValueError("Image dimensions differ from the M4 corrected coordinate frame")
        return words, (encode_page(self.processor, image, words) if words.readable else None)

    def predict(self, page: DocumentPage, image_path: Path) -> tuple[WordPrediction, ...]:
        boxes = sorted(page.text_boxes, key=lambda box: box.order_index)
        other = sorted({box.granularity for box in boxes} - {self.text_unit})
        if other:
            raise ValueError(f"This checkpoint labels {self.text_unit} boxes; the page has {'/'.join(other)} boxes. "
                             "Boxes are never split or merged to fit a model")
        if not boxes or page.quality.state == "unreadable":
            return ()
        words, encoding = self.encode(page, image_path)
        predictions = {}
        for window in range(len(encoding["input_ids"]) if encoding is not None else 0):
            inputs = {key: encoding[key][window:window + 1].to(self.device)
                      for key in ("input_ids", "attention_mask", "bbox", "pixel_values")}
            with self.torch.inference_mode():
                probabilities = self.model(**inputs).logits[0].softmax(-1)
            for token, word in enumerate(encoding.word_ids(batch_index=window)):
                if word is None or word in predictions:
                    continue
                # Training labels only the first subword; a window can start mid-word.
                if int(encoding["offset_mapping"][window, token, 0]) != 0:
                    continue
                score, label_id = probabilities[token].max(-1)
                label = self.model.config.id2label[int(label_id)]
                match = LABEL.fullmatch(label)
                if match and self.text_normalisation is not None:
                    field_suffix = f"_{match[2]}"
                    score = sum(
                        float(probabilities[token, idx])
                        for idx, candidate in self.model.config.id2label.items()
                        if LABEL.fullmatch(candidate) and candidate.endswith(field_suffix)
                    )
                predictions[word] = WordPrediction(
                    words.boxes[words.readable[word]].box_id, label, min(1.0, float(score)))
        if len(predictions) != len(words.readable):
            raise ValueError("Tokenization did not cover every OCR word; no partial success written")
        by_box = {words.readable[word]: prediction for word, prediction in predictions.items()}
        # A box with no visible text gave the model nothing to read: unassigned evidence, never omitted.
        # (Only with declared whitespace handling; otherwise every box is readable and was checked above.)
        return tuple(by_box.get(index) or WordPrediction(box.box_id, UNREAD_LABEL, 0.0)
                     for index, box in enumerate(words.boxes))


def adapt_predictions(pages, predictions, *, config: LayoutFamilyConfig,
                      vocabulary: EstimateVocabulary, family_id: str, job_key: str,
                      claim_id: str, input_revision: int, currency: str, cost_basis: str,
                      model_version: str, provenance: Provenance,
                      min_confidence: float = 0.5) -> ParseResult:
    """Return the existing M5 result type; never claim validated declaration coverage.

    The caller explicitly selects the reviewed layout's numeric conventions. All
    non-row words remain unparsed evidence. Row-slot grouping is a suggestion until
    evaluated; completeness therefore stays partial (or unreadable).
    """
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between zero and one")
    _check_inputs(pages, (), job_key, claim_id, input_revision, provenance)
    family = next((f for f in config.families if f.family_id == family_id), None)
    if family is None:
        raise ValueError(f"Unknown configured layout family: {family_id}")
    versions = {"extraction_method": "layoutlmv3", "line_items_model": model_version,
                "alignment": ALIGNMENT_VERSION, "layout_families": config.config_version,
                "vocabulary": vocabulary.version, "taxonomy": vocabulary.taxonomy_version}
    builder = _Builder(config, vocabulary, job_key, claim_id, input_revision,
                       currency, cost_basis, versions, provenance)
    rows, items, regions, page_results = [], [], [], []
    readable = False
    band_tol = max(family.band_tolerance(config.parser), config.parser.max_wrap_gap_frac)
    for page_index, page in enumerate(pages):
        source = {b.box_id: b for b in page_boxes(page)}
        predicted = predictions.get(page.page_id, ())
        ids = [p.box_id for p in predicted]
        if len(ids) != len(set(ids)) or set(ids) != set(source):
            if page.quality.state != "unreadable":
                raise ContractError("prediction_alignment_mismatch", "Need one prediction per OCR box")
        matched_by_box = {}
        unparsed_set = set()
        for prediction in predicted:
            if prediction.box_id not in source or not 0 <= prediction.confidence <= 1:
                raise ValueError("Invalid prediction source or confidence")
            match = LABEL.fullmatch(prediction.label)
            if match and match[2] in FIELDS:
                matched_by_box[prediction.box_id] = (prediction, FIELDS[match[2]])
            else:
                unparsed_set.add(prediction.box_id)
        candidate_bands = group_bands([source[box_id] for box_id in matched_by_box], band_tol)
        groups = {}
        slot = 0
        for band in candidate_bands:
            band_fields = [matched_by_box[b.box_id] for b in band.boxes]
            if any(field == "description" for _, field in band_fields):
                slot += 1
                groups[slot] = band_fields
            else:
                unparsed_set.update(b.box_id for b in band.boxes)
        unparsed = [p.box_id for p in predicted if p.box_id in unparsed_set]
        readable |= page.quality.state != "unreadable" and bool(source)
        if unparsed:
            regions.append(UnparsedRegion(page.page_id, page.page_number,
                                          "model_unassigned_text", None, tuple(unparsed)))
        for slot, fields in sorted(groups.items()):
            assignments = []
            for prediction, field in sorted(fields, key=lambda pair: source[pair[0].box_id].order_index):
                box = source[prediction.box_id]
                # Require both OCR and model confidence; keep the original M4 record intact.
                uncertain = (prediction.confidence < min_confidence or box.confidence is None
                             or box.confidence < config.parser.min_box_confidence)
                assignments.append(BoxAssignment(box, "assigned", field, (field,) if uncertain else ()))
            x0, _, x1, _ = union_box([a.box for a in assignments])
            binding = ColumnBinding(family_id, "model_labels", 0,
                tuple(ColumnInterval(f, x0, x1, x0, x1) for f in FIELDS.values()), 0)
            raw = RawRow(page_index, "item", (Band(slot, tuple(a.box for a in assignments)),),
                         tuple(assignments), binding, ("model_row_grouping_unvalidated",))
            row, item = builder.row(raw, page, family)
            # Builder's geometric uncertainty reason is replaced for label-based assignments.
            fields_out = tuple(replace(f, reason="model_or_ocr_confidence_low")
                               if f.reason in ("spanning_box", "overlapping_columns") else f for f in row.fields)
            row = replace(row, fields=fields_out)
            if item:
                item = item.model_copy(update={"field_uncertainty": [
                    u.model_copy(update={"reason": "model_or_ocr_confidence_low"})
                    if u.reason in ("spanning_box", "overlapping_columns") else u for u in item.field_uncertainty]})
                items.append(item)
            rows.append(row)
        page_results.append(PageParse(page.page_id, page.page_number, page.quality.state,
                                      "parsed" if groups else "page_without_table", family_id, None))
    reasons = ["model_declaration_coverage_unvalidated"] if readable else ["all_pages_unreadable"]
    if readable and not items:
        reasons.append("no_rows_matched")
    completeness = DeclarationCompleteness(
        claim_id=claim_id, input_revision=input_revision, provenance=provenance, versions=versions,
        state="partial" if readable else "unreadable", reasons=reasons,
        unparsed_region_count=len(regions), layout_family=family_id,
        pages_covered=[p.page_id for p in pages], source="parser")
    return ParseResult(tuple(items), completeness, tuple(regions), family_id,
                       tuple(rows), tuple(page_results), versions)
