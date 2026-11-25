// Robustesse du cockpit (chantier Interface V1). Ce parcours doit arrêter puis relancer le serveur : il démarre donc
// lui-même sa propre instance de démonstration JETABLE (port libre, dossier de données temporaire supprimé à la fin) :
//     node tests/e2e/robustness_check.js          (PYTHON = interpréteur Python du dépôt, « python » par défaut)
// 1. double clic : une commande n'est envoyée qu'une fois, un seul message, une seule tâche côté serveur ;
// 2. réglage enregistré : conservé après rechargement de la page puis après redémarrage du serveur ;
// 3. session expirée côté serveur : l'écran de connexion l'explique, sans pile de messages d'erreur, et la reconnexion
//    ramène sur la même page ; une déconnexion volontaire ne l'annonce pas comme une expiration ;
// 4. serveur arrêté : « Flux déconnecté » et un bandeau « Serveur injoignable » (pas un état vide trompeur) ; serveur relancé :
//    bandeau retiré, temps réel rétabli, session conservée, page rechargée avec ses données ;
// 5. le badge « Simulation » est affiché sur chacune des 15 pages.
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.PYTHON || 'python';
const PWD = 'demo-mot-de-passe-123';
const PAGES = ['dashboard', 'performance', 'trade', 'ai', 'history', 'knowledge', 'apprentissage', 'macro', 'preparation',
  'memecoin', 'dmh', 'news', 'signals', 'settings', 'logs'];
const DATA = fs.mkdtempSync(path.join(os.tmpdir(), 'sentinelle-e2e-robustesse-'));
const check = (ok, message) => { if (!ok) throw new Error(message); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });

let server = null;
function start(port) {
  const env = { ...process.env, PYTHONIOENCODING: 'utf-8' };
  delete env.SENTINELLE_DATA;
  const log = fs.openSync(path.join(DATA, 'panel.log'), 'a');
  server = spawn(PY, ['-m', 'sentinelle', '--demo', '--no-browser', '--port', String(port), '--data', path.join(DATA, 'data')],
    { cwd: ROOT, env, stdio: ['ignore', log, log] });
}
const running = () => server !== null && server.exitCode === null && server.signalCode === null;  // arrêté par signal : exitCode reste null
const stop = () => new Promise(res => { if (!running()) return res(); server.once('exit', res); server.kill('SIGTERM'); });
process.on('exit', () => { if (running()) server.kill('SIGKILL'); });
async function healthy(port, ms = 90000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await fetch(`http://127.0.0.1:${port}/api/health`)).ok) return true; } catch { /* pas encore */ }
    await sleep(300);
  }
  return false;
}
// Expiration de toutes les sessions directement dans la base de l'instance jetable (comme après 30 jours d'inactivité).
const expireSessions = () => execFileSync(PY, ['-c', 'import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute("UPDATE sessions SET expires_at = 0");c.commit()',
  path.join(DATA, 'data', 'sentinelle.db')]);

