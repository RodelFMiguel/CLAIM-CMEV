# Claude handover: M4–M6 report update

Date: 24 October 2026, Asia/Singapore. Prepared by Codex.

**Final status: report update and validation completed.** No implementation work remains for this request. Optional follow-up: review the prose in Microsoft Word and refresh the full table of contents. The source report is ready for teammate review.

## User request and scope

Update `docs/Project Report_ACERY_v1.docx` in place with concise, plain technical English explaining M4, M5 and M6 implementation. Include model origins, project training data, module workflows and available references. Use the module specifications and `notebooks/M5_Document_Worker.ipynb` / `notebooks/M6_Document_Worker.ipynb`. Preserve teammates’ material elsewhere in the report.

The user also requested this handover so Claude can continue if Codex credits end. The report work remains authorised; no further approval is needed for ordinary local edits and checks. No commit or push has been requested.

## Current deliverables

- `docs/Project Report_ACERY_v1.docx`: edited. Previously blank chapters 6–8 now have approximately 1,030 words describing M4–M6, plus nine additional references. Existing chapter numbering and assigned authors remain. References include working external model/paper hyperlinks and relative links to repository notebooks/specifications/dataset provenance.
- This handover: `docs/handoffs/2026-10-24-m4-m6-report-claude.md`.
- Earlier requested presentation is already complete: `docs/CLAIM_CMEV_Final_Presentation_v1.pptx`. It is unrelated to the remaining report checks; preserve it.

## Verified implementation facts

- M4: PaddleOCR 2.10.0 / PaddlePaddle 2.6.2. Official English PP-OCRv3 detection, PP-OCRv4 recognition, mobile v2 orientation classifier. No project-specific OCR training. PDF rasterisation at 300 dpi, conservative geometry correction, actual line-box granularity, original/corrected coordinate transforms and quality/status records.
- M5: current live neural route uses `microsoft/layoutlmv3-base`, despite older parser-only specification text. Actual M4 line boxes are aligned to annotated fields; unrelated boxes receive O and ambiguous alignments are masked. Text, 0–999 layout boxes and 224×224 images are encoded into 512-token windows with 64-token overlap. AdamW LR 0.00005, batch four with two accumulation steps, up to 20 epochs. Current selected export completed 13 epochs, best epoch eight.
- M6: torchvision `fasterrcnn_resnet50_fpn`, initialised with `FasterRCNN_ResNet50_FPN_Weights.COCO_V1`, new two-class foreground head. SGD LR 0.005, momentum 0.9, weight decay 0.0005, batch four, up to 20 epochs; selected export best epoch 20. Resize short side 800, long-side cap 1333. Actual live linking thresholds: exclusion 0.89 / price change 0.59.
- Both trained document models use `synthetic_motor_claims_500_w_annotation`: 500 pages, 250 two-page claims, grouped 350/76/74 page split (train/validation/test), one layout, three handwriting fonts. No CORD adaptation or real-estimate/real-handwriting fine-tuning. Final-test evaluation is disabled in both notebooks.
- M6 links detections to the actual M5 row/amount geometry using deterministic rules. Model proposals start pending; only confirmed exclusions remove rows. Price changes require a human-entered amount, currency and cost basis. Pending repricing does not use the printed amount. TrOCR is disabled. Confirmations/corrections preserve history and create revisions.
- Do not conflate historical 12 October reference-run numbers in notebook prose with the selected 13 October exports. Current selected manifests/configs were inspected to verify epoch details. No new model training/evaluation was run.

## Sources

Read current configuration/code before interpreting older proposed specification text:

