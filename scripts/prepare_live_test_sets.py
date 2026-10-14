"""Assemble four upload-ready claim sets (vehicle photographs + marked estimate pages) for manual live testing.

Sources are local, already acquired data: HITL photographs and the synthetic surveyor-annotated
estimate pages. Only VALIDATION-split items are used, so the frozen test splits stay untouched and
nothing a served model was trained on is included. Photographs and documents come from different
sources: the cars in the photographs are not the vehicle named on the estimate, and the photographs
in one set show different cars. The sets exercise the pipeline; they are not accuracy evidence.

    venv/bin/python scripts/prepare_live_test_sets.py            # writes data/synthetic/live-test-sets
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HITL = ROOT / "data/processed/hitl/v3"
CLAIMS = ROOT / "data/raw/synthetic_motor_claims_500_w_annotation"
M4_CACHE = ROOT / "data/processed/synthetic_motor_claims_500_w_annotation/m4-v1/manifest.json"
MAGIC = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg"}

# (HITL source file name, what the photograph shows). Chosen by eye from the validation split.
SETS = [
    {"slug": "set-01-mixed-marks-short-estimate", "claim": "SYN-00182", "vehicle_class": "sedan_standard",
     "purpose": "Baseline. Three estimate rows with one accepted tick, one exclusion and one price change.",
     "photos": [("Car damages 279.png", "front, bumper and lamp damage"),
                ("Car damages 136.png", "front-left corner, headlamp and bonnet"),
                ("Car damages 300.png", "side, door damage")]},
    {"slug": "set-02-no-surveyor-decisions", "claim": "SYN-00212", "vehicle_class": "suv_crossover",
     "purpose": "No exclusion and no price change: only acceptance ticks, which are not pen-mark targets. "
                "Expect no exclusion or price-change proposal on the estimate page.",
     "photos": [("Car damages 137.png", "rear, bumper and tail lamps"),
                ("Car damages 690.png", "rear-left corner, bumper damage"),
                ("Car damages 713.png", "side, front door damage"),
                ("Car damages 176.png", "front, headlamps and bumper")]},
    {"slug": "set-03-long-estimate-many-marks", "claim": "SYN-00185", "vehicle_class": "sedan_standard",
     "purpose": "Eight estimate rows (the layout maximum) with two exclusions and three price changes, "
                "and five photographs covering front, side and rear.",
     "photos": [("Car damages 865.png", "front-right corner, headlamp and wing"),
                ("Car damages 1165.png", "front wing and bumper damage"),
                ("Car damages 243.png", "front wing and door, dent"),
                ("Car damages 135.png", "front wing and wheel arch"),
                ("Car damages 1284.png", "rear, tailgate and bumper damage")]},
    {"slug": "set-04-estimate-page-ocr-cannot-read", "claim": "SYN-00201", "vehicle_class": "sedan_standard",
     "purpose": "Failure path. With the real OCR worker (docker-compose.ocr.yml) the estimate page is "
                "unreadable for M4 (nested text regions). Expect a failed page-read stage with a reason, "
                "never an empty estimate and never a passed check. With fixture document stages the "
                "page content is not read, so this set then behaves like the others.",
     "photos": [("Car damages 234.png", "front, bumper and grille"),
                ("Car damages 1345.png", "front bumper damage"),
                ("Car damages 228.png", "rear quarter and tail lamp"),
                ("Car damages 324.png", "side, broken door mirror")]},
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def real_suffix(path: Path) -> str:
    """Extension from the file's magic bytes; the API refuses a content type that contradicts them."""
    head = path.read_bytes()[:8]
    for magic, suffix in MAGIC.items():
        if head.startswith(magic):
            return suffix
    raise ValueError(f"{path} is neither PNG nor JPEG")


def hitl_validation_photos() -> dict[str, dict]:
    """Validation-split HITL part records by source file name, with the labelled classes present."""
    manifest = json.loads((HITL / "manifest.json").read_text())
    names = manifest["class_names"]
    by_hash: dict[str, dict] = {}
    for record in manifest["records"]:
        if record["split"] != "val":
            continue
        counts = record["pixel_counts"]
        counts = counts if isinstance(counts, dict) else dict(zip(names[record["task"]], counts))
        total = sum(counts.values()) or 1
        present = sorted((n for n, c in counts.items() if n != "background" and c / total >= 0.01),
                         key=lambda n: -counts[n])
        entry = by_hash.setdefault(record["content_sha256"], {"source_image": record["source_image"]})
        entry[record["task"]] = present
    return {Path(e["source_image"]).name: e for e in by_hash.values() if "parts" in e}


