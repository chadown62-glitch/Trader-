// Point d'entrée du panel Sentinelle
import { api, connectWs, closeWs, on, safe } from './api.js';
import { store, toast } from './store.js';
import Dashboard from './views/dashboard.js';
import Trade from './views/trade.js';
import AI from './views/ai.js';
import History from './views/history.js';
import News from './views/news.js';
import Settings from './views/settings.js';
import Logs from './views/logs.js';
import Signals from './views/signals.js';
import Knowledge from './views/knowledge.js';
import Performance from './views/performance.js';
import Macro from './views/macro.js';
import Apprentissage from './views/apprentissage.js';
import Preparation from './views/preparation.js';

const { createApp, ref, computed, onMounted, onBeforeUnmount, watch } = Vue;

const NAV = [
  { id: 'dashboard', label: 'Tableau de bord', ico: '◫' },
  { id: 'performance', label: 'Performances', short: 'Résultats', ico: '%' },
  { id: 'trade', label: 'Trader', ico: '↗' },
  { id: 'ai', label: 'IA', ico: '✦' },
  { id: 'history', label: 'Historique', ico: '☰' },
  { id: 'knowledge', label: 'Fiches cryptos', ico: '❖' },
  { id: 'apprentissage', label: 'Apprentissage', ico: '◉' },
  { id: 'macro', label: 'Contexte macro', short: 'Macro', ico: '◷' },
  { id: 'preparation', label: 'Préparation', ico: '✓' },
  { id: 'news', label: 'Actualités', ico: '✉' },
  { id: 'signals', label: 'Signaux', ico: '≈' },
  { id: 'settings', label: 'Réglages', ico: '⚙' },
  { id: 'logs', label: 'Journal', ico: '≡' },
];
const VIEWS = { dashboard: Dashboard, trade: Trade, ai: AI, history: History, news: News, signals: Signals, settings: Settings, logs: Logs, knowledge: Knowledge, performance: Performance, macro: Macro, apprentissage: Apprentissage, preparation: Preparation };
const LOGO = `<svg viewBox="0 0 64 64" aria-hidden="true"><path fill="#3b82f6" d="M32 4 8 14v16c0 15 10 26 24 30 14-4 24-15 24-30V14z"/><path fill="#fff" d="m20 36 8-10 7 7 9-12 4 3-13 16-7-7-5 6z"/></svg>`;

const Login = {
  setup() {
    const password = ref('');
    const password2 = ref('');
    const busy = ref(false);
    const error = ref('');
    const code = ref('');
    const needCode = ref(false);
    const submit = async () => {
      error.value = '';
      if (store.auth.setupRequired && password.value !== password2.value) { error.value = 'Les deux mots de passe ne correspondent pas.'; return; }
      busy.value = true;
      try {
        const body = { password: password.value };
        if (needCode.value) body.code = code.value;
        await api.post(store.auth.setupRequired ? '/api/auth/setup' : '/api/auth/login', body);
        store.auth.authenticated = true;
        store.auth.setupRequired = false;
      } catch (e) {
        if (e.data && e.data.need_code) {
          if (needCode.value) error.value = e.message;
          needCode.value = true;
        } else error.value = e.message;
      }
      busy.value = false;
    };
    return { store, password, password2, busy, error, submit, code, needCode, LOGO };
  },
  template: `
  <div class="login">
    <form class="card" @submit.prevent="submit">
      <div class="brand"><span v-html="LOGO"></span><span>Sentinelle</span></div>
      <p class="center muted small">Bot de trading crypto prudent — simulation uniquement, aucun ordre réel possible</p>
      <template v-if="store.auth.setupRequired">
        <div class="note info" style="margin:12px 0">Premier lancement : choisissez le mot de passe qui protégera votre panel (10 caractères minimum).</div>
        <div v-if="!store.auth.setupAllowed" class="note warn" style="margin-bottom:12px">Pour des raisons de sécurité, le premier mot de passe doit être créé depuis l'ordinateur où tourne Sentinelle (adresse http://127.0.0.1).</div>
        <div class="field"><label>Nouveau mot de passe</label><input type="password" v-model="password" autocomplete="new-password" autofocus></div>
        <div class="field"><label>Confirmer le mot de passe</label><input type="password" v-model="password2" autocomplete="new-password"></div>
      </template>
      <template v-else>
        <div class="field" style="margin-top:14px"><label>Mot de passe</label><input type="password" v-model="password" autocomplete="current-password" autofocus></div>
        <div v-if="needCode" class="field"><label>Code de l'application d'authentification (6 chiffres)</label>
          <input v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"></div>
      </template>
      <div v-if="error" class="note danger" style="margin-bottom:10px">{{ error }}</div>
      <button class="primary block" :disabled="busy || !password">{{ store.auth.setupRequired ? 'Créer le mot de passe' : 'Se connecter' }}</button>
    </form>
  </div>`,
};

