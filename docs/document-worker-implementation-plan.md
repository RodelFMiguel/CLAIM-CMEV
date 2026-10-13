# Document worker implementation plan

Planning snapshot: 2026-10-11, Asia/Singapore, `document-worker` at `ec8bcba`.
This is a proposed implementation sequence, not a report of completed integration.
The user's intended neural M5 path is retained. Existing parser-based specifications
describe the earlier baseline; reconcile those sections as neural serving is implemented.

## Detailed module connections

```mermaid
flowchart TB
    upload["Estimate upload: PDF or page image<br/>Original bytes, file ID, hash, claim/input revision"]
    store[("MinIO: immutable originals and derived artifacts")]
    orch["Existing orchestrator + Kafka<br/>Dispatch commands; track completion and failures"]
    upload --> store
    upload --> orch

    subgraph M4["M4 · Page reading · library exists; live worker missing"]
      fetch["Fetch original; verify SHA-256<br/>PDF rasterization or image decode + EXIF"]
      geom["Boundary detection → perspective correction<br/>Otherwise safe deskew or no correction"]
      ocr["Pinned pretrained PaddleOCR<br/>Text + confidence + located regions"]
      words["IMPLEMENT / VALIDATE word localization<br/>Current engine returns line segments<br/>Never fabricate word boxes from a line"]
      page["DocumentPage + page_reading.json<br/>Quality / failure reasons; source box IDs<br/>Corrected and original coordinates<br/>Homography + inverse + PDF mapping"]
      fetch --> geom --> ocr --> words --> page
    end
    orch -->|"cmev.cmd.page-read.v1"| fetch
    store -.->|"original bytes"| fetch
    geom -->|"render.png + rectified.png"| store
    page -->|"cmev.evt.page-read.v1"| orch

    subgraph M5["M5 · Neural line-item extraction · local adapter exists; live serving missing"]
      input["Verify M4 image hash and dimensions<br/>Word text + true boxes + corrected image"]
      model["Local LayoutLMv3 checkpoint<br/>Image + text + normalized coordinates<br/>512-token windows, overlap, source alignment"]
      assembly["Word labels → page-local row slots → fields<br/>Description / operation / qty / unit price / amount<br/>Validate grouping and distinguish repair rows from totals"]
      parsing["Existing vocabulary + exact decimal parsing<br/>Explicit currency, cost basis and numeric conventions<br/>Keep missing / uncertain fields unresolved"]
      rows["LineItem + field source links + row/amount boxes<br/>DeclarationCompleteness + unparsed evidence<br/>Current adapter keeps coverage partial"]
      input --> model --> assembly --> parsing --> rows
    end
    orch -->|"cmev.cmd.line-items-extract.v1 after M4"| input
    page -.->|"DocumentPage / OCR artifacts"| input
    store -.->|"corrected page pixels"| input
    rows -->|"cmev.evt.line-items-extracted.v1"| orch

    subgraph M6["M6 · Pen marks · linker/state library exists; detector and live worker missing"]
      detect["IMPLEMENT trained detector on corrected page<br/>Exclusion X / price-change region<br/>Core path detects regions; does not read revised digits"]
      link["Existing geometric linker<br/>Intersect mark with M5 row / printed-amount boxes<br/>Unique link or explicit candidate / unlinked / conflict"]
      marks["PenMark proposals: pending<br/>Mark box, score, entry candidates, detector/config versions<br/>Detection artifact + page overlay"]
      detect --> link --> marks
    end
    orch -->|"cmev.cmd.pen-marks-detect.v1 after M4 + M5"| detect
    store -.->|"same corrected page"| detect
    rows -.->|"same revision: row and amount geometry"| link
    marks -->|"cmev.evt.pen-marks-detected.v1"| orch

    persist[("PostgreSQL + transactional outbox<br/>Machine records, jobs, version lineage, outbound events")]
    page --> persist
    rows --> persist
    marks --> persist
    page -->|"reading JSON"| store
    rows -->|"extraction artifact"| store
    marks -->|"detections + overlays"| store

    review["M9 evidence review + API<br/>Confirm / reject / relink / add missed mark<br/>Human types revised amount; original extraction retained"]
    revision["Decision-changing action<br/>New input / assessment revision<br/>Immutable machine results + human-action history"]
    checks["M8 deterministic consolidation<br/>M3 photo evidence + M5 declared scope + M6 state + pinned M7 costs<br/>Pending/conflicting/unlinked marks withhold decisions<br/>Only confirmed exclusions skipped; pending repricing has no printed-price fallback"]
    marks --> review
    rows --> review
    store -.->|"page and aligned overlays"| review
    review --> revision --> orch
    orch -->|"document and image branches ready"| checks
    persist -.->|"version-matched records"| checks

    classDef existing fill:#e7f4ec,stroke:#27764a,color:#153923
    classDef missing fill:#fff0d6,stroke:#ac6d00,color:#573800
    classDef records fill:#e8efff,stroke:#4167ad,color:#162f59
    class fetch,geom,ocr,model,parsing,link,orch existing
    class words,assembly,detect missing
    class page,rows,marks,persist,store records
```

Green denotes existing reusable components, not completed live integration.
Amber denotes a missing capability or an assembly policy needing validation.
Blue denotes exchanged/persisted evidence. All live module handlers still need wiring.
Dotted arrows are artifact/data dependencies; solid event arrows pass through the
orchestrator. M6 does not maintain its own cross-topic join.

