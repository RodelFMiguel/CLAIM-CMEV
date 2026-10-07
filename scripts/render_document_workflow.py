"""Render the explanatory neural M5 workflow; no inference or training is run.

Requires Graphviz's `dot`. Run from any directory with Python 3.10+.
Outputs are ignored exports. See docs/document-neural-workflow.md for sources.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/exports/document-workflow"
COLORS = {"neutral": "#e2e8f0", "ocr": "#dbeafe", "m5": "#ede9fe",
          "m6": "#ffedd5", "rule": "#dcfce7", "gate": "#fef3c7", "white": "#ffffff"}


class Drawing:
    def __init__(self, title, subtitle):
        self.lines = ['digraph G {', 'graph [rankdir=TB, newrank=true, bgcolor="#f8fafc", pad="0.35", '
                      'nodesep="0.35", ranksep="0.38", splines=polyline, fontname="Helvetica", '
                      'fontsize=23, labelloc=t, label=' + json.dumps(title + "\n" + subtitle, ensure_ascii=False) + '];',
                      'node [shape=box, style="rounded,filled", color="#94a3b8", penwidth=1.1, '
                      'fontname="Helvetica", fontsize=13, fontcolor="#0f172a", margin="0.18,0.13"];',
                      'edge [color="#64748b", arrowsize=0.7, penwidth=1.3, fontname="Helvetica", fontsize=11];']

    def node(self, name, title, body, kind="white"):
        text = '<B>' + html.escape(title) + '</B><BR/>' + '<BR/>'.join(html.escape(x) for x in body.split('\n'))
        self.lines.append(f'{name} [fillcolor="{COLORS[kind]}", label=<{text}>];')

    def edge(self, source, target, label="", dashed=False, constraint=True):
        opts = ["label=" + json.dumps(label, ensure_ascii=False), "constraint=" + str(constraint).lower()]
        if dashed:
            opts += ['style=dashed', 'color="#b45309"']
        self.lines.append(f'{source} -> {target} [{", ".join(opts)}];')

    def cluster(self, name, label, color):
        self.lines.append(f'subgraph cluster_{name} {{ label={json.dumps(label, ensure_ascii=False)}; '
                          f'color="{color}"; style="rounded"; fontsize=19; margin=18;')

    def end(self):
        self.lines.append('}')

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.edge(a, b)

    def same(self, *names):
        self.lines.append('{rank=same; ' + '; '.join(names) + ';}')

    def render(self, stem):
        dot = OUT / f"{stem}.dot"
        dot.write_text('\n'.join(self.lines + ['}']), encoding="utf-8")
        for ext in ("svg", "pdf", "png"):
            args = ["dot", f"-T{ext}", str(dot), "-o", str(OUT / f"{stem}.{ext}")]
            if ext == "png":
                args.insert(1, "-Gdpi=120")
            subprocess.run(args, check=True)
        ET.parse(OUT / f"{stem}.svg")
        subprocess.run(["dot", "-Gsize=14,22", "-Gdpi=96", "-Tpng", str(dot),
                        "-o", str(OUT / f"{stem}-preview.png")], check=True)


def workflow():
    g = Drawing("M4 + neural M5 + M6 | From uploaded estimate to reviewed repair scope",
                "Tensor-level design • LayoutLMv3 candidate • 2026-10-07 • Intended integration, not a measured model run")
    g.node("upload", "ORIGINAL DOCUMENT • preserve immutable bytes and hash",
           "PDF: one file → pages; JPEG / PNG: stored pixels [H₀, W₀, 3]\n"
           "Keep claim ID, input revision, file ID and page identity\n"
           "A page may contain printed rows, pen marks, shadows and glare", "neutral")
    g.cluster("m4", "M4 | Page reading • off-the-shelf OCR + deterministic geometry", "#60a5fa")
    g.node("render", "RENDER / DECODE → GEOMETRY CORRECTION",
           "PDF → raster at configured 300 DPI; image → decode + EXIF orientation\n"
           "Page boundary reliable: perspective warp; otherwise reliable deskew or no warp\n"
           "Corrected image I: uint8 [Hc, Wc, 3]; retain original and rendered pages\n"
           "Example A4: Wc=2480, Hc=3508; dimensions are not fixed for every upload\n"
           "Store source ↔ render ↔ corrected transforms; keep the pen ink", "neutral")
    g.node("ocrdet", "PRETRAINED TEXT DETECTOR • en_PP-OCRv3_det_infer",
           "I → engine BGR convention → resize to detector grid Hd × Wd\n"
           "Scale / normalize with pinned detector processor → float32 [1, 3, Hd, Wd]\n"
           "DB inference probability map: [1, 1, hD, wD] (export-dependent grid)\n"
           "Threshold + contours + polygon expansion → text quadrilaterals\n"
           "Undo detector resize → Q: float [Ntext, 4, 2] in corrected-page pixels", "ocr")
    g.node("ocrcls", "TEXT CROPS + PRETRAINED ORIENTATION CLASSIFIER",
           "Perspective-crop each quadrilateral from full-resolution I\n"
           "Normalize / pad crops → float32 [Nc, 3, 48, 192]\n"
           "ch_ppocr_mobile_v2.0_cls_infer → class scores [Nc, 2]\n"
           "Decode 0° / 180°; rotate crop if accepted; page frame stays unchanged", "ocr")
    g.node("ocrrec", "PRETRAINED TEXT RECOGNISER • en_PP-OCRv4_rec_infer",
           "Oriented crops → resize height 48; pad width Wrec within crop batch\n"
           "float32 [Nc, 3, 48, Wrec]; (pixel / 255 − 0.5) / 0.5\n"
           "Wrec is variable; 320 is a nominal configuration, not a page width\n"
           "CTC inference probabilities [Nc, Trec, Vocr] → character IDs\n"
           "Collapse repeated IDs / blanks → strings + recognition confidence\n"
           "Trec = sequence steps; Vocr = checkpoint alphabet including blank", "ocr")
    g.node("m4out", "M4 OUTPUT → DocumentPage + located TextBox records",
           "For each region: text, actual granularity, confidence, source box ID\n"
           "Corrected and original-render quads; reading order; page quality / failures\n"
           "Current wrapper: line segments, NOT verified word boxes\n"
           "Corrected image artifact + transform + OCR weight/config versions\n"
           "The public OCR wrapper returns decoded records, not the internal tensors", "ocr")
    g.chain("render", "ocrdet", "ocrcls", "ocrrec", "m4out")
    g.end()
    g.edge("upload", "render")

    g.cluster("m5", "M5 | Neural field extraction • LayoutLMv3 + record assembly", "#a78bfa")
    g.node("alignment", "REQUIRED ALIGNMENT ADAPTER • not implemented",
           "Supply verified words + word boxes + source OCR links\n"
           "Current M4 line boxes do not establish precise word locations\n"
           "Validate a word-location OCR/alignment path before training or serving\n"
           "Never divide a line box evenly and claim measured word geometry\n"
           "Keep ambiguous / missing alignment explicit; flag affected fields", "gate")
    g.node("m5prep", "TWO INPUT BRANCHES → ONE LayoutLMv3Processor",
           "Image: I BGR → RGB → 224 × 224; /255; normalize by pinned processor\n"
           "Base processor example: per-channel mean=0.5, std=0.5\n"
           "Text: verified words → BPE subwords; keep word_ids / source mapping\n"
           "Boxes: (x0,y0,x1,y1) → integer [0,1000] relative to Wc,Hc\n"
           "Subwords inherit their source word box; apply_ocr=False (reuse M4)\n"
           "Long pages → overlapping token windows; preserve page and window IDs", "m5")
    g.node("m5inputs", "NEURAL INPUT TENSORS • example L=512 padded tokens",
           "input_ids: int64 [B5, L]; attention_mask: int64 [B5, L]\n"
           "bbox: int64 [B5, L, 4]; pixel_values: float32 [B5, 3, 224, 224]\n"
           "Optional token_type_ids: int64 [B5, L] (usually zeros)\n"
           "B5 counts page windows, not repair rows; L includes special / pad tokens\n"
           "Repeat the page image for its windows; never silently truncate a page", "m5")
    g.node("m5net", "FINE-TUNED LayoutLMv3ForTokenClassification",
           "microsoft/layoutlmv3-base + task-specific classification head\n"
           "Text + 2D box embeddings; image → 14 × 14 patches of 16 × 16\n"
           "196 image patches + one visual CLS token = 197 visual tokens\n"
           "Base encoder hidden states: [B5, L + 197, 768]\n"
           "Head selects TEXT positions → raw logits [B5, L, K]\n"
           "Example proposal K=11: O + B/I × {PART, OPERATION, QTY, UNIT_PRICE, AMOUNT}", "m5")
    g.node("m5decode", "LOGITS → FIELD SPANS • deterministic decoding",
           "softmax(dim=−1) → [B5, L, K]; argmax → int64 [B5, L]\n"
           "Ignore padding / specials; map subwords back to words and OCR evidence\n"
           "Merge overlapping windows; resolve BIO spans; retain low confidence\n"
           "Field labels classify the OCR text; they do not generate new text\n"
           "PART marks a description span, not a canonical part ID or a row ID", "white")
    g.node("m5assemble", "FIELD SPANS → REPAIR ROWS • still required with a neural M5",
           "Group spans by page / row geometry; attach wrapped fields conservatively\n"
           "Map description → part / side; operation → vocabulary; parse exact decimals\n"
           "Retain original print; detect totals / tax / missing fields / uncertain associations\n"
           "Derive row boxes + amount boxes from real source geometry\n"
           "Neural token labels alone do not establish which amount belongs to which item", "rule")
    g.node("m5out", "M5 OUTPUT → LineItem[] + declaration completeness",
           "entry_id, page_id, description, part, side, operation, quantity\n"
           "Printed unit price / amount as decimal strings + currency / cost basis\n"
           "row_box_norm + amount_box_norm [x0,y0,x1,y1] in corrected frame [0,1]\n"
           "Source box IDs; field uncertainty; model / tokenizer / mapping versions\n"
           "Unknown identity and unreadable amounts stay unresolved", "m5")
    g.chain("alignment", "m5prep", "m5inputs", "m5net", "m5decode", "m5assemble", "m5out")
    g.end()
    g.edge("m4out", "alignment", "text + geometry")

    g.cluster("m6", "M6 | Pen marks • trained detector + deterministic row linking", "#fb923c")
    g.node("m6prep", "DETECTOR INPUT • full corrected page, with pen marks",
           "I BGR → RGB → float /255 → list of B6 tensors [3, Hc, Wc]\n"
           "M5 rows are NOT neural detector inputs\n"
           "Torchvision internally normalizes: mean=(.485,.456,.406), std=(.229,.224,.225)\n"
           "Aspect resize: short side 800, cap long side 1333 (proposed recipe)\n"
           "A4 example ≈ 800 × 1131 (W × H); pad batch to multiples of 32\n"
           "Internal tensor [B6, 3, Hpad, Wpad]; retain each unpadded image size", "m6")
    g.node("m6backbone", "FINE-TUNED Faster R-CNN • ResNet-50 + FPN + RPN",
           "COCO-pretrained initialization; custom 3-class box predictor\n"
           "Feature pyramid: per-level [B6, 256, Hl, Wl]\n"
           "RPN: objectness [B6, A, Hl, Wl]; box deltas [B6, 4A, Hl, Wl]\n"
           "Decode anchors + filter / NMS → proposal boxes [Ri, 4] per page\n"
           "A = anchors per location; Ri varies; proposals do not yet identify mark type", "m6")
    g.node("m6head", "RoI HEAD • native tensors before inference filtering",
           "RoIAlign each proposal → [R, 256, 7, 7]; R = sum(Ri)\n"
           "Box head → class logits float [R, 3] + box deltas float [R, 12]\n"
           "Classes: 0 background; 1 exclusion; 2 price_change (proposed ID map)\n"
           "12 = 3 classes × 4 box coordinates; these are regression deltas", "m6")
    g.node("m6post", "TORCHVISION INFERENCE POSTPROCESSING",
           "Class softmax + box decoding; remove background / low-score / tiny boxes\n"
           "Per-class non-maximum suppression (NMS); retain configured top detections\n"
           "Undo internal resize → boxes in the input corrected-page pixels\n"
           "No second softmax on already returned scores; no second inverse resize", "white")
    g.node("m6outputs", "PUBLIC MODEL OUTPUT • list of B6 dictionaries",
           "For page i: boxes float [Ni, 4] in xyxy; labels int64 [Ni]; scores float [Ni]\n"
           "Ni varies by page; boxes locate marks, NOT rows or recognised amounts\n"
           "Convert boxes to corrected-frame [0,1] for the linker\n"
           "Attach page ID / versions; preserve confidence and filtering provenance\n"
           "Successful Ni=0 differs from failed or skipped detection", "m6")
    g.node("link", "COMBINE M5 + M6 • mark-to-row geometry",
           "Inputs: detections + M5 row/amount boxes; same page and input revision\n"
           "Measure row-band overlap, containment, coverage and column match\n"
           "Price changes: use unique amount overwrite or a clear row-score margin\n"
           "Unambiguous → entry_id; ambiguous → no link + ranked candidate rows\n"
           "Retain conflicts; missing rows never mean an empty estimate", "rule")
    g.node("m6out", "M6 OUTPUT → PenMark[] • every model proposal pending",
           "mark_id, page_id, mark_type, box, detection_confidence\n"
           "entry_id or candidate_entry_ids + link reason / component scores\n"
           "state=pending; confirmed_amount is absent\n"
           "Keep detector and linking versions; preserve evidence for human review\n"
           "Optional TrOCR: crop → text suggestion only; outside this core diagram", "m6")
    g.chain("m6prep", "m6backbone", "m6head", "m6post", "m6outputs", "link", "m6out")
    g.end()
    g.edge("render", "m6prep", "corrected page I", constraint=False)
    g.edge("m4out", "m6prep", "image artifact / lineage")
    g.edge("m4out", "m5prep", "same corrected page I", constraint=False)
    g.same("alignment", "m6prep")
    g.same("m5net", "m6post")
    g.same("m5out", "m6out")
    g.edge("m5out", "link", "row + amount boxes", constraint=False)

    g.node("review", "M9 REVIEW + M6 STATE RULES • human action",
           "Confirm / reject / relink / add a mark; type the exact revised amount\n"
           "Pending or conflicting marks withhold affected decisions\n"
           "Only confirmed exclusion skips a row; confirmed repricing requires amount + currency + basis\n"
           "Pending repricing NEVER falls back to the printed amount\n"
           "Keep printed, surveyor-agreed and final-approved amounts separate", "gate")
    g.node("m8", "M8 CONSUMES STRUCTURED EVIDENCE • no neural fusion",
           "M5 rows + completeness + M6 marks/actions + M3 photo evidence + pinned M7 costs\n"
           "Initial assessment may withhold checks; decision-changing review creates new input/assessment revisions\n"
           "Reuse compatible evidence by lineage; preserve original machine outputs and human history\n"
           "Source overlays: corrected coordinates → rendered page → original image pixels / PDF points", "rule")
    g.node("legend", "READING THIS DRAWING • implementation and shape boundaries",
           "Blue = pretrained OCR; purple = neural M5; orange = trained M6; green = deterministic logic; amber = gate / review\n"
           "M4 adapter, rule-based M5 and M6 linker exist; neural M5 / M6 inference integration remains pending\n"
           "User direction: neural M5 is planned; LayoutLMv3 and BIO-11 are illustrated design choices, not trained results\n"
           "Ntext = OCR regions; Nc = crop minibatch; B5 = token-window batch; B6 = page batch; Ni = detections on page i\n"
           "OCR internals are explanatory, not persisted tensors; exact grids / dictionary size follow pinned exports\n"
           "Detection can run beside M5, but linking waits for rows; current stage plan is M4 → M5 → M6\n"
           "Sources, training targets and outstanding decisions: docs/document-neural-workflow.md", "neutral")
    g.edge("m6out", "review")
    g.edge("m5out", "review", "printed scope")
    g.chain("review", "m8", "legend")
    return g


def example():
    g = Drawing("Worked example | Why model tensors still need record assembly",
                "Illustrative data only • corrected page Wc=1000, Hc=1400 • coordinates are xyxy")
    g.node("page", "ONE MARKED WORKSHOP ESTIMATE",
           "DESCRIPTION            OPERATION       QTY       AMOUNT\n"
           "Front bumper              Repair                 1           800.00\n"
           "                                                                  handwritten 650 above price\n"
           "Front fender                Replace              1           450.00 + X\n"
           "Original print and pen marks remain on the same image", "neutral")
    g.node("words", "M4 → VERIFIED ALIGNMENT → M5 INPUT",
           "Illustrative word sequence: Front | bumper | Repair | 1 | 800.00\n"
           "Amount word box: [780,400,880,425] in corrected-page pixels\n"
           "LayoutLMv3 box: [780,285,880,303] on its integer 0–1000 grid\n"
           "Tokenize words → subwords; inherit boxes; keep word/source IDs\n"
           "This example assumes the pending word-alignment adapter has been validated", "m5")
    g.node("labels", "M5 LOGITS → SPANS",
           "One page window: logits [1,512,11]; class probabilities → label IDs\n"
           "Ignoring specials and showing word-level decoded labels:\n"
           "Front       bumper       Repair          1          800.00\n"
           "B-PART    I-PART     B-OPERATION    B-QTY    B-AMOUNT\n"
           "The model labels the existing printed string 800.00; it does not output money directly", "m5")
    g.node("row", "M5 ASSEMBLY → row-1",
           "description='Front bumper'; part=front-bumper; operation=repair\n"
           "quantity=1; printed_amount='800.00'; currency=SGD; basis retained\n"
           "row box [100,400,900,425] → normalized [.10,.2857,.90,.3036]\n"
           "amount box → [.78,.2857,.88,.3036]; retain full source precision\n"
           "Side comes only from explicit evidence / taxonomy; never guessed", "rule")
    g.node("marks", "M6 PUBLIC DETECTIONS • hypothetical tensors",
           "boxes [2,4] = [[775,370,890,395], [780,470,875,500]]\n"
           "labels [2] = [2,1] → price_change, exclusion\n"
           "scores [2] = [.94,.96] → detection scores, not amount accuracy\n"
           "First box contains handwritten 650, but detector does NOT read 650\n"
           "Coordinates already refer to the corrected page after model postprocessing", "m6")
    g.node("linked", "GEOMETRIC LINKER → pending proposals",
           "mark-1 above row-1 amount → price_change linked to row-1 if thresholds pass\n"
           "mark-2 on row-2 amount → exclusion linked to row-2 if unambiguous\n"
           "Both state=pending; no confirmed amount yet\n"
           "If a mark sits between two plausible rows: retain both candidates instead", "rule")
    g.node("human", "SURVEYOR ACTIONS → NEW REVISION",
           "Confirm mark-1 and type '650.00', SGD, cost basis\n"
           "Confirm mark-2 exclusion\n"
           "row-1: preserve printed 800.00; assessment uses confirmed 650.00\n"
           "row-2: preserve printed 450.00; confirmed exclusion skips row\n"
           "Final approval is separate; rejected / superseded proposals remain in history", "gate")
    g.chain("page", "words", "labels", "row", "linked", "human")
    g.edge("page", "marks", "page pixels")
    g.edge("marks", "linked")
    g.same("marks", "labels")
    return g


def training():
    g = Drawing("Model preparation | What is pretrained, what must be learned?",
                "Planned neural M5 path • training targets are different from runtime inputs")
    g.node("ocr", "M4 • USE PRETRAINED OCR WEIGHTS",
           "Text detector + crop angle classifier + text recogniser\n"
           "No project-specific gradient training planned\n"
           "Validate text, amounts, quality flags and coordinate round trips on real pages\n"
           "Establish actual word locations before the proposed neural M5 route", "ocr")
    g.node("m5data", "M5 TRAINING EXAMPLES",
           "Corrected page + OCR words / real boxes + field and row annotations\n"
           "Optional CORD adaptation → generated workshop estimates → held-out project evaluation\n"
           "CORD does not supervise repair operations; project data must provide OPERATION\n"
           "Keep page / template / writer groups separate; retain OCR errors and uncertain alignment", "m5")
    g.node("m5train", "M5 • FINE-TUNE LayoutLMv3 + TOKEN HEAD",
           "Runtime inputs: ids [B,L], mask [B,L], bbox [B,L,4], pixels [B,3,224,224]\n"
           "Training adds labels int64 [B,L], example IDs 0..10 for BIO-11\n"
           "Ignore index −100 for padding, special tokens and invalid supervision\n"
           "Proposed first-subword supervision; continuation policy must match decoding\n"
           "Cross-entropy over valid tokens → update encoder + classification head\n"
           "Row associations evaluate the assembler; they are not a token-head output", "m5")
    g.node("m5eval", "M5 VALIDATION + RELEASE ARTIFACTS",
           "Token/entity quality AND complete-entry F1 / row association / exact amount accuracy\n"
           "Select checkpoint + thresholds on validation; final test remains untouched\n"
           "Bundle weights, processor, tokenizer, label map, window/decode rules, mappings and versions\n"
           "A generic pretrained backbone is not a ready repair-estimate extractor", "rule")
    g.node("m6data", "M6 TRAINING EXAMPLES",
           "Generated marked estimates + real development pages\n"
           "Correct with the same M4 geometry used at serving time\n"
           "Each image: mark bounding boxes + exclusion / price_change labels\n"
           "Also retain true row links for separate linker evaluation\n"
           "Use real held-out pages; separate writers / physical pages / template groups", "m6")
    g.node("m6train", "M6 • FINE-TUNE Faster R-CNN",
           "Start COCO-pretrained ResNet-50 FPN; replace predictor for 3 classes\n"
           "Inputs: list of RGB tensors [3,Hc,Wc]\n"
           "Targets per page: boxes float32 [Mi,4]; labels int64 [Mi] in {1,2}\n"
           "Mi = ground-truth marks; images with no marks have empty targets\n"
           "Train mode returns scalar losses: RPN objectness / box + RoI class / box\n"
           "Eval mode returns boxes [Ni,4], labels [Ni], scores [Ni]", "m6")
    g.node("m6eval", "M6 VALIDATION + RELEASE ARTIFACTS",
           "Evaluate detection and row linking BEFORE human corrections\n"
           "Report synthetic and real-page results separately\n"
           "Bundle checkpoint, transform config, class IDs, thresholds and linker config\n"
           "Detecting a price change never trains numeric handwriting recognition", "rule")
    g.node("optional", "OPTIONAL TrOCR • a separate handwriting suggestion model",
           "Price-change crop → pinned pretrained processor / recogniser → text suggestion\n"
           "No project fine-tuning committed; evaluate exact monetary-value accuracy\n"
           "Suggestion stored separately; human confirmation still required\n"
           "Outside the core M4–M6 tensor path", "neutral")
    g.edge("ocr", "m5data", "OCR / alignment")
    g.edge("ocr", "m6data", "shared geometry")
    g.chain("m5data", "m5train", "m5eval")
    g.chain("m6data", "m6train", "m6eval")
    g.same("m5data", "m6data")
    g.same("m5train", "m6train")
    g.same("m5eval", "m6eval")
    g.edge("m6eval", "optional", dashed=True)
    return g


def viewer(stems):
    tabs = ''.join(f'<button type="button" data-view="{i}" aria-pressed="{str(i == 0).lower()}">{title}</button>'
                   for i, (_, title) in enumerate(stems))
    panels = []
    for i, (stem, title) in enumerate(stems):
        svg = (OUT / f"{stem}.svg").read_text()
        svg = svg[svg.index('<svg'):]
        # Prefix graph IDs to keep all embedded SVG IDs unique.
        import re
        svg = re.sub(r'id="([^"]+)"', lambda m: f'id="v{i}-{m[1]}"', svg)
        svg = svg.replace('<svg ', f'<svg role="img" aria-label="{html.escape(title)}" ', 1)
        links = ' · '.join(f'<a href="{stem}.{ext}">{ext.upper()}</a>' for ext in ('svg', 'png', 'pdf', 'dot'))
        panels.append(f'<section id="view-{i}" {"hidden" if i else ""}><p>{links}</p>'
                      f'<div class="viewport"><div class="drawing">{svg}</div></div></section>')
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>M4–M6 document tensor workflow</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#0f172a;font:16px system-ui,sans-serif}
header{padding:24px 32px;background:white;border-bottom:1px solid #cbd5e1}h1{font-size:26px;margin:0 0 10px}
p{line-height:1.5;max-width:1100px}nav,.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.controls{margin-top:12px}button,a{font:inherit}button{padding:9px 14px;border:1px solid #94a3b8;border-radius:7px;background:white;cursor:pointer}
button[aria-pressed=true]{background:#e0e7ff;border-color:#6366f1}button:focus-visible,a:focus-visible{outline:3px solid #2563eb;outline-offset:3px}
main{padding:0 24px 24px}.viewport{overflow:auto;border:1px solid #cbd5e1;background:#f8fafc;height:78vh}
.drawing{width:100%;min-width:680px}.drawing svg{display:block;width:100%;height:auto}a{color:#1d4ed8}
@media print{header nav,.controls,section>p{display:none}.viewport{height:auto;overflow:visible;border:0}.drawing{width:100%!important;min-width:0}}
</style></head><body><header><h1>M4 → neural M5 → M6</h1>
<p>Uploaded estimate → OCR tensors → neural field labels → repair rows → detected marks → reviewed scope.
LayoutLMv3 is the illustrated M5 candidate. Amber boxes identify the alignment prerequisite and human review.
This is a proposed integration design, not an inference result.</p><nav aria-label="Choose drawing">TABS</nav>
<div class="controls" aria-label="Diagram zoom"><button id="out" aria-label="Zoom out">−</button>
<button id="fit">Fit width</button><button id="in" aria-label="Zoom in">+</button><output id="scale" aria-live="polite">100% of width</output></div>
</header><main>PANELS<p><a href="../../../docs/document-neural-workflow.md">Detailed notes and source references</a></p></main>
<script>let zoom=100;function setZoom(n){zoom=Math.max(50,Math.min(350,n));document.querySelectorAll('.drawing').forEach(d=>d.style.width=zoom+'%');document.getElementById('scale').textContent=zoom+'% of width';}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('main section').forEach((p,i)=>p.hidden=i!==Number(b.dataset.view));document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));}));
document.getElementById('in').addEventListener('click',()=>setZoom(zoom+25));document.getElementById('out').addEventListener('click',()=>setZoom(zoom-25));document.getElementById('fit').addEventListener('click',()=>setZoom(100));</script></body></html>'''
    (OUT / 'index.html').write_text(page.replace('TABS', tabs).replace('PANELS', ''.join(panels)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stems = [("document-tensor-workflow", "Full tensor workflow"),
             ("document-worked-example", "Worked estimate example"),
             ("document-model-training", "Models and training")]
    for drawing, (stem, _) in zip((workflow(), example(), training()), stems):
        drawing.render(stem)
    viewer(stems)
    print(f"Rendered three diagrams (SVG, PNG, PDF, DOT) and viewer: {OUT / 'index.html'}")


if __name__ == '__main__':
    main()
