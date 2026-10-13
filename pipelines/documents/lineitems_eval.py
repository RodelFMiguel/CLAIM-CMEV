"""Scores for the M5 line-item model, with their denominators, plus the plot helpers the notebook shares.

Three levels are scored, because they answer different questions:

- **boxes**: did each M4 text box get the right label? ``O`` is reported apart so that the
  many static-text boxes cannot inflate the headline. Masked boxes are not scored; they are
  counted.
- **fields and rows**: is a ground-truth field covered by exactly the boxes labelled with
  it, and is a whole table row labelled completely, slot included?
- **line items**: after the real serving code turned labels into ``LineItem`` records, do
  the printed amount, quantity and operation equal the claim's ground truth?

Every function takes plain alignments and predictions, so the same code scores validation
pages and, once and deliberately, the held-out test pages. Scores on synthetic pages are
synthetic evidence; nothing here says how the model reads a real estimate.

``position_baseline`` is the reference point for all of them: a lookup on the four box
coordinates alone, scored by the same functions. What it gets right, the layout gives away.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

import numpy as np

from claim_cmev.documents.line_items.layoutlmv3 import WordPrediction
from pipelines.documents.lineitems_data import (
    OUTSIDE,
    ROW_FIELD_TYPES,
    PageAlignment,
    field_name,
    field_type,
    ink_over,
    row_markings,
    row_slot,
)

CORE_ROW_FIELDS = ("description", "action", "quantity", "amount")  # what a complete row label needs
TYPE_ORDER = (*ROW_FIELD_TYPES, "page_field", OUTSIDE)
NO_SLOT = "no row"

# Okabe-Ito: eight colours that stay distinguishable with the common colour-vision deficiencies.
PALETTE = {"black": "#000000", "orange": "#E69F00", "sky": "#56B4E9", "green": "#009E73", "yellow": "#F0E442",
           "blue": "#0072B2", "vermillion": "#D55E00", "purple": "#CC79A7", "grey": "#8C8C8C"}
TYPE_COLOURS = {"quantity": PALETTE["purple"], "description": PALETTE["blue"], "action": PALETTE["green"],
                "amount": PALETTE["vermillion"], "remarks": PALETTE["orange"], "page_field": PALETTE["sky"],
                OUTSIDE: PALETTE["grey"], "masked": PALETTE["yellow"]}


# --------------------------------------------------------------------------- box level

def prf(true_positive: int, predicted: int, actual: int) -> dict[str, Any]:
    """Precision, recall and F1 with their counts; ``None`` where a denominator is zero.

    F1 is ``2 TP / (predicted + actual)``: 0 for a class that was never predicted although it
    occurs, and undefined only for a class that neither occurs nor was predicted.
    """
    precision = true_positive / predicted if predicted else None
    recall = true_positive / actual if actual else None
    f1 = 2 * true_positive / (predicted + actual) if predicted + actual else None
    return {"true_positive": true_positive, "predicted": predicted, "support": actual,
            "precision": precision, "recall": recall, "f1": f1}


def box_rows(alignments: Mapping[str, PageAlignment], predictions: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """One row per M4 box of the aligned pages: its training label, the prediction and whether it is scored.

    ``predictions[image_id]`` holds one object with ``box_id``, ``label`` and ``confidence``
    per box. A page with boxes but without a complete set of predictions is an error, never
    a silently shorter table.
    """
    rows: list[dict[str, Any]] = []
    for image_id, alignment in alignments.items():
        if not alignment.boxes:
            continue
        predicted = {p.box_id: p for p in predictions.get(image_id, ())}
        if set(predicted) != {box.box_id for box in alignment.boxes}:
            raise ValueError(f"{image_id}: need exactly one prediction per text box")
        for box in alignment.boxes:
            prediction = predicted[box.box_id]
            pred_field = field_name(prediction.label)
            true_field = box.field if box.outcome == "field" else None
            rows.append({
                "image_id": image_id, "split": alignment.split, "page_type": alignment.page_type,
                "box_id": box.box_id, "order_index": box.order_index, "text": box.text,
                "ocr_confidence": box.confidence, "outcome": box.outcome, "reason": box.reason,
                "scored": box.label is not None, "true": box.label, "pred": prediction.label,
                "confidence": float(prediction.confidence), "correct": box.label == prediction.label,
                "true_field": true_field, "pred_field": pred_field,
                "true_type": field_type(true_field), "pred_type": field_type(pred_field),
                "true_slot": row_slot(true_field), "pred_slot": row_slot(pred_field)})
    return rows


def label_metrics(rows: Sequence[Mapping[str, Any]], true_key: str = "true", pred_key: str = "pred") -> list[dict[str, Any]]:
    """Precision, recall and F1 of every class that occurs as a truth or a prediction among scored boxes."""
    scored = [row for row in rows if row["scored"]]
    truth, predicted = Counter(row[true_key] for row in scored), Counter(row[pred_key] for row in scored)
    hits = Counter(row[true_key] for row in scored if row[true_key] == row[pred_key])
    return [{"label": name, **prf(hits[name], predicted[name], truth[name])}
            for name in sorted(set(truth) | set(predicted), key=str)]


def box_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Headline box scores: field labels micro and macro, ``O`` apart, and what was not scored."""
    scored = [row for row in rows if row["scored"]]
    per_label = label_metrics(rows)
    fields = [m for m in per_label if m["label"] != OUTSIDE]
    micro = prf(sum(m["true_positive"] for m in fields), sum(m["predicted"] for m in fields),
                sum(m["support"] for m in fields))
    supported = [m["f1"] for m in fields if m["support"]]
    outside = next((m for m in per_label if m["label"] == OUTSIDE), prf(0, 0, 0))
    return {"boxes": len(rows), "scored_boxes": len(scored), "masked_boxes": len(rows) - len(scored),
            "accuracy_all_scored": sum(row["correct"] for row in scored) / len(scored) if scored else None,
            "field_micro": micro,
            "field_macro_f1": float(np.mean(supported)) if supported else None,
            "field_labels_with_support": len(supported),
            "outside": {key: outside[key] for key in ("true_positive", "predicted", "support", "precision", "recall", "f1")}}