const EmergencyModal = {
  setup() {
    const typed = ref('');
    const scope = ref('ai');
    const busy = ref(false);
    const pause = async () => {
      busy.value = true;
      const r = await safe(api.post('/api/emergency', { action: 'pause' }));
      busy.value = false;
      if (r) { toast(`IA arrêtées (${r.agents_stopped.length}). Les positions gardent leurs stops.`, 'warn'); store.emergencyOpen = false; }
    };
    const flatten = async () => {
      busy.value = true;
      const r = await safe(api.post('/api/emergency', { action: 'flatten', scope: scope.value, confirm: typed.value }));
      busy.value = false;
      if (r) { toast(`Arrêt d'urgence : ${r.positions_closed} position(s) fermée(s).` + (r.errors.length ? ' Erreurs : ' + r.errors.join(' ; ') : ''), r.errors.length ? 'error' : 'warn', 10000); store.emergencyOpen = false; }
    };
    return { store, typed, scope, busy, pause, flatten };
  },
  template: `
  <div class="modal-bg" @click.self="store.emergencyOpen=false">
    <div class="modal danger">
      <h2>🛑 Arrêt d'urgence</h2>
      <p class="muted">Deux niveaux possibles :</p>
      <div class="card tight" style="margin-bottom:10px">
        <h3>1. Mettre en pause</h3>
        <p class="small muted">Arrête toutes les IA. Les positions ouvertes restent protégées par leurs stops.</p>
        <button class="block" :disabled="busy" @click="pause">⏸ Arrêter les IA</button>
      </div>
      <div class="card tight">
        <h3>2. Tout revendre</h3>
        <p class="small muted">Arrête les IA et revend immédiatement au prix du marché.</p>
        <div class="seg" style="margin-bottom:10px">
          <button :class="{on: scope==='ai'}" @click="scope='ai'">Positions de l'IA</button>
          <button :class="{on: scope==='all'}" @click="scope='all'">Toutes les positions</button>
        </div>
        <div class="field"><label>Tapez <b>URGENCE</b> pour confirmer</label><input v-model="typed" placeholder="URGENCE"></div>
        <button class="danger block" :disabled="busy || typed.trim().toUpperCase()!=='URGENCE'" @click="flatten">Tout revendre maintenant</button>
      </div>
      <div class="btn-row" style="margin-top:12px;justify-content:flex-end"><button class="ghost" @click="store.emergencyOpen=false">Fermer</button></div>
    </div>
  </div>`,
};

const ConfirmModal = {
  setup() {
    const c = computed(() => store.confirm);
    const close = (v) => { const r = store.confirm.resolve; store.confirm = null; r(v); };
    return { c, close };
  },
  template: `
  <div class="modal-bg" @click.self="close(false)">
    <div class="modal" :class="{danger: c.danger}">
      <h2>{{ c.title }}</h2>
      <p style="white-space:pre-line">{{ c.message }}</p>
      <div v-if="c.phrase" class="field"><label>Tapez « {{ c.phrase }} » pour confirmer</label><input v-model="c.typed" :placeholder="c.phrase"></div>
      <div class="btn-row" style="justify-content:flex-end">
        <button class="ghost" @click="close(false)">Annuler</button>
        <button :class="c.danger ? 'danger' : 'primary'" :disabled="c.phrase && c.typed.trim().toUpperCase() !== c.phrase" @click="close(c.phrase ? c.typed.trim() : true)">{{ c.confirmLabel }}</button>
      </div>
    </div>
  </div>`,
};

