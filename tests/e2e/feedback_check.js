// Retours visibles des actions (chantier Interface V1, défauts D-UI1 à D-UI3 de docs/interface/inventaire-actions.md).
// Contre une instance de démonstration JETABLE (python tests/e2e/run.py feedback_check.js) :
// 1. une vraie erreur de validation du serveur (code de double authentification trop court) s'affiche en français, sans
//    le texte brut du validateur, et ne change rien ;
// 2. chaque type d'erreur de validation renvoyé par l'API est traduit (réponse 422 simulée sur un vrai bouton) ;
// 3. Signaux : « Ajouter » et « Enregistrer » sont inactifs tant qu'il n'y a rien à envoyer ; un canal ajouté est
//    réellement enregistré dans les réglages et affiché.
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PWD = 'demo-mot-de-passe-123';
const RAW = /String should|Field required|Input should|Value error/;
const check = (ok, message) => { if (!ok) throw new Error(message); };

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(e.message));
  await page.goto(BASE + '/');
  await page.waitForSelector('form.card');
  for (const input of await page.$$('input[type=password]')) await input.fill(PWD);
  await page.click('button.primary');
  await page.waitForSelector('.shell', { timeout: 15000 });
  const lastToast = async (kind) => (await page.locator('.toasts .toast.' + kind).last().innerText()).trim();

  // 1. Double authentification : code de 2 chiffres, refusé par le serveur (422).
  await page.goto(BASE + '/#/settings');
  await page.click('.tabs button:has-text("Sécurité")');
  await page.click('button:has-text("Activer la double authentification")');
  await page.fill('input[maxlength="6"]', '12');
  const [refused] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/auth/2fa/enable')),
    page.click('button:has-text("Valider")'),
  ]);
  check(refused.status() === 422, 'le serveur doit refuser un code de 2 chiffres (statut ' + refused.status() + ')');
  const shown = await lastToast('error');
  check(!RAW.test(shown), 'message brut du validateur affiché : ' + shown);
  check(/« code » : au moins 6 caractères/.test(shown), 'message attendu en français : ' + shown);
  check(!(await page.isVisible('text=Activée.')), 'la double authentification ne doit pas être activée');

  // 2. Chaque type d'erreur 422 est traduit (réponse simulée sur « Analyser maintenant »).
  await page.goto(BASE + '/#/news');
  await page.waitForSelector('button:has-text("Activer le module Actualités"), button:has-text("Analyser maintenant")');
  const activate = page.locator('button:has-text("Activer le module Actualités")');
  if (await activate.isVisible()) await activate.click();  // a real setting change: the module is enabled
  await page.waitForSelector('button:has-text("Analyser maintenant")');
  await page.route('**/api/news/refresh', route => route.fulfill({ status: 422, json: { detail: [
    { type: 'missing', loc: ['body', 'symbol'], msg: 'Field required' },
    { type: 'greater_than_equal', loc: ['body', 'amount'], msg: 'Input should be greater than or equal to 10', ctx: { ge: 10 } },
    { type: 'int_parsing', loc: ['query', 'limit'], msg: 'Input should be a valid integer, unable to parse string as an integer' },
    { type: 'value_error', loc: ['body', 'currency'], msg: 'Value error, devise non prise en charge' },
  ] } }));
  await page.click('button:has-text("Analyser maintenant")');
  await page.waitForTimeout(500);
  const translated = await lastToast('error');
  check(!RAW.test(translated), 'erreurs 422 non traduites : ' + translated);
  for (const part of ['« symbol » : valeur obligatoire', '« amount » : au moins 10', '« limit » : nombre entier attendu',
    '« currency » : devise non prise en charge']) {
    check(translated.includes(part), 'traduction attendue « ' + part + ' » dans : ' + translated);
  }
  await page.unroute('**/api/news/refresh');

  // 3. Signaux : rien à envoyer → boutons inactifs ; un canal saisi est réellement enregistré.
  await page.goto(BASE + '/#/signals');
  const add = page.locator('button:has-text("Ajouter")');
  const save = page.locator('.btn-row button:has-text("Enregistrer")').first();
  await add.waitFor();
  check(await add.isDisabled(), '« Ajouter » doit être inactif quand le champ du canal est vide');
  check(await save.isDisabled(), '« Enregistrer » doit être inactif quand aucun identifiant n\'est saisi');
  await page.fill('input[placeholder^="@nom_du_canal"]', '   ');
  check(await add.isDisabled(), '« Ajouter » doit rester inactif pour un canal fait d\'espaces');
  await page.fill('input[placeholder^="@nom_du_canal"]', '@canal_de_test');
  check(!(await add.isDisabled()), '« Ajouter » doit s\'activer quand un canal est saisi');
  await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/settings') && r.request().method() === 'PUT' && r.status() === 200),
    add.click(),
  ]);
  const settings = await page.evaluate(async () => (await fetch('/api/settings', { headers: { 'X-Sentinelle': '1' } })).json());
  check((settings.signals?.channels || []).includes('@canal_de_test'), 'le canal doit être enregistré dans les réglages');
  await page.waitForSelector('td:has-text("@canal_de_test")');
  await page.fill('input[placeholder="+33612345678"]', '+33600000000');
  check(!(await save.isDisabled()), '« Enregistrer » doit s\'activer quand un identifiant est saisi');

  check(pageErrors.length === 0, 'erreurs JavaScript : ' + pageErrors.join(' ; '));
  console.log('retours des actions : validation en français, boutons Signaux cohérents, canal enregistré');
  await browser.close();
})().catch(e => { console.error(e.message || e); process.exit(1); });