- `docs/specs/module-04-page-reading.md`
- `docs/specs/module-05-line-item-extraction.md` (especially final live neural integration section)
- `docs/specs/module-06-pen-mark-recognition.md` (especially final live integration section)
- `docs/document-worker-live.md`
- `docs/synthetic-surveyor-annotations.md`
- `data/manifests/synthetic_motor_claims_m4_cache_20261012.json`
- `configs/models/ocr.yaml`, `ocr-manifest.json`, `lineitems.yaml`, `penmarks.yaml`
- `configs/pipeline/m4_page_reading.yaml`, `m6_linking_serving.yaml`
- `src/claim_cmev/documents/text_layout/`, `line_items/layoutlmv3.py`, `line_items/worker.py`, `pen_marks/worker.py`
- `pipelines/documents/lineitems_training.py`, `penmarks_training.py`, `penmarks_data.py`
- The two notebooks named by the user.

Selected local model manifests (ignored artifacts, available on this workstation):

- `artifacts/models/lineitems/lineitems-layoutlmv3-20261013T141341Z-ec8bcba-s20260922-serving/manifest.json`
- `artifacts/models/penmarks/penmarks-fasterrcnn-20261013T142838Z-78d92779-serving/manifest.json`

External references were opened and checked:

- https://www.paddleocr.ai/v2.10.0/en/ppocr/model_list.html
- https://huggingface.co/microsoft/layoutlmv3-base
- https://arxiv.org/abs/2204.08387
- https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.detection.fasterrcnn_resnet50_fpn.html
- https://arxiv.org/abs/1506.01497

## Editing method and preservation

The report was untracked when supplied. A full original backup is at `scratch/report-m4-m6/original/Project Report_ACERY_v1.docx` (ignored scratch; do not delete).

Original SHA-256: `c461c38dda2828b5e478cc840a233801421a5d6fd8a839638ecf3a8d709221e3`.

The script `scratch/report-m4-m6/update_report.py` edits OOXML directly, rebuilding from the backup. It preserves all existing ZIP entries except `word/document.xml`, `word/_rels/document.xml.rels`, and `word/settings.xml`. All unrelated body nodes are checked for canonical XML equality; figures, tables and the existing locked Mendeley bibliography are preserved. Additional references sit after the Mendeley field so that field is not rewritten. Cached TOC chapter titles are updated and TOC fields marked for refresh; Word is configured to update fields on open. New subsection TOC entries/page numbers may require an explicit full TOC refresh in Word.

**Before rerunning the script**, compare the current report hash with `scratch/report-m4-m6/validation.json` and check for later human edits. The script rebuilds from the original backup, so it must not overwrite subsequent teammate changes.

## Validation and local tools

- ZIP integrity and unchanged non-target archive entries passed.
- Unrelated body content equality passed. All 16 existing tables remain.
- Original and updated reports rendered successfully with headless LibreOffice. Original: 78 pages; initial edited draft: 79 pages. Pre-existing blank chapters are retained.
- Reviewed the contact sheet of report pages 69–72 (new modules) and page 79 (reference additions). Simplified the learning rate to `0.00005` and left-aligned added references. No visual clipping.
- Final automatic paragraph comparison passed for all 27 inserted body paragraphs/subheadings and all three chapter titles after excluding page footers.
- All 13 new reference hyperlinks are present; every relative repository target exists. Public sources were checked with browsing.
- Final document remains 79 rendered pages. The report content is complete; `CONTEXT.md` records the final handoff.
- Final report SHA-256: `6776fd2e3c8e0095adee9ae036d9b7b7338405914b44f81b6ec4e6f90bc05de2`. This also appears in `scratch/report-m4-m6/validation.json`.

Useful files (ignored):

- `scratch/report-m4-m6/update_report.py`: edit/build script.
- `scratch/report-m4-m6/inserted-content.json`: inserted prose for audit/checks.
- `scratch/report-m4-m6/validate_report.py`: final preservation, links and rendered-text checks.
- `scratch/report-m4-m6/validation.json`: hashes and summary, final status `passed`.
- `scratch/report-m4-m6/original/Project Report_ACERY_v1.pdf`: baseline rendering.
- `scratch/report-m4-m6/updated/Project Report_ACERY_v1.pdf`: latest rendering.
- `scratch/report-m4-m6/updated/contact.png`: module/reference contact sheet.

