// Vérification visuelle du panel avec un vrai navigateur (Playwright).
// Usage : lancer `python -m sentinelle --demo --no-browser --port 8766`, puis
//   BASE=http://127.0.0.1:8766 OUT=./captures node tests/e2e/ui_check.js
const { chromium, devices } = require('playwright');
const OUT = process.env.OUT || require('os').tmpdir();
require('fs').mkdirSync(OUT, { recursive: true });
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PWD = 'demo-mot-de-passe-123';

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${m.type()}] ${m.text()}`); });
  page.on('pageerror', e => errors.push(`[pageerror] ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForSelector('form.card');
  const inputs = await page.$$('input[type=password]');
  for (const i of inputs) await i.fill(PWD);
  await page.click('button.primary');
  await page.waitForSelector('.shell', { timeout: 10000 });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/01-dashboard.png`, fullPage: true });

  await page.goto(BASE + '/#/trade');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${OUT}/02-trade.png`, fullPage: true });
  await page.click('text=Calculer la quantité selon le risque');
  await page.locator('.ticket .pill-row button', { hasText: '−2 %' }).click();
  await page.locator('.ticket .pill-row button', { hasText: '+4 %' }).click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${OUT}/03-ticket.png`, fullPage: false });
  await page.click('.ticket button.buy');
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/04-after-buy.png`, fullPage: true });

  for (const [name, hash] of [['05-ai', 'ai'], ['06-history', 'history'], ['07-news', 'news'], ['08-settings', 'settings'], ['09-logs', 'logs'], ['13-signals', 'signals']]) {
    await page.goto(`${BASE}/#/${hash}`);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  }
  // Réglages ajoutés pour le temps réel : bouclier, calendrier automatique, canaux de dépêches
  await page.goto(BASE + '/#/settings');
  await page.waitForTimeout(800);
  for (const [name, label] of [['15-settings-shield', 'Bouclier'], ['16-settings-macro', 'Calendrier macro'], ['17-settings-news', 'Actualités']]) {
    await page.locator('.tabs button', { hasText: label }).click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  }
  await page.locator('.tabs button', { hasText: 'Bouclier' }).click();
  const cooldown = page.locator('.field', { hasText: 'Durée de la pause après un mouvement brutal' }).locator('input');
  await cooldown.fill('45');
  await page.locator('.card button.primary', { hasText: 'Enregistrer' }).click();
  await page.waitForTimeout(800);
  const saved = await page.evaluate(async () => (await (await fetch('/api/settings')).json()).shield.cooldown_minutes);
  if (saved !== 45) errors.push(`[réglages] bouclier non enregistré : ${saved}`);
  await page.locator('.tabs button', { hasText: 'Calendrier macro' }).click();
  const countries = page.locator('.field', { hasText: 'Monnaies suivies' }).locator('input');
  await countries.fill('usd, eur, gbp');
  await countries.blur();
  await page.locator('.card button.primary', { hasText: 'Enregistrer' }).click();
  await page.waitForTimeout(800);
  const cc = await page.evaluate(async () => (await (await fetch('/api/settings')).json()).macro.calendar_countries.join(','));
  if (cc !== 'USD,EUR,GBP') errors.push(`[réglages] monnaies non enregistrées : ${cc}`);

  // Capacités d'apprentissage (banc d'essai) et fiches par crypto
  await page.goto(BASE + '/#/ai');
  await page.waitForTimeout(800);
  await page.locator('.tabs button', { hasText: 'Capacités d' }).click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/18-ai-learning.png`, fullPage: true });
  if (process.env.BENCH === '1') {
    await page.click('text=Tester les capacités');
    await page.waitForSelector('text=Détail des épreuves', { timeout: 600000 });
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/18b-ai-learning-done.png`, fullPage: true });
  }
  await page.goto(BASE + '/#/knowledge');
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/19-knowledge.png`, fullPage: true });

  await page.goto(BASE + '/#/ai');
  await page.waitForTimeout(800);
  await page.click("text=Démarrer l'entraînement");
  await page.waitForTimeout(8000);
  await page.screenshot({ path: `${OUT}/10-ai-started.png`, fullPage: true });

  await page.goto(BASE + '/#/dashboard');
  await page.click('button[title="Thème clair"]');
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/14-light-dashboard.png`, fullPage: true });
  await page.click('button[title="Thème sombre"]');
  const mob = await browser.newContext({ ...devices['iPhone 13'] });
  const mp = await mob.newPage();
  mp.on('pageerror', e => errors.push(`[mobile pageerror] ${e.message}`));
  await mp.goto(BASE + '/');
  await mp.waitForSelector('form.card');
  await mp.fill('input[type=password]', PWD);
  await mp.click('button.primary');
  await mp.waitForSelector('.shell');
  await mp.waitForTimeout(2500);
  await mp.screenshot({ path: `${OUT}/11-mobile-dashboard.png`, fullPage: true });
  await mp.goto(BASE + '/#/trade');
  await mp.waitForTimeout(2500);
  await mp.screenshot({ path: `${OUT}/12-mobile-trade.png`, fullPage: true });
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  console.log('mobile horizontal overflow:', overflow);
  console.log('ERRORS:\n' + (errors.join('\n') || 'aucune'));
  await browser.close();
})();
