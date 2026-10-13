#!/usr/bin/env python3
"""Overlay reproducible synthetic surveyor ink; preserve source labels separately.

Uses Pillow only. Output is synthetic supervision, never a human confirmation.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import random
import shutil
import platform
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import PIL
from PIL import Image, ImageDraw, ImageFont

VERSION = "surveyor-ink-v1"
FONTS = ["/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
         "/System/Library/Fonts/Noteworthy.ttc", "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


class Ink:
    def __init__(self, image, rng, fonts):
        self.layer = Image.new("RGBA", image.size)
        self.draw = ImageDraw.Draw(self.layer)
        self.rng = rng
        self.font_path = rng.choice(fonts)
        self.color = rng.choice([(18, 48, 145, 235), (25, 69, 163, 235), (34, 34, 44, 235)])
        self.words = []

    def stroke(self, points):
        # Each stroke has its own measured raster box; no artificial word boxes.
        layer = Image.new("RGBA", self.layer.size)
        draw = ImageDraw.Draw(layer)
        draw.line(points, fill=self.color, width=self.rng.randint(2, 4), joint="curve")
        self.layer.alpha_composite(layer)
        return list(layer.getbbox())

    def text(self, text, x, y, max_width=158):
        size = self.rng.randint(19, 24)
        font = ImageFont.truetype(self.font_path, size)
        while font.getlength(text) > max_width and size > 12:
            size -= 1
            font = ImageFont.truetype(self.font_path, size)
        layer = Image.new("RGBA", self.layer.size)
        draw = ImageDraw.Draw(layer)
        boxes = []
        # Render word by word and record actual ink extents (synthetic words only).
        for word in text.split():
            word_layer = Image.new("RGBA", self.layer.size)
            ImageDraw.Draw(word_layer).text((int(x), y), word, font=font,
                                            fill=self.color, stroke_width=0)
            box = word_layer.getbbox()
            if box:
                boxes.append(list(box))
                self.words.append({"text": word, "bbox": list(box), "geometry_source": "rendered_ink", "origin": "surveyor_annotation", "m5_intended_field": "O"})
            layer.alpha_composite(word_layer)
            x += font.getlength(word + " ")
        self.layer.alpha_composite(layer)
        return union(boxes)


def annotate(image, ann, claim, rng, fonts):
    ink = Ink(image, rng, fonts)
    marks = []
    decisions = []
    regions = {r["label"]: r for r in ann["regions"]}

    def add(cls, style, boxes, row=None, text=None, revised=None, original=None):
        marks.append({"mark_id": f"{ann['image_id']}-mark-{len(marks)+1:02d}",
                      "class": cls, "style": style, "bbox": union(boxes),
                      "component_bboxes": boxes, "target_row_id": f"part_{row}" if row else None,
                      "target_entry_id": f"{ann['image_id']}:part_{row}" if row else None,
                      "text": text, "revised_amount": revised, "original_amount": original,
                      "currency": "SGD" if original else None,
                      "amount_basis": "line_total_pre_tax_excluding_labour" if original else None,
                      "provenance": "synthetic_intended_action", "confirmation_status": "not_human_confirmed"})

    if ann["page_type"] != "repair_estimate":
        note = rng.choice(["Reviewed", "See marked estimate", "Survey checked", "Refer to page 2"])
        add("review_note", "handwritten_note", [ink.text(note, 82, 1505, 420)], text=note)
    else:
        count = len(claim["parts"])
        order = list(range(1, count + 1))
        rng.shuffle(order)
        # Most estimates mix exclusions, repricing and accepted/untouched examples.
        actions = {order[0]: "exclusion", order[1]: "price_change", order[2]: "accepted"}
        negative_page = rng.random() < .10
        for row in range(1, count + 1):
            action = actions.get(row, rng.choices(["exclusion", "price_change", "accepted", "unmarked"], [22, 25, 35, 18])[0])
            if negative_page:
                action = rng.choice(["accepted", "unmarked"])
            top = 477 + (row - 1) * 79
            amount = regions[f"part_{row}_amount"]["bbox"]
            description = regions[f"part_{row}_description"]["bbox"]
            cy = int((description[1] + description[3]) / 2)
            original = format(Decimal(str(claim["parts"][row-1]["line_total"])), ".2f")
            revised = None
            mark_start = len(marks)
            if action == "exclusion":
                style = rng.choice(["row_strike", "x", "remark", "row_strike_and_remark", "x_and_remark"])
                boxes = []
                if "row_strike" in style:
                    boxes.append(ink.stroke([(210, cy+2), (470, cy-2+rng.randint(-3,3)), (975, cy+3)]))
                if "x" in style:
                    x = rng.choice([650, 950])
                    boxes.extend([ink.stroke([(x-8, cy-9), (x+9, cy+9)]), ink.stroke([(x+8, cy-10), (x-8, cy+10)])])
                note = None
                if "remark" in style:
                    note = rng.choice(["Not accepted", "Not allowed", "Exclude", "Rejected"])
                    boxes.append(ink.text(note, 998, top+38))
                add("exclusion", style, boxes, row, note, original=original)
            elif action == "price_change":
                revised_value = (Decimal(original) * Decimal(str(rng.uniform(.60, .90))) / Decimal(5)).quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(5)
                revised = format(max(Decimal(5), revised_value), ".2f")
                ax1, ay1, ax2, ay2 = amount
                mid = (ay1+ay2)//2
                style = rng.choice(["amount_strike", "amount_cross"])
                boxes = [ink.stroke([(ax1-3, mid+2), (ax2+4, mid-2)])]
                if style == "amount_cross":
                    boxes.append(ink.stroke([(ax1-2, ay1-3), (ax2+2, ay2+3)]))
                note = rng.choice([f"Allow {revised}", f"Nett {revised}", f"SGD {revised}"])
                boxes.append(ink.text(note, 998, top+38))
                add("price_change", style, boxes, row, note, revised, original)
            elif action == "accepted":
                x = 652
                boxes = [ink.stroke([(x-9, cy), (x-3, cy+7), (x+13, cy-12)])]
                add("acceptance_tick", "tick", boxes, row, original=original)
            decisions.append({"row_id": f"part_{row}", "row_bbox": [70, top, 1170, top+79],
                              "synthetic_action": action, "declared_amount": original,
                              "surveyor_revised_amount": revised, "final_approved_amount": None,
                              "mark_ids": [m["mark_id"] for m in marks[mark_start:]]})
    if not marks:
        add("review_note", "handwritten_note", [ink.text("Reviewed", 82, 1160, 420)], text="Reviewed")
    for word in ink.words:
        matches = [m for m in marks if m["bbox"][0] <= word["bbox"][0] and m["bbox"][1] <= word["bbox"][1] and m["bbox"][2] >= word["bbox"][2] and m["bbox"][3] >= word["bbox"][3]]
        if matches:
            word["mark_id"] = matches[0]["mark_id"]
            word["target_entry_id"] = matches[0]["target_entry_id"]
    out = Image.alpha_composite(image.convert("RGBA"), ink.layer).convert("RGB")
    return out, marks, decisions, ink.words, ink.font_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/raw/synthetic_motor_claims_500"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/synthetic_motor_claims_500_w_annotation"))
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument("--font", type=Path, action="append")
    args = parser.parse_args()
    fonts = [str(p) for p in args.font] if args.font else [p for p in FONTS if Path(p).exists()]
    if not fonts:
        parser.error("No handwriting fonts available; supply --font PATH")
    if args.output.exists():
        parser.error("Output already exists; choose a new output to preserve prior data")
    annotations = [json.loads(line) for line in (args.source / "annotations.jsonl").read_text().splitlines()]
    claims = {c["claim_id"]: c for c in map(json.loads, (args.source / "claims.jsonl").read_text().splitlines())}
    for ann in annotations:
        if not (args.source / ann["file_name"]).is_file():
            parser.error(f"Missing image: {ann['file_name']}")
    args.output.mkdir(parents=True)
    results = []
    manifest = []
    coco = {split: {"images": [], "annotations": [], "categories": [{"id": 1, "name": "exclusion"}, {"id": 2, "name": "price_change"}]} for split in ["train", "validation", "test"]}
    for image_index, ann in enumerate(annotations, 1):
        src = args.source / ann["file_name"]
        dst = args.output / ann["file_name"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        page_seed = int(hashlib.sha256(f"{args.seed}:{ann['image_id']}".encode()).hexdigest()[:16], 16)
        with Image.open(src) as source_image:
            image = source_image.convert("RGB")
        out, marks, decisions, words, used_font = annotate(image, ann, claims[ann["claim_id"]], random.Random(page_seed), fonts)
        out.save(dst, "JPEG", quality=95, subsampling=0)
        result = dict(ann)
        result.update({"source_file_name": ann["file_name"], "source_sha256": digest(src), "output_sha256": digest(dst),
                       "generator_version": VERSION, "page_seed": page_seed, "font_file": used_font,
                       "marks": marks, "row_decisions": decisions, "handwritten_words": words,
                       "source_box_granularity": "field_value", "source_regions_unchanged": True,
                       "surveyor_regions": [{"label": m["class"], "text": m["text"], "bbox": m["bbox"], "mark_id": m["mark_id"]} for m in marks],
                       "source_amount_occlusions": [{"mark_id": m["mark_id"], "row_id": m["target_row_id"]} for m in marks if m["class"] == "price_change"]})
        results.append(result)
        manifest.append({k: result[k] for k in ["image_id", "claim_id", "split", "file_name", "width", "height", "source_sha256", "output_sha256", "page_seed"]})
        partition = coco[ann["split"]]
        partition["images"].append({"id": image_index, "file_name": ann["file_name"], "width": ann["width"], "height": ann["height"]})
        for mark in marks:
            if mark["class"] not in {"exclusion", "price_change"}:
                continue
            x1, y1, x2, y2 = mark["bbox"]
            partition["annotations"].append({"id": len(partition["annotations"])+1, "image_id": image_index,
                "category_id": 1 if mark["class"] == "exclusion" else 2, "bbox": [x1,y1,x2-x1,y2-y1],
                "area": (x2-x1)*(y2-y1), "iscrowd": 0, "mark_id": mark["mark_id"]})
    write_jsonl(args.output / "annotations.jsonl", results)
    shutil.copyfile(args.source / "claims.jsonl", args.output / "claims.jsonl")
    shutil.copyfile(args.source / "annotations.jsonl", args.output / "source_annotations.jsonl")
    with (args.output / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    for split, records in coco.items():
        (args.output / f"marks_coco_{split}.json").write_text(json.dumps(records, indent=2)+"\n")
    provenance = {"generator_version": VERSION, "seed": args.seed, "source": str(args.source),
        "source_annotation_sha256": digest(args.source / "annotations.jsonl"),
        "source_claims_sha256": digest(args.source / "claims.jsonl"),
        "generator_sha256": digest(__file__), "fonts": [{"path": p, "sha256": digest(p)} for p in fonts],
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "pillow": PIL.__version__},
        "currency_provenance": "SGD assumed for synthetic Singapore estimates",
        "page_count": len(results), "split_counts": {s: len(c["images"]) for s,c in coco.items()},
        "provenance": "fully_synthetic", "geometry": "source pixels, xyxy; COCO xywh",
        "limitations": ["Font-rendered ink is not real handwriting", "One shared layout family across all splits", "Source M5 boxes cover field values, not OCR words", "Original remarks may conflict with new marks; original text is retained", "No human confirmations or final approvals"]}
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2)+"\n")
    (args.output / "README.md").write_text("""# Synthetic surveyor annotations

