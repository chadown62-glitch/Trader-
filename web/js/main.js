// Point d'entrée du panel Sentinelle
import { api, connectWs, closeWs, on, safe } from './api.js';
import { store, toast, money, signed, pct, cls } from './store.js';
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
import Dmh from './views/dmh.js';
import Memecoin from './views/memecoin.js';

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
  { id: 'memecoin', label: 'Memecoin', ico: '◇' },
  { id: 'dmh', label: 'Hub de marché', short: 'Hub', ico: '⇄' },
  { id: 'news', label: 'Actualités', ico: '✉' },
  { id: 'signals', label: 'Signaux', ico: '≈' },
  { id: 'settings', label: 'Réglages', ico: '⚙' },
  { id: 'logs', label: 'Journal', ico: '≡' },
];
// Menu latéral regroupé par usage ; la barre du bas (téléphone) garde les 4 premières pages de NAV et « Plus » les autres.
const GROUPS = [
  { label: 'Pilotage', ids: ['dashboard', 'performance', 'history', 'logs'] },
  { label: 'Trading fictif', ids: ['trade', 'memecoin', 'dmh'] },
  { label: 'Modèles IA', ids: ['ai', 'apprentissage', 'preparation'] },
  { label: 'Informations', ids: ['knowledge', 'macro', 'news', 'signals'] },
  { label: 'Configuration', ids: ['settings'] },
].map(g => ({ ...g, items: g.ids.map(id => NAV.find(n => n.id === id)) }));
const VIEWS = { memecoin: Memecoin, dashboard: Dashboard, trade: Trade, ai: AI, history: History, news: News, signals: Signals, settings: Settings, logs: Logs, knowledge: Knowledge, performance: Performance, macro: Macro, apprentissage: Apprentissage, preparation: Preparation, dmh: Dmh };
const LOGO = `<svg viewBox="0 0 64 64" aria-hidden="true"><path fill="#1597c7" d="M32 4 8 14v16c0 15 10 26 24 30 14-4 24-15 24-30V14z"/><path fill="#fff" d="m20 36 8-10 7 7 9-12 4 3-13 16-7-7-5 6z"/></svg>`;

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
        store.auth.expired = false;
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
  <div class="login" id="codespaces-login">
    <form class="card" @submit.prevent="submit">
      <div class="brand"><span v-html="LOGO"></span><span>Sentinelle</span></div>
      <h1>{{ store.auth.setupRequired ? 'Configurer le cockpit' : 'Connexion au cockpit' }}</h1>
      <p class="muted">Portefeuille de simulation</p>
      <template v-if="store.auth.setupRequired">
        <div class="note info" style="margin:12px 0">Premier lancement : choisissez le mot de passe qui protégera votre panel (10 caractères minimum).</div>
        <div v-if="!store.auth.setupAllowed" class="note warn" style="margin-bottom:12px">Pour des raisons de sécurité, le premier mot de passe doit être créé depuis l'ordinateur où tourne Sentinelle (adresse http://127.0.0.1).</div>
        <div class="field"><label for="new-password">Nouveau mot de passe</label><input id="new-password" type="password" v-model="password" autocomplete="new-password" autofocus></div>
        <div class="field"><label for="confirm-password">Confirmer le mot de passe</label><input id="confirm-password" type="password" v-model="password2" autocomplete="new-password"></div>
      </template>
      <template v-else>
        <div v-if="store.auth.expired" class="note warn" role="status" style="margin-top:12px">Votre session a expiré ou a été fermée. Reconnectez-vous : vous reviendrez sur la même page.</div>
        <div class="field" style="margin-top:14px"><label for="password">Mot de passe</label><input id="password" type="password" v-model="password" autocomplete="current-password" autofocus></div>
        <div v-if="needCode" class="field"><label for="otp">Code de l'application d'authentification (6 chiffres)</label>
          <input id="otp" v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"></div>
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
    // Un clic sur le fond ferme la fenêtre, sauf le second clic d'un double clic sur le bouton qui vient de l'ouvrir.
    const dismiss = (e) => { if (e.detail < 2) store.emergencyOpen = false; };
    return { store, typed, scope, busy, pause, flatten, dismiss };
  },
  template: `
  <div class="modal-bg" @click.self="dismiss">
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
    const dismiss = (e) => { if (e.detail < 2) close(false); };  // même règle que la fenêtre d'urgence
    return { c, close, dismiss };
  },
  template: `
  <div class="modal-bg" @click.self="dismiss">
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

    let refreshing = false;
    const refresh = async () => {
      if (!store.auth.authenticated || refreshing) return;
      refreshing = true;
      try { store.overview = await api.get('/api/overview'); } catch (e) { /* géré par l'API */ }
      finally { refreshing = false; }
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
    on('equity', e => { if (e.account_id === store.selectedAccount) refresh(); });
    onMounted(() => { window.addEventListener('hashchange', onHash); checkAuth(); });
    onBeforeUnmount(() => window.removeEventListener('hashchange', onHash));

    // Thème : la préférence enregistrée, sinon celle du système. Les contrôles natifs (listes, barres de défilement) et la
    // barre du navigateur mobile suivent le thème actif.
    const preferred = () => {
      try { const saved = localStorage.getItem('stn-theme'); if (saved === 'dark' || saved === 'light') return saved; } catch { /* ignoré */ }
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    };
    const theme = ref(preferred());
    const applyTheme = () => {
      document.documentElement.dataset.theme = theme.value;
      document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', theme.value);
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme.value === 'light' ? '#F4F6FB' : '#05070D');
    };
    applyTheme();
    const toggleTheme = () => { theme.value = theme.value === 'dark' ? 'light' : 'dark'; try { localStorage.setItem('stn-theme', theme.value); } catch { /* ignoré */ } applyTheme(); };
    // Déconnexion volontaire : l'écran de connexion d'abord (aucune requête en cours n'est prise pour une session expirée).
    const logout = async () => { store.auth.authenticated = false; store.auth.expired = false; await safe(api.post('/api/auth/logout')); };
    const marketOk = computed(() => store.overview?.market?.status?.ok);
    const account = computed(() => store.overview?.accounts?.find(a =>
      a.id === store.selectedAccount && a.kind === 'paper' && a.connected && !a.archived_paper));
    const delta = computed(() => {
      const a = account.value;
      return Number.isFinite(a?.equity) && a.initial_balance > 0 ? a.equity - a.initial_balance : null;
    });
    const dollarChange = computed(() => {
      const a = account.value, fx = store.overview?.portfolio?.fx;
      if (delta.value == null) return null;
      if (a.quote === 'USD') return delta.value;
      const usd = fx?.rates?.USD;
      if (!usd || usd.stale || !(usd.rate > 0) || fx.base !== a.quote) return null;
      return delta.value / usd.rate;
    });
    const more = ref(false);
    return { store, route, view, NAV, GROUPS, logout, LOGO, marketOk, theme, toggleTheme, more, account, delta, dollarChange, money, signed, pct, cls };
  },
  template: `
  <div v-if="!store.auth.checked" class="boot">Connexion…</div>
  <Login v-else-if="!store.auth.authenticated" />
  <div v-else class="shell">
    <header class="topbar">
      <div class="brand"><span v-html="LOGO"></span><span class="name">Sentinelle</span></div>
      <span class="light hud-live"><span class="dot" :class="store.ws ? 'connected' : 'warn'"></span>{{ store.ws ? 'En direct' : 'Hors ligne' }}</span>
      <span class="badge paper">Simulation</span>
      <div v-if="account" class="hud-account hide-mobile">
        <span>{{ account.name }}</span><strong>{{ money(account.equity, account.quote) }}</strong>
        <span :class="cls(delta)" :title="dollarChange == null ? 'Variation en devise du compte' : 'Variation indicative en USD'">
          {{ dollarChange == null ? signed(delta, account.quote) : signed(dollarChange, 'USD') }} · {{ pct(delta == null ? null : delta / account.initial_balance) }}</span>
      </div>
      <span class="spacer"></span>
      <button class="urgence" @click="store.emergencyOpen = true" title="Arrêt d'urgence">🛑 <span class="hide-mobile">Urgence</span></button>
      <button class="ghost small" @click="toggleTheme" :title="theme==='dark' ? 'Thème clair' : 'Thème sombre'">{{ theme==='dark' ? '☀' : '☾' }}</button>
      <button class="ghost small" @click="logout" title="Se déconnecter">⎋</button>
    </header>
    <nav class="nav" aria-label="Navigation principale">
      <template v-for="g in GROUPS" :key="g.label">
        <div class="nav-group">{{ g.label }}</div>
        <a v-for="n in g.items" :key="n.id" :href="'#/' + n.id" :aria-current="route===n.id ? 'page' : null" :class="{active: route===n.id}"><span class="ico">{{ n.ico }}</span>{{ n.label }}</a>
      </template>
      <div class="foot">Sentinelle {{ store.overview?.version }}</div>
    </nav>
    <main class="main">
      <div v-if="store.offline" class="note danger" role="alert" style="margin-bottom:12px">Serveur injoignable : les informations affichées peuvent être incomplètes ou anciennes. Nouvelle tentative automatique ; la page se recharge dès le retour de la connexion.</div>
      <component :is="view" :key="route + ':' + store.reconnects" />
    </main>
    <nav class="bottom-nav" aria-label="Navigation mobile">
      <a v-for="n in NAV.slice(0,4)" :key="n.id" :href="'#/' + n.id" :aria-current="route===n.id ? 'page' : null" :class="{active: route===n.id}" @click="more=false"><span class="ico">{{ n.ico }}</span>{{ n.short || n.label.split(' ')[0] }}</a>
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
