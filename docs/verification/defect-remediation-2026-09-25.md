# Reported-defect remediation verification (2026-09-25)

Baseline: f280209. Changes are uncommitted. Existing architecture/effort documentation in CONTEXT.md was preserved. This records the defects identifiable from the supplied summary and ignored review reproductions; the separate individually numbered 32-finding report was not supplied in this conversation.

## Changes and evidence

| Reported defect group | Implemented behavior | Verification |
| --- | --- | --- |
| Rows after subtotal disappear | Scan subsequent sections and retain later totals/taxes | M5 section regression; four expected rows retained |
| Continuation rows before a later header disappear | Preserve unbound prefix regions and mark declaration partial | M5 continuation regression; source boxes retained, additions withheld by completeness |
| Poison message prevents all processing | Persist original in dead-letter store; publish bounded references with sanitized envelope metadata and transport/content deduplication | Runtime idempotency regression: malformed versions, inline binary and oversized payload followed by a successfully processed message |
| Unknown operation stalls reassessment | Represent unknown as null with unmapped status and uncertainty | HTTP correction -> worker -> new assessment regression |
| Confirmations degrade all coverage | Carry existing measured view signals through summary replay | Backend identity replay preserves adequate slots; M3 regression suite |
| Correction form chooses repair for unmapped operation | Initialize unknown explicitly | Focused Chromium regression |
| Unsided parser rows never reach OK | Vocabulary 0.2.0 uses taxonomy not_applicable with absent text source | Actual parser -> M8 bumper/hood OK; sided door without side remains insufficient, using explicit fixture image evidence |
| Last side confirmation wins for both sides in one photo | Retain conflicting statements and withhold photo-level identity | M3 grouping regression; no last-side assignment |
| Unreadable page treated as complete declaration | Include all page quality states; withhold additions and require human completeness review before finalization | HTTP extra-unreadable-page regression |
| Explicitly empty despite parsed rows | Reject with declaration_has_rows | HTTP regression; revision unchanged |
| Amount correction erases original extraction | Preserve first numeric extraction and original text through repeated corrections, API and frozen report | Backend repeated-correction/freeze regression; Chromium PDF export and text/visual verification |
| Upload discards decisions | Carry audit history; reuse corrections for unchanged source branches; expose invalidated corrections for changed branches | Added-photo regression preserves confirmed price and note; changed-source policy documented |
| Photos-only/pages-only cannot finalize | Allow completed supplied-input processing to freeze an explicitly incomplete-evidence report | HTTP finalization/report regression for each absent branch; failed processing still blocks |
| Outcomes look identical | Distinct result text, icons and border/background styles | Chromium unsupported-border assertion; production build |
| Tabs overwrite pending edits | Atomic compare/update/delete queue, correct authenticated actor, tab-specific drafts, duplicated-tab locks, legacy pending-request migration | Two-tab and duplicated-tab Chromium regression; reload preserves independent drafts and pending payload |
| Failed stages lack retry | Display retryable jobs even with an existing assessment | Browser retry control; supplied consolidate retry reproduction completes once and clears failures |
| Proxy login limiter affects unrelated users | Key failures by peer and normalized account; compare UTF-8 bytes safely | HTTP limiter regression for different accounts and non-ASCII password |
| Accepting addition does not change reassessment | Pass accepted human scope into consolidation and remove already-accepted suggestions | Backend and Chromium acceptance/reassessment checks; no fabricated declared row |
| View evidence opens unrelated file | Missing-link state; explicit file browsing clears row context | Focused Chromium regression with an unrelated available file |
| Refresh stops after 15 minutes | Continue polling while processing and recover automatically after connection failure | Chromium clock advancement beyond 15 minutes plus failed/successful polling |
| Schema/record mismatches | Align version/provenance bounds, expose decimal/box schema constraints, include optional PDF matrix | Contract suite; valid long record metadata accepted on wire; empty version rejected by both |
| Rotated/cropped PDF coordinates | Preserve render-to-original-PDF matrix, compose with inverse correction | Eight crop/rotation cases plus M4 adapter round trips; legacy matrix-less PDF conversion refuses explicitly |
| Incorrect identity-history before value | Read previous confirmed identity from current evidence | HTTP repeated-confirmation audit regression |
| Kafka producer leak on reconnect | Close producer and each already-created consumer in finally | Partial consumer-startup regression |
| Slow repeated database queries | Batch claim-list metadata, assessment-file reads and inherited-action source assessments | Batched list equals individual views with five SELECTs for repeated claim sets; backend suite |

## Executed checks

- Full Python suite: **1314 passed, 1 skipped**, 89.32 seconds. The skipped test requires real PaddleOCR weights/an OCR environment. Two existing Starlette/httpx deprecation warnings remain.
- Workbench TypeScript check and Vite production build: passed.
- Chromium baseline: upload, review, lost-acknowledgement retry and frozen-report flow passed.
- Chromium focused defects: two-tab queue/draft preservation, duplicate-tab isolation, legacy pending-request migration, unknown operation, unrelated-file handling, outcome styling, long-running/recovering polling and retry with an existing assessment passed.
- Chromium review controls: accepted addition, amount correction, dismissal, completeness confirmation, stale-draft recovery and tablet flow passed.
- Corrected synthetic claim CLM-24020: frozen assessment/review **4/6** exported as a three-page PDF. Original extraction **$1,150.00**, corrected printed reading **$1,160.00**, and independently confirmed effective price **$980.00** remain distinct. Verified against stored frozen report values and inspected the rendered affected page.
- `git diff --check`: passed.

## Environment, artifacts and limits

Python 3.12/SQLite, local transport, fixture stage producers, deterministic parser/comparison rules, synthetic reference costs, Node 22 and headless Chromium. No trained-model accuracy, live OCR, Kafka broker outage/recovery or production database performance claim is made.

Disposable browser database: `runtime/review-scratch/remediation-browser.db`. Ignored outputs: `artifacts/evaluation/ui-baseline/`, `artifacts/evaluation/ui-defects/`, `artifacts/evaluation/ui-review/frozen-report.pdf`, and `frozen-report-original-amount.png` in the same directory. Temporary API/worker/Vite processes were stopped after verification; data/artifacts were retained.

Conservative limits are intentional: unbound continuation regions need review; photo-level identity cannot resolve separate left/right instances within one image; corrections referring to replaced evidence require reconfirmation; legacy PDF records need regenerated geometry before original-PDF highlighting. Historical frozen reports are not rewritten. New amount fields and PDF matrices are optional under schema 0.2.0; unsided document mapping is pinned as vocabulary 0.2.0. Model integration/training and the broader remaining-delivery estimate in CONTEXT.md remain outstanding.
