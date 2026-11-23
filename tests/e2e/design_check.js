// Navigation et thème (chantier Interface V1). Défaut D-UI4, constaté sur main (6d46dc9) après l'inventaire : système en
// clair, le cockpit s'ouvrait en sombre ; en thème clair, listes et barres de défilement restaient sombres (color-scheme
// « dark ») et la barre du navigateur mobile gardait la couleur sombre (theme-color #05070D).
// Contre une instance de démonstration JETABLE (python tests/e2e/run.py design_check.js) :
// 1. thème : sans choix enregistré, le cockpit suit le système (clair ou sombre) ; les contrôles natifs (color-scheme) et
//    la couleur de la barre du navigateur (theme-color) suivent le thème actif ; le choix de l'utilisateur est conservé
//    après rechargement ;
// 2. menu latéral : 5 rubriques nommées couvrant les 15 pages, chacune une seule fois ; chaque lien ouvre sa page ;
// 3. téléphone (390 px) : barre du bas (4 pages + « Plus »), le menu « Plus » donne les 11 autres, aucune page ne
//    déborde horizontalement.
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PWD = 'demo-mot-de-passe-123';
const GROUPS = {
  'Pilotage': ['dashboard', 'performance', 'history', 'logs'],
  'Trading fictif': ['trade', 'memecoin', 'dmh'],
  'Modèles IA': ['ai', 'apprentissage', 'preparation'],
  'Informations': ['knowledge', 'macro', 'news', 'signals'],
  'Configuration': ['settings'],
};
const PAGES = Object.values(GROUPS).flat();
const BOTTOM = ['dashboard', 'performance', 'trade', 'ai'];
const check = (ok, message) => { if (!ok) throw new Error(message); };
const hashes = ids => ids.map(id => '#/' + id);

const readTheme = page => page.evaluate(() => ({
  theme: document.documentElement.dataset.theme,
  native: getComputedStyle(document.documentElement).colorScheme,
  meta: document.querySelector('meta[name="color-scheme"]')?.content,
  bar: document.querySelector('meta[name="theme-color"]')?.content,
}));
const expectTheme = async (page, theme, when) => {
  const t = await readTheme(page);
  const want = { theme, native: theme, meta: theme, bar: theme === 'light' ? '#F4F6FB' : '#05070D' };
  check(JSON.stringify(t) === JSON.stringify(want), `${when} : thème ${JSON.stringify(t)}, attendu ${JSON.stringify(want)}`);
};

async function login(context, errors) {
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(BASE + '/');
  await page.waitForSelector('form.card');
  for (const input of await page.$$('input[type=password]')) await input.fill(PWD);  // 2 champs à la première connexion
  await page.click('button.primary');
  await page.waitForSelector('.shell', { timeout: 15000 });
  return page;
}

(async () => {
  const browser = await chromium.launch();
  const errors = [];

  // 1. Thème. Système en clair, aucun choix enregistré : le cockpit s'ouvre en clair, dès l'écran de connexion.
  const light = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'light' });
  const probe = await light.newPage();
  await probe.goto(BASE + '/');
  await probe.waitForSelector('form.card');
  await expectTheme(probe, 'light', 'écran de connexion, système en clair');
  await probe.close();
  let page = await login(light, errors);
  await expectTheme(page, 'light', 'cockpit, système en clair');
  const toggle = page.locator('header.topbar button[title^="Thème"]');
  check((await toggle.getAttribute('title')) === 'Thème sombre', 'le bouton doit proposer le thème sombre');
  await toggle.click();
  await expectTheme(page, 'dark', 'après « Thème sombre »');
  await page.reload();
  await page.waitForSelector('.shell');
  await expectTheme(page, 'dark', 'choix sombre après rechargement (système toujours en clair)');
  await light.close();

  // Système en sombre, nouveau profil : le cockpit s'ouvre en sombre.
  const dark = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'dark' });
  page = await login(dark, errors);
  await expectTheme(page, 'dark', 'cockpit, système en sombre');

  // 2. Menu latéral regroupé.
  const sections = await page.$$eval('.nav > .nav-group, .nav > a[href]', els => els.map(e =>
    e.matches('a') ? { href: e.getAttribute('href') } : { group: e.textContent.trim() }));
  const found = {};
  let current = null;
  for (const s of sections) {
    if (s.group) { current = s.group; found[current] = []; } else {
      check(current, 'lien hors rubrique : ' + s.href);
      found[current].push(s.href);
    }
  }
  check(JSON.stringify(Object.keys(found)) === JSON.stringify(Object.keys(GROUPS)), 'rubriques : ' + Object.keys(found).join(', '));
  for (const [g, ids] of Object.entries(GROUPS)) {
    check(JSON.stringify(found[g]) === JSON.stringify(hashes(ids)), `rubrique « ${g} » : ${found[g]}`);
  }
  const all = Object.values(found).flat();
  check(all.length === 15 && new Set(all).size === 15, '15 pages, chacune une seule fois : ' + all.length);
  for (const id of PAGES) {
    await page.click(`.nav a[href="#/${id}"]`);
    await page.waitForFunction(id => location.hash === '#/' + id, id);
    await page.waitForSelector(`.nav a[href="#/${id}"][aria-current="page"]`);
    check(await page.locator('main.main > *').count() > 0, 'page vide : ' + id);
  }

  // 3. Téléphone.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + '/#/dashboard');
  await page.waitForSelector('.bottom-nav a');
  check(!(await page.isVisible('.nav')), 'le menu latéral doit être masqué sur téléphone');
  const bottom = await page.$$eval('.bottom-nav a', els => els.map(e => [e.getAttribute('href'), e.textContent.trim()]));
  check(JSON.stringify(bottom.slice(0, 4).map(b => b[0])) === JSON.stringify(hashes(BOTTOM)), 'barre du bas : ' + bottom);
  check(bottom.length === 5 && bottom[4][1].endsWith('Plus'), 'dernier bouton « Plus » : ' + bottom);
  await page.click('.bottom-nav a:has-text("Plus")');
  const others = await page.$$eval('.more-sheet a[href]', els => els.map(e => e.getAttribute('href')));
  check(JSON.stringify([...others].sort()) === JSON.stringify(hashes(PAGES.filter(id => !BOTTOM.includes(id))).sort()),
    'menu « Plus » : ' + others);
  await page.click('.more-sheet a[href="#/settings"]');
  await page.waitForFunction(() => location.hash === '#/settings' && !document.querySelector('.more-sheet'));
  const overflowing = [];
  for (const id of PAGES) {
    await page.goto(BASE + '/#/' + id);
    await page.waitForTimeout(1200);  // données chargées : une table ou un graphique peut élargir la page
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (over > 1) overflowing.push(`${id} (+${over} px)`);
  }
  check(overflowing.length === 0, 'débordement horizontal à 390 px : ' + overflowing.join(', '));

  check(errors.length === 0, 'erreurs JavaScript : ' + errors.join(' ; '));
  console.log('thème du système puis choix conservé ; 5 rubriques, 15 pages ; téléphone sans débordement');
  await browser.close();
})().catch(e => { console.error(e.message || e); process.exit(1); });