def build(out: Path) -> None:
    photos = hitl_validation_photos()
    claims = {c["claim_id"]: c for c in map(json.loads, (CLAIMS / "claims.jsonl").read_text().splitlines())}
    pages = {a["image_id"]: a for a in map(json.loads, (CLAIMS / "annotations.jsonl").read_text().splitlines())}
    m4 = {p["image_id"]: p for p in json.loads(M4_CACHE.read_text())["pages"]} if M4_CACHE.is_file() else {}
    out.mkdir(parents=True)
    index = []
    for spec in SETS:
        claim, folder = claims[spec["claim"]], out / spec["slug"]
        if claim["split"] != "validation":
            raise ValueError(f"{spec['claim']} is not a validation claim")
        (folder / "photos").mkdir(parents=True)
        (folder / "documents").mkdir()
        files = []
        for number, (name, shows) in enumerate(spec["photos"], start=1):
            if name not in photos:
                raise ValueError(f"{name} is not a HITL validation photograph")
            source = ROOT / "data/raw" / photos[name]["source_image"]
            target = folder / "photos" / f"photo-{number}{real_suffix(source)}"
            shutil.copyfile(source, target)
            files.append({"file": f"photos/{target.name}", "role": "photograph", "shows": shows,
                          "source": photos[name]["source_image"], "sha256": sha256(target),
                          "hitl_labelled_parts": photos[name]["parts"],
                          "hitl_labelled_damage": photos[name].get("damage", [])})
        estimate, assessment = pages[f"{spec['claim']}_P2"], pages[f"{spec['claim']}_P1"]
        for page, name in ((estimate, "estimate-page.jpg"), (assessment, "survey-assessment-page.jpg")):
            source = CLAIMS / page["file_name"]
            if sha256(source) != page["output_sha256"]:
                raise ValueError(f"{source} does not match its recorded hash")
            shutil.copyfile(source, folder / "documents" / name)
            files.append({"file": f"documents/{name}", "role": "estimate_page", "source": page["file_name"],
                          "page_type": page["page_type"], "sha256": page["output_sha256"]})
        # Optional PDF variant: both pages at A4 (1240x1754 px at 150 dpi), assessment first.
        pdf = folder / "documents" / "optional-both-pages.pdf"
        first, second = (Image.open(CLAIMS / p["file_name"]).convert("RGB") for p in (assessment, estimate))
        first.save(pdf, "PDF", resolution=150.0, save_all=True, append_images=[second])
        files.append({"file": "documents/optional-both-pages.pdf", "role": "estimate_page",
                      "note": "alternative to the two JPEG pages; not used when the M4 cache was built",
                      "sha256": sha256(pdf)})

        marks = [m for m in estimate["marks"] if m["class"] in ("exclusion", "price_change")]
        decisions = {d["row_id"]: d for d in estimate["row_decisions"]}
        rows = []
        for number, part in enumerate(claim["parts"], start=1):
            decision = decisions[f"part_{number}"]
            rows.append({"row": number, "description": part["part_name"], "operation": part["action"],
                         "quantity": part["quantity"], "declared_line_amount": decision["declared_amount"],
                         "synthetic_surveyor_action": decision["synthetic_action"],
                         "synthetic_revised_amount": decision["surveyor_revised_amount"]})
        cached = m4.get(estimate["image_id"], {})
        expected = {
            "set": spec["slug"], "purpose": spec["purpose"], "provenance": "synthetic documents; public HITL photographs",
            "intake": {"reference": f"LIVE-{spec['claim']}", "vehicle_make": claim["vehicle"]["make"],
                       "vehicle_model": claim["vehicle"]["model"], "vehicle_year": claim["vehicle"]["model_year"],
                       "vehicle_class": spec["vehicle_class"], "plate": claim["vehicle"]["registration_number"],
                       "workshop": claim["repairer"], "loss_date": claim["accident_date"], "currency": "SGD"},
            "files": files,
            "estimate_ground_truth": {
                "rows": rows, "printed_totals": claim["costs"],
                "pen_marks": [{"class": m["class"], "style": m["style"], "row": m["target_row_id"],
                               "handwritten_text": m["text"], "revised_amount": m["revised_amount"]} for m in marks],
                "not_pen_mark_targets": sorted({m["class"] for m in estimate["marks"]} - {"exclusion", "price_change"}),
                "note": "Surveyor actions are synthetic intended decisions, not human confirmations or approvals."},
            "m4_reading_of_estimate_page_jpeg": {
                "page_status": cached.get("page_status"), "text_boxes": cached.get("box_count"),
                "reasons": cached.get("reasons"),
                "source": "M4 cache built 2026-10-12" if cached else "M4 cache not present"},
        }
        (folder / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
        index.append({"set": spec["slug"], "claim": spec["claim"], "purpose": spec["purpose"],
                      "photographs": len(spec["photos"]), "estimate_rows": len(rows),
                      "exclusions": sum(m["class"] == "exclusion" for m in marks),
                      "price_changes": sum(m["class"] == "price_change" for m in marks),
                      "m4_page_status": cached.get("page_status")})
    (out / "manifest.json").write_text(json.dumps({"generator": "scripts/prepare_live_test_sets.py",
                                                   "splits_used": "validation only", "sets": index}, indent=2) + "\n")
    lines = ["# Live test sets", "",
             "Four upload-ready claims for manual testing of the running system. Generated by",
             "`scripts/prepare_live_test_sets.py`; this folder is git-ignored local data.", "",
             "| Set | Photographs | Estimate rows | Exclusions | Price changes | M4 reading of the estimate JPEG |",
             "| --- | ---: | ---: | ---: | ---: | --- |"]
    lines += [f"| `{i['set']}` | {i['photographs']} | {i['estimate_rows']} | {i['exclusions']} | {i['price_changes']} | "
              f"{i['m4_page_status'] or 'not recorded'} |" for i in index]
    lines += ["", "## How to use one set", "",
              "1. Create a claim with the values under `intake` in the set's `expected.json`.",
              "2. Upload everything in `photos/` as photographs.",
              "3. Upload `documents/estimate-page.jpg` as the estimate page. `survey-assessment-page.jpg` is the",
              "   claim's other page (no repair table) and can be uploaded as a second estimate page.",
              "   `optional-both-pages.pdf` holds both pages and replaces the two JPEGs; use one form, not both.",
              "4. Compare what the review screen shows with `estimate_ground_truth` in `expected.json`.", "",
              "## What each set is for", ""]
    lines += [f"- **`{i['set']}`**: {i['purpose']}" for i in index]
    lines += ["", "## Read this before judging results", "",
              "- Which stages are real depends on the Compose overlays that are running. With",
              "  `docker-compose.image.yml` the photographs are read by the real M1, M2 and M3 workers. With",
              "  `docker-compose.ocr.yml` the estimate page is read by the real M4 worker. M5 and M6 are still",
              "  fixtures in every mode, so the rows and pen marks on the review screen do not come from the",
              "  uploaded page, and `estimate_ground_truth` cannot be expected to match them yet.",
              "- Photographs and documents come from different sources. The cars in the photographs are not the",
              "  vehicle named on the estimate, and one set mixes several cars. The system does not compare",
              "  make or model with the photographs. Photographed parts only roughly match the estimate rows.",
              "- `hitl_labelled_parts` and `hitl_labelled_damage` in `expected.json` are the dataset's own labels",
              "  for each photograph (classes covering at least 1% of the image). The served damage model may",
              "  use another vocabulary (CarDD), so compare damage by eye, not by class name.",
              "- Only validation-split items are used. No served model was trained on them, and the frozen test",
              "  splits are untouched. Validation items were used to select models, so results here are not",
              "  held-out accuracy evidence.",
              "- The PDF variant was not used when the M4 cache was built; its M4 reading is not recorded.",
              "- To test an unphotographed part, upload a set's documents with another set's photographs.", ""]
    (out / "README.md").write_text("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "data/synthetic/live-test-sets")
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"{args.output} already exists; remove it or choose another --output")
    build(args.output)
    print(f"Wrote {len(SETS)} sets to {args.output}")
