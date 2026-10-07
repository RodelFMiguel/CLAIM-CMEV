# M4–M6 document workflow with neural M5

Date: 2026-10-07. This is a design explanation requested by the user, with reproducible drawings. The user plans a neural M5. The drawings use **LayoutLMv3 as the candidate already described in the training specification**; choosing this exact architecture, BIO label encoding and adapter policies remains proposed. This does not claim model training, serving integration or acceptance completion. Earlier specifications that call neural M5 a stretch route describe the preceding baseline; this note records the new planning direction without silently rewriting their historical status.

## Drawings

Open [the local viewer](../artifacts/exports/document-workflow/index.html). It contains three zoomable drawings, each with SVG, PNG, PDF and editable DOT downloads:

1. [Full tensor workflow](../artifacts/exports/document-workflow/document-tensor-workflow.svg): source document, M4 model internals, neural M5 inputs/logits, M6 detector internals, record assembly, linking and human actions.
2. [Worked estimate example](../artifacts/exports/document-workflow/document-worked-example.svg): printed SGD 800, a handwritten price-change mark, field labels, row geometry and a separately confirmed SGD 650.
3. [Model preparation](../artifacts/exports/document-workflow/document-model-training.svg): pretrained components, task-specific training targets, validation and release artifacts.

Regenerate with `python3 scripts/render_document_workflow.py`. Graphviz `dot` must be available. This runs no models and downloads no weights. Generated files live in the ignored `artifacts/exports/` location; this document and the rendering script are the shareable sources.

## Source document and coordinates

PDF bytes are rasterised page by page at the configured 300 DPI. Images are decoded with their EXIF orientation. M4 retains the original, the rendered page and its corrected image. Perspective correction requires a reliable boundary; otherwise the module attempts reliable deskew or records no correction. It does not erase the marks before OCR or detection.

The canonical downstream frame is the **corrected page**. `I` is a uint8 image `[Hc,Wc,3]`, with an illustrative A4 width/height of 2480/3508. The current OpenCV path uses BGR internally; adapters explicitly convert to RGB for LayoutLMv3 and torchvision.

| Frame | Units | Use |
| --- | --- | --- |
| Original image / PDF | Stored pixels before EXIF / PDF user-space points | Immutable evidence |
| Rendered page | Pixels, top-left origin | Raster/EXIF result; current `quad_original` refers to this frame |
| Corrected page | Pixels, top-left origin | OCR quads and M6 public detection boxes |
| M5 layout input | Integer coordinates in 0–1000 | `bbox` spatial embeddings |
| Shared row/mark records | Floats in 0–1 of corrected page dimensions | M6 geometric comparison and evidence records |
| Model-internal resized image | Model-specific pixels | OCR detector grid, 224-square M5 view, padded M6 view |

For a corrected-page box `(x0,y0,x1,y1)`, M5 uses the clipped integer form of `(1000*x0/Wc,1000*y0/Hc,1000*x1/Wc,1000*y1/Hc)`. The proposed example uses integer truncation. Shared record boxes divide by width/height without the factor of 1000. Preserve the original floating-point geometry separately: quantised M5 boxes must not become the only source for high-resolution overlays. See the implemented [page transform](../src/claim_cmev/documents/text_layout/page_transform.py).

## M4: off-the-shelf OCR and decoded page records

The current [PaddleOCR wrapper](../src/claim_cmev/documents/text_layout/paddle.py) pins PaddleOCR 2.10.0 / PaddlePaddle 2.6.2, with English PP-OCRv3 detection, English PP-OCRv4 recognition and the mobile angle classifier. It exposes located text records, not internal network arrays.

The diagram opens that black box for explanation:

- Text detector: normalized page tensor → DB probability map → quadrilaterals. Spatial dimensions follow the actual exported detector and resize policy; the diagram deliberately leaves them symbolic.
- Crop orientation: normalized text crops → two orientation scores → rotate accepted upside-down crops.
- Text recognition: resized crops → CTC character probabilities → collapse repeats/blanks → text and recognition confidence.