def type_metrics(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Scores per field type (row slot and B/I prefix ignored), in a fixed order."""
    by_type = {m["label"]: m for m in label_metrics(rows, "true_type", "pred_type")}
    return [by_type[name] for name in TYPE_ORDER if name in by_type]


def type_confusion(rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[str]]:
    """Counts ``[true type, predicted type]`` over scored boxes."""
    names = list(TYPE_ORDER)
    matrix = np.zeros((len(names), len(names)), dtype=np.int64)
    for row in rows:
        if row["scored"]:
            matrix[names.index(row["true_type"]), names.index(row["pred_type"])] += 1
    return matrix, names


def slot_confusion(rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[str]]:
    """Counts ``[true row slot, predicted row slot]`` over scored boxes whose truth is a table cell."""
    slots = sorted({s for row in rows for s in (row["true_slot"], row["pred_slot"]) if s is not None})
    names = [str(slot) for slot in slots] + [NO_SLOT]
    matrix = np.zeros((len(slots), len(names)), dtype=np.int64)
    for row in rows:
        if row["scored"] and row["true_slot"] is not None:
            column = slots.index(row["pred_slot"]) if row["pred_slot"] is not None else len(slots)
            matrix[slots.index(row["true_slot"]), column] += 1
    return matrix, names


def reliability(rows: Sequence[Mapping[str, Any]], bins: int = 10) -> list[dict[str, Any]]:
    """Equal-width confidence bins over scored boxes: count, mean confidence and share correct."""
    scored = [row for row in rows if row["scored"]]
    edges = np.linspace(0.0, 1.0, bins + 1)
    table = []
    for low, high in zip(edges[:-1], edges[1:]):
        members = [row for row in scored if low < row["confidence"] <= high or (low == 0.0 and row["confidence"] == 0.0)]
        table.append({"low": float(low), "high": float(high), "boxes": len(members),
                      "mean_confidence": float(np.mean([m["confidence"] for m in members])) if members else None,
                      "accuracy": float(np.mean([m["correct"] for m in members])) if members else None})
    return table


def expected_calibration_error(table: Sequence[Mapping[str, Any]]) -> float | None:
    """Count-weighted mean gap between confidence and accuracy over the bins of ``reliability``."""
    total = sum(row["boxes"] for row in table)
    if not total:
        return None
    return sum(row["boxes"] * abs(row["mean_confidence"] - row["accuracy"]) for row in table if row["boxes"]) / total


# --------------------------------------------------------------------------- the trivial baseline

def position_baseline(train_pages: Sequence[Any], train_alignments: Mapping[str, PageAlignment],
                      pages: Sequence[Any]) -> dict[str, tuple[WordPrediction, ...]]:
    """Label every box of ``pages`` with the label of the nearest training box: four coordinates, nothing else.

    No text, no image and no model. A box is its ``box_norm`` (x0, y0, x1, y1 as shares of
    the corrected page) and takes the training label of the labelled training box at the
    smallest squared distance; a tie goes to the first in training order. Masked training
    boxes have no label and are not candidates. The result has the shape of model
    predictions (one per box, with a placeholder confidence of 1), so the same scoring
    functions apply to it. A high score here means the page layout alone gives the labels
    away; it is the number a model's score on the same pages has to be read against.
    """
    def coordinates(page: Any) -> np.ndarray:
        boxes = sorted(page.page.text_boxes, key=lambda box: box.order_index) if page.page is not None else []
        return np.array([box.box_norm for box in boxes], dtype=float).reshape(-1, 4)

    known: list[np.ndarray] = []
    known_labels: list[str] = []
    for page in train_pages:
        labels = [box.label for box in train_alignments[page.image_id].boxes]
        points = coordinates(page)
        if len(points) != len(labels):
            raise ValueError(f"{page.image_id}: the alignment does not describe the page's text boxes")
        keep = [index for index, label in enumerate(labels) if label is not None]
        known.append(points[keep])
        known_labels += [labels[index] for index in keep]
    if not known_labels:
        raise ValueError("the baseline has no labelled training box to look up")
    reference = np.concatenate(known)
    predictions: dict[str, tuple[WordPrediction, ...]] = {}
    for page in pages:
        points = coordinates(page)
        if not len(points):
            predictions[page.image_id] = ()
            continue
        nearest = ((points[:, None, :] - reference[None, :, :]) ** 2).sum(axis=-1).argmin(axis=1)
        ordered = sorted(page.page.text_boxes, key=lambda box: box.order_index)
        predictions[page.image_id] = tuple(WordPrediction(box.box_id, known_labels[int(index)], 1.0)
                                           for box, index in zip(ordered, nearest))
    return predictions


# --------------------------------------------------------------------------- field and row level

def _predicted_fields(predictions: Sequence[Any]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for prediction in predictions:
        name = field_name(prediction.label)
        if name is not None:
            found.setdefault(name, set()).add(prediction.box_id)
    return found


def entity_scores(alignments: Mapping[str, PageAlignment], predictions: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    """Field-level ("entity") precision, recall and F1.

    A true entity is a matched ground-truth field: a label plus the set of boxes that carry
    it. A predicted entity is, per page, a field label plus the set of boxes predicted with
    it (``B-`` or ``I-``). It counts as correct only when label and box set are identical.
    Masked boxes belong to neither side. Fields M4 delivered no box for cannot be found by
    any labeller; they are counted in ``fields_without_box``, outside the recall denominator.
    """
    correct = predicted_total = true_total = without_box = 0
    for image_id, alignment in alignments.items():
        masked = {box.box_id for box in alignment.boxes if box.outcome == "masked"}
        truth = {match.label: set(match.box_ids) for match in alignment.fields if match.status == "matched"}
        without_box += sum(match.status != "matched" for match in alignment.fields)
        predicted = {name: boxes - masked for name, boxes in _predicted_fields(predictions.get(image_id, ())).items()}
        predicted = {name: boxes for name, boxes in predicted.items() if boxes}
        true_total += len(truth)
        predicted_total += len(predicted)
        correct += sum(predicted.get(name) == boxes for name, boxes in truth.items())
    return {**prf(correct, predicted_total, true_total), "fields_without_box": without_box}


def row_rows(alignments: Mapping[str, PageAlignment], predictions: Mapping[str, Sequence[Any]],
             annotations: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per ground-truth table row of every estimate page: is each core field labelled right?

    A field is *right* when the boxes predicted ``part_N_<field>`` are exactly the boxes
    aligned to it. For a field M4 delivered no box for (or that the page does not print),
    *right* is undefined and a box carrying its label is a *stray*. ``complete_available``
    needs every delivered core field right, no stray and no ambiguous field;
    ``complete_all_four`` also needs all four delivered. Rows of a page M4 could not read
    fail both.
    """
    rows: list[dict[str, Any]] = []
    for image_id, alignment in alignments.items():
        if alignment.page_type != "repair_estimate":
            continue
        markings = row_markings(annotations[image_id])
        predicted = _predicted_fields(predictions.get(image_id, ()))
        for slot in sorted({row_slot(match.label) for match in alignment.fields} - {None}):
            marking = markings.get(f"part_{slot}", {})
            row: dict[str, Any] = {"image_id": image_id, "split": alignment.split, "slot": slot,
                                   "page_status": alignment.page_status,
                                   "row_style": marking.get("style", "unmarked"),
                                   "row_action": marking.get("action")}
            delivered = right = strays = 0
            blocked = False
            for kind in CORE_ROW_FIELDS:
                name = f"part_{slot}_{kind}"
                match = alignment.field(name)
                status = match.status if match is not None else "not_printed"
                guess = predicted.get(name, set())
                is_right = None
                if status == "matched":
                    delivered += 1
                    is_right = guess == set(match.box_ids)
                    right += is_right
                elif status in ("missed", "not_printed"):
                    strays += bool(guess)
                else:
                    blocked = True
                row[f"{kind}_status"], row[f"{kind}_right"] = status, is_right
            row.update(fields_delivered=delivered, fields_right=right, stray_labels=strays,
                       complete_available=(not blocked and delivered > 0 and right == delivered and strays == 0),
                       complete_all_four=(not blocked and delivered == len(CORE_ROW_FIELDS) and right == delivered))
            rows.append(row)
    return rows


def row_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts behind the row-level statements, every one over all ground-truth rows."""
    total = len(rows)
    return {"rows": total,
            "rows_on_unreadable_pages": sum(row["page_status"] == "unreadable" for row in rows),
            "complete_available": sum(bool(row["complete_available"]) for row in rows),
            "complete_all_four": sum(bool(row["complete_all_four"]) for row in rows),
            "rows_with_all_four_delivered": sum(row["fields_delivered"] == len(CORE_ROW_FIELDS) for row in rows),
            **{f"{kind}_delivered": sum(row[f"{kind}_status"] == "matched" for row in rows) for kind in CORE_ROW_FIELDS},
            **{f"{kind}_right": sum(row[f"{kind}_right"] is True for row in rows) for kind in CORE_ROW_FIELDS}}


def validation_report(alignments: Mapping[str, PageAlignment], predictions: Mapping[str, Sequence[Any]],
                      annotations: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Everything scored from labels alone, as one JSON-ready report with denominators."""
    rows = box_rows(alignments, predictions)
    pages = list(alignments.values())
    return {"pages": len(pages), "pages_unreadable": sum(not page.boxes for page in pages),
            "splits": sorted({page.split for page in pages}),
            "boxes": box_summary(rows), "entities": entity_scores(alignments, predictions),
            "rows": row_summary(row_rows(alignments, predictions, annotations)),
            "per_type": type_metrics(rows), "per_label": label_metrics(rows)}


# --------------------------------------------------------------------------- line items (end to end)

def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "")) if value is not None else None
    except InvalidOperation:
        return None