(async () => {
  const port = await freePort();
  const BASE = `http://127.0.0.1:${port}`;
  start(port);
  check(await healthy(port), "l'instance de démonstration n'a pas démarré : " + fs.readFileSync(path.join(DATA, 'panel.log'), 'utf8').slice(-1500));
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const commands = [];
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') commands.push(r.method() + ' ' + new URL(r.url()).pathname); });
  const toasts = () => page.$$eval('.toasts .toast', els => els.map(e => e.textContent.trim()));
  const json = p => page.evaluate(async p => (await fetch(p)).json(), p);
  const login = async () => {
    await page.waitForSelector('form.card');
    for (const input of await page.$$('input[type=password]')) await input.fill(PWD);  // 2 champs à la première connexion
    await page.click('button.primary');
    await page.waitForSelector('.shell', { timeout: 15000 });
  };
  await page.goto(BASE + '/');
  await login();
  await page.waitForSelector('.hud-live:has-text("Flux connecté")', { timeout: 15000 });

  // 1. Double clic.
  await page.goto(BASE + '/#/dashboard');
  const startButton = page.locator('button:has-text("Démarrer")').first();
  await startButton.waitFor();
  commands.length = 0;
  await startButton.dblclick();
  await sleep(2000);
  const starts = commands.filter(c => /\/api\/agents\/[^/]+\/start$/.test(c));
  check(starts.length === 1, 'double clic sur « Démarrer » : ' + starts.length + ' commande(s) envoyée(s)');
  const started = (await toasts()).filter(t => t.startsWith('Préparation de l’IA'));
  check(started.length === 1, 'double clic sur « Démarrer » : ' + started.length + ' message(s)');
  const prepare = (await json('/api/jobs')).filter(j => j.kind === 'prepare');
  check(prepare.length === 1, 'double clic sur « Démarrer » : ' + prepare.length + ' préparation(s) côté serveur');
  await page.goto(BASE + '/#/signals');
  const evaluate = page.locator('button:has-text("Évaluer les signaux")');
  await evaluate.waitFor();
  commands.length = 0;
  await evaluate.dblclick();
  await sleep(1500);
  check(commands.filter(c => c.endsWith('/api/signals/resolve')).length === 1, 'double clic sur « Évaluer les signaux » : ' + commands);
  check((await toasts()).filter(t => /signal\(aux\) évalué/.test(t)).length === 1, 'un seul message attendu : ' + await toasts());

  // 2. Réglage enregistré, puis rechargement.
  await page.goto(BASE + '/#/settings');
  await page.waitForSelector('.chips .chip');
  const symbols = () => page.$$eval('.chips .chip', els => els.map(e => e.firstChild.textContent.trim()));
  const before = await symbols();
  const removed = before[before.length - 1];
  await page.click('.chips .chip >> nth=-1 >> button');
  await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/settings') && r.request().method() === 'PUT' && r.status() === 200),
    page.click('.card button.primary:has-text("Enregistrer") >> nth=0'),
  ]);
  await page.reload();
  await page.waitForSelector('.chips .chip');
  const reloaded = await symbols();
  check(!reloaded.includes(removed) && reloaded.length === before.length - 1, `après rechargement : ${reloaded} (retiré : ${removed})`);

  // 3. Session expirée côté serveur, puis reconnexion.
  await page.goto(BASE + '/#/history');
  await page.waitForSelector('main.main > *');
  const shown = (await toasts()).length;
  expireSessions();
  await page.click('.nav a[href="#/logs"]');
  await page.waitForSelector('form.card', { timeout: 15000 });
  check(/Votre session a expiré ou a été fermée/.test(await page.innerText('form.card')), "l'écran de connexion doit expliquer l'expiration");
  await sleep(500);
  check(!(await toasts()).slice(shown).some(t => t === 'Connexion requise'), 'messages « Connexion requise » empilés : ' + await toasts());
  await login();
  check((await page.evaluate(() => location.hash)) === '#/logs', 'la reconnexion doit ramener sur la page demandée');
  await page.click('header.topbar button[title="Se déconnecter"]');
  await page.waitForSelector('form.card');
  check(!/session a expiré/.test(await page.innerText('form.card')), 'une déconnexion volontaire n\'est pas une expiration');
  await login();

  // 4. Serveur arrêté puis relancé.
  await page.goto(BASE + '/#/performance');
  await page.waitForSelector('main.main > *');
  await sleep(1500);
  check(!(await page.isVisible('.card.empty:has-text("Aucune donnée.")')), 'Performances doit afficher ses données quand le serveur répond');
  await stop();
  await page.waitForSelector('.hud-live:has-text("Flux déconnecté")', { timeout: 15000 });
  await page.click('.nav a[href="#/dashboard"]');
  await page.click('.nav a[href="#/performance"]');
  await page.waitForSelector('main.main [role="alert"]:has-text("Serveur injoignable")', { timeout: 15000 });
  check((await toasts()).filter(t => t.startsWith('Serveur injoignable')).length <= 1, 'messages « Serveur injoignable » répétés : ' + await toasts());
  start(port);
  check(await healthy(port), "l'instance n'a pas redémarré");
  await page.waitForSelector('main.main [role="alert"]', { state: 'detached', timeout: 20000 });
  await page.waitForSelector('.hud-live:has-text("Flux connecté")', { timeout: 30000 });
  check(!(await page.isVisible('form.card')), 'la session doit survivre au redémarrage du serveur');
  await sleep(1500);
  check(!(await page.isVisible('.card.empty:has-text("Aucune donnée.")')), 'la page doit être rechargée au retour de la connexion');
  check(!(await json('/api/settings')).general.watchlist.includes(removed), 'le réglage doit survivre au redémarrage');

  // 5. Données fictives présentées comme telles.
  for (const id of PAGES) {
    await page.goto(BASE + '/#/' + id);
    await page.waitForSelector('main.main > *');
    check((await page.innerText('header.topbar .badge.paper')).trim() === 'Simulation', 'badge « Simulation » absent : ' + id);
  }

  check(errors.length === 0, 'erreurs JavaScript : ' + errors.join(' ; '));
  console.log('double clic, réglage conservé, session expirée expliquée, coupure signalée puis rétablie, simulation affichée');
  await browser.close();
  await stop();
  fs.rmSync(DATA, { recursive: true, force: true });
})().catch(async e => {
  console.error(e.message || e);
  await stop();
  fs.rmSync(DATA, { recursive: true, force: true });
  process.exit(1);
});
