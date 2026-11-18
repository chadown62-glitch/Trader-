// Parcours du panel dans un vrai navigateur (Chromium), en 1440 px et 390 px.
// À lancer UNIQUEMENT contre une instance de démonstration jetable :
//   python tests/e2e/run.py phase_h_check.js
// Vérifie : navigation (menu latéral, barre du bas, feuille « Plus »), erreurs JavaScript et console,
// défilement horizontal, libellés « argent fictif », démo jamais présentée comme performance réelle,
// prix périmé, codes internes traduits, page de préparation sans bouton de démarrage.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const OUT = process.env.OUTPUT || require('node:os').tmpdir();
const PASSWORD = 'mot-de-passe-jetable-e2e';
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}) });
  const results = [];
  try {
    for (const width of [1440, 390]) {
      const ctx = await browser.newContext({ viewport: { width, height: width > 600 ? 900 : 844 } });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push('pageerror: ' + e.message));
      page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
      page.on('response', r => { if (r.status() >= 500) errors.push('http ' + r.status() + ' ' + r.url()); });
      await page.goto(BASE + '/');
      const fields = page.locator('input[type=password]');
      await fields.first().waitFor();
      for (let i = 0; i < await fields.count(); i++) await fields.nth(i).fill(PASSWORD);
      await page.locator('button.primary').click();
      await page.locator('.shell').waitFor();

      const go = async (id) => {
        if (width < 600) {
          const direct = page.locator(`.bottom-nav a[href="#/${id}"]`);
          if (await direct.count()) await direct.click();
          else {
            await page.locator('.bottom-nav a').filter({ hasText: 'Plus' }).click();
            const link = page.locator(`.more-sheet a[href="#/${id}"]`);
            await link.scrollIntoViewIfNeeded();
            await link.click();
          }
        } else await page.locator(`.nav a[href="#/${id}"]`).click();
        await page.waitForTimeout(400);
      };
      const noOverflow = async (name) => {
        const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        assert.ok(over <= 0, `${name} @${width}px : défilement horizontal de ${over}px`);
      };
      const shot = async (name) => page.screenshot({ path: `${OUT}/${name}-${width}.png`, fullPage: true });

      // Performances : argent fictif, démonstration signalée, rien d'inventé
      await go('performance');
      await page.getByText('Validation scientifique :', { exact: false }).waitFor();
      await page.locator('.badge.paper', { hasText: 'ARGENT FICTIF' }).first().waitFor();
      await page.getByText('Marché simulé (démonstration)', { exact: false }).first().waitFor();
      assert.match(await page.locator('[data-nature]').innerText(), /marché simulé \(démonstration\)/);
      const perf = await page.evaluate(async () => (await fetch('/api/performance')).json());
      assert.equal(perf.capital.initial, 30);
      assert.equal(perf.account.currency, 'EUR');
      await noOverflow('performance'); await shot('performance');

      // Apprentissage : états vides explicites, aucun code brut
      await go('apprentissage');
      await page.getByRole('heading', { name: 'Apprentissage', exact: true }).waitFor();
      await page.getByText('Aucune comparaison prospective enregistrée. Aucun résultat inventé.').waitFor();
      await page.getByText('Aucun essai enregistré.').waitFor();
      await page.locator('#journal-filter').selectOption('refusé');
      await page.getByText('Aucune décision enregistrée.', { exact: true }).waitFor();
      const appText = await page.locator('.main').innerText();
      assert.ok(!/unverified_or_unreadable|T\d\d:\d\d:\d\d(\.\d+)?Z/.test(appText), 'apprentissage : code ou date ISO brute affichés');
      await noOverflow('apprentissage'); await shot('apprentissage');

      // Contexte macro : états traduits, lecture seule
      await go('macro');
      await page.getByRole('heading', { name: 'Contexte macro et actualités' }).waitFor();
      await page.getByText('Données insuffisantes', { exact: true }).waitFor();
      await page.getByText('Jamais reçue').first().waitFor();
      const macroText = await page.locator('.main').innerText();
      assert.ok(!/\bmissing\b|unknown_not_supplied|T\d\d:\d\d:\d\d(\.\d+)?Z/.test(macroText), 'macro : code ou date ISO brute affichés');
      await page.getByRole('button', { name: 'Relire le journal local' }).click();
      await noOverflow('macro'); await shot('macro');

      // Trading fictif : badge argent fictif, puis prix périmé simulé par le navigateur (aucune donnée modifiée côté serveur)
      await go('trade');
      await page.locator('.badge.paper', { hasText: 'ARGENT FICTIF' }).first().waitFor();
      assert.equal(await page.locator('.stale-badge').count(), 0, 'prix frais présentés comme périmés');
      await page.route('**/api/overview', async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        for (const s of Object.keys(body.market.fresh || {})) body.market.fresh[s] = false;
        await route.fulfill({ response, json: body });
      });
      await page.locator('.stale-badge', { hasText: 'prix périmé' }).waitFor({ timeout: 12000 });
      await page.getByText('Au-delà de 3 minutes sans prix', { exact: false }).waitFor();
      await shot('trade-prix-perime');
      await page.unroute('**/api/overview');
      await noOverflow('trade'); await shot('trade');

      // Paramètres : onglets, portefeuille 30 €
      await go('settings');
      await page.locator('.tabs button', { hasText: 'Portefeuille' }).click();
      await page.waitForTimeout(300);
      await noOverflow('settings'); await shot('settings');

      // Préparation de l'observation : verdict BLOCKED en démonstration, aucun bouton de démarrage
      await go('preparation');
      await page.getByRole('heading', { name: "Préparation de l'observation" }).waitFor();
      const verdict = page.locator('[data-verdict]');
      await verdict.waitFor();
      assert.match(await verdict.innerText(), /BLOQUÉ \(BLOCKED\)/);
      for (const key of ['runner', 'market_source', 'windows']) await page.locator(`[data-check="${key}"]`).first().waitFor();
      const buttons = await page.locator('.main button').allInnerTexts();
      assert.deepEqual(buttons.map(b => b.trim()), ['Refaire le contrôle']);
      assert.ok(!buttons.some(b => /démarrer/i.test(b)));
      await page.getByRole('button', { name: 'Refaire le contrôle' }).click();
      await verdict.waitFor();
      await noOverflow('preparation'); await shot('preparation');

      if (width < 600) {
        // Feuille « Plus » : tous les liens restent accessibles, même sur petit écran
        await page.setViewportSize({ width: 390, height: 600 });
        await page.locator('.bottom-nav a').filter({ hasText: 'Plus' }).click();
        const last = page.locator('.more-sheet a').last();
        await last.scrollIntoViewIfNeeded();
        assert.ok(await last.isVisible(), 'dernier lien de la feuille « Plus » inaccessible');
        await page.locator('.more-sheet').click({ position: { x: 5, y: 5 } });
      }
      assert.deepEqual(errors, [], `erreurs navigateur @${width}px`);
      results.push(`${width}px : OK`);
      await ctx.close();
    }
    console.log('Parcours phase H : ' + results.join(' ; ') + ` (captures : ${OUT})`);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