const App = {
  components: { Login, EmergencyModal, ConfirmModal },
  setup() {
    const route = ref((location.hash.replace(/^#\/?/, '') || 'dashboard').split('?')[0]);
    const onHash = () => { route.value = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('?')[0]; };
    const view = computed(() => VIEWS[route.value] || Dashboard);
    let timer = null;

    const refresh = async () => {
      if (!store.auth.authenticated) return;
      try { store.overview = await api.get('/api/overview'); } catch (e) { /* géré par l'API */ }
    };
    const loadStatic = async () => {
      try {
        store.settings = await api.get('/api/settings');
        store.markets = await api.get('/api/markets');
      } catch (e) { /* ignoré */ }
    };
    const checkAuth = async () => {
      try {
        const s = await api.get('/api/auth/status');
        store.auth.setupRequired = s.setup_required;
        store.auth.setupAllowed = s.setup_allowed;
        store.auth.authenticated = s.authenticated;
      } catch (e) { toast(e.message, 'error'); }
      store.auth.checked = true;
    };
    watch(() => store.auth.authenticated, async (ok) => {
      if (ok) {
        await loadStatic(); await refresh(); connectWs();
        clearInterval(timer); timer = setInterval(refresh, 5000);
      } else { closeWs(); clearInterval(timer); }
    });
    on('tick', (d) => { store.markets.prices[d.symbol] = d.price; });
    on('decision', (d) => { store.decisions.unshift({ ...d, ts: Date.now() / 1000 }); store.decisions.splice(60); });
    on('job', (d) => { store.jobs[d.id] = d; });
    const notify = (title, body) => {
      try {
        if (document.hidden && 'Notification' in window && Notification.permission === 'granted' && localStorage.getItem('stn-notif') === '1') {
          new Notification(title, { body, icon: 'data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 64 64%27%3E%3Cpath fill=%27%233b82f6%27 d=%27M32 4 8 14v16c0 15 10 26 24 30 14-4 24-15 24-30V14z%27/%3E%3C/svg%3E' });
        }
      } catch { /* notifications indisponibles */ }
    };
    on('alert', (d) => { toast(d.message, d.level === 'critical' ? 'error' : 'warn', 12000); notify('Sentinelle : alerte', d.message); });
    on('trade_closed', (d) => notify(`Sentinelle : vente ${d.symbol}`, `${(d.realized_pnl || 0) >= 0 ? '+' : ''}${(d.realized_pnl || 0).toFixed(2)} (${d.reason_close || ''})`));
    on('trade_closed', (d) => { const p = d.realized_pnl || 0; toast(`${d.origin === 'ai' ? 'IA' : 'Manuel'} — vente ${d.symbol} : ${p >= 0 ? '+' : ''}${p.toFixed(2)} (${d.reason_close || ''})`, p >= 0 ? 'success' : 'warn'); refresh(); });
    on('position_opened', (d) => { if (d.origin === 'ai') toast(`IA — achat ${d.symbol}`, 'info'); refresh(); });
    on('training_done', (d) => toast(`Entraînement terminé : modèle ${d.verdict}`, d.verdict === 'validé' ? 'success' : 'warn', 10000));
    on('settings', loadStatic);
    on('agent', refresh);
    onMounted(() => { window.addEventListener('hashchange', onHash); checkAuth(); });
    onBeforeUnmount(() => window.removeEventListener('hashchange', onHash));

    const theme = ref((() => { try { return localStorage.getItem('stn-theme') || 'dark'; } catch { return 'dark'; } })());
    const applyTheme = () => { document.documentElement.dataset.theme = theme.value; };
    applyTheme();
    const toggleTheme = () => { theme.value = theme.value === 'dark' ? 'light' : 'dark'; try { localStorage.setItem('stn-theme', theme.value); } catch { /* ignoré */ } applyTheme(); };
    const logout = async () => { await safe(api.post('/api/auth/logout')); store.auth.authenticated = false; };
    const marketOk = computed(() => store.overview?.market?.status?.ok);
    const more = ref(false);
    return { store, route, view, NAV, logout, LOGO, marketOk, theme, toggleTheme, more };
  },
  template: `
  <div v-if="!store.auth.checked" class="boot">Connexion…</div>
  <Login v-else-if="!store.auth.authenticated" />
  <div v-else class="shell">
    <header class="topbar">
      <div class="brand"><span v-html="LOGO"></span><span class="name">Sentinelle</span></div>
      <span class="light hide-mobile" title="Données de marché"><span class="dot" :class="marketOk ? 'ok' : 'warn'"></span>Marché</span>
      <span class="light" title="Mises à jour en temps réel"><span class="dot" :class="store.ws ? 'ok' : 'err'"></span><span class="hide-mobile">Temps réel</span></span>
      <span class="badge paper hide-mobile" title="Cette version ne peut passer aucun ordre réel">🔒 Simulation uniquement</span>
      <span class="spacer"></span>
      <button class="urgence" @click="store.emergencyOpen = true" title="Arrêt d'urgence">🛑 <span class="hide-mobile">Urgence</span></button>
      <button class="ghost small" @click="toggleTheme" :title="theme==='dark' ? 'Thème clair' : 'Thème sombre'">{{ theme==='dark' ? '☀' : '☾' }}</button>
      <button class="ghost small" @click="logout" title="Se déconnecter">⎋</button>
    </header>
    <nav class="nav">
      <a v-for="n in NAV" :key="n.id" :href="'#/' + n.id" :class="{active: route===n.id}"><span class="ico">{{ n.ico }}</span>{{ n.label }}</a>
      <div class="foot">Sentinelle {{ store.overview?.version }}<br>Le trading comporte un risque de perte en capital.</div>
    </nav>
    <main class="main"><component :is="view" :key="route" /></main>
    <nav class="bottom-nav">
      <a v-for="n in NAV.slice(0,4)" :key="n.id" :href="'#/' + n.id" :class="{active: route===n.id}" @click="more=false"><span class="ico">{{ n.ico }}</span>{{ n.short || n.label.split(' ')[0] }}</a>
      <a href="#" role="button" :aria-expanded="more" :class="{active: more || NAV.slice(4).some(n => n.id === route)}" @click.prevent="more = !more"><span class="ico">⋯</span>Plus</a>
    </nav>
    <div v-if="more" class="more-sheet" @click.self="more=false">
      <div class="card"><a v-for="n in NAV.slice(4)" :key="n.id" :href="'#/' + n.id" @click="more=false"><span class="ico">{{ n.ico }}</span> {{ n.label }}</a></div>
    </div>
  </div>
  <EmergencyModal v-if="store.emergencyOpen" />
  <ConfirmModal v-if="store.confirm" />
  <div class="toasts"><div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.kind">{{ t.message }}</div></div>`,
};

createApp(App).mount('#app');
