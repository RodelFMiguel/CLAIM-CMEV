import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
try {
  const login = await page.request.post(base + '/api/v1/auth/login', { data: {
    email: process.env.CMEV_DEMO_EMAIL || 'surveyor@claim-cmev.demo',
    password: process.env.CMEV_DEMO_PASSWORD || 'Demo2026!'
  }});
  assert.ok(login.ok());
  const claims = await (await page.request.get(base + '/api/v1/claims')).json();
  const c = claims.items.find(c => c.reference === (process.env.CMEV_E2E_PRINT_REFERENCE || 'CLM-24019'));
  const path = '/api/v1/claims/' + c.claim_id + '/assessments/' + c.assessment_revision;
  const review = await (await page.request.get(base + path + '/review')).json();
  if (!review.finalized) {
    const result = await page.request.post(base + path + '/finalize', { data: { expected_review_revision: c.review_revision },
      headers: { 'Idempotency-Key': crypto.randomUUID() } });
    assert.ok(result.ok(), await result.text());
  }
  await page.goto(base + '/claims/' + c.claim_id + '/print?assessment=' + c.assessment_revision + '&review=' + c.review_revision);
  await page.getByRole('button', { name: 'Print to PDF' }).waitFor();
  const assessment = await (await page.request.get(base + path)).json();
  for (const row of assessment.line_items.filter(r => r.printed_amount_corrected)) {
    assert.ok(row.original_printed_amount !== undefined);
    await page.getByText(/Original extraction:/).first().waitFor();
  }
  await mkdir('artifacts/evaluation/ui-review' , { recursive: true });
  await page.pdf({ path: 'artifacts/evaluation/ui-review/frozen-report.pdf', format: 'A4', printBackground: true });
  console.log('Exported frozen report ' + c.assessment_revision + '/' + c.review_revision);
} finally { await browser.close(); }
