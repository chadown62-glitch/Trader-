// Actions critiques du cockpit, de bout en bout (chantier Interface V1, fiabilisation finale).
// Contre une instance de démonstration JETABLE (python tests/e2e/run.py critical_check.js) : marché simulé, base SQLite
// temporaire, argent fictif. Pour chaque action : le clic, la requête envoyée, l'état relu sur le serveur, le message
// affiché et le cas d'erreur.
// 1. Ticket : 25 %, stop −2 %, objectif +4 % ; double clic sur « Acheter » → un seul ordre, position avec stop et objectif.
// 2. Protections : « Modifier » puis « Enregistrer » change le stop ; « Annuler » ne change rien.
// 3. Fermeture : double clic sur « Fermer » → la confirmation reste ouverte (D-UI8) ; « Annuler » ne ferme rien ;
//    double clic sur la confirmation → une seule fermeture.
// 4. Ordre à cours limité, puis « Annuler » : l'ordre et la position en attente disparaissent.
// 5. Ordre refusé par le serveur (erreur simulée) : message affiché, aucune position, bouton de nouveau disponible.
// 6. Arrêt d'urgence : double clic sur « Urgence » → la fenêtre reste ouverte (D-UI8) ; revente refusée tant que URGENCE
//    n'est pas saisi ; toutes les positions fermées ; clic sur le fond → fermeture ; « Arrêter les IA » arrête aussi une IA
//    en préparation (D-UI10).
// 7. Réglages : valeur hors bornes et champ vide refusés avec le libellé et l'unité du champ (D-UI9), rien d'enregistré ;
//    valeur valide enregistrée et conservée après rechargement.
// 8. WebSocket fermé par le serveur : « Flux déconnecté », puis reconnexion automatique.
// 9. Memecoin : démarrage refusé par le serveur (réponse simulée) : message affiché, paramètres envoyés corrects.
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PWD = 'demo-mot-de-passe-123';
const check = (ok, message) => { if (!ok) throw new Error(message); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const near = (a, b, tol) => Math.abs(Number(a) - Number(b)) <= tol;

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const calls = [];
  page.on('request', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/') && r.method() !== 'GET') calls.push(`${r.method()} ${u.pathname}`); });
  const sent = (re) => calls.filter(c => re.test(c)).length;
  const toasts = () => page.$$eval('.toasts .toast', els => els.map(e => e.textContent.trim()));
  const json = p => page.evaluate(async p => (await fetch(p)).json(), p);
  await page.goto(BASE + '/');
  await page.waitForSelector('form.card');
  for (const input of await page.$$('input[type=password]')) await input.fill(PWD);
  await page.click('button.primary');
  await page.waitForSelector('.shell', { timeout: 15000 });
  const account = (await json('/api/overview')).accounts.find(a => a.role === 'main');
  const positions = async () => ((await json(`/api/accounts/${account.id}`)).positions || []);
  const open = async () => (await positions()).filter(p => p.status === 'open');

  // 1. Ticket d'achat.
  const detailRoute = '**/api/accounts/' + account.id;
  let releaseDetail;
  const holdDetail = new Promise(resolve => { releaseDetail = resolve; });
  await page.route(detailRoute, async route => { await holdDetail; await route.continue(); });
  await page.goto(BASE + '/#/trade');
  const ticket = page.locator('.ticket');
  await ticket.waitFor();
  const quarterButton = ticket.getByRole('button', { name: '25 %', exact: true });
  check(await quarterButton.isDisabled(), 'le raccourci de montant attend le solde du compte');
  releaseDetail();
  await ticket.locator('.pill-row button:enabled').filter({ hasText: '25 %' }).waitFor();
  const amount = ticket.locator('.field:has-text("Montant") input').first();
  await ticket.locator('.pill-row button:has-text("25 %")').click();
  const quarter = Number(await amount.inputValue());
  check(quarter > 0.2 * account.cash && quarter <= 0.25 * account.cash, `25 % du disponible (${account.cash}) : ${quarter}`);
  await amount.fill('10');
  const price = (await json('/api/markets')).prices['BTC/USDC'];
  await ticket.locator('button:has-text("−2 %")').click();
  await ticket.locator('button:has-text("+4 %")').click();
  const sl = Number(await ticket.locator('input[placeholder="recommandé"]').inputValue());
  const tp = Number(await ticket.locator('.field:has-text("Objectif de gain") input').inputValue());
  check(near(sl, price * 0.98, price * 0.005) && near(tp, price * 1.04, price * 0.005), `stop ${sl} et objectif ${tp} pour ${price}`);
  const buy = ticket.locator('button.block');
  await page.waitForFunction(() => !document.querySelector('.ticket button.block').disabled && document.querySelector('.ticket .preview'));
  calls.length = 0;
  await buy.dblclick();
  await page.waitForFunction(() => [...document.querySelectorAll('.toasts .toast')].some(t => t.textContent.includes('Ordre d’achat exécuté')));
  await sleep(500);
  check(sent(/POST \/api\/orders$/) === 1, 'double clic sur « Acheter » : ' + sent(/POST \/api\/orders$/) + ' ordre(s)');
  let pos = await open();
  check(pos.length === 1 && pos[0].symbol === 'BTC/USDC', 'une position BTC ouverte attendue : ' + JSON.stringify(pos));
  check(near(pos[0].stop_price, sl, 0.01) && near(pos[0].target_price, tp, 0.01), 'stop et objectif enregistrés : ' + JSON.stringify(pos[0]));

  // 2. Modifier les protections.
  const row = page.locator('tr').filter({ hasText: 'BTC/USDC' }).filter({ has: page.locator('button:has-text("Fermer")') }).first();
  await page.click('button:has-text("Modifier")');
  const stopInput = page.locator('.card.tight input[type=number]').first();
  const newStop = Math.round(pos[0].stop_price * 0.99 * 100) / 100;
  await stopInput.fill(String(newStop));
  calls.length = 0;
  await page.click('.card.tight button:has-text("Enregistrer")');
  await page.waitForFunction(() => !document.querySelector('.card.tight'));
  check(sent(/PATCH \/api\/positions\/\d+$/) === 1, 'modification du stop non envoyée');
  check(near((await open())[0].stop_price, newStop, 0.01), 'nouveau stop non enregistré');
  check((await toasts()).includes('Protections mises à jour'), 'message « Protections mises à jour » attendu');
  await page.click('button:has-text("Modifier")');
  await page.locator('.card.tight input[type=number]').first().fill('1');
  calls.length = 0;
  await page.click('.card.tight button:has-text("Annuler")');
  check(calls.length === 0 && near((await open())[0].stop_price, newStop, 0.01), '« Annuler » ne doit rien envoyer ni changer');

  // 3. Fermeture avec confirmation.
  const close = row.locator('button:has-text("Fermer")');
  calls.length = 0;
  await close.dblclick();
  await sleep(300);
  check(await page.isVisible('.modal-bg .modal'), 'double clic sur « Fermer » : la confirmation doit rester ouverte (D-UI8)');
  await page.click('.modal button:has-text("Annuler")');
  check(calls.length === 0 && (await open()).length === 1, '« Annuler » ne doit pas fermer la position');
  await close.click();
  await page.locator('.modal .btn-row button').last().dblclick();
  await page.waitForFunction(() => [...document.querySelectorAll('.toasts .toast')].some(t => t.textContent === 'Position fermée'));
  await sleep(300);
  check(sent(/POST \/api\/positions\/\d+\/close$/) === 1, 'double clic sur la confirmation : ' + sent(/\/close$/) + ' fermeture(s)');
  check((await open()).length === 0, 'la position doit être fermée');
  const trades = await json(`/api/trades?account_id=${account.id}&limit=10`);
  check((trades.trades || trades).some(t => t.symbol === 'BTC/USDC'), 'la vente doit figurer dans l\'historique');

  // 4. Ordre à cours limité puis annulation.
  await ticket.locator('button:has-text("À cours limité")').click();
  await ticket.locator('.field:has-text("Prix limite") input').fill(String(Math.round(price * 0.5)));
  await amount.fill('10');
  await ticket.locator('button:has-text("−2 %")').click();
  await page.waitForFunction(() => !document.querySelector('.ticket button.block').disabled && document.querySelector('.ticket .preview'));
  await buy.click();
  await page.waitForSelector('.c-orders button:has-text("Annuler")');
  let detail = await json(`/api/accounts/${account.id}`);
  check(detail.orders.length === 1 && detail.orders[0].type === 'limit', 'un ordre limite attendu : ' + JSON.stringify(detail.orders));
  check(detail.positions.some(p => p.status === 'pending'), 'une position en attente attendue');
  calls.length = 0;
  await page.click('.c-orders button:has-text("Annuler")');
  await page.waitForFunction(() => !document.querySelector('.c-orders button'));
  check(sent(/DELETE \/api\/orders\/\d+$/) === 1, 'annulation non envoyée');
  detail = await json(`/api/accounts/${account.id}`);
  check(detail.orders.length === 0 && !detail.positions.length, 'ordre et position en attente doivent disparaître');
  await ticket.locator('button:has-text("Au marché")').click();

  // 5. Ordre refusé par le serveur (erreur simulée).
  await page.route('**/api/orders', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 500, json: { detail: 'Erreur simulée du serveur' } }) : route.continue());
  await amount.fill('10');
  await ticket.locator('button:has-text("−2 %")').click();
  await page.waitForFunction(() => !document.querySelector('.ticket button.block').disabled && document.querySelector('.ticket .preview'));
  await buy.click();
  await page.waitForFunction(() => [...document.querySelectorAll('.toasts .toast.error')].some(t => t.textContent === 'Erreur simulée du serveur'));
  check((await open()).length === 0, 'aucune position après un refus du serveur');
  check(!(await buy.isDisabled()), '« Acheter » doit redevenir disponible après l\'erreur');
  await page.unroute('**/api/orders');

  // 6. Arrêt d'urgence.
  await buy.click();
  await page.waitForFunction(() => [...document.querySelectorAll('.toasts .toast')].some(t => t.textContent.includes('Ordre d’achat exécuté')));
  check((await open()).length === 1, 'une position ouverte avant l\'arrêt d\'urgence');
  await page.locator('header .urgence').dblclick();
  await sleep(300);
  check(await page.isVisible('.modal-bg .modal'), 'double clic sur « Urgence » : la fenêtre doit rester ouverte (D-UI8)');
  await page.click('.modal button:has-text("Toutes les positions")');
  const flatten = page.locator('.modal button:has-text("Tout revendre maintenant")');
  check(await flatten.isDisabled(), '« Tout revendre » doit être inactif sans URGENCE');
  await page.fill('.modal input[placeholder="URGENCE"]', 'URGENCE');
  calls.length = 0;
  await flatten.click();
  await page.waitForSelector('.modal-bg', { state: 'detached' });
  check(sent(/POST \/api\/emergency$/) === 1 && (await open()).length === 0, 'la revente d\'urgence doit fermer la position');
  check((await toasts()).some(t => t.startsWith('Arrêt d\'urgence : 1 position(s) fermée(s)')), 'message de revente attendu : ' + await toasts());
  await page.click('header .urgence');
  await page.mouse.click(10, 890);
  await page.waitForSelector('.modal-bg', { state: 'detached' });
  const started = await page.evaluate(async () => (await fetch('/api/agents/ia_entrainement/start', { method: 'POST', headers: { 'X-Sentinelle': '1', 'Content-Type': 'application/json' }, body: '{}' })).json());
  check(['préparation', 'en marche'].includes(started.status), 'IA d\'entraînement non démarrée : ' + JSON.stringify(started));
  await page.click('header .urgence');
  await page.click('.modal button:has-text("Arrêter les IA")');
  await page.waitForFunction(() => [...document.querySelectorAll('.toasts .toast')].some(t => t.textContent.startsWith('IA arrêtées (1)')));
  await sleep(3000);  // une préparation éventuelle se termine : l'IA ne doit pas démarrer ensuite
  const agent = (await json('/api/overview')).agents.find(a => a.name === 'ia_entrainement');
  check(agent.status === 'arrêté' && !agent.running, 'l\'IA doit rester arrêtée après l\'arrêt d\'urgence : ' + JSON.stringify(agent));

  // 7. Réglages numériques.
  await page.goto(BASE + '/#/settings');
  await page.click('.tabs button:has-text("Risque")');
  const risk = page.locator('.field:has-text("Risque par trade") input').first();
  const saveRisk = page.locator('button.primary:has-text("Enregistrer")').first();
  const before = (await json('/api/settings')).risk;
  const saveSettings = async (button) => {
    const [response] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/settings') && r.request().method() === 'PUT'), button.click()]);
    await sleep(200);
    return { status: response.status(), shown: (await page.locator('.toasts .toast').last().innerText()).trim() };
  };
  await risk.fill('9');
  let saved = await saveSettings(saveRisk);
  let shown = saved.shown;
  check(saved.status === 400, 'valeur hors bornes : statut ' + saved.status);
  check(shown === '« Risque par trade » doit être compris entre 0,1 % et 2 %', 'message hors bornes (D-UI9) : ' + shown);
  check((await json('/api/settings')).risk.risk_per_trade === before.risk_per_trade, 'une valeur hors bornes ne doit pas être enregistrée');
  await risk.fill('0.8');
  saved = await saveSettings(saveRisk);
  check(saved.status === 200 && saved.shown === 'Réglages enregistrés', 'enregistrement attendu : ' + JSON.stringify(saved));
  check((await json('/api/settings')).risk.risk_per_trade === 0.008, 'valeur valide non enregistrée');
  await page.reload();
  await page.click('.tabs button:has-text("Risque")');
  check(await page.locator('.field:has-text("Risque par trade") input').first().inputValue() === '0.8', 'valeur non conservée après rechargement');
  await page.locator('.field:has-text("Positions simultanées maximum") input').first().fill('');
  shown = (await saveSettings(page.locator('button.primary:has-text("Enregistrer")').first())).shown;
  check(shown === '« Positions simultanées maximum » doit être un nombre', 'message de champ vide (D-UI9) : ' + shown);
  check((await json('/api/settings')).risk.max_positions === before.max_positions, 'un champ vide ne doit rien enregistrer');

  // 8. WebSocket fermé par le serveur, puis reconnexion.
  let sockets = 0, live = null;
  await page.routeWebSocket('**/api/ws', ws => { sockets++; live = ws; ws.connectToServer(); });
  await page.reload();
  await page.waitForSelector('.hud-live:has-text("Flux connecté")', { timeout: 15000 });
  await live.close({ code: 1011, reason: 'coupure simulée' });
  await page.waitForSelector('.hud-live:has-text("Flux déconnecté")', { timeout: 5000 });
  await page.waitForSelector('.hud-live:has-text("Flux connecté")', { timeout: 20000 });
  check(sockets === 2, 'une reconnexion attendue : ' + sockets + ' connexion(s)');

  await page.route('**/api/overview', async route => {
    const response = await route.fetch();
    const snapshot = await response.json();
    for (const symbol of snapshot.market.watch) snapshot.market.fresh[symbol] = false;
    await route.fulfill({ response, json: snapshot });
  });
  await page.getByText('Cours non confirmés', { exact: true }).waitFor({ timeout: 15000 });
  check((await page.locator('.hud-live').innerText()).includes('Flux connecté'), 'transport connecté malgré des cotations non confirmées');
  await page.unroute('**/api/overview');
  await page.getByText('Cours frais', { exact: true }).waitFor({ timeout: 15000 });

  // A delayed decision retains the engine time in both views. Legacy events may have no timestamp.
  await page.goto(BASE + '/#/ai');
  await page.getByRole('button', { name: 'Journal des décisions' }).click();
  const decision = { agent: 'audit', symbol: 'AUDIT/EUR', kind: 'refus', message: 'Événement de test retardé', ts: 946684800 };
  const expected = await page.evaluate(async ts => (await import('/js/store.js')).dt(ts), decision.ts);
  live.send(JSON.stringify({ topic: 'decision', data: decision }));
  const event = page.locator('.feed-item').filter({ hasText: decision.message });
  await event.waitFor();
  check((await event.innerText()).includes(expected), 'la décision doit conserver la date de son journal');
  await page.goto(BASE + '/#/dashboard');
  await event.waitFor();
  check((await event.innerText()).includes(expected), 'le tableau de bord doit afficher la même date');
  const legacy = { agent: 'audit', kind: 'refus', message: 'Décision sans horodatage' };
  live.send(JSON.stringify({ topic: 'decision', data: legacy }));
  const undated = page.locator('.feed-item').filter({ hasText: legacy.message });
  await undated.waitFor();
  check((await undated.locator('.meta').innerText()).includes('—'), 'la date absente reste inconnue');

  // 9. Memecoin : démarrage refusé par le serveur (réponse simulée : aucune source publique interrogée).
  let startBody = null;
  await page.route('**/api/dmh/memecoin/worker/start', route => {
    startBody = route.request().postDataJSON();
    route.fulfill({ status: 503, json: { detail: 'Sources publiques indisponibles (réponse simulée)' } });
  });
  await page.goto(BASE + '/#/memecoin');
  const start = page.locator('button[type=submit]:has-text("Démarrer")');
  await start.waitFor();
  await page.click('button:has-text("100 $")');
  await start.click();
  await page.waitForSelector('.mc [role="alert"]:has-text("Sources publiques indisponibles")');
  check(JSON.stringify(startBody) === JSON.stringify({ mode: 'paper', initial_capital: 100, currency: 'USD', interval_seconds: 15 }),
    'paramètres de démarrage : ' + JSON.stringify(startBody));
  check(!(await start.isDisabled()), '« Démarrer » doit redevenir disponible après le refus');

  check(errors.length === 0, 'erreurs JavaScript : ' + errors.join(' ; '));
  console.log('ticket, protections, fermeture, ordre limite, refus serveur, urgence, réglages, WebSocket, Memecoin : vérifiés');
  await browser.close();
})().catch(e => { console.error(e.message || e); process.exit(1); });
