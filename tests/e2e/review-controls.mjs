import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1024, height: 900 } });
const page = await context.newPage();
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', e => errors.push(e.message));
async function api(path, body) {
  const response = body ? await page.request.post(base + '/api/v1' + path, {
    data: body, headers: { 'Idempotency-Key': crypto.randomUUID() }
  }) : await page.request.get(base + '/api/v1' + path);
  assert.ok(response.ok(), await response.text());
  return response.json();
}
let cid, c, a;
async function refresh() {
  c = await api('/claims/' + cid);
  a = await api('/claims/' + cid + '/assessments/' + c.assessment_revision);
}
async function reassessed(previous) {
  const deadline = Date.now() + 120000;
  while (true) {
    const status = await api('/claims/' + cid);
    if (status.assessment_revision && status.input_revision === previous + 1) break;
    assert.ok(Date.now() < deadline, 'Reassessment did not complete');
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  await page.reload();
  await page.getByRole('heading', { name: 'Estimate line items', exact: true }).waitFor();
  await refresh();
  // Processing can finish before the browser sees the POST acknowledgement.
  const retry = page.getByRole('button', { name: 'Retry saved request', exact: true });
  if (await retry.isVisible()) {
    await retry.click();
    await page.waitForFunction(() => !document.querySelector('[aria-label="Saved pending request"]'));
  }
}
try {
  await page.goto(base + '/login');
  await page.getByLabel('Email address').fill(process.env.CMEV_DEMO_EMAIL || 'surveyor@claim-cmev.demo');
  await page.getByLabel('Password', { exact: true }).fill(process.env.CMEV_DEMO_PASSWORD || 'Demo2026!');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByRole('heading', { name: /Claim queue/ }).waitFor();
  cid = (await api('/claims')).items.find(c => c.reference === 'CLM-24020').claim_id;
  await page.goto(base + '/claims/' + cid + '/review');
  await page.getByRole('heading', { name: 'Estimate line items', exact: true }).waitFor();
  await refresh();
  let previous = c.input_revision;
  if (!a.review_overlay.accepted_scope.length) {
    const addition = page.locator('form').filter({ has: page.getByRole('button', { name: 'Accept addition', exact: true }) }).first();
    await addition.getByLabel('Operation supplied by surveyor').selectOption('repair');
    await addition.getByLabel('Quantity supplied by surveyor').fill('1');
    await addition.getByLabel('Reason code if amount absent').fill('workshop_to_quote');
    await addition.getByRole('button', { name: 'Accept addition', exact: true }).click();
    await reassessed(previous);
  }
  assert.equal(a.review_overlay.accepted_scope[0].new_values.amount, null);
  const item = a.line_items.find(i => i.row_state === 'active');
  const row = page.locator('.line-items-panel article').filter({ has: page.getByRole('heading', { name: item.description, exact: true }) });
  await row.getByText('Correct row or add a missed mark', { exact: true }).click();
  const correction = row.locator('form').filter({ has: page.getByRole('button', { name: 'Save correction', exact: true }) });
  const correctedAmount = (Number(item.printed_amount) + 10).toFixed(2);
  await correction.getByLabel('Corrected printed amount').fill(correctedAmount);
  await correction.getByLabel('Correction reason').selectOption('ocr_error');
  previous = c.input_revision;
  await correction.getByRole('button', { name: 'Save correction', exact: true }).click();
  await reassessed(previous);
  assert.equal(a.line_items.find(i => i.entry_id === item.entry_id).printed_amount, correctedAmount);
  if (!a.review_overlay.dismissals[a.line_items.find(i => i.entry_id === item.entry_id).finding_id]) {
  await row.getByText('Dismiss finding', { exact: true }).click();
  const dismiss = row.locator('form').filter({ has: page.getByRole('button', { name: 'Save dismissal', exact: true }) });
  await dismiss.getByLabel('Dismissal reason').selectOption('inadequate_photograph');
  const beforeReview = c.review_revision;
  await dismiss.getByRole('button', { name: 'Save dismissal', exact: true }).click();
  await page.getByText(/Finding dismissed:/).waitFor();
  await refresh();
  assert.equal(c.review_revision, beforeReview + 1);
  assert.equal(c.input_revision, previous + 1);

  }

  await page.getByText('Confirm declaration completeness', { exact: true }).click();
  await page.getByLabel('Printed scope').selectOption('complete');
  previous = c.input_revision;
  await page.getByRole('button', { name: 'Confirm completeness', exact: true }).click();
  await reassessed(previous);
  assert.equal(a.declaration.source, 'human_confirmation');

  // A concurrent server note makes this browser's revision stale; preserve the submitted draft.
  await api('/claims/' + cid + '/assessments/' + c.assessment_revision + '/review-actions', {
    action_type: 'add_note', expected_review_revision: c.review_revision, note: 'Concurrent synthetic note'
  });
  const draft = 'Draft retained after conflict ' + Date.now();
  await page.getByLabel('Add note', { exact: true }).fill(draft);
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await page.getByRole('button', { name: 'Reload current revision', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: 'Retry saved request', exact: true }).waitFor();
  assert.equal(await page.getByLabel('Add note', { exact: true }).inputValue(), draft);
  await page.getByRole('button', { name: 'Discard local request and refresh', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('[aria-label="Saved pending request"]'));
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await page.locator('.review-history').getByText(draft, { exact: true }).waitFor();

  await page.setViewportSize({ width: 768, height: 1024 });
  await mkdir('artifacts/evaluation/ui-review', { recursive: true });
  await page.screenshot({ path: 'artifacts/evaluation/ui-review/controls-tablet.png', fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: 'passed', claim_id: cid, checks: ['accept_addition', 'row_correction', 'dismissal', 'completeness', 'stale_draft_recovery', 'tablet'] }));
} catch (error) {
  await mkdir('artifacts/evaluation/ui-review', { recursive: true });
  await page.screenshot({ path: 'artifacts/evaluation/ui-review/failure.png', fullPage: true });
  console.error(await page.getByRole('alert').allTextContents());
  throw error;
} finally { await browser.close(); }
