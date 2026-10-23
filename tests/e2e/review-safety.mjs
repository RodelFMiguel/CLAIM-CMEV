// Review-screen safety rules, with synthetic API responses; writes are intercepted, never stored.
//  1. Confirmation forms never pre-fill a side, an unresolved part or a coverage decision.
//  2. An undecided amount (pending mark) makes the agreed total "pending", never $0.
//  3. Supplementary upload never commits a revision without the claim's existing evidence,
//     and a retry reuses its idempotency keys.
// Run a local workbench, then:
//   CMEV_E2E_URL=http://127.0.0.1:4175 CMEV_BROWSER_CHANNEL=chrome node tests/e2e/review-safety.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:4175';

const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="240"><rect width="400" height="240" fill="#dde6ea"/></svg>');
const claim = { claim_id: 'safety-test', reference: 'SYNTHETIC-SAFETY', status: 'in_review',
  vehicle: { make: 'Test', model: 'Vehicle', year: 2026, plate: 'TEST' }, workshop: 'Test Workshop', currency: 'SGD',
  input_revision: 1, assessment_revision: 1, review_revision: 0, photograph_count: 2 };
const damageRow = (summary_id, part_code, extra = {}) => ({ summary_id, part_code, damage_codes: ['dent'],
  reason_codes: ['identity_not_resolved'], side: 'unknown', identity_status: part_code === 'unresolved' ? 'unresolved' : 'part_only',
  coverage: 'unresolved', photo_count: 1, source_kind: 'real', reason: 'Synthetic reason', ...extra });
const obs = (id, candidates) => ({ observation_id: id, photo_id: 'photo-1', damage_code: 'dent', damage_confidence: 0.8,
  assignment_status: 'unresolved', part_reason: 'ambiguous_between_parts', background_containment: 0.05,
  candidates: candidates.map(([part_code, containment], i) => ({ part_code, containment, rank: i + 1 })) });
const lineItem = (entry_id, description, printed, effective, mark_state, y0) => ({ entry_id, finding_id: `finding-${entry_id}`,
  description, page_id: 'page-1', part_code: 'front-bumper', side: 'not_applicable', operation: 'replace',
  printed_amount: printed, quantity: '1', row_state: 'active', row_box_norm: [0.05, y0, 0.95, y0 + 0.03], cost_range: null,
  effective_amount: effective, mark_state, photo_check: 'not_evaluated', cost_check: 'not_evaluated',
  overall_result: 'insufficient_evidence', reason: 'Synthetic reason', evidence_ids: ['estimate-1'], dismissal: null });
const assessment = { assessment_revision: 1, input_revision: 1, state: 'ready',
  line_items: [
    lineItem('row-bumper', 'Front bumper replace', '1000.00', null, 'pending', 0.2),  // pending price change
    lineItem('row-door', 'Front door repair', '480.00', '480.00', 'none', 0.3),
  ],
  marks: [{ mark_id: 'mark-1', page_id: 'page-1', entry_id: 'row-bumper', candidate_entry_ids: [], type: 'price_change',
    state: 'pending', reason: '', box_norm: [0.6, 0.2, 0.9, 0.23] }],
  pages: [], review_photo_ids: ['photo-1', 'photo-2'], declaration_completeness: 'complete', possible_additions: [],
  missing_repairs_check: { result: 'insufficient_evidence', reasons: [] },
  review_overlay: { dismissals: {}, addition_decisions: {}, accepted_scope: [] },
  finalize_preconditions: { can_finalize: false, checks: [] },
  versions: { cost_table: 'synthetic-test' }, evidence_sources: { image: 'real', document: 'real' },
  fixture_notice: 'Synthetic browser test data; no model run.',
  files: ['photo-1', 'photo-2'].map((file_id) => ({ file_id, original_name: `${file_id}.svg`, media_type: 'image/svg+xml',
    role: 'photograph', url: image })),
  damage_summary: [
    damageRow('uncertain', 'unresolved'),
    damageRow('fender', 'fender'),
    damageRow('bumper', 'front-bumper'),
  ],
  part_summaries: [
    { summary_id: 'uncertain', member_observation_ids: ['o-uncertain'] },
    { summary_id: 'fender', member_observation_ids: ['o-fender'] },
    { summary_id: 'bumper', member_observation_ids: ['o-bumper'] },
  ],
  observations: [obs('o-uncertain', [['front-bumper', 0.47], ['fender', 0.43]]), obs('o-fender', [['fender', 0.9]]),
    obs('o-bumper', [['front-bumper', 0.9]])],
};
const review = { review_revision: 0, assessment_revision: 1, finalized: false, actions: [] };
const existingFiles = [
  { file_id: 'photo-1', original_name: 'photo-1.png', media_type: 'image/png', role: 'photograph' },
  { file_id: 'photo-2', original_name: 'photo-2.png', media_type: 'image/png', role: 'photograph' },
  { file_id: 'estimate-1', original_name: 'estimate.png', media_type: 'image/png', role: 'estimate_page' },
];

