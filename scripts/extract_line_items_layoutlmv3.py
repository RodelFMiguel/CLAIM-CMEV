"""Run the notebook checkpoint against M4 OCR pages; see --help and docs guide."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON with pages: [{page: DocumentPage, image_path: ...}]")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "artifacts/models/document-worker-layoutlmv3")
    parser.add_argument("--family-id", required=True, help="Explicit configured family for numeric conventions")
    parser.add_argument("--currency", required=True, help="Explicit claim/document ISO currency")
    parser.add_argument("--cost-basis", required=True)
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda", "mps"))
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--layout-config", type=Path)
    parser.add_argument("--vocabulary", type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("CLAIM-CMEV requires Python 3.12 or newer")
    from claim_cmev.contracts.common import Provenance, make_job_key
    from claim_cmev.contracts.documents import DocumentPage
    from claim_cmev.documents.line_items.config import load_layout_families
    from claim_cmev.documents.line_items.vocabulary import load_estimate_vocabulary
    from claim_cmev.documents.line_items.layoutlmv3 import ALIGNMENT_VERSION, LocalLayoutLMv3, adapt_predictions

    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest["pages"]
    if not records:
        raise ValueError("Manifest must enumerate all expected M4 pages, including unreadable pages")
    pages = [DocumentPage.model_validate(record["page"]) for record in records]
    config = load_layout_families(args.layout_config)
    vocabulary = load_estimate_vocabulary(args.vocabulary)
    if args.family_id not in {family.family_id for family in config.families}:
        raise ValueError(f"Unknown layout family: {args.family_id}")
    if not 0 <= args.min_confidence <= 1:
        raise ValueError("min-confidence must be between zero and one")
    provenance = Provenance.model_validate(manifest["provenance"])
    if provenance.runtime_profile != "full":
        raise ValueError("Neural document inference requires explicit full provenance")
    model = LocalLayoutLMv3(args.model_dir, args.device)
    predictions = {}
    for page, record in zip(pages, records):
        if page.quality.state == "unreadable":
            predictions[page.page_id] = ()
            continue
        image_path = Path(record["image_path"])
        if not image_path.is_absolute():
            image_path = manifest_path.parent / image_path
        # Confirm the exact corrected artifact, not only its dimensions.
        import hashlib
        if hashlib.sha256(image_path.read_bytes()).hexdigest() != page.corrected_render_ref.sha256:
            raise ValueError(f"Corrected image hash mismatch: {page.page_id}")
        predictions[page.page_id] = model.predict(page, image_path)
    claim_id, revision = manifest["claim_id"], manifest["input_revision"]
    result = adapt_predictions(
        pages, predictions, config=config, vocabulary=vocabulary, family_id=args.family_id,
        job_key=make_job_key(claim_id, revision, "line_items_extract",
                             {"line_items_model": model.model_version, "extraction_method": "layoutlmv3",
                              "alignment": ALIGNMENT_VERSION, "vocabulary": vocabulary.version,
                              "layout_families": config.config_version, "family": args.family_id,
                              "confidence_threshold": str(args.min_confidence)},
                             target="document"),
        claim_id=claim_id, input_revision=revision, currency=args.currency, cost_basis=args.cost_basis,
        model_version=model.model_version, provenance=provenance, min_confidence=args.min_confidence)
    output = {"line_items": [item.model_dump(mode="json") for item in result.line_items],
              "completeness": result.completeness.model_dump(mode="json"),
              "rows": [asdict(row) for row in result.rows],
              "pages": [asdict(page) for page in result.pages],
              "unparsed_regions": [asdict(region) for region in result.unparsed_regions],
              "versions": result.versions,
              "word_predictions": {key: [asdict(p) for p in value] for key, value in predictions.items()}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Replace only after every page succeeds; errors never become successful empty results.
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"Wrote {len(result.line_items)} rows; declaration {result.completeness.state}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