def line_item_rows(result: Any, page_id: str, alignment: PageAlignment, annotation: Mapping[str, Any],
                   claim: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compare the ``LineItem`` records of one estimate page with the claim's ground truth, row by row.

    ``result`` is the ``ParseResult`` of ``adapt_predictions``; a line item belongs to the
    row slot stored as its parsed row's ``row_band_index``. The printed amount must equal
    ``line_total`` exactly as a decimal. A row without a line item is a miss, and a line
    item whose slot is not a true row is returned with ``extra=True``.

    For the quantity the row also says whether M4 delivered a box for the cell at all, what
    OCR read in it and why the record's quantity is null when it is: a null quantity (no
    box, or a text the parser refused) and a wrong accepted quantity are different outcomes.
    """
    parsed = {row.entry_id: row for row in result.rows if row.page_id == page_id}
    items = {parsed[item.entry_id].row_band_index: item for item in result.line_items if item.entry_id in parsed}
    regions = {region["label"]: region for region in annotation["regions"]}
    markings = row_markings(annotation)
    by_id = {box.box_id: box for box in alignment.boxes}
    rows: list[dict[str, Any]] = []
    for slot, part in enumerate(claim["parts"], start=1):
        printed = regions.get(f"part_{slot}_amount")
        truth = _decimal(part["line_total"])
        if printed is not None and _decimal(printed["text"]) != truth:
            raise ValueError(f"{alignment.image_id}: row {slot} of the annotations and the claim record disagree")
        item = items.get(slot)
        description = regions.get(f"part_{slot}_description", {}).get("text", "")
        quantity_field = alignment.field(f"part_{slot}_quantity")
        quantity_boxes = [by_id[box_id] for box_id in quantity_field.box_ids] \
            if quantity_field is not None and quantity_field.status == "matched" else []
        quantity_confidences = [box.confidence for box in quantity_boxes if box.confidence is not None]
        row: dict[str, Any] = {
            "image_id": alignment.image_id, "slot": slot, "extra": False, "found": item is not None,
            "page_status": alignment.page_status,
            "row_style": markings.get(f"part_{slot}", {}).get("style", "unmarked"),
            "ink_over_amount": ink_over(printed["bbox"], annotation) if printed is not None else None,
            "true_amount": str(truth), "true_quantity": part["quantity"], "true_operation": part["action"],
            "printed_description": description, "amount": None, "amount_text": None, "amount_exact": False,
            "quantity": None, "quantity_match": False, "quantity_delivered": bool(quantity_boxes),
            "quantity_text": " ".join(box.text for box in quantity_boxes) if quantity_boxes else None,
            "quantity_ocr_confidence": min(quantity_confidences) if quantity_confidences else None,
            "quantity_reason": None, "operation": None, "operation_match": False,
            "description": None, "description_similarity": None, "part_code": None, "part_mapped": False}
        if item is not None:
            read_amount, read_quantity = _decimal(item.printed_line_amount), _decimal(item.quantity)
            row.update(
                amount=item.printed_line_amount, amount_text=item.original_amount_text,
                amount_exact=read_amount is not None and read_amount == truth,
                quantity=item.quantity, quantity_match=read_quantity is not None and read_quantity == part["quantity"],
                quantity_reason=", ".join(u.reason for u in item.field_uncertainty if u.field == "quantity") or None,
                operation=item.operation, operation_match=item.operation == part["action"],
                description=item.original_part_text,
                description_similarity=SequenceMatcher(None, description.lower(), item.original_part_text.lower()).ratio(),
                part_code=item.part_code, part_mapped=item.part_code is not None)
        rows.append(row)
    for slot, item in sorted(items.items()):
        if slot > len(claim["parts"]):
            rows.append({"image_id": alignment.image_id, "slot": slot, "extra": True, "found": True,
                         "page_status": alignment.page_status, "amount": item.printed_line_amount,
                         "description": item.original_part_text})
    return rows


def line_item_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts over the true rows (``extra`` line items are counted apart)."""
    true_rows = [row for row in rows if not row["extra"]]
    found = [row for row in true_rows if row["found"]]

    def amounts(members: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        return {"rows": len(members), "amount_exact": sum(bool(row["amount_exact"]) for row in members)}

    similarities = [row["description_similarity"] for row in found]
    return {"true_rows": len(true_rows), "line_items_found": len(found),
            "extra_line_items": sum(bool(row["extra"]) for row in rows),
            "rows_on_unreadable_pages": sum(row["page_status"] == "unreadable" for row in true_rows),
            "amount_exact": sum(bool(row["amount_exact"]) for row in true_rows),
            "amount_exact_clean": amounts([row for row in true_rows if row["ink_over_amount"] is False]),
            "amount_exact_ink_over": amounts([row for row in true_rows if row["ink_over_amount"]]),
            "quantity_match": sum(bool(row["quantity_match"]) for row in true_rows),
            "quantity_boxes_delivered": sum(bool(row["quantity_delivered"]) for row in true_rows),
            "quantity_wrong_accepted": sum(row["quantity"] is not None and not row["quantity_match"] for row in found),
            "quantity_null": sum(row["quantity"] is None for row in found),
            "operation_match": sum(bool(row["operation_match"]) for row in true_rows),
            "part_mapped": sum(bool(row["part_mapped"]) for row in true_rows),
            "description_similarity_mean": float(np.mean(similarities)) if similarities else None,
            "description_similarity_rows": len(similarities)}


# --------------------------------------------------------------------------- plot helpers

def style_axes() -> None:
    """One look for every figure of the notebook: readable sizes, light grid, colour-blind-safe cycle."""
    import matplotlib as mpl
    from cycler import cycler

    mpl.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 130, "font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10,
        "legend.fontsize": 9, "xtick.labelsize": 9, "ytick.labelsize": 9, "axes.grid": True, "grid.alpha": 0.25,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.prop_cycle": cycler(color=[PALETTE[name] for name in ("blue", "vermillion", "green", "orange", "purple",
                                                                    "sky", "black", "yellow")])})


