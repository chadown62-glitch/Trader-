// Tableau de bord : comptes, IA, capital, décisions en direct, voyants
import { api, safe } from '../api.js';
import { LineChart } from '../charts.js';
import { store, money, signed, cls, pct, dt, ago, ROLE_LABELS, REASON_ICONS } from '../store.js';

const { ref, computed, onMounted, watch } = Vue;

export default {
  components: { LineChart },
  setup() {
    const selected = ref(null);
    const equity = ref([]);
    const days = ref(7);
    const decisions = ref([]);
    const readiness = ref(null);
    const accounts = computed(() => store.overview?.accounts || []);
    const agents = computed(() => store.overview?.agents || []);
    const current = computed(() => accounts.value.find(a => a.id === selected.value) || accounts.value[0]);

    const loadEquity = async () => {
      if (!current.value) return;
      const rows = await safe(api.get(`/api/accounts/${current.value.id}/equity?days=${days.value}`));
      equity.value = rows ? [{ data: rows.map(r => [r[0] * 1000, r[1]]), color: current.value.kind === 'live' ? '#ef4444' : '#3b82f6' }] : [];
    };
    const loadDecisions = async () => { decisions.value = (await safe(api.get('/api/decisions?limit=40'))) || []; };
    const loadReadiness = async () => { readiness.value = await safe(api.get('/api/ai/readiness')); };
    onMounted(() => { loadDecisions(); loadReadiness(); });
    watch(() => current.value?.id, loadEquity, { immediate: true });
    watch(days, loadEquity);
    const feed = computed(() => {
      const live = store.decisions.map(d => ({ ...d, live: true }));
      return [...live, ...decisions.value].slice(0, 40);
    });

    const startAgent = async (a) => {
      if (a.mode === 'live') { location.hash = '#/ai'; return; }
      await safe(api.post(`/api/agents/${a.name}/start`, {}), 'Préparation de l’IA en cours (téléchargement de l’historique)…');
    };
    const stopAgent = async (a) => { await safe(api.post(`/api/agents/${a.name}/stop`, { close_positions: false }), 'IA arrêtée (les positions gardent leurs stops)'); };
    const lights = computed(() => {
      const o = store.overview;
      if (!o) return [];
      const ext = o.extensions || {};
      return [
        { label: 'Moteur', ok: o.engine_alive },
        { label: 'Données de marché', ok: o.market?.status?.ok, warn: !o.market?.status?.ok },
        { label: 'Portefeuille réel', ok: o.live?.connected, off: !o.live?.connected && !o.live?.error, err: !!o.live?.error },
        { label: 'Actualités (IA locale)', ok: ext.news?.enabled && ext.news?.ollama_ok !== false && !ext.news?.error, off: !ext.news?.enabled },
        { label: 'Telegram', ok: ext.telegram?.connected, off: !ext.telegram?.enabled },
      ];
    });
    return { store, accounts, agents, current, selected, equity, days, feed, readiness, lights, startAgent, stopAgent,
      money, signed, cls, pct, dt, ago, ROLE_LABELS, REASON_ICONS };
  },
  template: `
  <div>
    <div class="page-head"><h1>Tableau de bord</h1><span class="spacer"></span>
      <div class="lights"><span v-for="l in lights" :key="l.label" class="light"><span class="dot" :class="l.ok ? 'ok' : l.off ? '' : l.err ? 'err' : 'warn'"></span>{{ l.label }}</span></div>
    </div>
    <div v-if="!store.overview" class="card empty">Chargement…</div>
    <template v-else>
      <div class="grid cols-3" style="margin-bottom:14px">
        <div v-for="a in accounts" :key="a.id" class="card account-card" :class="{selected: current && a.id===current.id}" @click="selected = a.id">
          <div class="card-head"><h3>{{ a.name }}</h3><span class="spacer"></span>
            <span class="badge" :class="a.kind==='live' ? 'live' : 'paper'">{{ a.kind==='live' ? 'RÉEL' : 'SIMULATION' }}</span></div>
          <div v-if="a.connected === false" class="muted small">Non connecté — <a href="#/settings">renseigner les clés API</a></div>
          <template v-else>
            <div class="big">{{ money(a.equity, a.quote) }}</div>
            <div class="row small" style="margin-top:6px">
              <div>Jour <span class="num" :class="cls(a.day_pnl)">{{ signed(a.day_pnl) }}</span></div>
              <div v-if="a.total_pnl !== null && a.total_pnl !== undefined">Total <span class="num" :class="cls(a.total_pnl)">{{ signed(a.total_pnl) }}</span></div>
              <div>{{ (a.positions || []).length }} position(s)</div>
            </div>
            <div v-if="a.risk && a.risk.halted" class="note danger small" style="margin-top:8px">Arrêt de sécurité : {{ a.risk.halted_reason }}</div>
          </template>
          <div class="faint small" style="margin-top:6px">{{ ROLE_LABELS[a.role] || a.role }}</div>
        </div>
      </div>

      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card">
          <div class="card-head"><h2>Intelligence artificielle</h2><span class="spacer"></span><a href="#/ai" class="small">Détails →</a></div>
          <div v-for="a in agents" :key="a.name" class="card tight" style="margin-bottom:10px;background:var(--bg-2)">
            <div class="inline" style="flex-wrap:wrap">
              <span class="dot" :class="a.running ? 'ok' : a.status==='préparation' ? 'warn' : ''"></span>
              <b>{{ a.label }}</b>
              <span class="badge" :class="a.mode==='live' ? 'live' : 'paper'">{{ a.mode==='live' ? 'RÉEL' : 'FICTIF' }}</span>
              <span class="spacer" style="flex:1"></span>
              <span class="muted small">{{ a.running ? 'en marche' : a.status }}</span>
              <button v-if="!a.running && a.status!=='préparation'" class="small" :class="a.mode==='live' ? '' : 'primary'" @click="startAgent(a)">{{ a.mode==='live' ? 'Conditions…' : 'Démarrer' }}</button>
              <button v-else-if="a.running" class="small" @click="stopAgent(a)">Arrêter</button>
            </div>
            <div v-if="a.message" class="small muted" style="margin-top:6px">{{ a.message }}</div>
            <div v-if="a.running" class="small muted" style="margin-top:6px">Unité de temps {{ a.timeframe }} · {{ a.stats.entries }} entrée(s) · modèle : {{ a.model || 'aucun (exploration)' }}</div>
          </div>
          <div v-if="readiness" style="margin-top:6px">
            <div class="inline small"><b>Préparation au réel</b><span class="spacer" style="flex:1"></span><span class="num">{{ readiness.score }} %</span></div>
            <div class="progress" :class="{ok: readiness.unlocked}" style="margin-top:6px"><div :style="{width: readiness.score + '%'}"></div></div>
            <div class="small muted" style="margin-top:6px">{{ readiness.unlocked ? 'Toutes les conditions sont remplies.' : 'Le mode réel de l’IA reste verrouillé tant que les conditions ne sont pas remplies.' }}</div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Capital — {{ current?.name }}</h2><span class="spacer"></span>
            <div class="seg"><button v-for="d in [1,7,30,90]" :key="d" :class="{on: days===d}" @click="days=d">{{ d }} j</button></div></div>
          <LineChart v-if="equity.length && equity[0].data.length > 1" :series="equity" height="small" />
          <div v-else class="empty">La courbe apparaîtra après quelques minutes de fonctionnement.</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Décisions de l'IA (journal « pourquoi »)</h2><span class="spacer"></span><a href="#/ai" class="small">Tout voir →</a></div>
        <div v-if="!feed.length" class="empty">Aucune décision pour le moment. Démarrez l'IA d'entraînement : elle analysera le marché à chaque clôture de bougie.</div>
        <div class="feed">
          <div v-for="(d, i) in feed" :key="i" class="feed-item" :class="d.kind">
            <div class="meta">{{ REASON_ICONS[d.kind] || '•' }} {{ dt(d.ts) }} · {{ d.symbol }} · {{ d.agent }}</div>
            <div>{{ d.message }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>`,
};