Temporary Python environment: `/private/tmp/cmev-final-presentation-venv/bin/python` (lxml, Pillow, pymupdf available). No dependency changes were made to the repository.

Rendering command:

```sh
soffice -env:UserInstallation=file:///private/tmp/cmev-report-lo --headless --convert-to pdf --outdir scratch/report-m4-m6/updated 'docs/Project Report_ACERY_v1.docx'
```

Default sandbox execution cannot launch LibreOffice correctly on this workstation; approved escalated execution worked. Do not infer future permission from this note; follow the host agent’s current permission rules.

## Next actions

1. Read `CONTEXT.md` and inspect the current working tree before starting any subsequent work. Existing presentation and review-fix history are separate tasks; do not overwrite them.
2. Optional: open the report in Microsoft Word, update the entire table of contents and review the M4–M6 prose with the assigned authors. LibreOffice rendering was checked; native Word editing was not.
3. If asked to revise, preserve current human changes. Do not rerun the build script blindly, since it uses the original backup. The validation script can be run read-only after rerendering.
4. No commit/push has been made. Follow the user’s next instructions; no new training or accuracy claims are needed.

## Update by Claude Code: tables, figures and workflows (24 October 2026)

At the user's request, chapters 6–8 were rewritten with more detail, tables, images and workflow diagrams. This supersedes the ~1,030-word Codex text above; the nine added references are unchanged.

- **Content:** M4 has 5 subsections, M5 7 and M6 7. Added 13 tables (Tables 15–27) and 15 figures (Figures 11–25). Numbering continues from the teammates' chapters. Captions use the report's own `SEQ` caption fields, tables use its `GridTable4-Accent1` style, and body text uses its double spacing.
- **Figures:** four new Graphviz workflows (document branch, M4 page reading, M5 extraction, M6 detection and linking). The rest are notebook figures from the *selected 13 October runs*: `artifacts/evaluation/lineitems-layoutlmv3-20261013T141341Z-…/figures/` and `artifacts/evaluation/penmarks-fasterrcnn-20261013T142838Z-78d92779/`. Two figures were re-cut: the Faster R-CNN diagram is split into two rows, and the row-link figure has its blank space removed.
- **Numbers:** the M5 figures come from `val_metrics.json`, `line_items_validation.json`, `position_baseline_validation.json`, `shift_probe_validation.json` and `comparison_with_earlier_checkpoint.json` in that run folder. The M6 figures come from `validation_report.json` and the serving manifest. The M4 figures come from the M4 cache receipt. All are synthetic validation results; the test split was not used. Note that the 13 October exclusion results (51/54 found, 13 false positives; 47/54 rows linked with cell-union rows, 32/54 with M5 rows) differ from the 12 October numbers in the spec notes.
- **Build:** `scratch/report-m4-m6-v2/build_report.py` reads its text from `content.py` and rebuilds from the original backup. It refuses to run unless the report still has the expected hash, unless you pass `--force`. A copy of the Codex build is in `codex-build-backup.docx`, and diagram sources are in `figs/*.dot`. The build also fixes the `updateFields` position in `settings.xml`, which the earlier build had placed in the wrong spot.
- **Checks:** the docx skill's `validate.py` passes against the original, with no new errors. Every original body node except the replaced blank paragraphs and the three chapter titles is canonically unchanged; the only other change is the `dirty` flags on the TOC, table-list and figure-list fields. There are 29 tables (16 original + 13 new), and all 8 local link targets exist. The LibreOffice render has 95 pages, with M4–M6 on pages 69–88. I looked at every new page: no table splits across pages and no figure is clipped. It was not checked in Microsoft Word.
- **Final SHA-256:** `ecb9d9b824ae48e055e7efd13b52d0099f159ca74990e1fc7879a20e3a06e190`.
- **Next:** open the report in Word, update all fields (the contents, list of tables and list of figures refresh on open), and have Arun review chapter 7.
