// Réglages : chaque valeur est expliquée et bornée par le serveur (plafonds de sécurité)
import { api, safe } from '../api.js';
import { store, toast, confirmBox, money, dt } from '../store.js';

const { ref, reactive, computed, onMounted, watch } = Vue;

const SECTIONS = {
  risk: { title: 'Gestion du risque', fields: [
    { key: 'risk_per_trade', label: 'Risque par trade', kind: 'pct', step: 0.1, help: 'Perte maximale si le stop-loss est touché. Conseillé : 0,5 %. Plafond absolu : 2 %.' },
    { key: 'max_positions', label: 'Positions simultanées maximum', kind: 'int', help: 'Nombre de cryptos détenues en même temps par l’IA (1 à 5).' },
    { key: 'max_exposure', label: 'Exposition totale maximum', kind: 'pct', step: 5, help: 'Part maximale du capital investie en même temps.' },
    { key: 'max_exposure_per_asset', label: 'Exposition maximum par crypto', kind: 'pct', step: 5, help: 'Part maximale du capital sur une seule crypto.' },
    { key: 'daily_loss_limit', label: 'Perte maximale par jour', kind: 'pct', step: 0.5, help: 'Au-delà, plus aucune nouvelle position jusqu’au lendemain.' },
    { key: 'weekly_loss_limit', label: 'Perte maximale sur 7 jours', kind: 'pct', step: 0.5, help: 'Au-delà, pause des nouvelles positions.' },
    { key: 'max_drawdown', label: 'Baisse maximale depuis le plus haut', kind: 'pct', step: 1, help: 'Au-delà : ARRÊT COMPLET, reprise manuelle obligatoire. Plafond : 25 %.' },
    { key: 'max_consecutive_losses', label: 'Pertes d’affilée avant pause', kind: 'int' },
    { key: 'pause_hours_after_losses', label: 'Durée de la pause (heures)', kind: 'int' },
    { key: 'min_reward_to_fees', label: 'Gain visé minimum (× les frais)', kind: 'num', step: 0.5, help: 'Évite les trades où les frais mangeraient le gain.' },
    { key: 'cooldown_minutes_after_loss', label: 'Délai après une perte sur une crypto (minutes)', kind: 'int', help: 'Pas de rachat « pour se refaire ».' },
  ] },
  ai: { title: 'Intelligence artificielle', fields: [
    { key: 'timeframe', label: 'Unité de temps de l’IA', kind: 'select', options: [['15m', '15 minutes'], ['1h', '1 heure'], ['4h', '4 heures']], help: 'Plus l’unité est longue, moins les frais pèsent. Changer d’unité demande un nouvel entraînement.' },
    { key: 'exploration', label: 'Exploration en entraînement', kind: 'bool', help: 'Sans modèle validé, l’IA d’entraînement prend les signaux de base (taille réduite) pour apprendre. Jamais en réel.' },
    { key: 'exploration_risk_factor', label: 'Taille en exploration', kind: 'pct', step: 10, help: 'Fraction du risque normal utilisée en exploration.' },
    { key: 'learning', label: 'Apprentissage continu', kind: 'bool', help: 'Ré-entraîne régulièrement l’IA avec les données récentes (le nouveau modèle n’est activé que s’il est validé).' },
    { key: 'retrain_hours', label: 'Fréquence de ré-entraînement (heures)', kind: 'int' },
    { key: 'min_new_samples', label: 'Nouvelles situations minimum avant ré-entraînement', kind: 'int' },
    { key: 'regime_filter', label: 'Rester à l’abri en marché baissier', kind: 'bool', help: 'Aucun achat quand le BTC est sous ses moyennes de fond (recommandé en spot).' },
    { key: 'use_news', label: 'Veto des actualités', kind: 'bool', help: 'Bloque les achats si une actualité très négative et confirmée concerne la crypto.' },
  ] },
  paper: { title: 'Simulation (argent fictif)', fields: [
    { key: 'fee_rate', label: 'Frais par ordre', kind: 'pct', step: 0.01, help: 'Hypothèse prudente : 0,1 %.' },
    { key: 'slippage_bps', label: 'Glissement de prix (points de base)', kind: 'num', step: 1, help: '5 = 0,05 % de prix moins favorable à chaque exécution.' },
    { key: 'initial_balance', label: 'Capital fictif de départ', kind: 'num', step: 100 },
  ] },
  live: { title: 'Portefeuille réel', fields: [
    { key: 'exchange', label: 'Exchange', kind: 'select', options: [['binance', 'Binance'], ['kraken', 'Kraken'], ['bybit', 'Bybit'], ['okx', 'OKX'], ['coinbase', 'Coinbase']], help: 'Choisissez une plateforme agréée MiCA (vérifiez sur le registre de l’ESMA / la liste de l’AMF).' },
    { key: 'testnet', label: 'Testnet (argent fictif sur la vraie plateforme)', kind: 'bool', help: 'À utiliser en premier. Les clés testnet se créent sur le site de test de l’exchange.' },
    { key: 'budget', label: 'Budget maximal confié à l’IA', kind: 'num', step: 10, help: 'L’IA n’utilise jamais plus que ce montant, même si le compte contient plus.' },
    { key: 'manual_max_order_value', label: 'Plafond d’un ordre manuel réel', kind: 'num', step: 10, help: 'Protection contre les erreurs de saisie.' },
    { key: 'unlock_min_days', label: 'Déverrouillage : jours d’entraînement minimum', kind: 'int' },
    { key: 'unlock_min_trades', label: 'Déverrouillage : trades minimum', kind: 'int' },
    { key: 'unlock_max_drawdown', label: 'Déverrouillage : baisse maximale tolérée', kind: 'pct', step: 1 },
  ] },
  telegram: { title: 'Telegram', fields: [
    { key: 'enabled', label: 'Activer Telegram', kind: 'bool' },
    { key: 'chat_id', label: 'Votre identifiant Telegram', kind: 'text', help: 'Écrivez /start à votre bot : il vous répond avec votre identifiant.' },
    { key: 'notify_level', label: 'Notifications', kind: 'select', options: [['all', 'Tout (y compris l’entraînement)'], ['important', 'Important seulement'], ['critical', 'Critique seulement']] },
    { key: 'daily_summary_hour', label: 'Heure du résumé quotidien', kind: 'int' },
  ] },
  news: { title: 'Actualités (IA locale)', fields: [
    { key: 'enabled', label: 'Activer les actualités', kind: 'bool' },
    { key: 'ollama_url', label: 'Adresse d’Ollama', kind: 'text' },
    { key: 'model', label: 'Modèle Ollama', kind: 'text', help: 'Ex. qwen2.5:7b (tient dans 8 Go de mémoire vidéo).' },
    { key: 'poll_minutes', label: 'Fréquence de lecture (minutes)', kind: 'int' },
    { key: 'max_age_hours', label: 'Ignorer les actualités plus vieilles que (heures)', kind: 'int' },
    { key: 'veto_threshold', label: 'Seuil de veto (−1 à 0)', kind: 'num', step: 0.05 },
  ] },
};

