// Run with python tests/e2e/run.py ai_reports_check.js on its disposable demo.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({
    headless: true, ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}),
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = process.env.BASE;
    await page.goto(base);
    await page.locator('input[type=password]').first().waitFor();
    for (const field of await page.locator('input[type=password]').all()) await field.fill('test-local-rapports');
    await page.locator('button.primary').click();
    await page.locator('.shell').waitFor();

    const versions = ['archive-a', 'archive-b', 'archive-c'];
    await page.route('**/api/ai/models', route => route.fulfill({
      json: versions.map(version => ({ version, status: 'challenger', metrics: {} })),
    }));
    let releaseA;
    const gate = new Promise(resolve => { releaseA = resolve; });
    await page.route('**/api/ai/models/*/report', async route => {
      const version = route.request().url().split('/').at(-2);
      if (version === 'archive-a') await gate;
      const report = { symbols: [version], timeframe: '4h', reasons: [], verdict: 'non validé' };
      if (version === 'archive-b') report.backtest_model = {
        equity_metrics: { return_pct: 0, max_dd_pct: null }, equity: [], buy_hold_pct: null,
      };
      if (version === 'archive-c') report.paper_comparison = { reason: 'Fenêtre finale déjà évaluée' };
      await route.fulfill({ json: report });
    });
    const firstReport = page.waitForRequest(req => req.url().endsWith('/archive-a/report'));
    await page.goto(base + '/#/ai');
    await page.getByRole('button', { name: "Apprentissage sur l'historique" }).click();
    await firstReport;
    const row = version => page.getByRole('row').filter({ hasText: version });
    await row('archive-b').getByRole('button', { name: 'Rapport' }).click();
    const header = page.locator('.card-head').filter({ hasText: "Rapport d'entraînement" });
    await header.getByText('archive-b · 4h', { exact: false }).waitFor();
    const receivedA = page.waitForResponse(response => response.url().endsWith('/archive-a/report'));
    releaseA();
    await (await receivedA).finished();
    // Drain the pending fetch and Vue's DOM update before examining the visible report.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.match(await header.innerText(), /archive-b · 4h/);
    const summary = page.locator('.stats-row').filter({ hasText: 'Acheter et garder' });
    assert.deepEqual(await summary.locator('.value').allTextContents(), ['0,00 %', '—', '—']);
    await row('archive-c').getByRole('button', { name: 'Rapport' }).click();
    await page.getByText('Simulation indisponible. Fenêtre finale déjà évaluée').waitFor();
    await row('archive-a').getByRole('button', { name: 'Rapport' }).click();
    await page.getByText('Le rapport ne précise pas le motif.', { exact: false }).waitFor();

    // The job and its results are served by FastAPI against synthetic prices.
    const simulation = page.getByRole('button', { name: 'Simulation accélérée' });
    await simulation.click();
    await page.locator('main input[type=number]').fill('30');
    const launched = page.waitForResponse(r => r.url().endsWith('/api/ai/backtest') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Lancer la simulation' }).click();
    const { id } = await (await launched).json();
    const result = page.locator('main .stats-row').filter({ hasText: 'Frais payés' });
    await result.waitFor({ timeout: 90000 });
    const before = await result.innerText();
    await page.reload();
    await simulation.click();
    await result.waitFor();
    assert.equal(await result.innerText(), before);
    await page.getByText('Simulation ' + id, { exact: false }).waitFor();
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