def draw_boxes(axis: Any, image: Any, boxes_px: Sequence[Sequence[float]], colours: Sequence[str], *,
               texts: Sequence[str | None] | None = None, linewidth: float = 1.4, fill_alpha: float = 0.0,
               text_size: float = 6.0, extent: Sequence[float] | None = None) -> None:
    """Show a page (or a crop of it, with ``extent`` as ``x0, y0, x1, y1``) with one rectangle per box."""
    from matplotlib.colors import to_rgb
    from matplotlib.patches import Rectangle

    axis.imshow(image)
    for index, (box, colour) in enumerate(zip(boxes_px, colours)):
        x0, y0, x1, y1 = box
        face = (*to_rgb(colour), fill_alpha) if fill_alpha else "none"
        axis.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=face, edgecolor=colour, linewidth=linewidth))
        if texts is not None and texts[index]:
            axis.text(x0, y0 - 2, texts[index], fontsize=text_size, color=colour, va="bottom", ha="left",
                      clip_on=True, bbox={"facecolor": "white", "alpha": 0.75, "pad": 0.4, "edgecolor": "none"})
    if extent is not None:
        axis.set_xlim(extent[0], extent[2])
        axis.set_ylim(extent[3], extent[1])
    axis.set_xticks([])
    axis.set_yticks([])
    axis.grid(False)
    for spine in axis.spines.values():
        spine.set_visible(False)