export default {
  setup() {
    const tab = ref('general');
    const form = reactive({});
    const secrets = ref(null);
    const secretInputs = reactive({ exchange_api_key: '', exchange_secret: '', exchange_password: '', telegram_token: '' });
    const addPair = ref('');
    const feedsText = ref('');
    const newAcc = reactive({ name: 'Mon deuxième compte', initial_balance: 1000 });
    const pwd = reactive({ current: '', new: '', new2: '' });
    const newsStatus = ref(null);
    const accounts = computed(() => store.overview?.accounts || []);
    const live = computed(() => store.overview?.live || {});

    const load = () => {
      if (!store.settings) return;
      Object.assign(form, JSON.parse(JSON.stringify(store.settings)));
      feedsText.value = (form.news?.feeds || []).join('\n');
    };
    watch(() => store.settings, load, { immediate: true });
    const loadSecrets = async () => { secrets.value = await safe(api.get('/api/secrets')); };
    onMounted(async () => { loadSecrets(); newsStatus.value = await safe(api.get('/api/news/status')); });

    const display = (section, f) => {
      const v = form[section]?.[f.key];
      return f.kind === 'pct' && v !== null && v !== undefined ? Math.round(v * 10000) / 100 : v;
    };
    const setValue = (section, f, raw) => {
      let v = raw;
      if (f.kind === 'pct') v = raw === '' ? null : Number(raw) / 100;
      else if (f.kind === 'num') v = raw === '' ? null : Number(raw);
      else if (f.kind === 'int') v = parseInt(raw, 10);
      form[section][f.key] = v;
    };
    const save = async (section) => {
      const patch = { [section]: JSON.parse(JSON.stringify(form[section])) };
      if (section === 'news') patch.news.feeds = feedsText.value.split('\n').map(s => s.trim()).filter(Boolean);
      const r = await safe(api.put('/api/settings', patch), 'Réglages enregistrés');
      if (r) { store.settings = r; }
    };
    const saveGeneral = async () => {
      const r = await safe(api.put('/api/settings', { general: { watchlist: form.general.watchlist }, ai: { strategies: form.ai.strategies } }), 'Réglages enregistrés');
      if (r) { store.settings = r; store.markets = await api.get('/api/markets'); }
    };
    const addSymbol = () => {
      const s = addPair.value.trim().toUpperCase();
      if (s && !form.general.watchlist.includes(s)) form.general.watchlist.push(s);
      addPair.value = '';
    };
    const removeSymbol = (s) => { form.general.watchlist = form.general.watchlist.filter(x => x !== s); };
    const saveSecrets = async (keys) => {
      const values = {};
      keys.forEach(k => { if (secretInputs[k]) values[k] = secretInputs[k]; });
      if (!Object.keys(values).length) { toast('Rien à enregistrer', 'warn'); return; }
      const r = await safe(api.put('/api/secrets', { values }), 'Clés enregistrées sur votre ordinateur');
      if (r) { secrets.value = r; keys.forEach(k => { secretInputs[k] = ''; }); }
    };
    const clearSecret = async (k) => {
      const ok = await confirmBox({ title: 'Supprimer ce secret', message: 'Il sera effacé de votre ordinateur.', confirmLabel: 'Supprimer', danger: true });
      if (ok) secrets.value = await safe(api.put('/api/secrets', { values: { [k]: null } }), 'Supprimé');
    };
    const connect = async () => { const r = await safe(api.post('/api/live/connect')); if (r) toast(r.connected ? 'Connecté à l’exchange' : 'Connexion impossible : ' + r.error, r.connected ? 'success' : 'error', 8000); };
    const testTelegram = async () => { await safe(api.post('/api/telegram/test'), 'Message de test envoyé'); };
    const createAccount = async () => { if (await safe(api.post('/api/accounts', newAcc), 'Compte papier créé')) store.overview = await api.get('/api/overview'); };
    const resetAccount = async (a) => {
      const ok = await confirmBox({ title: `Réinitialiser « ${a.name} »`, message: 'Positions, ordres et courbe de capital seront effacés. L’historique des trades est conservé.', confirmLabel: 'Réinitialiser', danger: true });
      if (ok) await safe(api.post(`/api/accounts/${a.id}/reset`, {}), 'Compte réinitialisé');
    };
    const resumeAccount = async (a) => { await safe(api.post(`/api/accounts/${a.id}/resume`), 'Reprise autorisée'); };
    const changePassword = async () => {
      if (pwd.new !== pwd.new2) { toast('Les deux nouveaux mots de passe ne correspondent pas', 'error'); return; }
      if (await safe(api.post('/api/auth/password', { current: pwd.current, new: pwd.new }), 'Mot de passe modifié')) { pwd.current = pwd.new = pwd.new2 = ''; }
    };
    const TABS = [['general', 'Général'], ['risk', 'Risque'], ['ai', 'IA'], ['paper', 'Simulation'], ['live', 'Exchange réel'], ['telegram', 'Telegram'], ['news', 'Actualités'], ['accounts', 'Comptes'], ['security', 'Sécurité'], ['about', 'À propos']];
    const STRATS = { trend_breakout: 'Cassure de tendance', pullback: 'Repli en tendance', mean_reversion: 'Retour à la moyenne', momentum: 'Élan haussier' };
    return { store, tab, form, secrets, secretInputs, addPair, feedsText, newAcc, pwd, accounts, live, newsStatus, SECTIONS, TABS, STRATS,
      display, setValue, save, saveGeneral, addSymbol, removeSymbol, saveSecrets, clearSecret, connect, testTelegram, createAccount,
      resetAccount, resumeAccount, changePassword, money, dt };
  },
  template: `
  <div v-if="form.general">
    <div class="page-head"><h1>Réglages</h1></div>
    <div class="tabs"><button v-for="t in TABS" :key="t[0]" :class="{on: tab===t[0]}" @click="tab=t[0]">{{ t[1] }}</button></div>

    <div v-if="tab==='general'" class="grid cols-2">
      <div class="card">
        <h2>Cryptos suivies</h2>
        <p class="muted small">Cotées en {{ form.general.quote }}. Restez sur des cryptos très liquides (moins de manipulation, moins de glissement).</p>
        <div class="chips" style="margin-bottom:10px"><span v-for="s in form.general.watchlist" :key="s" class="chip">{{ s }}<button @click="removeSymbol(s)" title="Retirer">✕</button></span></div>
        <div class="row"><input v-model="addPair" list="pairs" placeholder="ex. XRP/USDC"><button @click="addSymbol" style="flex:0">Ajouter</button></div>
        <datalist id="pairs"><option v-for="p in store.markets.available" :key="p" :value="p"></option></datalist>
        <h3 style="margin-top:16px">Stratégies de l'IA</h3>
        <label v-for="(label, k) in STRATS" :key="k" class="check"><input type="checkbox" v-model="form.ai.strategies[k]"> {{ label }}</label>
        <button class="primary" style="margin-top:10px" @click="saveGeneral">Enregistrer</button>
      </div>
      <div class="card">
        <h2>Données de marché</h2>
        <dl class="kv"><dt>Source</dt><dd>{{ form.market.source === 'synthetic' ? 'Marché simulé (démo hors-ligne)' : form.market.source + (form.market.binance_public_mirror ? ' (données publiques, sans compte)' : '') }}</dd>
          <dt>Relevé des prix</dt><dd>toutes les {{ form.market.poll_seconds }} s</dd><dt>Historique</dt><dd>{{ form.market.history_days }} jours</dd></dl>
        <p class="faint small">Le mode démo se lance avec <code>lancer.bat --demo</code>.</p>
      </div>
    </div>

    <div v-for="(sec, key) in SECTIONS" :key="key">
      <div v-if="tab===key" class="grid cols-2">
        <div class="card">
          <h2>{{ sec.title }}</h2>
          <div v-for="f in sec.fields" :key="f.key" class="field">
            <label v-if="f.kind==='bool'" class="check"><input type="checkbox" v-model="form[key][f.key]"> {{ f.label }}</label>
            <template v-else>
              <label>{{ f.label }}</label>
              <select v-if="f.kind==='select'" v-model="form[key][f.key]"><option v-for="o in f.options" :key="o[0]" :value="o[0]">{{ o[1] }}</option></select>
              <div v-else-if="f.kind==='pct'" class="input-suffix"><input type="number" :step="f.step || 0.1" :value="display(key, f)" @input="setValue(key, f, $event.target.value)"><span>%</span></div>
              <input v-else-if="f.kind==='text'" type="text" v-model="form[key][f.key]">
              <input v-else type="number" :step="f.step || 1" :value="display(key, f)" @input="setValue(key, f, $event.target.value)">
            </template>
            <div v-if="f.help" class="hint">{{ f.help }}</div>
          </div>
          <div v-if="key==='news'" class="field"><label>Flux RSS (un par ligne)</label><textarea rows="6" v-model="feedsText"></textarea></div>
          <button class="primary" @click="save(key)">Enregistrer</button>
        </div>
        <div v-if="key==='live'" class="card">
          <h2>Clés API de l'exchange</h2>
          <div class="note warn small" style="margin-bottom:12px">Créez des clés avec les droits <b>lecture + trading</b> uniquement, <b>retrait désactivé</b>, et si possible limitées à l'adresse IP de votre box. Ne les communiquez à personne.</div>
          <div v-for="k in ['exchange_api_key','exchange_secret','exchange_password']" :key="k" class="field">
            <label>{{ secrets?.[k]?.label }} <span v-if="secrets?.[k]?.set" class="badge ok">définie {{ secrets[k].hint || '' }}</span></label>
            <div class="row"><input type="password" v-model="secretInputs[k]" autocomplete="off" :placeholder="secrets?.[k]?.set ? 'laisser vide pour conserver' : ''">
              <button v-if="secrets?.[k]?.set" class="small ghost" style="flex:0" @click="clearSecret(k)">Effacer</button></div>
          </div>
          <div class="btn-row"><button class="primary" @click="saveSecrets(['exchange_api_key','exchange_secret','exchange_password'])">Enregistrer les clés</button><button @click="connect">Se connecter / tester</button></div>
          <div class="note small" :class="live.connected ? 'ok' : live.error ? 'danger' : ''" style="margin-top:12px">
            {{ live.connected ? 'Connecté à ' + live.exchange + (live.testnet ? ' (testnet)' : ' (RÉEL)') : live.error ? 'Non connecté : ' + live.error : 'Non connecté.' }}</div>
        </div>
        <div v-if="key==='telegram'" class="card">
          <h2>Connexion du bot</h2>
          <ol class="small muted" style="padding-left:18px">
            <li>Dans Telegram, ouvrez <b>@BotFather</b>, envoyez <code>/newbot</code> et suivez les étapes.</li>
            <li>Copiez le jeton reçu ci-dessous et enregistrez.</li>
            <li>Activez Telegram, puis écrivez <code>/start</code> à votre bot : il vous donne votre identifiant.</li>
            <li>Collez cet identifiant à gauche et enregistrez. Le bot ne répondra qu'à vous.</li>
          </ol>
          <div class="field"><label>Jeton du bot <span v-if="secrets?.telegram_token?.set" class="badge ok">défini {{ secrets.telegram_token.hint }}</span></label>
            <input type="password" v-model="secretInputs.telegram_token" autocomplete="off"></div>
          <div class="btn-row"><button class="primary" @click="saveSecrets(['telegram_token'])">Enregistrer le jeton</button><button @click="testTelegram">Envoyer un message de test</button></div>
          <p class="small muted" style="margin-top:10px">État : {{ store.overview?.extensions?.telegram?.connected ? 'connecté (@' + store.overview.extensions.telegram.bot + ')' : (store.overview?.extensions?.telegram?.error || 'non connecté') }}</p>
        </div>
        <div v-if="key==='news'" class="card">
          <h2>IA locale (Ollama)</h2>
          <p class="small muted">Gratuit, tourne sur votre carte graphique : rien ne sort de votre PC. Installation : <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a>, puis <code>ollama pull {{ form.news.model }}</code>.</p>
          <p class="small">Ollama : <b :class="newsStatus?.ollama_ok ? 'up' : 'down'">{{ newsStatus?.ollama_ok ? 'connecté' : 'injoignable' }}</b></p>
          <div v-if="newsStatus?.models_installed?.length" class="small">Modèles installés : <span v-for="m in newsStatus.models_installed" :key="m" class="badge" style="margin:2px">{{ m }}</span></div>
        </div>
      </div>
    </div>

    <div v-if="tab==='accounts'" class="grid cols-2">
      <div class="card">
        <h2>Comptes</h2>
        <div class="table-wrap"><table><thead><tr><th>Nom</th><th>Type</th><th class="num">Capital</th><th></th></tr></thead>
          <tbody><tr v-for="a in accounts" :key="a.id"><td>{{ a.name }}</td><td><span class="badge" :class="a.kind==='live' ? 'live' : 'paper'">{{ a.kind==='live' ? 'réel' : 'papier' }}</span></td>
            <td class="num">{{ money(a.equity) }}</td>
            <td class="nowrap"><button v-if="a.kind==='paper'" class="small" @click="resetAccount(a)">Réinitialiser</button>
              <button v-if="a.risk && a.risk.halted" class="small danger" @click="resumeAccount(a)">Reprendre après arrêt de sécurité</button></td></tr></tbody></table></div>
      </div>
      <div class="card">
        <h2>Nouveau compte papier</h2>
        <div class="field"><label>Nom</label><input v-model="newAcc.name"></div>
        <div class="field"><label>Capital fictif</label><input type="number" v-model.number="newAcc.initial_balance" min="10"></div>
        <button class="primary" @click="createAccount">Créer</button>
      </div>
    </div>

    <div v-if="tab==='security'" class="card" style="max-width:520px">
      <h2>Mot de passe du panel</h2>
      <div class="field"><label>Mot de passe actuel</label><input type="password" v-model="pwd.current" autocomplete="current-password"></div>
      <div class="field"><label>Nouveau mot de passe (10 caractères minimum)</label><input type="password" v-model="pwd.new" autocomplete="new-password"></div>
      <div class="field"><label>Confirmer</label><input type="password" v-model="pwd.new2" autocomplete="new-password"></div>
      <button class="primary" @click="changePassword">Modifier</button>
      <p class="faint small" style="margin-top:12px">Accès depuis le téléphone : lancez Sentinelle avec l'option distante et passez par un réseau privé (Tailscale). Ne publiez jamais le panel directement sur internet.</p>
    </div>

    <div v-if="tab==='about'" class="card" style="max-width:720px">
      <h2>À propos</h2>
      <p>Sentinelle {{ store.overview?.version }} — bot de trading crypto prudent.</p>
      <div class="note warn small"><b>Avertissement.</b> Le trading comporte un risque de perte en capital. Aucun bot ne peut garantir des gains ; les résultats passés, réels ou simulés, ne préjugent pas des résultats futurs. Ce logiciel ne constitue pas un conseil financier ni fiscal.</div>
      <p class="small muted" style="margin-top:12px">Graphiques : TradingView Lightweight Charts™, Copyright © TradingView, Inc. — <a href="https://www.tradingview.com/" target="_blank" rel="noopener">tradingview.com</a>. Interface : Vue.js (licence MIT).</p>
    </div>
  </div>`,
};
