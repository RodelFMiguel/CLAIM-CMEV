// Browser regressions for the 2026-09-27 workbench fixes. Each check runs on its
// own pages and is reported separately, so one failure does not hide another.
// Display checks inject synthetic fixture payloads through route handlers; the
// race, draft and print checks use the running API. Screenshots stay under the
// ignored artifacts/evaluation directory.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:5173';
const out = 'artifacts/evaluation/ui-regressions';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
context.setDefaultTimeout(20000);
const pageErrors = [];
context.on('page', p => p.on('pageerror', e => pageErrors.push(e.message)));
const api = async (path, init) => {
  const r = init ? await context.request.post(base + '/api/v1' + path, { data: init,
    headers: { 'Idempotency-Key': crypto.randomUUID() } }) : await context.request.get(base + '/api/v1' + path);
  assert.ok(r.ok(), path + ': ' + r.status() + ' ' + await r.text());
  return r.json();
};
const results = [];
async function check(name, fn) {
  const before = pageErrors.length;
  const pages = [];
  const open = async url => { const p = await context.newPage(); pages.push(p); if (url) await p.goto(base + url); return p; };
  try {
    await fn(open);
    assert.deepEqual(pageErrors.slice(before), [], 'uncaught page errors');
    results.push({ check: name, result: 'passed' });
  } catch (e) {
    results.push({ check: name, result: 'failed', error: String(e.message).split('\n').slice(0, 4).join(' ') });
    if (pages[0]) await pages[0].screenshot({ path: `${out}/failure-${name}.png`, fullPage: true }).catch(() => {});
  } finally { for (const p of pages) await p.close().catch(() => {}); }
}
const reviewHeading = p => p.getByRole('heading', { name: 'Estimate line items', exact: true }).waitFor();
const card = (p, description) => p.locator('.line-items-panel article')
  .filter({ has: p.getByRole('heading', { name: description, exact: true }) });

