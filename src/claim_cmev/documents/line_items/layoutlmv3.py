"""Explicit, local-only S1 inference and conservative M5 contract adapter.

OCR must supply real word boxes on the corrected image. Model row-slot labels
are page-local; they are never used as part codes or globally unique entry IDs.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import re

from PIL import Image

from claim_cmev.contracts.common import ContractError, Provenance
from claim_cmev.contracts.documents import DeclarationCompleteness, DocumentPage
from .config import LayoutFamilyConfig
from .parser import ParseResult, PageParse, UnparsedRegion, _Builder, _check_inputs
from .table import Band, BoxAssignment, ColumnBinding, ColumnInterval, RawRow, page_boxes, union_box
from .vocabulary import EstimateVocabulary

ALIGNMENT_VERSION = "m5-layoutlmv3-slot-alignment/0.1.0"
LABEL = re.compile(r"^[BI]-part_(\d+)_(description|action|quantity|amount|unit_price|remarks)$")
FIELDS = {"description": "description", "action": "operation", "quantity": "qty",
          "amount": "amount", "unit_price": "unit_price"}


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

        self.torch = torch
        self.device = device
        self.processor = AutoProcessor.from_pretrained(
            str(model_dir), apply_ocr=False, local_files_only=True)
        self.model = AutoModelForTokenClassification.from_pretrained(
            str(model_dir), local_files_only=True).to(device).eval()
        if not self.processor.tokenizer.is_fast:
            raise ValueError("A fast tokenizer is required for source word alignment")
        if not any(LABEL.fullmatch(label) for label in self.model.config.id2label.values()):
            raise ValueError("Checkpoint does not contain the notebook's part_N field labels")
        # Hash the actual weights, rather than trusting the directory name.
        digest = hashlib.sha256()
        with (model_dir / "model.safetensors").open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        self.model_version = "sha256:" + digest.hexdigest()

    def predict(self, page: DocumentPage, image_path: Path) -> tuple[WordPrediction, ...]:
        boxes = sorted(page.text_boxes, key=lambda box: box.order_index)
        if any(box.granularity != "word" for box in boxes):
            raise ValueError("LayoutLMv3 requires word OCR; line/block boxes must not be split")
        if not boxes or page.quality.state == "unreadable":
            return ()
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        if image.size != (page.transform.corrected_width, page.transform.corrected_height):
            raise ValueError("Image dimensions differ from the M4 corrected coordinate frame")
        encoding = self.processor(
            images=image, text=[box.text for box in boxes],
            boxes=[[min(999, max(0, int(value * 1000))) for value in box.box_norm]
                   for box in boxes],
            padding="max_length", truncation=True, max_length=512, stride=64,
            return_overflowing_tokens=True, return_offsets_mapping=True, return_tensors="pt")
        # Some Transformers 4.x processors expand overflow images into a list even
        # with return_tensors='pt'. Normalize that documented batch boundary here.
        if isinstance(encoding["pixel_values"], list):
            encoding["pixel_values"] = self.torch.stack([
                self.torch.as_tensor(value) for value in encoding["pixel_values"]])
        predictions = {}
        for window in range(len(encoding["input_ids"])):
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
                predictions[word] = WordPrediction(
                    boxes[word].box_id, self.model.config.id2label[int(label_id)], float(score))
        if len(predictions) != len(boxes):
            raise ValueError("Tokenization did not cover every OCR word; no partial success written")
        return tuple(predictions[index] for index in range(len(boxes)))


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
    for page_index, page in enumerate(pages):
        source = {b.box_id: b for b in page_boxes(page)}
        predicted = predictions.get(page.page_id, ())
        ids = [p.box_id for p in predicted]
        if len(ids) != len(set(ids)) or set(ids) != set(source):
            if page.quality.state != "unreadable":
                raise ContractError("prediction_alignment_mismatch", "Need one prediction per OCR box")
        groups = defaultdict(list)
        unparsed = []
        for prediction in predicted:
            if prediction.box_id not in source or not 0 <= prediction.confidence <= 1:
                raise ValueError("Invalid prediction source or confidence")
            match = LABEL.fullmatch(prediction.label)
            if match and match[2] in FIELDS:
                groups[int(match[1])].append((prediction, FIELDS[match[2]]))
            else:
                unparsed.append(prediction.box_id)
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
