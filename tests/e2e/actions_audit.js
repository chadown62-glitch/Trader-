// Audit dynamique de chaque action visible du cockpit, contre une instance de démonstration JETABLE (marché simulé,
// base SQLite temporaire, aucun ordre réel). Exploration en profondeur : les actions révélées par un clic (contenu d'un
// onglet) sont explorées avant les suivantes. Pour chaque action : requêtes /api provoquées (jusqu'à ce qu'aucune ne soit
// en cours), messages, demandes de confirmation, changement visible, erreurs du navigateur. Plus : liens de navigation,
// fenêtre d'arrêt d'urgence (revente fictive confirmée par URGENCE), thème, navigation mobile, déconnexion.
//
//   python tests/e2e/run.py actions_audit.js          → <OUTPUT>/actions.json
//   python tests/e2e/actions_inventory.py <OUTPUT>/actions.json docs/interface/inventaire-actions.md
//
// La session memecoin n'est pas démarrée ici : elle interroge des sources publiques (couverte par memecoin_check.js).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE, PWD = 'demo-mot-de-passe-123';
const OUT = process.env.OUT || path.join(process.env.OUTPUT || require('os').tmpdir(), 'actions.json');
const PAGES = (process.env.PAGES || 'dashboard,performance,trade,ai,history,knowledge,apprentissage,macro,preparation,memecoin,dmh,news,signals,settings,logs').split(',');
const SKIP = /Se déconnecter|⎋/;

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  let net = [], errors = [], inflight = 0;
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/')) inflight++; });
  const done = r => { if (new URL(r.url()).pathname.startsWith('/api/')) inflight = Math.max(0, inflight - 1); };
  page.on('requestfinished', done); page.on('requestfailed', done);
  page.on('response', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) net.push({ m: r.request().method(), p: u.pathname, s: r.status(), at: Date.now() }); });
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)); });
  page.on('dialog', d => { errors.push('[dialog natif] ' + d.message().slice(0, 80)); d.dismiss(); });
  const settle = async () => {
    const end = Date.now() + 8000; let quiet = 0;
    await page.waitForTimeout(300);
    while (Date.now() < end && quiet < 4) { quiet = inflight === 0 ? quiet + 1 : 0; await page.waitForTimeout(200); }
  };
  const results = [];
  await page.goto(BASE + '/');
  await page.waitForSelector('form.card');
  for (const i of await page.$$('input[type=password]')) await i.fill(PWD);
  {
    const t0 = Date.now();
    await page.click('button.primary');
    await page.waitForSelector('.shell', { timeout: 15000 });
    await page.waitForTimeout(800);
    results.push({ page: 'barre', label: 'Connexion (premier mot de passe)', outcome: 'cliqué', toasts: [], confirm: null, changed: true, errors: [],
      calls: [...new Set(net.filter(n => n.at >= t0 && n.p.startsWith('/api/auth/')).map(n => n.m + ' ' + n.p + ' ' + n.s))] });
  }
  const visible = async () => {
    const out = [], counts = {};
    for (const el of await page.$$('main.main button, main.main select, main.main input[type=checkbox]')) {
      if (!(await el.isVisible())) continue;
      const tag = await el.evaluate(e => e.tagName.toLowerCase());
      const text = ((await el.innerText().catch(() => '')) || (await el.getAttribute('title')) || (await el.getAttribute('aria-label'))
        || (await el.getAttribute('placeholder')) || tag).trim().replace(/\s+/g, ' ').slice(0, 60);
      counts[text] = (counts[text] || 0) + 1;
      out.push({ el, tag, text, key: tag + ':' + text + '#' + counts[text] });
    }
    return out;
  };
  const record = async (pageId, label, fn, keepModal = false) => {
    const t0 = Date.now(); errors = [];
    const before = await page.$$eval('.toasts .toast', ts => ts.map(t => t.innerText));
    const shown = await page.$eval('main.main', m => m.innerText).catch(() => '');
    let outcome = 'cliqué';
    try { await fn(); } catch (e) { outcome = 'échec du clic : ' + e.message.split('\n')[0].slice(0, 80); }
    await settle();
    let confirm = null;
    const modal = keepModal ? null : await page.$('.modal');
    if (modal) {
      confirm = (await modal.innerText()).split('\n')[0].slice(0, 70);
      const cancel = await page.$('.modal button.ghost');
      if (cancel) await cancel.click(); else await page.keyboard.press('Escape');
      await settle();
    }
    const after = await page.$eval('main.main', m => m.innerText).catch(() => '');
    const toasts = (await page.$$eval('.toasts .toast', ts => ts.map(t => t.className.replace('toast', '').trim() + ': ' + t.innerText)))
      .filter(t => !before.some(b => t.endsWith(b))).map(t => t.slice(0, 110));
    const calls = net.filter(n => n.at >= t0).map(n => n.m + ' ' + n.p + ' ' + n.s);
    results.push({ page: pageId, label, outcome, calls: [...new Set(calls)], toasts, confirm, changed: after !== shown,
      errors: [...new Set(errors)].slice(0, 3), hash: await page.evaluate(() => location.hash) });
  };
  for (const id of PAGES) {
    await page.goto(BASE + '/#/' + id);
    await settle(); await page.waitForTimeout(1200);
    const seen = new Set(), stack = [];
    let prev = new Set((await visible()).map(v => v.key));
    for (let guard = 0; guard < 120; guard++) {
      if (!(await page.evaluate(() => location.hash)).startsWith('#/' + id)) { await page.goto(BASE + '/#/' + id); await settle(); }
      const now = await visible();
      for (const v of now) if (!prev.has(v.key) && !seen.has(v.key) && !stack.includes(v.key)) stack.push(v.key);  // revealed by the last click
      let pick = null;
      while (stack.length && !pick) { const k = stack.pop(); pick = now.find(v => v.key === k && !seen.has(k)) || null; }
      if (!pick) pick = now.find(v => !seen.has(v.key) && !SKIP.test(v.text)) || null;
      if (!pick) break;
      seen.add(pick.key);
      prev = new Set(now.map(v => v.key));
      const label = (pick.tag === 'button' ? '' : pick.tag + ' ') + pick.text;
      if (await pick.el.evaluate(e => e.disabled === true)) { results.push({ page: id, label, outcome: 'désactivé', calls: [], toasts: [], confirm: null, changed: false, errors: [] }); continue; }
      await record(id, label, async () => {
        if (pick.tag === 'select') {
          const opts = await pick.el.$$eval('option', os => os.map(o => o.value));
          if (opts.length > 1) await pick.el.selectOption(opts[1]);
        } else await pick.el.click({ timeout: 3000 });
      });
    }
  }
  // desktop navigation: every link leads to its page
  await page.goto(BASE + '/#/dashboard'); await settle();
  for (const href of await page.$$eval('nav.nav a', as => as.map(a => a.getAttribute('href')))) {
    await record('nav', 'Lien ' + href, async () => { await page.click('nav.nav a[href="' + href + '"]'); });
    results[results.length - 1].arrived = (await page.evaluate(() => location.hash)) === href;
  }
  // emergency panel: scope, the typed confirmation, a real fictitious flatten, the pause, closing
  await page.goto(BASE + '/#/dashboard'); await settle();
  const modalStep = async (label, fn) => { await record('urgence', label, fn); };
  await page.click('button.urgence'); await page.waitForTimeout(500);
  const inModal = (text) => page.locator('.modal-bg .modal button', { hasText: text });
  await record('urgence', 'Positions de l\'IA', async () => { await inModal('Positions de l\'IA').click(); }, true);
  results[results.length - 1].changed = await inModal('Positions de l\'IA').evaluate(b => b.classList.contains('on'));
  await record('urgence', 'Toutes les positions', async () => { await inModal('Toutes les positions').click(); }, true);
  results[results.length - 1].changed = await inModal('Toutes les positions').evaluate(b => b.classList.contains('on'));
  results.push({ page: 'urgence', label: 'Tout revendre maintenant (sans saisie)', outcome: (await inModal('Tout revendre maintenant').isDisabled()) ? 'désactivé' : 'actif sans saisie',
    calls: [], toasts: [], confirm: null, changed: false, errors: [] });
  await page.fill('.modal-bg .modal input', 'URGENCE');
  await record('urgence', 'Tout revendre maintenant (URGENCE saisi)', async () => { await inModal('Tout revendre maintenant').click(); });
  await page.click('button.urgence'); await page.waitForTimeout(500);
  await record('urgence', '⏸ Arrêter les IA', async () => { await inModal('Arrêter les IA').click(); });
  if (await page.$('.modal-bg')) await record('urgence', 'Fermer', async () => { await inModal('Fermer').click(); });
  await record('barre', 'Urgence', async () => { await page.click('button.urgence'); });
  if (await page.$('.modal-bg')) await inModal('Fermer').click();
  await record('barre', 'Thème', async () => { await page.click('header.topbar button.ghost.small >> nth=0'); });
  results[results.length - 1].theme = await page.evaluate(() => document.documentElement.dataset.theme || document.body.className);
  // mobile: bottom navigation and the « Plus » sheet
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const m = await mctx.newPage();
  await m.goto(BASE + '/'); await m.waitForSelector('form.card');
  for (const i of await m.$$('input[type=password]')) await i.fill(PWD);
  await m.click('button.primary'); await m.waitForSelector('.shell');
  for (const href of await m.$$eval('nav.bottom-nav a[href^="#/"]', as => as.map(a => a.getAttribute('href')))) {
    await m.click('nav.bottom-nav a[href="' + href + '"]'); await m.waitForTimeout(600);
    results.push({ page: 'mobile', label: 'Barre basse ' + href, outcome: 'cliqué', calls: [], toasts: [], confirm: null, changed: true, errors: [],
      arrived: (await m.evaluate(() => location.hash)) === href });
  }
  await m.click('nav.bottom-nav a[role=button]'); await m.waitForTimeout(500);
  const sheet = await m.$$eval('.more-sheet a', as => as.map(a => a.getAttribute('href')));
  results.push({ page: 'mobile', label: 'Plus (menu)', outcome: sheet.length ? 'cliqué' : 'aucun menu', calls: [], toasts: [], confirm: null, changed: !!sheet.length, errors: [] });
  if (sheet.length) {
    await m.click('.more-sheet a[href="' + sheet[0] + '"]'); await m.waitForTimeout(600);
    results.push({ page: 'mobile', label: 'Plus → ' + sheet[0], outcome: 'cliqué', calls: [], toasts: [], confirm: null, changed: true, errors: [],
      arrived: (await m.evaluate(() => location.hash)) === sheet[0], closed: !(await m.$('.more-sheet')) });
  }
  const overflow = await m.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  results.push({ page: 'mobile', label: 'Largeur de page à 390 px', outcome: overflow ? 'débordement horizontal' : 'sans débordement', calls: [], toasts: [], confirm: null, changed: false, errors: [] });
  await mctx.close();
  await record('barre', 'Déconnexion', async () => { await page.click('header.topbar button[title="Se déconnecter"]'); });
  results[results.length - 1].login_shown = !!(await page.$('form.card'));
  fs.writeFileSync(OUT, JSON.stringify(results, null, 1));
  console.log(results.length, 'actions cliquées');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
