# Solution Architecture and Data Flow

**Project:** Cross-Modal Damage and Cost Verification for Motor Insurance Claims
**Companion:** `problem_statement_v1.md`
**Status:** For team agreement

---

## 1. Online — inference on one claim

```
╔════════════════════════════════════════════════════════════════════════════╗
║                           ONLINE  ·  PER CLAIM                             ║
╚════════════════════════════════════════════════════════════════════════════╝

      ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
      │  N damage photos  │   │  surveyor report  │   │  vehicle attrs    │
      │  (tens–hundreds)  │   │  scan / PDF       │   │  make/model/year  │
      └─────────┬─────────┘   └─────────┬─────────┘   └─────────┬─────────┘
                │                       │                       │
   ┌────────────▼────────────┐  ┌───────▼────────────────┐      │
   │  ① VISION BRANCH        │  │  ② DOCUMENT BRANCH     │      │
   │                         │  │                        │      │
   │  ┌───────────────────┐  │  │  ┌──────────────────┐  │      │
   │  │ part segmentation │  │  │  │ text + layout    │  │      │
   │  │ ViT · 21 classes  │  │  │  │ detection (OCR)  │  │      │
   │  └─────────┬─────────┘  │  │  └────────┬─────────┘  │      │
   │            ▼            │  │           ▼            │      │
   │  ┌───────────────────┐  │  │  ┌──────────────────┐  │      │
   │  │ damage segment'n  │  │  │  │ line-item        │  │      │
   │  │ ViT ·  8 classes  │  │  │  │ recognition      │  │      │
   │  └─────────┬─────────┘  │  │  │ LayoutLMv3/Donut │  │      │
   │            ▼            │  │  └────────┬─────────┘  │      │
   │  ┌───────────────────┐  │  └───────────┼────────────┘      │
   │  │ mask intersection │  │              │                   │
   │  │   part × damage   │  │              ▼                   │
   │  └─────────┬─────────┘  │      DECLARED SCOPE              │
   │            ▼            │   [(part, operation, cost), …]   │
   │  ┌───────────────────┐  │              │                   │
   │  │ multi-view        │  │              │                   │
   │  │ aggregation       │  │              │                   │
   │  │ (sense-making)    │  │              │                   │
   │  └─────────┬─────────┘  │              │                   │
   └────────────┼────────────┘              │                   │
                ▼                           │                   │
     OBSERVED DAMAGE STATE                  │                   │
  [(part, damage type, confidence), …]      │                   │
                │                           │                   │
                └───────────┬───────────────┴───────────────────┘
                            ▼
             ┌──────────────────────────────┐      ┌────────────────────┐
             │  ③ RECONCILIATION ENGINE     │      │  REFERENCE COST    │
             │                              │ read │  TABLE             │
             │  a) coverage check           │◄─────┤                    │
             │     declared ⊆ observed?     │      │  key:  part ×      │
             │     → phantom part,          │      │        damage ×    │
             │       panel spillover        │      │        veh class   │
             │                              │      │  val:  [lo, hi]    │
             │  b) cost band check          │      │        band        │
             │     declared cost vs [lo,hi] │      └─────────▲──────────┘
             │     → labour inflation,      │                │
             │       grade substitution     │                │
             │                              │                │
             │  c) evidence sufficiency     │                │
             │     part declared but in     │                │
             │     no photo → request view, │                │
             │     do NOT flag              │                │
             └──────────────┬───────────────┘                │
                            ▼                                │
             ┌──────────────────────────────┐                │
             │  ④ SURVEYOR WORKBENCH (UI)   │                │
             │                              │                │
             │  · pre-filled parts list     │                │
             │  · flags + reasons           │                │
             │  · mask overlay on photo     │                │
             │  · surveyor confirms / edits │                │
             │    and enters agreed amount  │                │
             └──────────────┬───────────────┘                │
                            │                                │
                            └────────────────────────────────┘
                              WRITE-BACK
                              confirmed (part, damage, agreed cost)

                    ══ this loop is what builds the baseline ══
```

