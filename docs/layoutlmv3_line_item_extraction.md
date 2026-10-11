# Local LayoutLMv3 line-item extraction

`scripts/extract_line_items_layoutlmv3.py` serves the checkpoint saved by
`notebooks/Document_Worker.ipynb`. The requirement document is
`docs/specs/module-05-line-item-extraction.md` (the requested `models-05` path does
not exist). This is an explicit S1 command; it does not change the default parser
or connect the model to Kafka workers.

Use Python 3.12+ with the project installed and the optional neural dependencies:

```powershell
python -m pip install -e ".[document-model]"
python scripts/extract_line_items_layoutlmv3.py runtime/document-input.json --output runtime/line-items.json --family-id family-a-ruled-grid --currency SGD --cost-basis single_part_pre_tax_no_discount_v1
```

The default weights directory is `artifacts/models/document-worker-layoutlmv3`.
Use `--model-dir` to override it, and `--device cuda` for an available CUDA device.
Use a separate Python environment from the `vision` extra: vision requires
Transformers 5, while `document-model` requires Transformers 4; these extras
cannot currently be installed together.

Weights and processor load locally without downloads. The saved processor has
OCR disabled: supply M4 OCR outputs rather than expecting the model to read raw
PDFs or images alone. Input must contain actual word boxes; line/block OCR boxes
are rejected rather than divided into invented word locations.

The JSON manifest has this shape (replace the placeholder with a complete,
validated `DocumentPage` record from M4):

```json
{
  "claim_id": "01J8Z3N4V6W8X0Y2Z4A6B8C0D2",
  "input_revision": 1,
  "provenance": {
    "source_kind": "real",
    "runtime_profile": "full",
    "producer_service": "local-layoutlmv3-extraction"
  },
  "pages": [
    {"image_path": "corrected-page-1.png", "page": "<DocumentPage object>"}
  ]
}
```

Enumerate every expected page, including M4 unreadable records. Image paths are
relative to the manifest or absolute. Each readable image must match its M4
artifact hash and corrected dimensions. Currency and basis are explicit inputs;
the command does not infer them. Select the configured layout family explicitly
to apply its exact numeric conventions; this is not automatic layout detection.

The adapter maps `part_N_description`, `action`, `quantity`, `amount`, and
`unit_price` labels into shared M5 records through the existing vocabulary and
decimal validation. Unknown identities, absent quantities/unit prices and
unreadable amounts stay unresolved. It preserves OCR box IDs, field source links,
all raw predictions and unassigned text. Remarks and claim metadata labels are
retained as unparsed evidence. The notebook's 0–999 coordinate normalization is
preserved, and overflow windows prevent silent truncation at 512 tokens.

Output includes `line_items`, `rows`, `completeness`, `pages`, `unparsed_regions`,
`versions`, and `word_predictions`. Every model record pins the weight SHA-256,
alignment version, vocabulary and layout versions. Repeated rows and row slots
on different pages retain distinct deterministic entry IDs.

Declaration coverage is **partial** for readable inputs: row-slot labels have
not established table termination, omission recall or reliable row association.
An all-unreadable input stays unreadable; zero predictions never establish an
explicitly empty scope. Model and OCR confidence gate fields, but model scores
are uncalibrated. This command does not detect pen marks, change surveyor amounts,
publish events, or establish held-out extraction accuracy. Validate on the same
reserved pages as the parser before production integration or relaxing coverage.

Implementation reference: [Hugging Face LayoutLMv3 documentation](https://huggingface.co/docs/transformers/model_doc/layoutlmv3).
