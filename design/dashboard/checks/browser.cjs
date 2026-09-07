/* Protect the minimum reading journey and reusable dashboard behavior. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const { labels: t } = JSON.parse(fs.readFileSync(path.join(__dirname, '../content.json')));
const base = process.env.PC_DESIGN_URL || 'http://127.0.0.1:8765';

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1536, height: 1024 } });
    const page = await context.newPage();
    const errors = [];
    const unexpected = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (!request.url().startsWith(base) || request.method() !== 'GET') unexpected.push(request.url());
    });
    const visit = route => page.goto(`${base}${route}`);
    const text = () => page.locator('#main').innerText();

    await visit('/home');
    await page.selectOption('#scope', 'research');
    await page.getByRole('button', { name: t.apply, exact: true }).click();
    await page.waitForURL('**scope=research**');
    assert.ok((await text()).includes(t.research));
    await page.getByRole('link', { name: t.read_experience }).click();
    await page.waitForURL('**/experience?**');
    assert.ok((await text()).includes(t.research));
    await page.goBack();
    await page.locator('#handoff').waitFor();
    assert.ok((await text()).includes(t.research));

    await visit('/home');
    await page.getByRole('link', { name: t.read_experience }).click();
    await page.waitForURL('**/experience?**');
    assert.equal(await page.title(), `${t.experience} · PowerContext`);
    await page.locator('#request-status').waitFor({ state: 'hidden' });
    await page.locator('[data-evidence]').first().focus();
    await page.keyboard.press('Enter');
    await page.locator('#evidence.show').waitFor();
    assert.match(await page.locator('#evidence-content').innerText(), /connection-investigation/);
    await page.locator('#evidence-content .nav-link').nth(1).click();
    await page.waitForFunction(() => document.querySelector('#evidence-content').textContent.includes('cancellation-test'));
    const [source] = await Promise.all([
      context.waitForEvent('page'), page.getByRole('link', { name: t.open_full }).click(),
    ]);
    await source.waitForLoadState();
    assert.match(await source.locator('main').innerText(), /cancellation-test/);
    await source.close();
    await page.locator('#evidence .btn-close').focus();
    await page.keyboard.press('Escape');
    await page.locator('#evidence.show').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('[data-evidence]').first().evaluate(el => el === document.activeElement), true);
    await page.getByRole('link', { name: t.back_methods }).click();
    await page.waitForURL('**#methods');
    await page.locator('#methods').waitFor();
    await page.locator('#skill-preview .accordion-button').click();
    assert.ok((await text()).includes(t.skill_notice));

    await visit('/experience?state=missing');
    await page.locator('[data-evidence]').first().click();
    await page.locator('#evidence.show').waitFor();
    assert.ok((await page.locator('#evidence-content').innerText()).includes(t.source_unavailable));
    await visit('/home?state=empty');
    assert.equal(await page.locator('.handoff-sheet').count(), 0);
    await visit('/home');
    await page.route('**/experience?**', route => route.abort());
    await page.getByRole('link', { name: t.read_experience }).click();
    await page.locator('#network-error:not([hidden])').waitFor();
    assert.equal(await page.locator('#handoff').count(), 1);
    await page.unroute('**/experience?**');

    await visit('/usage');
    assert.match(await page.locator('.big-number').innerText(), /40/);
    await page.getByRole('button', { name: t.daily_details, exact: true }).click();
    assert.equal(await page.locator('.daily-table tbody tr').count(), 7);
    assert.equal(await page.locator('.daily-table tbody td:nth-child(2)').evaluateAll(cells => cells.reduce((sum, cell) => sum + Number(cell.textContent.replaceAll(',', '')), 0)), 120000);
    await page.getByRole('link', { name: t.today, exact: true }).click();
    await page.waitForURL('**period=today**');
    assert.equal(await page.locator('.daily-table tbody tr').count(), 1);
    await page.getByRole('link', { name: t['30d'], exact: true }).click();
    await page.waitForURL('**period=30d**');
    assert.equal(await page.locator('.daily-table tbody tr').count(), 30);
    await visit('/usage?state=unknown');
    assert.ok((await text()).includes(t.unknown_value));
    await visit('/usage?state=uncomparable');
    assert.equal(await page.locator('.big-number').count(), 0);
    assert.match(await text(), /0 \/ 24/);
    await visit('/usage?state=negative');
    assert.ok((await text()).includes(t.increased));
    assert.match(await text(), /7,000/);
    await visit('/usage?state=unavailable');
    assert.ok((await text()).includes(t.usage_unavailable));
    await visit('/experience?ablation=evidence');
    assert.equal(await page.locator('[data-evidence]').count(), 0);
    await visit('/usage?ablation=chart');
    assert.equal(await page.locator('[data-comparison-chart]').count(), 0);
    assert.equal(await page.locator('.daily-table').count(), 1);

    const partial = await context.request.get(`${base}/home`, { headers: { 'HX-Request': 'true' } });
    assert.doesNotMatch(await partial.text(), /<!doctype/i);
    const restored = await context.request.get(`${base}/home`, { headers: { 'HX-Request': 'true', 'HX-History-Restore-Request': 'true' } });
    assert.match(await restored.text(), /<!doctype/i);
    assert.equal((await context.request.get(`${base}/not-a-page`)).status(), 404);
    const plain = await browser.newPage({ javaScriptEnabled: false });
    await plain.goto(`${base}/home`);
    await plain.getByRole('link', { name: t.read_experience }).click();
    await plain.locator('[data-evidence]').first().click();
    assert.match(await plain.locator('main').innerText(), /connection-investigation/);
    await plain.close();

    await page.setViewportSize({ width: 390, height: 844 });
    for (const route of ['/home', '/experience', '/usage', '/usage?period=30d']) {
      await visit(route);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${route} overflows on mobile`);
    }
    await visit('/experience');
    await page.locator('[data-evidence]').first().click();
    await page.locator('#evidence.show').waitFor();
    await page.getByRole('button', { name: t.evidence_close }).click();
    await page.locator('#evidence.show').waitFor({ state: 'hidden' });
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log('Reading journey, history, keyboard, fallback HTML, failures, statistics and mobile baseline passed.');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