try {
  assert.ok((await context.request.post(base + '/api/v1/auth/login', { data: {
    email: process.env.CMEV_DEMO_EMAIL || 'surveyor@claim-cmev.demo',
    password: process.env.CMEV_DEMO_PASSWORD || 'Demo2026!' } })).ok());
  const user = (await api('/auth/me')).user;
  // An assessed, unfinalized claim for the live checks (seed processing may still be running).
  let claim;
  for (let i = 0; i < 90 && !claim; i++) {
    for (const c of (await api('/claims')).items.filter(c => c.assessment_revision && c.status === 'in_review'))
      if (!(await api(`/claims/${c.claim_id}/assessments/${c.assessment_revision}/review`)).finalized) { claim = c; break; }
    if (!claim) await new Promise(r => setTimeout(r, 1000));
  }
  assert.ok(claim, 'no assessed, unfinalized claim is available');
  const cid = claim.claim_id;
  const assessmentPath = `/api/v1/claims/${cid}/assessments/${claim.assessment_revision}`;
  const assessment = await api(assessmentPath.slice(7));
  const review = `/claims/${cid}/review`;

  // Item 1: an amount-only correction on an unmapped row sends only the amount.
  await check('correct-row-sends-only-changed-fields', async open => {
    const fake = structuredClone(assessment);
    const row = fake.line_items.find(r => r.row_state !== 'excluded');
    row.part_code = 'unmapped'; row.operation = 'unmapped';
    const p = await open();
    await p.route('**' + assessmentPath, route => route.fulfill({ json: fake }));
    const sent = [];
    await p.route('**/review-actions', route => { sent.push(route.request().postDataJSON());
      return route.fulfill({ status: 422, json: { detail: { reason_code: 'probe', message: 'Probe stop.' } } }); });
    await p.goto(base + review); await reviewHeading(p);
    const c = card(p, row.description);
    await c.getByText('Correct row or add a missed mark', { exact: true }).click();
    const form = c.locator('form').filter({ has: p.getByRole('button', { name: 'Save correction' }) });
    assert.equal(await form.locator('input[name=part_code]').inputValue(), '');
    assert.equal(await form.locator('select[name=operation]').inputValue(), '');
    await form.getByLabel('Correction reason').selectOption('ocr_error');
    await form.getByRole('button', { name: 'Save correction' }).click();
    await form.getByText('Change at least one value before saving a correction.').waitFor();
    assert.equal(sent.length, 0, 'an unchanged correction was submitted');
    const amount = (Number(row.printed_amount ?? 100) + 10).toFixed(2);
    await form.getByLabel('Corrected printed amount').fill(amount);
    await form.getByRole('button', { name: 'Save correction' }).click();
    for (let i = 0; i < 50 && !sent.length; i++) await p.waitForTimeout(100);
    assert.deepEqual(sent[0]?.corrections, { printed_line_amount: amount });
  });

  // Items 2 and 3: result styling differs by colour role, border, icon and wording; excluded rows get no result chip.
  await check('result-styling-and-excluded-rows', async open => {
    const fake = structuredClone(assessment);
    const template = fake.line_items[0];
    const kinds = ['ok', 'unsupported', 'cost_outlier', 'insufficient_evidence', 'not_evaluated'];
    fake.line_items = kinds.map((kind, i) => ({ ...structuredClone(fake.line_items[i] ?? template),
      entry_id: 'fixture-entry-' + kind, finding_id: 'fixture-finding-' + kind,
      description: 'Synthetic fixture row ' + kind, overall_result: kind,
      row_state: kind === 'not_evaluated' ? 'excluded' : 'active',
      reason: 'Synthetic fixture reason for ' + kind + '.' }));
    fake.marks = [];
    const p = await open();
    await p.route('**' + assessmentPath, route => route.fulfill({ json: fake }));
    await p.goto(base + review); await reviewHeading(p);
    const style = async kind => card(p, 'Synthetic fixture row ' + kind).locator('.result-message').evaluate(e => {
      const s = getComputedStyle(e);
      return { border: s.borderTopStyle, borderColor: s.borderTopColor, bg: s.backgroundColor, color: s.color,
        icon: e.querySelector('svg')?.getAttribute('class'), label: e.querySelector('strong')?.textContent };
    });
    const s = Object.fromEntries(await Promise.all(kinds.slice(0, 4).map(async k => [k, await style(k)])));
    const rgb = v => v.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number);
    const lum = v => { const [r, g, b] = rgb(v).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
    const info = s.insufficient_evidence;
    assert.equal(info.border, 'dashed');
    assert.equal(info.label, 'More information needed');
    assert.match(info.icon, /lucide-info/);
    for (const flag of ['unsupported', 'cost_outlier']) {
      assert.equal(s[flag].border, 'solid', flag + ' border');
      assert.notEqual(s[flag].icon, info.icon, flag + ' icon');
      assert.notEqual(s[flag].label, info.label, flag + ' wording');
      assert.notEqual(s[flag].bg, info.bg, flag + ' background');
      assert.notEqual(s[flag].borderColor, info.borderColor, flag + ' border colour');
    }
    assert.notEqual(s.unsupported.icon, s.cost_outlier.icon);
    for (const [k, v] of Object.entries(s)) assert.ok(contrast(v.color, v.bg) >= 4.5, k + ' text contrast ' + contrast(v.color, v.bg).toFixed(2));
    // N11: the excluded row shows a neutral row-state note, never a result chip or the warning triangle.
    const excluded = card(p, 'Synthetic fixture row not_evaluated');
    assert.equal(await excluded.locator('.result-message').count(), 0);
    assert.equal(await excluded.locator('.lucide-triangle-alert, .lucide-alert-triangle').count(), 0);
    await excluded.locator('.row-state-note').getByText('Excluded by surveyor').waitFor();
    // Greyscale screenshot: the border style and icon shape carry the distinction without colour.
    await p.addStyleTag({ content: 'html { filter: grayscale(1) !important; }' });
    await p.locator('.line-items-panel').screenshot({ path: `${out}/results-greyscale.png` });
  });

  // Item 4: status-aware queue text and a discrepancy-only open-findings stat.
  await check('queue-status-text', async open => {
    const list = await api('/claims');
    const t = list.items[0];
    const item = (reference, extra) => ({ ...structuredClone(t), claim_id: 'fixture-' + reference, reference, ...extra });
    const withCounts = { ...list, total: 6, stats: { ...list.stats, open_findings: 99 }, items: [
      item('FX-QUEUED', { status: 'processing', processing_state: 'queued', assessment_revision: null, finding_count: 0 }),
      item('FX-RUNNING', { status: 'processing', processing_state: 'processing', assessment_revision: null, finding_count: 0 }),
      item('FX-FAILED', { status: 'incomplete', processing_state: 'incomplete', assessment_revision: null, latest_assessment_revision: 1, finding_count: 0 }),
      item('FX-MIXED', { status: 'in_review', assessment_revision: 2, finding_count: 5, discrepancy_count: 2, information_needed_count: 3 }),
      item('FX-INFO', { status: 'in_review', assessment_revision: 1, finding_count: 1, discrepancy_count: 0, information_needed_count: 1 }),
      item('FX-CLEAN', { status: 'ready_to_print', assessment_revision: 1, finding_count: 0, discrepancy_count: 0, information_needed_count: 0 }),
    ] };
    const p = await open();
    let payload = withCounts;
    await p.route('**/api/v1/claims', route => route.fulfill({ json: payload }));
    await p.goto(base + '/claims');
    const row = ref => p.locator('.claim-table tbody tr').filter({ hasText: ref });
    await row('FX-QUEUED').getByText('Queued. Nothing processed yet').waitFor();
    await row('FX-RUNNING').getByText('Processing evidence').waitFor();
    await row('FX-FAILED').getByText('Processing needs attention').waitFor();
    for (const ref of ['FX-QUEUED', 'FX-RUNNING', 'FX-FAILED'])
      assert.doesNotMatch(await row(ref).innerText(), /No open findings/, ref);
    const mixed = row('FX-MIXED');
    await mixed.locator('.queue-discrepancy').getByText('2 discrepancies').waitFor();
    await mixed.locator('.queue-information').getByText('More information needed: 3 rows').waitFor();
    assert.notEqual(await mixed.locator('.queue-discrepancy').evaluate(e => getComputedStyle(e).color),
      await mixed.locator('.queue-information').evaluate(e => getComputedStyle(e).color));
    assert.equal(await row('FX-INFO').locator('.queue-discrepancy').count(), 0);
    await row('FX-CLEAN').getByText('No open findings').waitFor();
    const stat = p.locator('.stat-card').filter({ hasText: 'Open findings' });
    assert.equal(await stat.locator('strong').innerText(), '02');
    await stat.getByText('More information needed: 4').waitFor();
    // Older API without the split counts: never claim a zero for unassessed claims.
    payload = { ...withCounts, items: withCounts.items.map(c => {
      const { discrepancy_count, information_needed_count, ...rest } = c; return rest; }) };
    await p.reload();
    await row('FX-MIXED').getByText('5 findings to review').waitFor();
    assert.doesNotMatch(await row('FX-FAILED').innerText(), /No open findings/);
    assert.equal(await p.locator('.stat-card').filter({ hasText: 'Open findings' }).locator('strong').innerText(), '—');
  });

  // Item 9 (N10): a processing-status failure leaves the loaded review usable with a warning.
  await check('processing-failure-non-blocking', async open => {
    const p = await open();
    await p.route(`**/api/v1/claims/${cid}/processing`, route => route.fulfill({ status: 500, json: { detail: 'Synthetic processing outage.' } }));
    await p.goto(base + review); await reviewHeading(p);
    await p.getByText(/Processing status could not be loaded/).waitFor();
  });

  // Item 6: after a failed reassessment the latest assessment opens read-only beside the retry control.
  await check('prior-assessment-read-only', async open => {
    const p = await open();
    const rev = claim.assessment_revision;
    await p.route(`**/api/v1/claims/${cid}`, route => route.fulfill({ json: { ...claim, assessment_revision: null,
      latest_assessment_revision: rev, input_revision: claim.input_revision + 1, status: 'incomplete', processing_state: 'incomplete' } }));
    await p.route(`**/api/v1/claims/${cid}/processing`, route => route.fulfill({ json: { state: 'incomplete', jobs: [
      { job_key: 'fixture-failed-job', stage: 'consolidate', state: 'dead_lettered', retryable: true, error: 'Synthetic failure.' }] } }));
    await p.goto(base + review);
    await p.getByRole('button', { name: `Open assessment ${rev} read-only` }).click();
    await reviewHeading(p);
    await p.getByRole('region', { name: 'Assessment not current' }).getByText(/read-only and cannot be finalized/).waitFor();
    assert.equal(await p.getByRole('button', { name: 'Finalize review' }).count(), 0);
    assert.ok(await p.getByLabel('Add note', { exact: true }).isDisabled());
    assert.ok(await p.locator('.line-items-panel fieldset').first().evaluate(e => e.disabled));
    assert.ok(await p.getByRole('button', { name: 'Retry consolidate', exact: true }).isEnabled());
  });

  // Item 7 (N8): tab A's request commits after tab B discarded it and saved its own.
  await check('discard-commit-race', async open => {
    const queue = `pending:${user.id}:${cid}`;
    const a = await open(review), b = await open(review);
    await reviewHeading(a); await reviewHeading(b);
    let releaseA, releaseB;
    const heldA = new Promise(r => { releaseA = r; }), heldB = new Promise(r => { releaseB = r; });
    await a.route('**/assessments/*/notes', async route => { await heldA; await route.continue(); });
    await b.route('**/assessments/*/notes', async route => { await heldB; await route.continue(); });
    const stamp = Date.now(), noteA = 'Race note A ' + stamp, noteB = 'Race note B ' + stamp;
    await a.getByLabel('Add note', { exact: true }).fill(noteA);
    await a.getByRole('button', { name: 'Save note', exact: true }).click();
    await a.getByRole('heading', { name: 'Request awaiting acknowledgement' }).waitFor();
    await b.reload(); await reviewHeading(b);
    await b.getByRole('button', { name: 'Discard local request and refresh' }).click();
    await b.waitForFunction(() => !document.querySelector('[aria-label="Saved pending request"]'));
    await b.getByLabel('Add note', { exact: true }).fill(noteB);
    await b.getByRole('button', { name: 'Save note', exact: true }).click();
    await b.getByRole('heading', { name: 'Request awaiting acknowledgement' }).waitFor();
    releaseA();
    await a.getByText('Review note saved.', { exact: true }).waitFor();
    await a.locator('.review-history').getByText(noteA, { exact: true }).waitFor();
    assert.equal(await a.getByLabel('Add note', { exact: true }).inputValue(), '', 'committed note draft kept');
    assert.doesNotMatch(await a.locator('body').innerText(), /Another tab has a saved request/);
    // Removing a request that no longer holds the slot never throws and never removes the other request.
    const kept = await a.evaluate(async queue => {
      const { localValue, removePending } = await import('/src/pendingActions.ts');
      await removePending(queue, 'not-the-current-request');
      return (await localValue(queue))?.payload?.text;
    }, queue);
    assert.equal(kept, noteB);
    // B's note was prepared on the review revision before A committed: a visible
    // stale-write conflict that keeps B's draft. B then discards and saves again.
    releaseB();
    await b.getByRole('button', { name: 'Reload current revision', exact: true }).waitFor();
    assert.equal(await b.getByLabel('Add note', { exact: true }).inputValue(), noteB);
    await b.getByRole('button', { name: 'Discard local request and refresh' }).click();
    await b.waitForFunction(() => !document.querySelector('[aria-label="Saved pending request"]'));
    await b.unroute('**/assessments/*/notes');
    await b.getByRole('button', { name: 'Save note', exact: true }).click();
    await b.getByText('Review note saved.', { exact: true }).waitFor();
    assert.equal(await b.getByLabel('Add note', { exact: true }).inputValue(), '');
    await b.reload(); await reviewHeading(b);
    const history = await b.locator('.review-history').innerText();
    assert.equal(history.split(noteA).length - 1, 1, 'note A stored once');
    assert.equal(history.split(noteB).length - 1, 1, 'note B stored once');
    // A's Discard control stays safe whichever request it shows.
    await a.reload(); await reviewHeading(a);
    const discard = a.getByRole('button', { name: 'Discard local request and refresh' });
    if (await discard.count()) await discard.click();
  });

  // Item 8 (N9): a legacy draft moves into one tab once, and stale tab drafts are pruned.
  await check('draft-migration-and-pruning', async open => {
    const seed = await open('/claims');
    const day = 24 * 60 * 60 * 1000, prefix = `draft:${user.id}:${cid}:`;
    await seed.evaluate(async ({ cid, prefix, day }) => {
      const { storeLocal } = await import('/src/pendingActions.ts');
      await storeLocal('draft:undefined:' + cid, { note: 'Legacy synthetic draft', amounts: {} });
      await storeLocal(prefix + 'closed-old', { note: 'old', amounts: {}, updatedAt: Date.now() - 30 * day });
      await storeLocal(prefix + 'closed-empty', { note: '', amounts: {}, updatedAt: Date.now() });
      await storeLocal(prefix + 'closed-recent', { note: 'recent unsent', amounts: {}, updatedAt: Date.now() });
      await storeLocal(prefix + 'closed-unstamped', { note: 'unstamped unsent', amounts: {} });
    }, { cid, prefix, day });
    const first = await open(review); await reviewHeading(first);
    await first.getByLabel('Add note', { exact: true }).waitFor();
    await first.waitForFunction(() => document.querySelector('#review-note')?.value === 'Legacy synthetic draft');
    const state = await first.evaluate(async ({ cid, prefix }) => {
      const { localValue } = await import('/src/pendingActions.ts');
      const tab = sessionStorage.getItem('cmev-review-tab');
      for (let i = 0; i < 50 && await localValue(prefix + 'closed-old'); i++) await new Promise(r => setTimeout(r, 100));
      const get = k => localValue(prefix + k);
      return { legacy: await localValue('draft:undefined:' + cid), own: (await get(tab))?.note,
        old: await get('closed-old'), empty: await get('closed-empty'), recent: (await get('closed-recent'))?.note,
        unstamped: typeof (await get('closed-unstamped'))?.updatedAt };
    }, { cid, prefix });
    assert.deepEqual(state, { legacy: undefined, own: 'Legacy synthetic draft', old: undefined, empty: undefined,
      recent: 'recent unsent', unstamped: 'number' });
    const second = await open(review); await reviewHeading(second);
    await second.waitForTimeout(500);
    assert.equal(await second.getByLabel('Add note', { exact: true }).inputValue(), '', 'legacy draft adopted twice');
  });

  // Item 5: revisions and pinned versions print in the report body, not only in the @page margin.
  await check('print-body-revisions', async open => {
    const frozen = (await api('/claims')).items.find(c => c.status === 'ready_to_print' && c.assessment_revision);
    assert.ok(frozen, 'no finalized claim is available');
    const a = await api(`/claims/${frozen.claim_id}/assessments/${frozen.assessment_revision}`);
    const p = await open(`/claims/${frozen.claim_id}/print?assessment=${frozen.assessment_revision}&review=${frozen.review_revision}`);
    await p.getByRole('button', { name: 'Print to PDF' }).waitFor();
    await p.emulateMedia({ media: 'print' });
    assert.notEqual(await p.locator('.report-versions').evaluate(e => getComputedStyle(e).display), 'none');
    const header = await p.locator('.report-details').innerText();
    for (const text of [`Input ${a.input_revision}`, `Assessment ${a.assessment_revision}`, `Review ${frozen.review_revision}`,
      ...Object.values(a.versions)]) {
      assert.ok(header.includes(text), 'header lacks ' + text);
      assert.ok((await p.locator('.report-versions').innerText()).includes(text), 'footer lacks ' + text);
    }
    await p.pdf({ path: `${out}/frozen-report-body-versions.pdf`, format: 'A4', printBackground: true });
  });
} finally {
  await browser.close();
}
console.log(JSON.stringify({ results, source: 'synthetic browser fixtures against the local API' }, null, 1));
if (results.some(r => r.result !== 'passed')) process.exit(1);
