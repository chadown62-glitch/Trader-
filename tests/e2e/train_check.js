// Vérification de l'entraînement depuis le panel, sur données réelles.
// Usage : lancer `python -m sentinelle --no-browser --port 8768 --data <dossier>`, puis
//   BASE=http://127.0.0.1:8768 OUT=./captures node tests/e2e/train_check.js
const { chromium } = require('playwright');
const OUT = process.env.OUT || require('os').tmpdir();
const BASE = process.env.BASE || 'http://127.0.0.1:8768';
require('fs').mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(BASE + '/');
  await page.waitForSelector('form.card');
  for (const i of await page.$$('input[type=password]')) await i.fill('mot-de-passe-de-test-123');
  await page.click('button.primary');
  await page.waitForSelector('.shell');
  await page.goto(BASE + '/#/ai');
  await page.click("text=Apprentissage sur l'historique");
  await page.selectOption('select', { label: '4 ans' });
  await page.click("text=Lancer l'entraînement");
  const t0 = Date.now();
  await page.waitForFunction(() => document.body.innerText.includes('Dernier entraînement'), null, { timeout: 600000 });
  await page.waitForTimeout(3000);
  console.log('entraînement terminé en', Math.round((Date.now() - t0) / 1000), 's');
  await page.screenshot({ path: `${OUT}/train-report.png`, fullPage: true });
  await page.click('text=Simulation accélérée');
  await page.selectOption('select >> nth=0', { label: '1 an' });
  await page.selectOption('select >> nth=1', { label: 'Tous les signaux (sans filtre IA)' });
  await page.click('text=Lancer la simulation');
  await page.waitForFunction(() => document.body.innerText.includes('Frais payés'), null, { timeout: 600000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/backtest.png`, fullPage: true });
  console.log('ERREURS :', errors.length ? errors.join('\n') : 'aucune');
  await browser.close();
})();
