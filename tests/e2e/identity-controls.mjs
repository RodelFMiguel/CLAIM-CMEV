// Confirmation-form acceptance with intercepted synthetic API requests. No backend writes.
// Run a local workbench, then: CMEV_E2E_URL=http://127.0.0.1:4175 node tests/e2e/identity-controls.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile } from 'node:fs/promises';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:4175';
const out = 'artifacts/evaluation/ui-identity-controls-20261010';
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
    if (route.request().method() !== 'GET') {
      if (path.endsWith('/review-actions')) {
        writes.push(route.request().postDataJSON());
        return route.fulfill({ json: { ok: true } });
      }
      unexpected.push(path);
      return route.fulfill({ status: 400, json: {} });
    }
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
  // Keep the bundled options tied to the frozen shared taxonomy, including sidedness.
  const taxonomy = await readFile('configs/taxonomy/parts.yaml', 'utf8');
  const expectedParts = [...taxonomy.matchAll(/\{code: ([a-z-]+),.*?sided: (true|false),/g)]
    .map(([, code, sided]) => ({ code, sided: sided === 'true' }));
  const options = JSON.parse(await readFile('src/workbench/src/partOptions.json', 'utf8'));
  assert.deepEqual(options.parts.map(({ code, sided }) => ({ code, sided })), expectedParts);
  const controls = page.locator('details').filter({ has: page.locator('summary', { hasText: /^Confirm physical identity or coverage$/ }) });
  await controls.locator('summary').click();
  const identity = controls.locator('form').nth(0);
  const coverage = controls.locator('form').nth(1);
  assert.deepEqual(await identity.locator('[name=part] option').evaluateAll(opts => opts.map(o => o.value).filter(Boolean)), expectedParts.map(p => p.code));
  assert.match(await controls.innerText(), /does not reject a damage prediction/);
  assert.equal(await identity.evaluate(f => f.checkValidity()), false);
  await identity.locator('[name=part]').selectOption({ label: 'Front Door' });
  assert.deepEqual(await identity.locator('[name=side] option').evaluateAll(opts => opts.map(o => o.value)), ['', 'left', 'right']);
  await identity.getByLabel('Physical side').selectOption('left');
  await identity.getByLabel('Photographs (select one or more)').selectOption(['photo-1', 'photo-2']);
  assert.match(await identity.locator('[name=photos]').innerText(), /synthetic-1.svg/);
  await identity.locator('[name=part]').selectOption('windshield');
  assert.equal(await identity.getByLabel('Physical side').inputValue(), ''); // Never carry the old side to a new part.
  assert.equal(await identity.evaluate(f => f.checkValidity()), false);
  await identity.getByLabel('Physical side').selectOption('not_applicable');
  await identity.getByRole('button', { name: 'Save identity' }).click();
  await page.waitForFunction(() => document.body.innerText.includes('Review action saved.'));
  assert.equal(writes.length, 1);
  assert.equal(writes[0].part_code, 'windshield');
  assert.equal(writes[0].side, 'not_applicable');
  assert.deepEqual(writes[0].photo_ids, ['photo-1', 'photo-2']);
  await coverage.locator('[name=part]').selectOption('front-door');
  await coverage.getByLabel('Physical side').selectOption('left');
  await coverage.getByLabel('Photographs (select one or more)').selectOption('photo-1');
  await coverage.getByLabel('Do these views show enough of this part?').selectOption('no');
  assert.equal(await coverage.evaluate(f => f.checkValidity()), false); // No requires a reason.
  await coverage.getByLabel('Why is coverage insufficient?').selectOption('partially_visible');
  const explanation = 'This is not a front door issue, but windshield damage.';
  await coverage.getByLabel('Explanation (optional)').fill(explanation);
  assert.equal(await coverage.evaluate(f => f.checkValidity()), true); // Spaces and punctuation allowed in notes.
  await coverage.getByRole('button', { name: 'Save coverage' }).click();
  await page.waitForFunction(() => !document.querySelector('fieldset:disabled'));
  assert.equal(writes.length, 2);
  assert.equal(writes[1].action_type, 'confirm_coverage');
  assert.equal(writes[1].part_code, 'front-door');
  assert.equal(writes[1].covers_enough, false);
  assert.equal(writes[1].reason_code, 'partially_visible');
  assert.equal(writes[1].note, explanation);
  assert.equal(writes[1].expected_input_revision, 1);
  assert.equal(writes[1].expected_review_revision, 0);
  await coverage.getByLabel('Do these views show enough of this part?').selectOption('yes');
  assert.equal(await coverage.locator('[name=reason]').count(), 0);
  await coverage.getByRole('button', { name: 'Save coverage' }).click();
  await page.waitForFunction(() => !document.querySelector('fieldset:disabled'));
  assert.equal(writes.length, 3);
  assert.equal(writes[2].covers_enough, true);
  assert.equal(writes[2].reason_code, null); // No stale negative reason on a positive confirmation.
  await page.setViewportSize({ width: 768, height: 1024 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await controls.screenshot({ path: `${out}/controls.png` });
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log('PASS: frozen taxonomy, side reset, required fields, filenames, multi-photo identity, negative/positive coverage payloads, free text, revision fields and tablet layout. Requests intercepted; no backend persistence tested.');
} finally { await browser.close(); }