def plot_confusion(axis: Any, matrix: np.ndarray, row_names: Sequence[str], column_names: Sequence[str], *,
                   title: str, xlabel: str, ylabel: str) -> None:
    """A count matrix as a heat map: colour is the share of each true row, the number is the count."""
    totals = matrix.sum(axis=1, keepdims=True)
    share = np.divide(matrix, totals, out=np.zeros(matrix.shape), where=totals > 0)
    axis.imshow(share, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    axis.set_xticks(range(len(column_names)), column_names, rotation=35, ha="right")
    axis.set_yticks(range(len(row_names)), row_names)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            if matrix[i, j]:
                axis.text(j, i, f"{matrix[i, j]:,}", ha="center", va="center", fontsize=8,
                          color="white" if share[i, j] > 0.55 else "black")
    axis.set(title=title, xlabel=xlabel, ylabel=ylabel)
    axis.grid(False)


__all__ = ["CORE_ROW_FIELDS", "NO_SLOT", "PALETTE", "TYPE_COLOURS", "TYPE_ORDER", "box_rows", "box_summary",
           "draw_boxes", "entity_scores", "expected_calibration_error", "label_metrics", "line_item_rows",
           "line_item_summary", "plot_confusion", "position_baseline", "prf", "reliability", "row_rows", "row_summary", "slot_confusion",
           "style_axes", "type_confusion", "type_metrics", "validation_report"]