All source pages and claim-grouped train/validation/test membership are retained.
Images contain procedural font-rendered surveyor ink, not real human handwriting.
Original field-value annotations remain in `regions` and exact source labels in
`source_annotations.jsonl`; added ink is separate in `marks`, `surveyor_regions`
and `handwritten_words`. Only added words have measured rendered word boxes.
M5 OCR word alignment is still required; do not treat original field boxes as word boxes.

M6 COCO exports contain exclusion and price_change targets only. Acceptance ticks
and assessment notes are documented distractors, not positive targets. Each COCO
image list includes negative pages. Around 10% of estimates have only accepted/
unmarked rows; every assessment page has a review note.

Amounts retain original line totals, including quantity>1, excluding separate
labour and tax. Revised amounts are decimal strings and are synthetic intended
surveyor adjustments, not final approvals or human confirmations. SGD is assumed.
Printed totals remain unchanged. Existing Approved/Check/Nett text is fictional
source text and may be contradicted by later surveyor ink. Original data is untouched.

Split separation is by claim, with shared layouts and simulated writers; this
dataset cannot establish generalization to real handwriting or unseen layouts.
See provenance.json for hashes, seed, fonts and environment.
""")
    print(json.dumps({"output": str(args.output), "pages": len(results), "splits": provenance["split_counts"]}))


if __name__ == "__main__":
    main()
