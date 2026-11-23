// Actions secondaires du cockpit (chantier Interface V1, fiabilisation finale), contre une instance de démonstration
// JETABLE (python tests/e2e/run.py secondary_check.js). Chaque action est vérifiée par son effet : requête de lecture avec
// le bon paramètre, état enregistré sur le serveur ou dans le navigateur, contenu affiché.
// 1. Double authentification : activation avec un vrai code, connexion qui exige le code (mauvais code refusé), puis
//    désactivation ; la connexion suivante ne demande plus de code.
// 2. Trader : crypto et unité de temps (bougies relues), devise ou quantité, côtés Vendre puis Acheter.
// 3. Onglets, périodes et filtres : IA, Performances (boutons et lignes du tableau), Tableau de bord, Historique, Journal,
//    Réglages, Memecoin, Hub ; détail d'une vente fictive dans l'Historique.
// 3 bis. Arrêt d'urgence : « Fermer » ferme la fenêtre sans rien envoyer ; « Positions de l'IA » envoie la portée « ai »
//    (requête interceptée : rien n'est revendu).
// 4. Signaux : un canal ajouté puis retiré disparaît des réglages enregistrés.
// 5. Notifications du navigateur : activées puis désactivées (permission accordée par le test).
const { chromium } = require('playwright');
const crypto = require('crypto');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PWD = 'demo-mot-de-passe-123';
const check = (ok, message) => { if (!ok) throw new Error(message); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Code à usage unique (RFC 6238 : SHA-1, 30 s, 6 chiffres), comme une application d'authentification.
const base32 = s => {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bits = [...s.replace(/=+$/, '').toUpperCase()].map(c => A.indexOf(c).toString(2).padStart(5, '0')).join('');
  return Buffer.from(bits.match(/.{8}/g).map(b => parseInt(b, 2)));
};
const totp = (secret, step) => {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const mac = crypto.createHmac('sha1', base32(secret)).update(counter).digest();
  return String((mac.readUInt32BE(mac[mac.length - 1] & 15) & 0x7fffffff) % 1e6).padStart(6, '0');
};
let lastStep = 0;
// Le serveur refuse un code déjà utilisé et tolère ±30 s : fenêtre courante, sinon la suivante. Jamais la précédente, qui
// sortirait de la tolérance si la limite des 30 s tombait entre le calcul du code et sa vérification.
const freshCode = async (secret) => {
  for (;;) {
    const step = [0, 1].map(d => Math.floor(Date.now() / 30000) + d).find(s => s > lastStep);
    if (step) { lastStep = step; return totp(secret, step); }
    await sleep(1000);
  }
};

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.grantPermissions(['notifications'], { origin: BASE });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const reads = [];
  page.on('request', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/') && r.method() === 'GET') reads.push(u.pathname + u.search); });
  const toasts = () => page.$$eval('.toasts .toast', els => els.map(e => e.textContent.trim()));
  const json = p => page.evaluate(async p => (await fetch(p)).json(), p);
  const waitToast = text => page.waitForFunction(t => [...document.querySelectorAll('.toasts .toast')].some(x => x.textContent.trim() === t), text);
  const login = async (code) => {
    await page.waitForSelector('form.card');
    for (const input of await page.$$('input[type=password]')) await input.fill(PWD);
    if (code) await page.fill('#otp', code);
    await page.click('button.primary');
  };
  await page.goto(BASE + '/');
  await login();
  await page.waitForSelector('.shell', { timeout: 15000 });

  // 1. Double authentification.
  await page.goto(BASE + '/#/settings');
  await page.click('.tabs button:has-text("Sécurité")');
  await page.click('button:has-text("Activer la double authentification")');
  const secret = (await page.locator('p.mono').innerText()).trim();
  check(/^[A-Z2-7]{16,}$/.test(secret), 'clé secrète affichée attendue : ' + secret);
  await page.fill('input[maxlength="6"]', await freshCode(secret));
  await page.click('button:has-text("Valider")');
  await waitToast('Double authentification activée');
  await page.waitForSelector('.note:has-text("Activée.")');
  await page.click('header.topbar button[title="Se déconnecter"]');
  await login();
  await page.waitForSelector('#otp');  // le mot de passe seul ne suffit plus
  check(!(await page.isVisible('.shell')), 'la connexion doit exiger le code');
  await page.fill('#otp', '000000');
  await page.click('button.primary');
  await page.waitForSelector('.note.danger:has-text("Code de double authentification invalide")');
  await page.fill('#otp', await freshCode(secret));
  await page.click('button.primary');
  await page.waitForSelector('.shell', { timeout: 15000 });
  await page.goto(BASE + '/#/settings');
  await page.click('.tabs button:has-text("Sécurité")');
  await page.fill('.row input[type=password]', PWD);
  await page.fill('.row input[maxlength="6"]', await freshCode(secret));
  await page.click('button:has-text("Désactiver") >> nth=-1');
  await waitToast('Double authentification désactivée');
  await page.click('header.topbar button[title="Se déconnecter"]');
  await login();
  await page.waitForSelector('.shell', { timeout: 15000 });  // plus de code demandé

  // 2. Trader.
  await page.goto(BASE + '/#/trade');
  await page.waitForSelector('.symbol-tabs button');
  await sleep(800);
  reads.length = 0;
  await page.click('.symbol-tabs button:has-text("ETH")');
  await page.waitForFunction(() => document.querySelector('.symbol-tabs button.on')?.textContent.trim() === 'ETH');
  await sleep(500);
  check(reads.some(r => r.startsWith('/api/candles') && r.includes('symbol=ETH%2FUSDC')), 'bougies ETH attendues : ' + reads);
  reads.length = 0;
  await page.click('.seg button:has-text("4h")');
  await sleep(500);
  check(reads.some(r => r.startsWith('/api/candles') && r.includes('tf=4h')), 'bougies 4h attendues : ' + reads);
  const unit = () => page.locator('.ticket .field:has-text("Montant") .input-suffix span').innerText();
  const modes = page.locator('.ticket .field:has-text("Montant") .seg button');
  await modes.last().click();
  check((await unit()) === 'ETH', 'montant en ETH attendu : ' + await unit());
  await modes.first().click();
  check((await unit()) !== 'ETH', 'montant en devise attendu');
  await page.click('.ticket .side button:has-text("Vendre")');
  check((await page.locator('.ticket button.block').innerText()).trim() === 'Vendre ETH', 'bouton « Vendre ETH » attendu');
  check(!(await page.isVisible('.ticket input[placeholder="recommandé"]')), 'pas de stop-loss pour une vente');
  await page.click('.ticket .side button:has-text("Acheter")');
  check((await page.locator('.ticket button.block').innerText()).trim() === 'Acheter ETH', 'bouton « Acheter ETH » attendu');
  check(await page.isVisible('.ticket input[placeholder="recommandé"]'), 'stop-loss proposé pour un achat');

  // 3. Onglets, périodes et filtres.
  const cycle = async (route, buttons, expectRead) => {
    await page.goto(BASE + '/#/' + route);
    const group = page.locator(buttons);
    await group.first().waitFor();
    await sleep(600);
    const n = await group.count();
    for (let i = n - 1; i >= 0; i--) {
      const b = group.nth(i);
      reads.length = 0;
      const before = await page.locator('main.main').innerText();
      const isSelected = () => b.evaluate(el => el.classList.contains('on') || el.classList.contains('active') || el.getAttribute('aria-pressed') === 'true');
      const already = await isSelected();  // déjà sélectionné : rien à recharger ni à changer
      await b.click();
      await sleep(400);
      check(await isSelected(), `${route} : « ${(await b.innerText()).trim()} » doit être sélectionné`);
      if (already) continue;
      if (expectRead) {
        const want = expectRead(i, (await b.innerText()).trim());
        if (want) check(reads.some(r => r.includes(want)), `${route} : lecture « ${want} » attendue après « ${(await b.innerText()).trim()} » : ${reads}`);
      } else if (i < n - 1) {
        check((await page.locator('main.main').innerText()) !== before, `${route} : « ${(await b.innerText()).trim()} » doit changer l'affichage`);
      }
    }
    return n;
  };
  const counts = {};
  counts.ia = await cycle('ai', '.tabs button');
  const periods = ['today', '7d', '30d', '90d', 'all'];
  counts.performance = await cycle('performance', 'main .seg button', i => 'period=' + periods[i]);
  reads.length = 0;
  await page.click('main table tr:has-text("7 jours")');  // ligne du tableau des périodes
  await sleep(400);
  check(reads.some(r => r.includes('period=7d')) && (await page.locator('main .seg button.on').innerText()).trim() === '7 jours',
    'la ligne « 7 jours » doit choisir la période : ' + reads);
  counts.dashboard = await cycle('dashboard', 'main .seg button', (i, label) => 'days=' + parseInt(label, 10));
  counts.historique = await cycle('history', 'main .seg button', (i, label) => (label === 'IA' ? 'origin=ai' : label === 'Manuels' ? 'origin=manual' : null));
  // Une vente fictive (achat puis fermeture par l'API locale), puis son détail dans l'Historique.
  const account = (await json('/api/overview')).accounts.find(a => a.role === 'main');
  const post = (path, body) => page.evaluate(async ([path, body]) => (await fetch(path, { method: 'POST', headers: { 'X-Sentinelle': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json(), [path, body]);
  const price = (await json('/api/markets')).prices['BTC/USDC'];
  const order = await post('/api/orders', { account_id: account.id, symbol: 'BTC/USDC', side: 'buy', type: 'market', cash_amount: 10, stop_loss: Math.round(price * 0.98) });
  check(order.position?.id, 'achat fictif attendu : ' + JSON.stringify(order));
  check((await post(`/api/positions/${order.position.id}/close`, {})).status === 'closed', 'vente fictive attendue');
  await page.goto(BASE + '/#/dashboard');  // quitter la page, pour que l'Historique relise ses ventes
  await page.goto(BASE + '/#/history');
  const sale = page.locator('main tbody tr', { hasText: 'BTC/USDC' }).first();
  await sale.waitFor();
  await sale.click();
  await page.waitForSelector('main td[colspan="11"]');
  await sale.click();
  await page.waitForSelector('main td[colspan="11"]', { state: 'detached' });
  counts.journal = await cycle('logs', 'main .seg button', i => (i === 0 ? null : 'level='));
  counts.reglages = await cycle('settings', '.tabs button');
  counts.memecoin = await cycle('memecoin', '.mc-tabs button');
  counts.hub = await cycle('dmh', 'main .tabs button');
  check(counts.ia === 6 && counts.performance === 5 && counts.dashboard === 4 && counts.historique === 3 && counts.journal === 5 &&
    counts.reglages === 15 && counts.memecoin === 4 && counts.hub === 6,
    'nombre d\'onglets : ' + JSON.stringify(counts));
  await page.goto(BASE + '/#/memecoin');
  await page.click('.mc-tabs button:has-text("Live")');
  await page.click('button:has-text("1 000 $")');
  check((await page.locator('.mc-controls input[type=number]').first().inputValue()) === '1000', 'capital prédéfini de 1 000 $ attendu');

  // 3 bis. Arrêt d'urgence : « Fermer » et portée « Positions de l'IA ».
  const sentEmergency = [];
  page.on('request', r => { if (r.url().endsWith('/api/emergency')) sentEmergency.push(r.postDataJSON()); });
  await page.click('header .urgence');
  await page.click('.modal button.ghost:has-text("Fermer")');
  await page.waitForSelector('.modal-bg', { state: 'detached' });
  check(sentEmergency.length === 0, '« Fermer » ne doit rien envoyer');
  await page.route('**/api/emergency', route => route.fulfill({ json: { agents_stopped: [], positions_closed: 0, errors: [] } }));
  await page.click('header .urgence');
  await page.click('.modal button:has-text("Toutes les positions")');
  await page.click('.modal button:has-text("Positions de l\'IA")');
  check(await page.locator('.modal button:has-text("Positions de l\'IA")').evaluate(el => el.classList.contains('on')), 'portée IA sélectionnée');
  await page.fill('.modal input[placeholder="URGENCE"]', 'URGENCE');
  await page.click('.modal button:has-text("Tout revendre maintenant")');
  await page.waitForSelector('.modal-bg', { state: 'detached' });
  check(sentEmergency.length === 1 && sentEmergency[0].action === 'flatten' && sentEmergency[0].scope === 'ai' && sentEmergency[0].confirm === 'URGENCE',
    'revente limitée aux positions de l\'IA attendue : ' + JSON.stringify(sentEmergency));
  await page.unroute('**/api/emergency');

  // 4. Signaux : ajouter puis retirer un canal.
  await page.goto(BASE + '/#/signals');
  await page.fill('input[placeholder^="@nom_du_canal"]', '@canal_a_retirer');
  await page.click('button:has-text("Ajouter")');
  await page.waitForSelector('td:has-text("@canal_a_retirer")');
  await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/settings') && r.request().method() === 'PUT' && r.status() === 200),
    page.click('tr:has-text("@canal_a_retirer") button:has-text("Retirer")'),
  ]);
  check(!(await json('/api/settings')).signals.channels.includes('@canal_a_retirer'), 'le canal doit être retiré des réglages');
  await page.waitForSelector('td:has-text("@canal_a_retirer")', { state: 'detached' });

  // 5. Notifications du navigateur.
  await page.goto(BASE + '/#/settings');
  await page.click('.tabs button:has-text("Sécurité")');
  await page.click('button:has-text("Activer les notifications")');
  await waitToast('Notifications activées (quand le panel est en arrière-plan)');
  check((await page.evaluate(() => localStorage.getItem('stn-notif'))) === '1', 'préférence de notification enregistrée attendue');
  await page.click('button:has-text("Désactiver") >> nth=0');
  await page.waitForFunction(() => localStorage.getItem('stn-notif') === '0');

  check(errors.length === 0, 'erreurs JavaScript : ' + errors.join(' ; '));
  console.log('double authentification, Trader, ' + Object.values(counts).reduce((a, b) => a + b) + ' onglets et filtres, détail de l\'Historique, urgence (Fermer, portée IA), canal retiré, notifications : vérifiés');
  await browser.close();
})().catch(e => { console.error(e.stack || e); process.exit(1); });
