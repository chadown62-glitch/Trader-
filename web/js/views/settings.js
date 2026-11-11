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
    { key: 'timeframe', label: 'Unité de temps de l\u2019IA', kind: 'select', options: [['15m', '15 minutes'], ['1h', '1 heure'], ['4h', '4 heures (recommandé)'], ['1d', '1 jour (peu de trades)']], help: 'Plus l’unité est longue, moins les frais pèsent. Changer d’unité demande un nouvel entraînement.' },
    { key: 'exploration', label: 'Exploration en entraînement', kind: 'bool', help: 'Sans modèle validé, l’IA d’entraînement prend les signaux de base (taille réduite) pour apprendre. Toujours en argent fictif.' },
    { key: 'exploration_risk_factor', label: 'Taille en exploration', kind: 'pct', step: 10, help: 'Fraction du risque normal utilisée en exploration.' },
    { key: 'learning', label: 'Apprentissage continu', kind: 'bool', help: 'Ré-entraîne régulièrement l’IA avec les données récentes (le nouveau modèle n’est activé que s’il est validé).' },
    { key: 'retrain_hours', label: 'Fréquence de ré-entraînement (heures)', kind: 'int' },
    { key: 'min_new_samples', label: 'Nouvelles situations minimum avant ré-entraînement', kind: 'int' },
    { key: 'regime_filter', label: 'Rester à l’abri en marché baissier', kind: 'bool', help: 'Aucun achat quand le BTC est sous ses moyennes de fond (recommandé en spot).' },
    { key: 'use_news', label: 'Veto des actualités', kind: 'bool', help: 'Bloque les achats si une actualité très négative et confirmée concerne la crypto.' },
    { key: 'recipe', label: 'Recette du modèle', kind: 'select', options: [['mixte', 'Mixte : arbres simples + modèle linéaire (recommandé)'], ['auto', 'Choisie automatiquement à chaque entraînement'], ['prudent', 'Arbres simples'], ['lineaire', 'Linéaire seul'], ['moyen', 'Arbres intermédiaires'], ['riche', 'Arbres fins (ancien modèle)'], ['comite', 'Comité de trois modèles d\u2019arbres']], help: 'La recette mixte est celle qui apprend le mieux, sur le banc d\u2019essai (page IA → Capacités d\u2019apprentissage) comme sur les données réelles. S\u2019applique au prochain entraînement.' },
    { key: 'half_life_days', label: 'Oubli progressif : demi-vie (jours, vide = désactivé)', kind: 'num', step: 30, help: 'Donne plus de poids aux situations récentes. Mesuré : effet neutre à légèrement positif sur données réelles ; désactivé par défaut.' },
    { key: 'rules_fallback', label: 'Validation « règles seules »', kind: 'bool', help: 'Si le filtre IA n\u2019apporte rien mais que les règles des stratégies sont prouvées sur des données jamais vues, elles sont validées telles quelles.' },
  ] },
  market: { title: 'Données et sécurité des prix', fields: [
    { key: 'cross_check', label: 'Vérifier le prix sur une seconde plateforme', kind: 'bool', help: 'Avant chaque entrée de l\u2019IA, le prix est comparé à celui de Kraken : un écart anormal bloque le trade.' },
    { key: 'cross_check_max_pct', label: 'Écart maximal toléré (%)', kind: 'num', step: 0.1 },
    { key: 'context', label: 'Contexte de marché (Fear & Greed, indices, financement)', kind: 'bool' },
    { key: 'poll_seconds', label: 'Relevé des prix toutes les (secondes)', kind: 'int' },
    { key: 'history_days', label: 'Historique chargé (jours)', kind: 'int' },
  ] },
  macro: { title: 'Calendrier macro', fields: [
    { key: 'enabled', label: 'Pas de nouvelle position autour des annonces', kind: 'bool' },
    { key: 'blackout_minutes', label: 'Fenêtre avant et après l\u2019annonce (minutes)', kind: 'int' },
    { key: 'auto_calendar', label: 'Calendrier économique automatique', kind: 'bool', help: 'Importe les annonces à fort impact de la semaine (inflation, emploi, banques centrales…) depuis le calendrier public Forex Factory, avec les valeurs attendues.' },
    { key: 'calendar_countries', label: 'Monnaies suivies', kind: 'list', upper: true, help: 'Séparées par des virgules. USD (États-Unis) et EUR (zone euro) font bouger les cryptos ; GBP, JPY, CNY… sont possibles.' },
  ] },
  shield: { title: 'Bouclier temps réel', fields: [
    { key: 'enabled', label: 'Activer le bouclier', kind: 'bool', help: 'Empêche l\u2019IA d\u2019ouvrir une position quand le marché devient dangereux et vous avertit avant un achat manuel. Il ne vend jamais rien.' },
    { key: 'volatility_multiple', label: 'Mouvement brutal : bougie d\u2019une minute supérieure à (× la normale)', kind: 'num', step: 0.5 },
    { key: 'volatility_min_pct', label: 'Mouvement brutal : amplitude minimale (%)', kind: 'num', step: 0.1, help: 'Évite les fausses alertes quand le marché est très calme. Mesuré sur 30 jours : pause 0,1 % (BTC) à 2 % (ADA) du temps ; les 30 minutes suivantes sont 4 à 20 fois plus agitées que la normale.' },
    { key: 'cooldown_minutes', label: 'Durée de la pause après un mouvement brutal (minutes)', kind: 'int' },
    { key: 'max_spread_bps', label: 'Écart achat/vente maximal (points de base)', kind: 'num', step: 1, help: '20 = 0,20 %. Pendant une annonce, l\u2019écart s\u2019élargit : acheter coûterait cher.' },
    { key: 'depth_multiple', label: 'Liquidité minimale (× la taille de l\u2019ordre)', kind: 'num', step: 0.5, help: 'Quantité disponible à moins de 0,5 % du prix dans le carnet d\u2019ordres.' },
    { key: 'liquidations', label: 'Surveiller les liquidations forcées (marchés à levier)', kind: 'bool', help: 'Données publiques d\u2019OKX sur le BTC et l\u2019ETH, relevées chaque minute.' },
    { key: 'cascade_multiple', label: 'Cascade : liquidations sur 15 min supérieures à (× l\u2019habituel)', kind: 'num', step: 0.5 },
    { key: 'cascade_min_musd', label: 'Cascade : montant minimal (millions de dollars)', kind: 'num', step: 1, help: 'Mesuré sur le BTC et l\u2019ETH : environ 0,1 M$ par quart d\u2019heure en temps normal, 24 M$ lors de la chute éclair du 8 novembre 2026 (−1,9 % en une minute).' },
    { key: 'news_pause', label: 'Pause après une actualité majeure', kind: 'bool', help: 'Une nouvelle jugée très importante par l\u2019IA locale (dans un sens ou dans l\u2019autre) suspend les entrées sur les cryptos concernées.' },
    { key: 'news_pause_minutes', label: 'Durée de la pause « actualité » (minutes)', kind: 'int' },
  ] },
  monitoring: { title: 'Surveillance et sauvegardes', fields: [
    { key: 'heartbeat_url', label: 'Adresse de surveillance externe (facultatif)', kind: 'text', help: 'Créez un contrôle gratuit sur healthchecks.io (période 5 min) et collez son adresse « ping » : vous serez prévenu si Sentinelle ou le PC s\u2019arrête.' },
    { key: 'backup_keep_days', label: 'Sauvegardes conservées (jours)', kind: 'int', help: 'Une copie de la base est faite chaque jour dans data/backups.' },
  ] },
  paper: { title: 'Simulation (argent fictif)', fields: [
    { key: 'fee_rate', label: 'Frais par ordre', kind: 'pct', step: 0.01, help: 'Hypothèse prudente : 0,1 %.' },
    { key: 'slippage_bps', label: 'Glissement de prix (points de base)', kind: 'num', step: 1, help: '5 = 0,05 % de prix moins favorable à chaque exécution.' },
  ] },
  live: { title: 'Bulletin de fiabilité (argent fictif)', fields: [
    { key: 'unlock_min_days', label: 'Preuve : jours d’entraînement en direct minimum', kind: 'int', help: 'Ces seuils servent au bulletin de fiabilité de l’IA. Ils ne déverrouillent aucun mode réel.' },
    { key: 'unlock_min_trades', label: 'Preuve : trades minimum avec le modèle', kind: 'int' },
    { key: 'unlock_max_drawdown', label: 'Preuve : baisse maximale tolérée', kind: 'pct', step: 1 },
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
    const secretInputs = reactive({ telegram_token: '' });
    const addPair = ref('');
    const feedsText = ref('');
    const eventsText = ref('');
    const tgText = ref('');
    const notifState = ref((() => { try { return ('Notification' in window ? Notification.permission : 'indisponible') + (localStorage.getItem('stn-notif') === '1' ? '' : '-off'); } catch { return 'indisponible'; } })());
    const enableNotif = async () => {
      if (!('Notification' in window)) { toast('Notifications non prises en charge par ce navigateur', 'warn'); return; }
      const perm = await Notification.requestPermission();
      try { localStorage.setItem('stn-notif', perm === 'granted' ? '1' : '0'); } catch { /* ignoré */ }
      notifState.value = perm + (perm === 'granted' ? '' : '-off');
      toast(perm === 'granted' ? 'Notifications activées (quand le panel est en arrière-plan)' : 'Notifications refusées par le navigateur', perm === 'granted' ? 'success' : 'warn');
    };
    const disableNotif = () => { try { localStorage.setItem('stn-notif', '0'); } catch { /* ignoré */ } notifState.value = 'granted-off'; };
    const pwd = reactive({ current: '', new: '', new2: '' });
    const newsStatus = ref(null);
    const totp = reactive({ enabled: false, setup: null, code: '', qr: '', disablePwd: '', disableCode: '' });
    const loadTotp = async () => { const r = await safe(api.get('/api/auth/2fa')); if (r) totp.enabled = r.enabled; };
    const startTotp = async () => {
      const r = await safe(api.post('/api/auth/2fa/setup'));
      if (!r) return;
      totp.setup = r;
      try { const q = qrcode(0, 'M'); q.addData(r.uri); q.make(); totp.qr = q.createDataURL(5, 8); } catch { totp.qr = ''; }
    };
    const enableTotp = async () => { if (await safe(api.post('/api/auth/2fa/enable', { code: totp.code }), 'Double authentification activée')) { totp.enabled = true; totp.setup = null; totp.code = ''; } };
    const disableTotp = async () => { if (await safe(api.post('/api/auth/2fa/disable', { password: totp.disablePwd, code: totp.disableCode }), 'Double authentification désactivée')) { totp.enabled = false; totp.disablePwd = totp.disableCode = ''; } };
    const accounts = computed(() => store.overview?.accounts || []);
    const paperOnly = computed(() => store.overview?.paper_only || {});

    const load = () => {
      if (!store.settings) return;
      Object.assign(form, JSON.parse(JSON.stringify(store.settings)));
      feedsText.value = (form.news?.feeds || []).join('\n');
      eventsText.value = (form.macro?.events || []).join('\n');
      tgText.value = (form.news?.telegram_channels || []).join('\n');
    };
    watch(() => store.settings, load, { immediate: true });
    const loadSecrets = async () => { secrets.value = await safe(api.get('/api/secrets')); };
    onMounted(async () => { loadSecrets(); loadTotp(); newsStatus.value = await safe(api.get('/api/news/status')); });

    const display = (section, f) => {
      const v = form[section]?.[f.key];
      if (f.kind === 'list') return (v || []).join(', ');
      return f.kind === 'pct' && v !== null && v !== undefined ? Math.round(v * 10000) / 100 : v;
    };
    const setValue = (section, f, raw) => {
      let v = raw;
      if (f.kind === 'list') v = raw.split(',').map(x => (f.upper ? x.trim().toUpperCase() : x.trim())).filter(Boolean);
      if (f.kind === 'pct') v = raw === '' ? null : Number(raw) / 100;
      else if (f.kind === 'num') v = raw === '' ? null : Number(raw);
      else if (f.kind === 'int') v = parseInt(raw, 10);
      form[section][f.key] = v;
    };
    const save = async (section) => {
      const patch = { [section]: JSON.parse(JSON.stringify(form[section])) };
      if (section === 'news') {
        patch.news.feeds = feedsText.value.split('\n').map(s => s.trim()).filter(Boolean);
        patch.news.telegram_channels = tgText.value.split('\n').map(s => s.trim()).filter(Boolean);
      }
      if (section === 'macro') patch.macro.events = eventsText.value.split('\n').map(s => s.trim()).filter(Boolean);
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
    const clearLegacyKeys = async () => {
      const ok = await confirmBox({ title: 'Supprimer les anciennes clés API', message: 'Les anciennes clés de plateforme d’échange enregistrées sur ce PC seront effacées. Elles ne sont de toute façon plus utilisées.', confirmLabel: 'Supprimer', danger: true });
      if (ok) secrets.value = await safe(api.put('/api/secrets', { values: { exchange_api_key: null, exchange_secret: null, exchange_password: null } }), 'Anciennes clés supprimées');
    };
    const testTelegram = async () => { await safe(api.post('/api/telegram/test'), 'Message de test envoyé'); };
    const resetAccount = async (a) => {
      const ok = await confirmBox({ title: `Réinitialiser « ${a.name} »`, message: 'Le solde sera réinitialisé à 30 EUR fictifs. Les positions et les ordres ouverts seront annulés et la courbe de capital effacée. L’historique des trades est conservé.', confirmLabel: 'Réinitialiser', danger: true });
      if (ok) await safe(api.post(`/api/accounts/${a.id}/reset`, {}), 'Compte réinitialisé');
    };
    const resumeAccount = async (a) => { await safe(api.post(`/api/accounts/${a.id}/resume`), 'Reprise autorisée'); };
    const changePassword = async () => {
      if (pwd.new !== pwd.new2) { toast('Les deux nouveaux mots de passe ne correspondent pas', 'error'); return; }
      if (await safe(api.post('/api/auth/password', { current: pwd.current, new: pwd.new }), 'Mot de passe modifié')) { pwd.current = pwd.new = pwd.new2 = ''; }
    };
    const TABS = [['general', 'Général'], ['risk', 'Risque'], ['ai', 'IA'], ['market', 'Données'], ['macro', 'Calendrier macro'], ['shield', 'Bouclier'], ['paper', 'Simulation'], ['live', 'Verrou et fiabilité'], ['telegram', 'Telegram'], ['news', 'Actualités'], ['monitoring', 'Surveillance'], ['accounts', 'Comptes'], ['security', 'Sécurité'], ['about', 'À propos']];
    const STRATS = { trend_follow: 'Suivi de tendance (recommandé)', trend_breakout: 'Cassure de tendance (recommandé)', pullback: 'Repli en tendance', mean_reversion: 'Retour à la moyenne', momentum: 'Élan haussier' };
    return { store, tab, form, secrets, secretInputs, addPair, feedsText, eventsText, tgText, notifState, enableNotif, disableNotif, pwd, accounts, paperOnly, newsStatus, SECTIONS, TABS, STRATS,
      display, setValue, save, saveGeneral, addSymbol, removeSymbol, saveSecrets, clearSecret, clearLegacyKeys, testTelegram,
      resetAccount, resumeAccount, changePassword, totp, startTotp, enableTotp, disableTotp, money, dt };
  },
  template: `
  <div v-if="form.general">
    <div class="page-head"><h1>Réglages</h1></div>
    <div class="tabs"><button v-for="t in TABS" :key="t[0]" :class="{on: tab===t[0]}" @click="tab=t[0]">{{ t[1] }}</button></div>

    <div v-if="tab==='general'" class="grid cols-2">
      <div class="card">
        <h2>Cryptos suivies</h2>
        <p class="muted small">Cotées en {{ form.general.quote }}. Restez sur des cryptos très liquides (moins de manipulation, moins de glissement).</p>
        <p v-if="form.general.quote !== 'EUR'" class="small danger">Le portefeuille principal est en EUR : configurez des paires EUR pour trader. La conversion automatique n'est pas encore disponible.</p>
        <div class="chips" style="margin-bottom:10px"><span v-for="s in form.general.watchlist" :key="s" class="chip">{{ s }}<button @click="removeSymbol(s)" title="Retirer">✕</button></span></div>
        <div class="row"><input v-model="addPair" list="pairs" placeholder="ex. XRP/EUR"><button @click="addSymbol">Ajouter</button></div>
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
              <input v-else-if="f.kind==='list'" type="text" :value="display(key, f)" @change="setValue(key, f, $event.target.value)">
              <input v-else type="number" :step="f.step || 1" :value="display(key, f)" @input="setValue(key, f, $event.target.value)">
            </template>
            <div v-if="f.help" class="hint">{{ f.help }}</div>
          </div>
          <div v-if="key==='news'" class="field"><label>Flux RSS (un par ligne)</label><textarea rows="6" v-model="feedsText"></textarea>
            <div class="hint">Médias crypto et sources officielles (Fed, BCE, SEC). Lus toutes les quelques minutes.</div></div>
          <div v-if="key==='news'" class="field"><label>Canaux Telegram de dépêches (un par ligne, ex. @nom_du_canal)</label><textarea rows="3" v-model="tgText" placeholder="@nom_du_canal"></textarea>
            <div class="hint">Les flashs de ces canaux arrivent en quelques secondes et sont analysés dès réception. Nécessite le compte de lecture Telegram (page « Signaux Telegram »). Choisissez des canaux d\u2019information, pas de « signaux ».</div></div>
          <div v-if="key==='macro'" class="field"><label>Annonces supplémentaires (une par ligne : AAAA-MM-JJ HH:MM description, heure UTC)</label><textarea rows="6" v-model="eventsText"></textarea>
            <div class="hint">Le calendrier automatique couvre déjà la semaine en cours. Ajoutez ici les dates connues à l\u2019avance (réunions de la Fed sur federalreserve.gov…). Heure UTC (Paris = UTC+1 en hiver, UTC+2 en été).</div></div>
          <button class="primary" @click="save(key)">Enregistrer</button>
        </div>
        <div v-if="key==='live'" class="card">
          <h2>🔒 Simulation uniquement</h2>
          <p class="small">Cette version de Sentinelle ne peut <b>ni envoyer, ni modifier, ni annuler un ordre réel</b>.
            Ce n'est pas un réglage : aucun paramètre, aucune clé et aucune commande ne peut l'activer.
            Les cours du marché restent réels ; l'argent, lui, est toujours fictif.</p>
          <div v-if="paperOnly.legacy_keys" class="note warn small" style="margin-bottom:10px">
            D'anciennes clés API de plateforme d'échange sont enregistrées sur ce PC. Elles ne sont <b>plus jamais lues ni utilisées</b>.
            Vous pouvez les supprimer ici ; pensez aussi à les révoquer sur le site de la plateforme.
            <div style="margin-top:8px"><button class="small danger" @click="clearLegacyKeys">Supprimer les anciennes clés</button></div>
          </div>
          <div v-else class="note ok small" style="margin-bottom:10px">Aucune clé API de plateforme d'échange n'est enregistrée.</div>
          <div v-if="(paperOnly.legacy_accounts || []).length" class="note warn small">
            Ancien compte réel conservé pour mémoire (jamais connecté) :
            <span v-for="a in paperOnly.legacy_accounts" :key="a.id"> « {{ a.name }} » ({{ a.open_positions }} position(s), {{ a.open_orders }} ordre(s) enregistrés)</span>.
            {{ paperOnly.legacy_note }}
          </div>
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
          <tbody><tr v-for="a in accounts" :key="a.id"><td>{{ a.name }}</td><td><span class="badge" :class="a.kind==='paper' ? 'paper' : ''">{{ a.kind==='paper' ? 'papier' : 'ancien compte réel (inerte)' }}</span></td>
            <td class="num">{{ money(a.equity) }}</td>
            <td class="nowrap"><button v-if="a.role==='principal'" class="small" @click="resetAccount(a)">Réinitialiser</button>
              <button v-if="a.risk && a.risk.halted" class="small danger" @click="resumeAccount(a)">Reprendre après arrêt de sécurité</button></td></tr></tbody></table></div>
      </div>
      <div class="card">
        <h2>Portefeuille principal</h2>
        <p class="small muted">Les opérations manuelles et l'IA partagent le portefeuille fictif en EUR. Les anciens comptes sont conservés en lecture seule.</p>
      </div>
    </div>

    <div v-if="tab==='security'" class="card" style="max-width:520px">
      <h2>Mot de passe du panel</h2>
      <div class="field"><label>Mot de passe actuel</label><input type="password" v-model="pwd.current" autocomplete="current-password"></div>
      <div class="field"><label>Nouveau mot de passe (10 caractères minimum)</label><input type="password" v-model="pwd.new" autocomplete="new-password"></div>
      <div class="field"><label>Confirmer</label><input type="password" v-model="pwd.new2" autocomplete="new-password"></div>
      <button class="primary" @click="changePassword">Modifier</button>
      <h2 style="margin-top:20px">Notifications du navigateur</h2>
      <p class="small muted">Recevoir une notification (alertes, ventes) quand le panel est ouvert en arrière-plan.</p>
      <div class="btn-row"><button v-if="notifState !== 'granted'" @click="enableNotif">Activer les notifications</button>
        <button v-else class="small" @click="disableNotif">Désactiver</button><span class="small muted">État : {{ notifState === 'granted' ? 'activées' : notifState.startsWith('denied') ? 'refusées par le navigateur' : 'désactivées' }}</span></div>
      <h2 style="margin-top:20px">Double authentification</h2>
      <p class="small muted">Un code à 6 chiffres, donné par une application (Google Authenticator, Authy, Microsoft Authenticator…), est demandé en plus du mot de passe. Fortement conseillé si vous accédez au panel depuis le téléphone.</p>
      <div v-if="totp.enabled" class="note ok small" style="margin-bottom:10px">Activée.</div>
      <template v-if="!totp.enabled">
        <button v-if="!totp.setup" class="primary" @click="startTotp">Activer la double authentification</button>
        <div v-else>
          <p class="small">1. Scannez ce code avec votre application (ou saisissez la clé à la main) :</p>
          <img v-if="totp.qr" :src="totp.qr" alt="QR code" style="background:#fff;padding:6px;border-radius:8px">
          <p class="mono small" style="word-break:break-all">{{ totp.setup.secret }}</p>
          <div class="field"><label>2. Tapez le code affiché par l'application</label><input v-model="totp.code" inputmode="numeric" maxlength="6"></div>
          <button class="primary" @click="enableTotp">Valider</button>
        </div>
      </template>
      <template v-else>
        <div class="row"><div class="field"><label>Mot de passe</label><input type="password" v-model="totp.disablePwd"></div>
          <div class="field"><label>Code actuel</label><input v-model="totp.disableCode" inputmode="numeric" maxlength="6"></div></div>
        <button class="danger small" @click="disableTotp">Désactiver</button>
      </template>
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
