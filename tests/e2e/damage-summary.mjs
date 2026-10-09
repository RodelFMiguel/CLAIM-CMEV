// Display-only acceptance with synthetic API responses. No claims or review actions are written.
// Run a local workbench, then: CMEV_E2E_URL=http://127.0.0.1:4175 node tests/e2e/damage-summary.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:4175';
const out = 'artifacts/evaluation/ui-damage-summary-20261009';
await mkdir(out, { recursive: true });
const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="240"><rect width="400" height="240" fill="#dde6ea"/><text x="35" y="120">Synthetic photograph placeholder</text></svg>');
const claim = { claim_id: 'display-test', reference: 'SYNTHETIC-DISPLAY', status: 'in_review',
  vehicle: { make: 'Test', model: 'Vehicle', year: 2026, plate: 'TEST' }, currency: 'SGD',
  input_revision: 1, assessment_revision: 1, review_revision: 0, photograph_count: 2 };
const row = (summary_id, part_code, damage_codes, reason_codes, extra = {}) => ({ summary_id, part_code,
  damage_codes, reason_codes, side: 'unknown', identity_status: part_code === 'unresolved' ? 'unresolved' : 'part_only',
  coverage: 'unresolved', photo_count: 1, source_kind: 'real', reason: 'Legacy reason', ...extra });
const obs = (id, damage, candidates, reason) => ({ observation_id: id, photo_id: 'photo-2',
  damage_code: damage, damage_confidence: 0.82, assignment_status: reason ? 'unresolved' : 'assigned',
  part_reason: reason, background_containment: 0.1,
  candidates: candidates.map(([part_code, containment], i) => ({ part_code, containment, rank: i + 1 })) });