## 2. Offline — training

```
╔════════════════════════════════════════════════════════════════════════════╗
║                          OFFLINE  ·  TRAINING                              ║
╚════════════════════════════════════════════════════════════════════════════╝

   PART masks              DAMAGE masks             DocILE / CORD
   998 img · 21 cls        814 img ·  8 cls         6.7k docs · LIR track
   (polygon, Supervisely)  (polygon, Supervisely)   (line-item annotated)
         │                       │                        │
         └───────────┬───────────┘                        │
                     ▼                                    ▼
   ┌──────────────────────────────────┐    ┌────────────────────────────┐
   │  shared segmentation harness     │    │  VDU fine-tune             │
   │  Supervisely → COCO → SegFormer  │    │  LayoutLMv3 / Donut        │
   │  (two heads, one codebase)       │    │                            │
   └────────────────┬─────────────────┘    └─────────────┬──────────────┘
                    ▼                                    ▼
          part + damage weights                  line-item weights
                    │                                    │
                    └──────────────► ① ◄─────────────────┘  ──► ②


   prices_dataset.csv                    synthetic surveyor reports
   630 rows · 18 parts × 5 models        · layout transfer target ONLY
   × 7 years × 3 competing shops         · never used for accuracy claims
         │                                        │
         │  3 quotes per part = observed           └──► ② (transfer eval)
         │  legitimate price dispersion
         ▼
   ┌──────────────────────────────────┐
   │  cost band model                 │
   │  GBM on (part, damage, vehicle)  │
   │  + quantile / conformal interval │
   │            → [lo, hi]            │
   └────────────────┬─────────────────┘
                    ▼
          REFERENCE COST TABLE  ──► ③
          (seeded offline, grown online by write-back)
```

## 3. Where each PRS requirement is satisfied

| Requirement                          | Component                                          |
|--------------------------------------|----------------------------------------------------|
| Supervised learning                  | ① part + damage segmentation, ② line-item recognition |
| Unsupervised learning                | ③ cost outlier detection — no fraud labels used    |
| Machine learning / deep learning     | ViT segmentation, transformer VDU, GBM cost model  |
| Hybrid / ensemble approach           | ③ fusion of two independently-trained modalities   |
| Intelligent sensing / sense-making   | ① multi-view aggregation → one vehicle-level state |

Four of the four listed aspects; the module requires three.

## 4. Ownership — 5 members × 10 man-days

| # | Owner lane                                                    | Blocks    |
|---|---------------------------------------------------------------|-----------|
| 1 | Part segmentation (21 classes)                                | ①         |
| 2 | Damage segmentation (8 classes) + multi-view aggregation      | ①         |
| 3 | Document branch — OCR + line-item recognition                 | ②         |
| 4 | Reconciliation engine + cost band model + reference table     | ③         |
| 5 | System: API, workbench UI, pipeline, eval harness, deliverables | ④       |

Lanes 1 and 2 share one training harness — same data format, same pipeline,
different label sets — so they pair on the codebase and split the heads and
evaluation rather than building twice.

## 5. Interfaces to fix before coding

These are the only three contracts that cross lane boundaries. Agree them in
the first session and the lanes can then run independently.

```
  OBSERVED DAMAGE STATE   (lane 1,2 → lane 4)
    [ { part: str, damage: str, confidence: float,
        photo_ids: [str], mask_area_px: int } ]

  DECLARED SCOPE          (lane 3 → lane 4)
    [ { part: str, operation: "repair"|"replace",
        cost: float, line_no: int, page: int } ]

  ASSESSMENT              (lane 4 → lane 5)
    [ { part: str, damage: str, band: [lo, hi], declared: float|null,
        flag: "ok"|"cost_outlier"|"unsupported"|"insufficient_evidence",
        reason: str, evidence: [photo_id] } ]
```

A shared part vocabulary is a prerequisite: the 21 mask classes, the 18 priced
parts, and the report's part names must be mapped to one canonical list before
lane 4 can do anything. Assign that mapping explicitly — it is small, boring,
and blocks everything.