## Implementation sequence and acceptance gates

| Step | Concrete work | Evidence required before moving on |
| --- | --- | --- |
| 1. Freeze document fixtures and geometry | Select supported estimate families and permitted real acceptance pages. Preserve claim-grouped splits. Inspect synthetic field labels and annotation text; add actual marked-page variants and detector labels. | Manifest/provenance, claim-level split checks, visible-label alignment and ground truth for rows, amounts and marks. |
| 2. Complete the M4-to-neural-M5 interface | Validate a true word-localization path on corrected pages; retain line evidence if useful without pretending it is word evidence. Pin OCR weights/config. Reuse raster, geometry and transform libraries. | Actual engine word granularity; text/amount accuracy on supported pages; inverse-transform round trips for photos and rotated/cropped PDFs; honest unreadable/partial outcomes. |
| 3. Serve M4 through existing runtime | Implement object-store reads/hash checks, deterministic artifacts, record persistence and completion events. Register handler with shared consumer/job/outbox infrastructure. Package pinned OCR dependencies and weights. | Upload-to-real-DocumentPage integration, multipage/partial failure handling, duplicate delivery, retry/DLQ and historical revision preservation. |
| 4. Validate and strengthen neural M5 | Reuse local LayoutLMv3 inference, first-subword alignment and overflow. Validate row-slot grouping, cross-window deduplication, wrapped descriptions, totals exclusion, field confidence and row/amount geometry. Keep exact currency/unit semantics. | Held-out field extraction, row association, amount exactness and omission recall; inspect failed examples. Until coverage is established, keep declaration partial. A successful inference is not an accuracy milestone. |
| 5. Serve M5 through existing runtime | Read persisted M4 records/images, validate pins and hashes, store shared line items/completeness and raw prediction artifacts, emit completion. Supply stable row and amount geometry to M6. | Real M4→M5 contract integration; long/multipage documents; absent/uncertain amounts; distinct repeated row IDs; no silent fallback to parser or empty-success output. |
| 6. Build M6 detection and integration | Prepare exclusion/price-change labels, fit and version a detector, measure false positives on unmarked and unrelated handwriting. Feed detections to the existing linker/state machine; persist proposals and overlays. | Per-class detection precision/recall, row-link accuracy, ambiguous/conflicting/unlinked cases and explicit page failures. Every new proposal starts pending. |
| 7. Finish review evidence and reassessment | Serve original/corrected pages and aligned row/mark overlays. Bind visible selection to stable IDs. Connect confirmation/rejection/relink/manual amount actions to revision handling. | Browser acceptance with real persisted artifacts; stale action rejection; original/printed/revised amounts separate; changed decisions create new assessments; frozen report matches review. |
| 8. Release the integrated document lane | Add explicit live-document producer selection and Compose services; remove fixture ownership only for selected stages. Validate M4→M5→M6→M8 end to end with the real image branch. | Startup fail-closed on missing weights/config; broker restart/replay; lean/full semantic parity in compatible environments; no skipped stage represented as passed; documented held-out limitations. |

Packaging constraint: the current vision extra requires Transformers 5 and
`document-model` requires Transformers 4. Use separate compatible worker images/environments
for the initial deployment. A single combined environment requires an explicitly
tested dependency reconciliation; do not assume the two extras can coexist.

## Worked review flow

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant P as M4 page reader
    participant L as M5 LayoutLMv3
    participant M as M6 detector/linker
    participant H as Surveyor + review API
    participant C as M8
    O->>P: Read revision R estimate
    P-->>O: Corrected pages, OCR boxes, quality, artifact references
    O->>L: Extract rows from revision R pages
    L-->>O: Entry E, printed amount SGD 980, row/amount boxes, partial coverage
    O->>M: Detect marks using R pages and R rows
    M-->>O: Price-change proposal linked to E, pending; no revised numeric value
    O->>C: Assess available evidence and pinned versions
    C-->>H: Withhold price decision while repricing is pending; preserve partial scope
    H->>H: Inspect page; confirm link; type SGD 850
    H->>O: Record decision-changing action and new input revision R+1
    O->>C: Assess matching revised inputs; reuse unchanged evidence when valid
    C-->>H: Keep printed 980 and effective 850 separate; scope remains partial until resolved
```

The synthetic generator currently draws field-value regions and handwriting-font
entries, not measured word OCR or a labelled exclusion/price-change detector dataset.
Random “Approved” remarks are generated text, not evidence of final approval.
It can support development fixtures but does not by itself close M4 alignment,
M5 real-page generalization or M6 mark training requirements.

## References

- [M4 specification](specs/module-04-page-reading.md)
- [M5 specification](specs/module-05-line-item-extraction.md)
- [M6 specification](specs/module-06-pen-mark-recognition.md)
- [Integration contracts](specs/integration_contracts.md)
- [Data contracts](specs/data_contracts.md)
- [Existing neural tensor workflow](document-neural-workflow.md)
- [Local LayoutLMv3 command](layoutlmv3_line_item_extraction.md)

Validation of this planning artifact: reviewed current source and specifications;
no inference, dataset regeneration, runtime deployment or model evaluation performed.
