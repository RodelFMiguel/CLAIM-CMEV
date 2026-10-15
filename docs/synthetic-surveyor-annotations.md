# Synthetic surveyor annotation dataset

The derived dataset is generated at `data/raw/synthetic_motor_claims_500_w_annotation/`
from the existing `data/raw/synthetic_motor_claims_500/` pages. Both directories
are ignored local data. The source is preserved; the generator refuses to overwrite
an existing output directory.

## Reproduce and validate

Use a Python environment with Pillow installed (the current workstation's
`venv/bin/python` has it):

```sh
venv/bin/python scripts/prepare_marked_motor_claims.py
venv/bin/python scripts/validate_marked_motor_claims.py
```

For a separate reproducibility run, supply `--output` with an unused path.
The seed and available handwriting fonts are recorded in output provenance.
Reproduction across machines requires the same font files, Pillow version and
generator revision. `--font PATH` can explicitly select the font files.

## What the labels mean

Original field-value regions and claim ground truth remain separate from new
surveyor ink. The original `part_N_amount` describes the declared **line total**,
including quantity, rather than necessarily a unit price. Revised amounts are
synthetic intended replacements of that line total. They do not overwrite the
declared amount or establish final approval. Printed subtotal/tax/total text stays
unchanged after marks are added.

Repair-estimate pages contain randomized exclusions, price changes and acceptance
ticks. Exclusions can be an X, a row strike, a rejection remark, or combinations.
Price changes cross out the amount and write a replacement in the remarks.
Assessment pages have harmless review notes and no invented repair-row labels.
Ticks and notes are supplemental distractors, excluded from the two-class detector
targets. Rejection remarks and standalone strike lines extend the current core
M6 specification's X examples; evaluate these styles separately and explicitly
support them in the detector/linker implementation rather than assuming runtime
coverage already exists.

`marks_coco_train.json`, `marks_coco_validation.json` and `marks_coco_test.json`
provide detection labels: category 1 `exclusion`, category 2 `price_change`.
Boxes cover each logical mark, including its related handwritten remark when
present; component boxes are retained in `annotations.jsonl` for alternative
localization experiments. Claim/page/row identifiers provide linking ground truth.

## M5 and M6 training boundaries

- **M5:** retain source descriptions, quantities, operations and declared amounts
  despite surveyor occlusion. New handwritten remarks and replacement numbers are
  separate surveyor evidence, not substitutes for the original amount labels.
  Source boxes are field-value boxes, not verified OCR word boxes. Actual OCR and
  label alignment are still needed for word-based LayoutLMv3 training; ambiguous
  alignment should be masked or flagged. Rendered word boxes for added handwriting
  describe only the synthetic overlay.
- **M6:** train the two mark classes using the COCO exports and use supplemental
  row links for linking evaluation. Synthetic intended decisions are ground truth,
  not application `confirmed` mark states. Model proposals still start pending;
  core M6 does not automatically read or confirm the replacement amount.
- **Evaluation:** retain the inherited 350/76/74 image splits and claim groups.
  Do not combine source and marked variants into different partitions. All splits
  share the source layout and a small font pool; these splits cannot establish
  unseen-template, unseen-writer or real-handwriting performance. Keep any real
  acceptance evaluation separate and do not tune on the test partition.

The output README and provenance files describe the exact generated release.
These scripts prepare data; they do not train or evaluate a model.