const browser = await chromium.launch({ headless: true,
  ...(process.env.CMEV_BROWSER_CHANNEL ? { channel: process.env.CMEV_BROWSER_CHANNEL } : {}) });
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  context.setDefaultTimeout(10000);
  const errors = [], writes = [];
  const fail = { files: false, revision: false, commitOnce: false };
  await context.route('**/api/v1/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    if (request.method() !== 'GET') {
      writes.push({ path, key: request.headers()['idempotency-key'],
        body: path.endsWith('/files') ? null : request.postDataJSON() });
      if (path.endsWith('/files')) return route.fulfill({ json: { files: [{ file_id: 'photo-new' }] } });
      if (path.endsWith('/input-revisions')) {
        if (fail.commitOnce) { fail.commitOnce = false; return route.fulfill({ status: 503, json: { detail: { message: 'Synthetic outage' } } }); }
        return route.fulfill({ status: 202, json: { input_revision: 2, state: 'queued' } });
      }
      return route.fulfill({ json: { ok: true } });
    }
    let body;
    if (path === '/auth/me') body = { user: { id: 'synthetic-user', name: 'Test Reviewer', role: 'surveyor', initials: 'TR' } };
    else if (path.endsWith('/print-view')) body = { claim, assessment, review: { ...review, finalized: true }, disclaimer: 'Synthetic test' };
    else if (path.endsWith('/review')) body = review;
    else if (path.endsWith('/assessments/1')) body = assessment;
    else if (path.endsWith('/processing')) body = { state: 'completed', jobs: [] };
    else if (path.endsWith('/files')) {
      if (fail.files) return route.fulfill({ status: 500, json: { detail: { message: 'Synthetic files outage' } } });
      body = { files: existingFiles };
    } else if (path.endsWith('/input-revisions/1')) {
      if (fail.revision) return route.fulfill({ status: 500, json: { detail: { message: 'Synthetic revision outage' } } });
      body = { input_revision: 1, file_ids: existingFiles.map((f) => f.file_id) };
    } else if (path === '/claims/safety-test') body = claim;
    else return route.fulfill({ status: 404, json: {} });
    return route.fulfill({ json: body });
  });
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push(e.message));
  const open = async () => {
    await page.goto(base + '/claims/safety-test/review');
    await page.getByRole('heading', { name: 'Damage summary', exact: true }).waitFor();
  };
  await open();

  // 1a. Confirm-card modal: nothing the surveyor did not choose is pre-filled.
  const card = (heading) => page.locator('article.damage-item').filter({ has: page.locator('strong', { hasText: heading }) });
  const modal = page.locator('.quick-confirm-modal-card');
  const openConfirm = async (heading) => {
    await card(heading).getByRole('button', { name: 'Confirm Identity & Coverage' }).click();
    await modal.waitFor();
  };
  const coverageChoice = () => modal.locator('input[name=qc_coverage]:checked').count();
  for (const [heading, part, side] of [[/^Part uncertain$/, '', ''], [/^Fender$/i, 'fender', ''],
                                       [/^Front bumper$/i, 'front-bumper', 'not_applicable']]) {
    await openConfirm(heading);
    assert.equal(await modal.getByLabel('Vehicle Part').inputValue(), part, `${heading}: part`);
    assert.equal(await modal.getByLabel('Physical Side').inputValue(), side, `${heading}: side`);
    assert.equal(await coverageChoice(), 0, `${heading}: no coverage decision is pre-selected`);
    await modal.getByRole('button', { name: 'Cancel' }).click();
  }
  // Saving without a side or coverage decision sends nothing.
  await openConfirm(/^Fender$/i);
  await modal.getByRole('button', { name: 'Save Identity & Coverage' }).click();
  assert.equal(await modal.locator('form').evaluate((f) => f.checkValidity()), false, 'a side is required');
  await modal.getByLabel('Physical Side').selectOption('right');
  await modal.getByRole('button', { name: 'Save Identity & Coverage' }).click();
  await modal.getByRole('alert').waitFor();
  assert.equal(writes.length, 0, 'no write without an explicit side and coverage decision');
  await modal.locator('input[name=qc_coverage][value=inadequate]').check();
  await modal.locator('select').filter({ hasText: 'Choose a reason' }).selectOption('partially_visible');
  await modal.getByRole('button', { name: 'Save Identity & Coverage' }).click();
  await modal.waitFor({ state: 'detached' });
  assert.equal(writes.length, 1);
  const [identity, coverage] = writes[0].body.actions ?? writes[0].body;
  assert.deepEqual([identity.part_code, identity.side], ['fender', 'right']);
  assert.deepEqual([coverage.covers_enough, coverage.reason_code], [false, 'partially_visible']);
  writes.length = 0;
  console.log('PASS: confirm-card modal requires an explicit part, side and coverage decision');

  // 1b. Manual grounding form: coverage is not pre-selected either.
  const grounding = page.locator('details').filter({ has: page.locator('summary', { hasText: 'Manual Part Grounding' }) });
  await grounding.locator('summary').click();
  const form = grounding.locator('form');
  assert.equal(await form.locator('input[name=coverage_state]:checked').count(), 0, 'manual form: no coverage pre-selected');
  await form.locator('[name=part]').selectOption('front-door');
  await form.locator('[name=side]').selectOption('left');
  await form.locator('[name=photos]').selectOption('photo-1');
  await form.getByRole('button', { name: 'Save identity & coverage' }).click();
  await form.getByRole('alert').waitFor();
  assert.equal(writes.length, 0, 'manual form: no write without a coverage decision');
  console.log('PASS: manual grounding form requires an explicit coverage decision');

  // 2. Totals: a pending price change is not a $1,000 reduction.
  const bar = page.locator('.review-estimate-summary-bar');
  const barText = await bar.innerText();
  assert.doesNotMatch(barText, /-\s*\$?1,000\.00|-\s*SGD/, 'no net reduction from an undecided amount');
  assert.match(barText, /Pending/i);
  assert.match(barText, /1 row awaiting a decision/i);
  await page.goto(base + '/claims/safety-test/print?assessment=1&review=0');
  const total = page.locator('tfoot .report-table-total');
  await total.waitFor();
  const effectiveCell = await total.locator('td').nth(2).innerText();
  assert.doesNotMatch(effectiveCell, /\$/, 'print total does not count the pending row as zero');
  assert.match(effectiveCell, /Pending/i);
  console.log('PASS: undecided amounts make the agreed total pending in the review bar and the frozen report');

  // 3. Supplementary upload never drops the claim's existing evidence.
  const photoFile = { name: 'extra.png', mimeType: 'image/png', buffer: Buffer.from('synthetic') };
  const dialog = page.getByRole('dialog').filter({ hasText: 'Upload Supplementary Evidence' });
  const submit = () => dialog.getByRole('button', { name: /Upload & Reassess/ });
  const openUpload = async () => {
    await open();
    await page.getByRole('button', { name: 'Add Evidence' }).click();
    await dialog.waitFor();
    await dialog.locator('input[type=file]').first().setInputFiles(photoFile);
  };
  for (const outage of ['files', 'revision']) {
    fail.files = outage === 'files'; fail.revision = outage === 'revision';
    await openUpload();
    await dialog.getByRole('alert').first().waitFor();
    if (await submit().isEnabled()) await submit().click();
    await page.waitForTimeout(300);
    assert.deepEqual(writes, [], `${outage} outage: nothing is uploaded or committed`);
  }
  fail.files = fail.revision = false;
  fail.commitOnce = true;
  await openUpload();
  await submit().click();
  await dialog.getByRole('alert').filter({ hasText: 'Synthetic outage' }).waitFor();
  await submit().click();  // retry after the failed commit
  await dialog.waitFor({ state: 'detached' });
  const uploads = writes.filter((w) => w.path.endsWith('/files'));
  const commits = writes.filter((w) => w.path.endsWith('/input-revisions'));
  assert.equal(commits.length, 2);
  assert.deepEqual(commits[1].body.file_ids, ['photo-1', 'photo-2', 'estimate-1', 'photo-new']);
  assert.equal(new Set(uploads.map((w) => w.key)).size, 1, 'the retried upload reuses its idempotency key');
  assert.equal(commits[0].key, commits[1].key, 'the retried commit reuses its idempotency key');
  console.log('PASS: supplementary upload keeps existing evidence, blocks on load failure and retries idempotently');

  assert.deepEqual(errors, []);
} finally { await browser.close(); }
