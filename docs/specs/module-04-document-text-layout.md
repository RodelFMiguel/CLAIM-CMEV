# M04 - Document text and layout extraction

Owner: Lane 3. Runtime: document worker. Code: `src/claim_cmev/documents/text_layout/`; offline experiments: `pipelines/documents/`. Source: [proposal](../CLAIM-CMEV_project_proposal_v1.md), Module 4 and section 12.5. Status: specified; implementation pending.

## Purpose and scope

Read report pages while preserving a reliable route from extracted content back to the original document. This module extracts text/layout; M05 identifies repair entries and maps their fields.

## Inputs, outputs, and dependencies

| Direction | Contract |
| --- | --- |
| Input | Validated report file ID/hash, claim/input revision, extraction/render configuration and model/tool versions |
| Output | `DocumentPage`: page number, render reference, text/tokens/regions, locations, reading order, confidence/quality and extraction route |
| Consumers | M05 entry recognition; M09 source evidence viewer |
| Dependencies | File/render storage, [data contracts](data_contracts.md), document worker |

Use one-based page numbers and normalised top-left boxes with page dimensions, rotation and render transforms. Preserve original text. Never invent confidence for a text extractor that does not provide a calibrated score.

## Processing specification

1. Inspect page count, readability, rotation, and embedded text.
2. Extract embedded text where usable; use OCR for scanned/unusable pages under a documented quality rule.
3. Retain the page image/render and coordinate transform for all extraction routes.
4. Reconstruct tokens/regions and reading order while preserving table locations.
5. Save page-level extraction quality/errors and report completeness.
6. Publish stable page/region identifiers for M05.

A mixed PDF can use different routes per page. If Donut/direct-image recognition is selected, provide page images and an evaluated equivalent source-localisation path; generated field strings without locations do not satisfy evidence navigation.

## Failure and uncertainty handling

Encrypted/unreadable/corrupt documents produce explicit intake or extraction errors. A partly failed report retains successful pages but marks completeness; it must not silently declare an incomplete list to be the entire scope. Low-quality text remains uncertain for M05. Detect obviously unusable embedded text instead of treating its presence as proof of quality.

No report is a valid photograph-only claim state and does not schedule a fictitious document job.

## Acceptance criteria

- Digital, scanned, mixed and rotated-page fixtures retain correct original source locations.
- M05 can navigate every usable token/region to the correct file/page.
- Failed pages and unreliable text are visible and cannot become confident missing-repair findings.
- Extraction route/configuration versions are persisted; retry reuses page identities.
- Establish quality and source-location baselines; the proposal specifies no standalone numeric M04 target.

## Implementation tasks

- [ ] Select PDF rendering/text tools and OCR engine; record dependencies and licence checks.
- [ ] Define embedded-text quality and OCR fallback policy.
- [ ] Implement page rendering, rotation and coordinate transforms.
- [ ] Implement text/region ordering, quality signals and report completeness.
- [ ] Validate digital/scanned/mixed/rotated/encrypted/unreadable examples.
- [ ] Define direct-image localisation if Donut is selected with M05.
- [ ] Integrate page artifacts and evidence links through the backend.
- [ ] Measure extraction/source-location quality and document limits.

## Open decisions

OCR engine, rendering resolution, quality thresholds, supported file limits and direct-image localisation feasibility. Final report limits belong in versioned intake configuration.
