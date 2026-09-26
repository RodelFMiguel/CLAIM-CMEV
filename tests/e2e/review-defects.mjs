import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const require = createRequire(new URL('../../src/workbench/package.json', import.meta.url));
const { chromium } = require('playwright');
const base = process.env.CMEV_E2E_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const errors = [];
context.on('page', p => p.on('pageerror', e => errors.push(e.message)));
try {
  assert.ok((await context.request.post(base + '/api/v1/auth/login', {data: {
    email: 'surveyor@claim-cmev.demo', password: process.env.CMEV_DEMO_PASSWORD || 'Demo2026!'
  }})).ok());
  const claims = await (await context.request.get(base + '/api/v1/claims')).json();
  const claim = claims.items.find(c => c.reference === 'CLM-24020');
  const cid = claim.claim_id;
  const assessmentPath = `/api/v1/claims/${cid}/assessments/${claim.assessment_revision}`;
  const assessment = await (await context.request.get(base + assessmentPath)).json();
  const user = (await (await context.request.get(base + '/api/v1/auth/me')).json()).user;
  const queue = `pending:${user.id}:${cid}`;
  const a = await context.newPage(), b = await context.newPage();
  for (const page of [a, b]) {
    await page.goto(`${base}/claims/${cid}/review`);
    await page.getByRole('heading', {name: 'Estimate line items', exact: true}).waitFor();
    await page.route('**/assessments/*/notes', route => route.abort('failed'));
  }
  await a.getByLabel('Add note', {exact:true}).fill('Unsent edit from tab A');
  await b.getByLabel('Add note', {exact:true}).fill('Unsent edit from tab B');
  for (const [page, note] of [[a, 'Unsent edit from tab A'], [b, 'Unsent edit from tab B']]) {
    await page.waitForFunction(async ({userId, cid, note}) => {
      const {localValue} = await import('/src/pendingActions.ts');
      return (await localValue(`draft:${userId}:${cid}:${sessionStorage.getItem('cmev-review-tab')}`))?.note === note;
    }, {userId:user.id,cid,note});
  }
  await a.getByRole('button', {name:'Save note',exact:true}).click();
  await a.getByRole('heading', {name:'Request awaiting acknowledgement'}).waitFor();
  await b.getByRole('button', {name:'Save note',exact:true}).click();
  await b.getByText(/Another tab has a saved request/).waitFor();
  assert.equal(await b.getByLabel('Add note', {exact:true}).inputValue(), 'Unsent edit from tab B');
  await b.reload();
  await b.getByRole('heading', {name:'Request awaiting acknowledgement'}).waitFor();
  await b.getByText("Saved request values", {exact:true}).click();
  assert.match(await b.locator('[aria-label="Saved pending request"]').innerText(), /Unsent edit from tab A/);
  assert.equal(await b.getByLabel('Add note', {exact:true}).inputValue(), 'Unsent edit from tab B');
  await b.evaluate(async queue => {
    const {localValue,removePending} = await import('/src/pendingActions.ts');
    const action = await localValue(queue);
    await removePending(queue, action.key);
  }, queue);
  await b.reload();
  assert.equal(await b.getByLabel('Add note', {exact:true}).inputValue(), 'Unsent edit from tab B');

  const cloneReady = context.waitForEvent('page');
  await a.evaluate(url => window.open(url, '_blank'), `${base}/claims/${cid}/review`);
  const clone = await cloneReady;
  await clone.getByRole('heading', {name:'Estimate line items',exact:true}).waitFor();
  const originalTab = await a.evaluate(() => sessionStorage.getItem('cmev-review-tab'));
  await clone.waitForFunction(original => {
    const current = sessionStorage.getItem('cmev-review-tab');
    return current && current !== original;
  }, originalTab);
  await clone.close();
  await b.evaluate(async ({queue,cid}) => {
    const {storeLocal} = await import('/src/pendingActions.ts');
    await storeLocal('pending:undefined:' + cid, {key:'legacy-request-key',claimId:cid,
      path:'/unused',payload:{text:'Legacy unsent edit'},success:'Saved'});
  }, {queue,cid});
  await b.reload();
  await b.getByRole('heading', {name:'Request awaiting acknowledgement'}).waitFor();
  await b.evaluate(async ({queue,cid,actor}) => {
    const {localValue,removePending} = await import('/src/pendingActions.ts');
    const current = await localValue(queue);
    if(current.actor !== actor || current.payload.text !== 'Legacy unsent edit') throw new Error('Legacy request was lost');
    if(await localValue('pending:undefined:' + cid)) throw new Error('Legacy request was not migrated atomically');
    await removePending(queue, current.key);
  }, {queue,cid,actor:user.id});

  // Display-only fixture injection: an unmapped operation and a row without linked originals.
  const fake = structuredClone(assessment);
  const row = fake.line_items.find(r => r.row_state !== 'excluded');
  row.operation = 'unmapped'; row.evidence_ids = []; row.overall_result = 'unsupported';
  fake.files = [{file_id:'unrelated',original_name:'Unrelated upload.png',role:'photograph',
    media_type:'image/png',url:'/images/damaged-car.png'}];
  await b.route('**' + assessmentPath, route => route.fulfill({json:fake}));
  await b.reload();
  const card = b.locator(".line-items-panel article").filter({has:b.getByRole("heading",{name:row.description,exact:true})});
  await card.getByText('Correct row or add a missed mark', {exact:true}).click();
  assert.equal(await card.locator('select[name=operation]').inputValue(), 'unknown');
  await card.getByRole('button', {name:'View evidence'}).click();
  await b.getByText(/No linked original evidence is available/).waitFor();
  assert.equal(await b.locator('#evidence-panel img').count(),0);
  const style = await card.locator('.result-unsupported').evaluate(e => getComputedStyle(e).borderLeftStyle);
  assert.equal(style,'double');
  await mkdir('artifacts/evaluation/ui-defects', {recursive:true});
  await b.screenshot({path:'artifacts/evaluation/ui-defects/unlinked-and-unknown.png',fullPage:true});

  // Polling still works after fifteen minutes and recovers from a network failure.
  const p = await context.newPage();
  await p.clock.install();
  let reads=0, interrupted=false;
  await p.route(`**/api/v1/claims/${cid}`, route => {
    reads++;
    if(interrupted) return route.abort('failed');
    return route.fulfill({json:{...claim,status:'processing',assessment_revision:null}});
  });
  await p.route(`**/api/v1/claims/${cid}/processing`, route => route.fulfill({json:{state:'processing',jobs:[]}}));
  await p.goto(`${base}/claims/${cid}/review`);
  await p.getByRole('heading',{name:'Preparing your review'}).waitFor();
  const initial=reads;
  await p.clock.fastForward(16*60*1000);
  await p.waitForFunction(() => !document.body.innerText.includes('Automatic refresh paused'));
  assert.ok(reads>initial);
  interrupted=true;
  await p.clock.fastForward(4000);
  await p.getByText(/Connection interrupted. Retrying automatically/).waitFor();
  interrupted=false;
  await p.clock.fastForward(4000);
  await p.waitForFunction(() => !document.body.innerText.includes('Connection interrupted'));

  // Failed-stage retry is available even when an assessment has already been shown.
  let retried=false;
  await b.route(`**/api/v1/claims/${cid}/processing`, route => route.fulfill({json:{state:'incomplete',jobs:[
    {job_key:'failed-job',stage:'consolidate',state:'dead_lettered',retryable:true,error:'db_blip'}]}}));
  await b.route(`**/api/v1/claims/${cid}/jobs/failed-job/retry`, route => {retried=true; return route.fulfill({json:{ok:true}});});
  await b.reload();
  await b.getByRole('button',{name:'Retry consolidate',exact:true}).click();
  await b.getByText('Retry requested.',{exact:true}).waitFor();
  assert.ok(retried);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:'passed',checks:['two-tab queue and draft preservation','unmapped operation','unlinked evidence','distinct result styling','long-running reconnect polling','retry with existing assessment'],source:'synthetic browser fixtures'}));
} finally { await browser.close(); }
