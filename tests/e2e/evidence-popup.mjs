// Evidence popups, with synthetic API responses. No claim or review action is written.
// Run a local workbench (dev server exercises React StrictMode), then:
//   CMEV_E2E_URL=http://127.0.0.1:4175 CMEV_BROWSER_CHANNEL=chrome node tests/e2e/evidence-popup.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:4175';
const out = 'artifacts/evaluation/ui-evidence-popup-20261014';
await mkdir(out, { recursive: true });

const svg = (width, height, body) => 'data:image/svg+xml,' + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${body}</svg>`);
const photo = (label, colour) => svg(800, 500, `<rect width="800" height="500" fill="${colour}"/><text x="40" y="250" font-size="48">${label}</text>`);
const PHOTO_ONE = photo('PHOTO ONE', '#d9e4ea'), PHOTO_TWO = photo('PHOTO TWO', '#e8dcc8');
// A tall page, like a scanned estimate: row A near the top, row B near the bottom.
const PAGE = svg(1240, 1754, `<rect width="1240" height="1754" fill="#fafafa"/>
  <text x="60" y="80" font-size="40">SYNTHETIC ESTIMATE PAGE</text>
  <text x="60" y="320" font-size="34">ROW A FRONT BUMPER 980.00</text>
  <text x="60" y="1420" font-size="34">ROW B REAR DOOR 585.00</text>`);

const claim = { claim_id: 'popup-test', reference: 'SYNTHETIC-POPUP', status: 'in_review',
  vehicle: { make: 'Test', model: 'Vehicle', year: 2026, plate: 'TEST' }, currency: 'SGD',
  input_revision: 1, assessment_revision: 1, review_revision: 0, photograph_count: 2 };
const obs = (id, photo_id, damage) => ({ observation_id: id, photo_id, damage_code: damage, damage_confidence: 0.82,
  assignment_status: 'assigned', part_reason: null, background_containment: 0.1,
  candidates: [{ part_code: 'front-bumper', containment: 0.9, rank: 1 }] });
const damageRow = (summary_id, part_code, damage_codes, extra = {}) => ({ summary_id, part_code, damage_codes,
  reason_codes: ['identity_not_resolved'], side: 'unknown', identity_status: 'part_only', coverage: 'unresolved',
  photo_count: 1, source_kind: 'real', reason: 'Legacy reason', ...extra });
const lineItem = (entry_id, description, y0, extra = {}) => ({ entry_id, finding_id: `finding-${entry_id}`, description,
  page_id: 'page-1', part_code: 'front-bumper', side: 'unknown', operation: 'replace', printed_amount: '980.00',
  quantity: '1', row_state: 'active', row_box_norm: [0.05, y0, 0.95, y0 + 0.035], cost_range: null,
  effective_amount: '980.00', mark_state: 'none', photo_check: 'not_evaluated', cost_check: 'not_evaluated',
  overall_result: 'insufficient_evidence', reason: 'Some details could not be read.', evidence_ids: ['estimate-1'],
  dismissal: null, ...extra });
const assessment = { assessment_revision: 1, input_revision: 1, state: 'ready',
  line_items: [
    lineItem('row-a', 'Front bumper replace', 0.17),
    lineItem('row-b', 'Rear door replace', 0.80),
    // The page is not among the corrected pages, but an original photograph is linked.
    lineItem('row-c', 'Linked original only', 0.5, { page_id: 'page-missing', evidence_ids: ['photo-1'] }),
    // Nothing is linked at all.
    lineItem('row-d', 'Nothing linked', 0.6, { page_id: 'page-missing', evidence_ids: [] }),
  ],
  marks: [{ mark_id: 'mark-1', page_id: 'page-1', entry_id: 'row-a', candidate_entry_ids: [], type: 'exclusion',
    state: 'pending', reason: '', box_norm: [0.5, 0.17, 0.9, 0.205] }],
  pages: [{ page_id: 'page-1', file_id: 'estimate-1', page_number: 1, corrected_url: PAGE }],
  review_photo_ids: ['photo-1', 'photo-2'], declaration_completeness: 'partial', possible_additions: [],
  missing_repairs_check: { result: 'insufficient_evidence', reasons: [] },
  review_overlay: { dismissals: {}, addition_decisions: {}, accepted_scope: [] },
  finalize_preconditions: { can_finalize: true, checks: [] },
  versions: { cost_table: 'synthetic-test', damage_model: 'synthetic-test' },
  evidence_sources: { image: 'real', document: 'real' }, fixture_notice: 'Synthetic browser test data; no model run.',
  files: [
    { file_id: 'photo-1', original_name: 'photo-1.png', media_type: 'image/svg+xml', role: 'photograph', url: PHOTO_ONE },
    { file_id: 'photo-2', original_name: 'photo-2.png', media_type: 'image/svg+xml', role: 'photograph', url: PHOTO_TWO },
    { file_id: 'estimate-1', original_name: 'estimate-page.jpg', media_type: 'image/svg+xml', role: 'estimate_page', url: PAGE },
  ],
  damage_summary: [
    damageRow('one-photo', 'front-bumper', ['scratch']),
    damageRow('two-photos', 'hood', ['dent', 'cracked']),
    damageRow('fixture', 'windshield', ['cracked'], { source_kind: 'fixture' }),
  ],
  part_summaries: [
    { summary_id: 'one-photo', member_observation_ids: ['o1'] },
    { summary_id: 'two-photos', member_observation_ids: ['o2', 'o3'] },
    { summary_id: 'fixture', member_observation_ids: ['o4'] },
  ],
  observations: [obs('o1', 'photo-2', 'scratch'), obs('o2', 'photo-1', 'dent'), obs('o3', 'photo-2', 'cracked'),
    obs('o4', 'photo-1', 'cracked')],
};
const review = { review_revision: 0, assessment_revision: 1, finalized: false, actions: [] };

const browser = await chromium.launch({ headless: true,
  ...(process.env.CMEV_BROWSER_CHANNEL ? { channel: process.env.CMEV_BROWSER_CHANNEL } : {}) });
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  context.setDefaultTimeout(10000);
  const errors = [], writes = [], unexpected = [];
  await context.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '');
    if (route.request().method() !== 'GET') writes.push(path);
    let body;
    if (path === '/auth/me') body = { user: { id: 'synthetic-user', name: 'Test Reviewer', role: 'surveyor', initials: 'TR' } };
    else if (path.endsWith('/print-view')) body = { claim, assessment, review: { ...review, finalized: true }, disclaimer: 'Synthetic test' };
    else if (path.endsWith('/review')) body = review;
    else if (path.endsWith('/assessments/1')) body = assessment;
    else if (path.endsWith('/processing')) body = { state: 'completed', jobs: [] };
    else if (path === '/claims/popup-test') body = claim;
    else { unexpected.push(path); return route.fulfill({ status: 404, json: {} }); }
    return route.fulfill({ json: body });
  });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(base + '/claims/popup-test/review');
  await page.getByRole('heading', { name: 'Damage summary', exact: true }).waitFor();
  const dialog = page.getByRole('dialog');
  const focusedName = () => page.evaluate(() => document.activeElement?.getAttribute('aria-label') || document.activeElement?.textContent?.trim());
  // The browser stops exposing a closed dialog before React removes it, so wait for the element itself.
  const gone = () => page.waitForFunction(() => document.querySelectorAll('dialog').length === 0);
  const closedAndFocusOn = async (name) => {
    await gone();
    assert.match(String(await focusedName()), name);
    assert.equal(await page.evaluate(() => document.body.style.overflow), '', 'page scrolling is restored');
  };

  // ---- damage cards
  const cards = page.locator('.damage-item');
  assert.equal(await cards.filter({ hasText: 'windshield' }).getByRole('button', { name: /View evidence/ }).count(), 0,
    'a fixture row is not linked to uploaded pixels, so it has no evidence button');
  const bumper = cards.filter({ hasText: 'front bumper' });
  const open = bumper.getByRole('button', { name: /View evidence for front bumper, scratch/ });
  await open.click();
  await dialog.waitFor();
  assert.equal(await dialog.getAttribute('aria-labelledby') !== null, true);
  await page.getByRole('heading', { name: /front bumper: photographic evidence/i }).waitFor();
  assert.match(await dialog.innerText(), /Predicted damage: scratch/);
  assert.equal(await dialog.locator('img').count(), 1, 'only the photograph recorded for this part');
  assert.equal(await dialog.locator('img').getAttribute('src'), PHOTO_TWO);
  assert.match(await dialog.innerText(), /photo-2\.png: scratch, model confidence 82\.0%/);
  assert.match(await dialog.innerText(), /region is not\s+outlined/);
  const box = await dialog.boundingBox();
  const close = dialog.getByRole('button', { name: 'Close evidence view' });
  const x = await close.boundingBox();
  assert.ok(box.x + box.width - (x.x + x.width) < 24 && x.y - box.y < 24, 'the X sits in the top right corner');
  assert.ok(x.width >= 40 && x.height >= 40, 'the X is a comfortable target');
  await page.screenshot({ path: `${out}/damage-popup.png` });
  await page.keyboard.press('Escape');
  await closedAndFocusOn(/View evidence for front bumper/);
  // The X button closes, and a click on the dark backdrop closes.
  await open.click(); await dialog.waitFor();
  await close.click();
  await closedAndFocusOn(/View evidence for front bumper/);
  await open.click(); await dialog.waitFor();
  await page.mouse.click(4, 4);
  await closedAndFocusOn(/View evidence for front bumper/);
  await open.click(); await dialog.waitFor();
  await dialog.locator('.evidence-dialog-body').click({ position: { x: 5, y: 5 } });
  assert.equal(await dialog.isVisible(), true, 'a click inside the popup does not close it');
  await page.keyboard.press('Escape');
  await gone();
  // A part seen in two photographs shows both, and only those.
  await cards.filter({ hasText: 'hood' }).getByRole('button', { name: /View evidence for hood, dent, cracked/ }).click();
  await dialog.waitFor();
  assert.deepEqual(await dialog.locator('img').evaluateAll(list => list.map(i => i.getAttribute('src'))), [PHOTO_ONE, PHOTO_TWO]);
  assert.match(await dialog.innerText(), /photo-1\.png: dent/);
  assert.match(await dialog.innerText(), /photo-2\.png: cracked/);
  await page.keyboard.press('Escape');
  await gone();
  console.log('Damage evidence popup passed');

  // ---- estimate rows
  const card = id => page.locator('.line-items-panel article').filter({ has: page.getByRole('heading', { name: id, exact: true }) });
  await card('Rear door replace').getByRole('button', { name: 'View evidence' }).click();
  await dialog.waitFor();
  await dialog.getByRole('heading', { name: 'Rear door replace' }).waitFor();
  assert.match(await dialog.innerText(), /estimate-page\.jpg · page 1/);
  assert.equal(await dialog.locator('img[alt="Corrected estimate page 1"]').count(), 1);
  assert.equal(await dialog.locator('svg[aria-label="Extracted rows and pen marks"] rect').count(), 3, 'the 2 rows on this page and the 1 pen mark');
  assert.match(await dialog.innerText(), /Blue: extracted rows\. Red: proposed or confirmed marks/);
  await dialog.locator('img[alt="Corrected estimate page 1"]').evaluate(img => img.complete || new Promise(r => img.addEventListener('load', r)));
  await page.waitForFunction(() => document.querySelector('.evidence-dialog-body').scrollTop > 0);
  const view = await dialog.locator('.evidence-dialog-body').boundingBox();
  const shaded = await dialog.locator('rect[fill="#2563eb22"]').boundingBox();
  assert.ok(shaded.y >= view.y && shaded.y + shaded.height <= view.y + view.height, 'the selected row is scrolled into view');
  assert.equal(await dialog.locator('rect[fill="#2563eb22"]').count(), 1, 'only the selected row is shaded');
  await page.screenshot({ path: `${out}/document-popup.png` });
  assert.equal(await page.locator('#evidence-panel img[alt="Corrected estimate page 1"]').count(), 1, 'the Evidence panel behind shows the same selection');
  await close.click();
  await closedAndFocusOn(/View evidence/);
  assert.equal(await page.evaluate(() => document.activeElement.closest('article')?.querySelector('h3')?.textContent), 'Rear door replace');
  // Row A is near the top: the popup starts at the top rather than scrolling past it.
  await card('Front bumper replace').getByRole('button', { name: 'View evidence' }).click();
  await dialog.waitFor();
  assert.ok(await page.evaluate(() => document.querySelector('.evidence-dialog-body').scrollTop) < 400);
  await page.keyboard.press('Escape');
  await gone();
  // A row with only a linked original image.
  await card('Linked original only').getByRole('button', { name: 'View evidence' }).click();
  await dialog.waitFor();
  assert.equal(await dialog.locator('img[alt="Original evidence: photo-1.png"]').count(), 1);
  await page.keyboard.press('Escape');
  await gone();
  // A row with nothing linked: say so; the Evidence panel keeps its own, separate message.
  await card('Nothing linked').getByRole('button', { name: 'View evidence' }).click();
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /No original file is linked to this row/);
  assert.equal(await dialog.locator('img').count(), 0);
  assert.equal(await page.getByText(/No linked original evidence is available/).count(), 1);
  await page.keyboard.press('Escape');
  await gone();
  console.log('Document evidence popup passed');

  // ---- small screens
  await page.setViewportSize({ width: 390, height: 800 });
  await card('Rear door replace').getByRole('button', { name: 'View evidence' }).click();
  await dialog.waitFor();
  const small = await dialog.boundingBox();
  assert.ok(small.x >= 0 && small.x + small.width <= 390 && small.y >= 0 && small.y + small.height <= 800, 'the popup fits a phone screen');
  assert.equal(await dialog.getByRole('button', { name: 'Close evidence view' }).isVisible(), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no horizontal overflow');
  await page.screenshot({ path: `${out}/document-popup-phone.png` });
  await dialog.getByRole('button', { name: 'Close evidence view' }).click();
  await gone();
  await page.setViewportSize({ width: 1280, height: 1000 });

  // ---- the printed report has no popup buttons
  await page.goto(base + '/claims/popup-test/print?assessment=1&review=0');
  await page.getByRole('button', { name: 'Print to PDF' }).waitFor();
  assert.equal(await page.getByRole('button', { name: /View evidence/ }).count(), 0);

  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  assert.deepEqual(unexpected, []);
  console.log('PASS: evidence popups for damage photographs and estimate rows (membership, fixture rows, top-right X, Esc, X, backdrop, focus return, row scroll, empty case, phone width, print). Synthetic API responses only.');
} finally { await browser.close(); }