const assessment = { assessment_revision: 1, input_revision: 1, state: 'ready', line_items: [], marks: [],
  review_photo_ids: ['photo-1', 'photo-2'], declaration_completeness: 'not_supplied', possible_additions: [],
  missing_repairs_check: { result: 'insufficient_evidence', reasons: [] },
  review_overlay: { dismissals: {}, addition_decisions: {}, accepted_scope: [] },
  finalize_preconditions: { can_finalize: true, checks: [] },
  versions: { cost_table: 'synthetic-test', damage_model: 'synthetic-hitl' },
  evidence_sources: { image: 'real', document: 'none' }, fixture_notice: 'Synthetic browser test data; no model run.',
  files: ['photo-1', 'photo-2'].map((file_id, i) => ({ file_id, original_name: `synthetic-${i + 1}.svg`,
    media_type: 'image/svg+xml', role: 'photograph', url: image })),
  damage_summary: [
    row('assigned', 'front-bumper', ['dent', 'cracked'], ['identity_not_resolved']),
    row('ambiguous', 'unresolved', ['dent'], ['ambiguous_between_parts']),
    row('background', 'unresolved', ['scratch'], ['mostly_background']),
    row('missing-mask', 'unresolved', ['broken-part'], ['part_masks_missing']),
    row('empty', 'hood', [], [], { side: 'not_applicable', identity_status: 'resolved', coverage: 'adequate' }),
  ],
  part_summaries: [
    { summary_id: 'assigned', member_observation_ids: ['assigned-observation'] },
    { summary_id: 'ambiguous', member_observation_ids: ['ambiguous-observation'] },
    { summary_id: 'background', member_observation_ids: ['background-observation'] },
    { summary_id: 'missing-mask', member_observation_ids: ['missing-observation'] },
  ],
  observations: [
    obs('background-observation', 'scratch', [['grille', 0.1]], 'mostly_background'),
    obs('assigned-observation', 'cracked', [['front-bumper', 0.9]], null),
    obs('ambiguous-observation', 'dent', [['front-bumper', 0.47], ['fender', 0.43]], 'ambiguous_between_parts'),
    obs('missing-observation', 'broken-part', [], 'part_masks_missing'),
  ],
};
assessment.observations[0].background_containment = 0.9;
const review = { review_revision: 0, assessment_revision: 1, finalized: false, actions: [] };
const browser = await chromium.launch({ headless: true, ...(process.env.CMEV_BROWSER_CHANNEL ? { channel: process.env.CMEV_BROWSER_CHANNEL } : {}) });
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  context.setDefaultTimeout(10000);
  const errors = [], writes = [], unexpected = [];
  let current = assessment;
  await context.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '');
    if (route.request().method() !== 'GET') writes.push(path);
    let body;
    if (path === '/auth/me') body = { user: { id: 'synthetic-user', name: 'Test Reviewer', role: 'surveyor', initials: 'TR' } };
    else if (path.endsWith('/print-view')) body = { claim, assessment: current, review: { ...review, finalized: true }, disclaimer: 'Synthetic display test' };
    else if (path.endsWith('/review')) body = review;
    else if (path.endsWith('/assessments/1')) body = current;
    else if (path.endsWith('/processing')) body = { state: 'completed', jobs: [] };
    else if (path === '/claims/display-test') body = claim;
    else { unexpected.push(path); return route.fulfill({ status: 404, json: {} }); }
    return route.fulfill({ json: body });
  });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  const open = async () => { await page.goto(base + '/claims/display-test/review'); await page.getByRole('heading', { name: 'Damage summary', exact: true }).waitFor(); };
  await open();
  console.log('Loaded synthetic review');
  const cards = page.locator('.damage-item');
  const assigned = cards.filter({ hasText: 'dent, cracked' });
  assert.match(await assigned.innerText(), /Predicted damage: dent, cracked/);
  assert.match(await assigned.innerText(), /Coverage: Needs confirmation/);
  assert.match(await assigned.innerText(), /Confirm the physical part/);
  assert.doesNotMatch(await assigned.innerText(), /identity_not_resolved/);
  console.log('Damage labels and identity text passed');
  await assigned.locator('summary').click();
  await assigned.getByRole('button', { name: 'View synthetic-2.svg' }).click();
  assert.equal(await page.locator('.evidence-select select').inputValue(), 'photo-2');
  const uncertain = page.locator('.damage-unresolved');
  assert.match(await uncertain.locator('summary').first().innerText(), /Unresolved observations \(3\)/);
  const ambiguous = uncertain.locator('.damage-item').filter({ hasText: '47.0% overlap' });
  assert.match(await ambiguous.innerText(), /Predicted damage: dent/);
  assert.match(await ambiguous.innerText(), /fender: 43.0% overlap/);
  assert.doesNotMatch(await ambiguous.innerText(), /grille/); // membership, not array order
  assert.match(await uncertain.innerText(), /does not establish that the damage is absent/);
  const missing = uncertain.locator('.damage-item').filter({ hasText: 'broken part' });
  assert.match(await missing.innerText(), /Candidate overlap unavailable/);
  assert.doesNotMatch(await missing.innerText(), /0.0% overlap/);
  assert.match(await cards.filter({ hasText: 'hood' }).innerText(), /No damage observation recorded/);
  await uncertain.locator('summary').first().focus();
  await page.keyboard.press('Enter');
  assert.equal(await ambiguous.isVisible(), false);
  await page.keyboard.press('Enter');
  assert.equal(await ambiguous.isVisible(), true);
  await page.locator('.review-main > section').first().screenshot({ path: `${out}/summary.png` });
  await page.setViewportSize({ width: 768, height: 1024 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'tablet overflow');
  await page.goto(base + '/claims/display-test/print?assessment=1&review=0');
  await page.getByRole('button', { name: 'Print to PDF' }).waitFor();
  await page.emulateMedia({ media: 'print' });
  assert.match(await page.locator('.damage-summary-list').innerText(), /47.0% overlap/);
  assert.equal(await page.locator('.damage-summary-list details').count(), 0);
  await page.pdf({ path: `${out}/synthetic-report.pdf`, format: 'A4', printBackground: true });
  await page.emulateMedia({ media: 'screen' });
  // CarDD and HITL labels remain literal vocabulary labels; fixture provenance stays explicit.
  current = { ...assessment, damage_summary: [row('fixture', 'windshield', ['glass-shatter'], ['identity_not_resolved'], { source_kind: 'fixture' })] };
  await open();
  assert.match(await cards.innerText(), /Illustrative damage: glass shatter/);
  assert.match(await cards.innerText(), /no model analysed the uploaded photographs/);
  // Old frozen snapshots have no detailed arrays: retain their explanation without claiming zero damage.
  current = { ...assessment, part_summaries: undefined, observations: undefined,
    damage_summary: [{ part_code: 'hood', side: 'unknown', coverage: 'unresolved', photo_count: 1, reason: 'Legacy explanation' }] };
  await open();
  assert.match(await cards.innerText(), /Details unavailable/);
  assert.match(await cards.innerText(), /Legacy explanation/);
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  assert.deepEqual(unexpected, []);
  console.log('PASS: damage labels, identity explanation, membership/candidates, background/missing masks, photo navigation, keyboard disclosure, tablet, print, vocabularies, fixture provenance, legacy snapshots. Synthetic API responses only.');
} finally { await browser.close(); }