The upstream crop defaults are `[Nc,3,48,192]` for orientation and nominal `[Nc,3,48,320]` for recognition. Recognition width can expand with the crop batch aspect ratios. `Nc` counts text crops, not pages. Alphabet size and sequence length depend on the selected export. See [PaddleOCR inference defaults](https://github.com/PaddlePaddle/PaddleOCR/blob/release/2.10/tools/infer/utility.py), [recognition preprocessing](https://github.com/PaddlePaddle/PaddleOCR/blob/release/2.10/tools/infer/predict_rec.py) and [CTC inference head](https://github.com/PaddlePaddle/PaddleOCR/blob/release/2.10/ppocr/modeling/heads/rec_ctc_head.py).

Internal maps in the drawing are conceptual inspection points, not arrays the current adapter persists or a claim that each export was executed here. PaddleOCR may filter detections internally; an absent OCR region is not proof that no print exists. Page quality, missing fields and declaration completeness must still gate downstream use.

## M5: model inputs, outputs and the required assembler

The proposed path uses `LayoutLMv3ForTokenClassification`. Its processor must have `apply_ocr=False` to reuse the recorded M4 evidence instead of silently running a second OCR engine.

| Input/output | Shape | Meaning |
| --- | --- | --- |
| `input_ids` | int64 `[B5,L]` | BPE subword IDs |
| `attention_mask` | int64 `[B5,L]` | Valid tokens versus padding |
| `bbox` | int64 `[B5,L,4]` | Word boxes inherited by subwords, 0–1000 |
| `pixel_values` | float32 `[B5,3,224,224]` | RGB page view, normalized by the pinned processor |
| Optional `token_type_ids` | int64 `[B5,L]` | Usually zero for a single sequence |
| Training-only labels | int64 `[B5,L]` | Field classes; ignored positions use −100 |
| Raw token logits | float `[B5,L,K]` | Unnormalized field-class scores |
| Decoded class IDs | int64 `[B5,L]` | One selected label per token before masking/assembly |

Here `B5` counts page windows, `L=512` is an illustrative padded length including special tokens, and `K=11` is a **proposed BIO example**: O plus B/I labels for PART, OPERATION, QTY, UNIT_PRICE and AMOUNT. O covers unrelated text; it is not a separate B/I field. Long documents require explicit overlapping windows, page-image replication and deduplication back to source words. Window stride, subword aggregation, confidence handling and overlap conflicts still need implementation and validation.

The base model combines text and spatial embeddings with 196 visual patches and one visual CLS token. Its hidden states include both modalities; the token-classification head selects text positions before classifying them. Consequently `[B5,L,11]`, **not `[B5,L+197,11]`**, is the example field-logit output. See the [LayoutLMv3 interface](https://huggingface.co/docs/transformers/model_doc/layoutlmv3) and [versioned token-head implementation](https://github.com/huggingface/transformers/blob/v4.57.1/src/transformers/models/layoutlmv3/modeling_layoutlmv3.py). The [base image processor configuration](https://huggingface.co/microsoft/layoutlmv3-base/blob/main/preprocessor_config.json) supplies the illustrated 224-square image and 0.5 channel means/standard deviations.

### The alignment prerequisite

**The current M4 wrapper supplies line segments, not verified word boxes.** A neural M5 adapter must establish a reliable word-location/alignment path before this design can be trained and served. Splitting a string into words does not measure their individual locations. Equally dividing its line rectangle would fabricate geometry.

Possible future approaches include a validated word-localisation OCR configuration or a separately evaluated region-level representation. The latter would change the illustrated training/input policy and needs its own measured justification. Neither approach is selected or implemented by this diagram. Ambiguous/missing training alignment must be masked or flagged, and inference uncertainty preserved.

### Neural extraction still needs deterministic assembly

Token labels identify field spans; they do not directly produce row IDs, canonical parts or monetary values. `PART` means a description span, not one class from the vehicle-part taxonomy. Postprocessing must merge subwords/windows, group fields into repair rows, handle wrapped descriptions, map vocabulary, parse exact decimal amounts and preserve completeness/uncertainty.

Row/amount boxes derive from source geometry, not newly predicted M5 boxes. The neural route must produce the same line-item and completeness contracts as the existing parser. Record OCR confidence separately from token-class probabilities; neither is automatically calibrated row-level correctness. Ink read by OCR must not become an automatically accepted revised amount or overwrite uncertain printed evidence.

## M6: detector tensors versus public detections

The proposed `fasterrcnn_resnet50_fpn` starts with COCO-pretrained weights and replaces its predictor for background, exclusion and price change. Task-specific fine-tuning is required. Its input is a **list** of RGB page tensors `[3,Hc,Wc]`, scaled to 0–1; M5 rows are inputs to the linker, not the network.

Torchvision normalizes/resizes and pads internally. The illustrated recipe uses short side 800 with long-side cap 1333. An A4 page becomes approximately 800×1131 (width×height); a single-image padded batch is then approximately `[1,3,1152,800]`. Do not normalize twice. The transforms depend on the pinned recipe, not on M5's 224-square view. See [torchvision's transform implementation](https://github.com/pytorch/vision/blob/v0.20.1/torchvision/models/detection/transform.py).

The drawing distinguishes RPN proposals, RoI class logits `[R,3]`, class-specific box deltas `[R,12]`, and the public inference result. After box decoding, filtering and class-wise NMS, each page returns `boxes [Ni,4]`, `labels [Ni]`, `scores [Ni]`. Those boxes have already been restored to the supplied corrected-page dimensions. The linker converts them to the shared 0–1 frame; it must not undo the internal resize again. See [Faster R-CNN API](https://docs.pytorch.org/vision/main/models/generated/torchvision.models.detection.fasterrcnn_resnet50_fpn.html) and [RoI postprocessing](https://github.com/pytorch/vision/blob/v0.20.1/torchvision/models/detection/roi_heads.py).

Training adds per-image ground-truth boxes and foreground class IDs; the detector returns loss terms during training. Correct row associations are retained for separate geometric-linking evaluation. Detecting `price_change` never constitutes recognizing its handwritten digits.

## Records, human review and status

M6 compares mark boxes with M5 row and amount boxes for the same page/revision. Ambiguous links retain candidate rows. All model-proposed marks start pending. Only a confirmed exclusion skips a row; repricing confirmation requires the exact amount, currency and cost basis. Pending repricing never falls back to the printed amount. Optional pretrained TrOCR would only supply a separate suggestion.

M8 can produce an initial assessment with checks withheld. Decision-changing human actions trigger new input/assessment revisions and reuse compatible evidence by lineage. Preserve printed estimates, surveyor-agreed amounts and final approvals separately. M5 rows, M6 marks and human actions are separate records rather than one overwritten JSON value.

At inspection on 2026-10-07, M4's page/OCR library, rule-based M5 and M6's linker/state code exist. The neural M5 adapter/training, real M6 detector and live document-stage integration remain pending. The user-directed neural M5 plan supersedes the earlier parser-only assumption for this design; it does not prove implementation. Existing specifications: [M4](specs/module-04-page-reading.md), [M5](specs/module-05-line-item-extraction.md), [M6](specs/module-06-pen-mark-recognition.md), [training](specs/model_training_specification.md).
