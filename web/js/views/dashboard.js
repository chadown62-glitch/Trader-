// Tableau de bord : comptes, IA, capital, décisions en direct, voyants
import { api, safe } from '../api.js';
import { LineChart } from '../charts.js';
import { ContextCard } from './context.js';
import { ShieldCard } from './shield.js';
import { store, money, signed, cls, pct, dt, ago, fxLine, confirmBox, ROLE_LABELS, REASON_ICONS } from '../store.js';

const { ref, computed, onMounted, onUnmounted, watch } = Vue;

export default {
  components: { LineChart, ContextCard, ShieldCard },
  setup() {
    const selected = ref(null);
    const equity = ref([]);
    const days = ref(7);
    const decisions = ref([]);
    const readiness = ref(null);
    const evaluation = ref(null);
    let evaluationTimer;
    const loadEvaluation = async () => {
      evaluation.value = await safe(api.get('/api/portfolio/evaluation'));
    };
    const accounts = computed(() => store.overview?.accounts || []);
    const agents = computed(() => store.overview?.agents || []);
    const current = computed(() => accounts.value.find(a => a.id === selected.value) || accounts.value[0]);

    const loadEquity = async () => {
      if (!current.value) return;
      const rows = await safe(api.get(`/api/accounts/${current.value.id}/equity?days=${days.value}`));
      equity.value = rows ? [{ data: rows.map(r => [r[0] * 1000, r[1]]), color: '#3b82f6' }] : [];
    };
    const loadDecisions = async () => { decisions.value = (await safe(api.get('/api/decisions?limit=40'))) || []; };
    const loadReadiness = async () => { readiness.value = await safe(api.get('/api/ai/readiness')); };
    onMounted(() => {
      loadDecisions(); loadReadiness(); loadEvaluation();
      evaluationTimer = setInterval(loadEvaluation, 60000);
    });
    onUnmounted(() => clearInterval(evaluationTimer));
    watch(() => current.value?.id, loadEquity, { immediate: true });
    watch(days, loadEquity);
    const feed = computed(() => {
      const live = store.decisions.map(d => ({ ...d, live: true }));
      return [...live, ...decisions.value].slice(0, 40);
    });

    const startAgent = async (a) => {
      await safe(api.post(`/api/agents/${a.name}/start`, {}), 'Préparation de l’IA en cours (téléchargement de l’historique)…');
    };
    const portfolio = computed(() => store.overview?.portfolio || {});
    const activateMain = async () => {
      const p = portfolio.value;
      const ok = await confirmBox({ title: 'Passer au portefeuille principal', confirmLabel: 'Passer au portefeuille principal',
        message: `Un portefeuille unique de ${money(p.initial, p.currency)} fictifs sera créé, partagé entre vous et l'IA.\n\n` +
          `Vos anciens comptes sont conservés et restent consultables (historique, courbes), mais on n'y tradera plus. ` +
          `Leurs positions encore ouvertes seront vendues au prix simulé du moment, et l'IA sera arrêtée : vous pourrez la relancer ensuite.` });
      if (!ok) return;
      if (await safe(api.post('/api/portfolio/activate', { confirm: true }), 'Portefeuille principal activé')) store.overview = await api.get('/api/overview');
    };
    const stopAgent = async (a) => { await safe(api.post(`/api/agents/${a.name}/stop`, { close_positions: false }), 'IA arrêtée (les positions gardent leurs stops)'); };
    const shieldPaused = (s) => !!(s && (Object.keys(s.volatility || {}).length || s.cascade || s.news));
    const lights = computed(() => {
      const o = store.overview;
      if (!o) return [];
      const ext = o.extensions || {};
      return [
        { label: 'Moteur', ok: o.engine_alive },
        { label: 'Données de marché', ok: o.market?.status?.ok, warn: !o.market?.status?.ok },
        { label: 'Argent fictif uniquement', ok: o.paper_only?.paper_only === true, err: o.paper_only?.paper_only !== true },
        { label: 'Actualités (IA locale)', ok: ext.news?.enabled && ext.news?.ollama_ok !== false && !ext.news?.error, off: !ext.news?.enabled },
        { label: 'Telegram', ok: ext.telegram?.connected, off: !ext.telegram?.enabled },
        { label: 'Bouclier', ok: o.shield?.enabled && !shieldPaused(o.shield), warn: o.shield?.enabled && shieldPaused(o.shield), off: !o.shield?.enabled },
      ];
    });
    return { store, evaluation, accounts, agents, current, selected, equity, days, feed, readiness, lights, startAgent, stopAgent, portfolio, activateMain, fxLine,
      money, signed, cls, pct, dt, ago, ROLE_LABELS, REASON_ICONS };
  },
  template: `
  <div>
    <div class="page-head"><h1>Tableau de bord</h1><span class="spacer"></span>
      <div class="lights"><span v-for="l in lights" :key="l.label" class="light"><span class="dot" :class="l.ok ? 'ok' : l.off ? '' : l.err ? 'err' : 'warn'"></span>{{ l.label }}</span></div>
    </div>
    <div v-if="!store.overview" class="card empty">Chargement…</div>
    <template v-else>
      <div v-if="portfolio.migration_available" class="note info" style="margin-bottom:14px">
        <b>Nouveau : un portefeuille principal de {{ money(portfolio.initial, portfolio.currency) }} fictifs</b>, partagé entre vous et l'IA, tenu en euros.
        Vos anciens comptes sont conservés et <b>rien ne change tant que vous n'avez pas confirmé</b>.
        <div style="margin-top:8px"><button class="primary small" @click="activateMain">Passer au portefeuille principal…</button></div>
      </div>
      <div class="grid cols-3" style="margin-bottom:14px">
        <div v-for="a in accounts" :key="a.id" class="card account-card" :class="{selected: current && a.id===current.id}" @click="selected = a.id">
          <div class="card-head"><h3>{{ a.name }}</h3><span class="spacer"></span>
            <span class="badge" :class="a.legacy || a.archived_paper ? '' : 'paper'">{{ a.legacy ? 'ANCIEN COMPTE — INERTE' : a.archived_paper ? 'ANCIEN COMPTE — LECTURE SEULE' : 'SIMULATION' }}</span></div>
          <div v-if="a.legacy" class="muted small">Ancien compte réel : conservé pour mémoire, inutilisable (simulation uniquement).</div>
          <div v-else-if="a.connected === false" class="muted small">Compte en cours de chargement…</div>
          <template v-else>
            <div class="big">{{ money(a.equity, a.quote) }}</div>
            <div class="row small" style="margin-top:6px">
              <div v-if="!a.archived_paper">Jour <span class="num" :class="cls(a.day_pnl)">{{ signed(a.day_pnl) }}</span></div>
              <div v-if="a.total_pnl !== null && a.total_pnl !== undefined">Total <span class="num" :class="cls(a.total_pnl)">{{ signed(a.total_pnl) }}</span></div>
              <div v-if="!a.archived_paper">{{ (a.positions || []).length }} position(s)</div>
            </div>
            <div v-if="a.available !== undefined" class="small muted" style="margin-top:6px">
              Disponible <span class="num">{{ money(a.available, a.quote) }}</span> · réservé <span class="num">{{ money(a.reserved, a.quote) }}</span> · investi <span class="num">{{ money(a.invested, a.quote) }}</span>
            </div>
            <div v-if="a.fx" class="faint small" style="margin-top:4px" :title="'Taux de change utilisé pour convertir en ' + a.quote">{{ fxLine(a.fx) }}</div>
            <div v-if="a.risk && a.risk.halted" class="note danger small" style="margin-top:8px">Arrêt de sécurité : {{ a.risk.halted_reason }}</div>
          </template>
          <div class="faint small" style="margin-top:6px">{{ ROLE_LABELS[a.role] || a.role }}</div>
        </div>
      </div>

      <section v-if="evaluation" class="card" style="margin-bottom:14px">
        <h2>Résultats du portefeuille fictif</h2>
        <p v-if="evaluation.as_of" class="muted small">Dernière valorisation {{ dt(evaluation.as_of) }}</p>
        <div class="grid cols-2">
          <div>Résultat réalisé <b>{{ money(evaluation.realized, 'EUR') }}</b></div>
          <div>Résultat latent estimé <b>{{ money(evaluation.unrealized, 'EUR') }}</b></div>
          <div>Frais enregistrés en EUR <b>{{ money(evaluation.fees_eur, 'EUR') }}</b></div>
          <div>Baisse maximale observée <b>{{ pct(evaluation.max_drawdown) }}</b></div>
          <div>Tentatives d’ordre refusées <b>{{ evaluation.rejected }}</b></div>
        </div>
        <details v-if="evaluation.days.length"><summary>Rendements quotidiens observés</summary>
          <div v-for="d in evaluation.days" :key="d.ts">{{ dt(d.ts) }} · {{ money(d.equity, 'EUR') }} · {{ pct(d.net_return) }}</div>
        </details>
        <details v-if="evaluation.trades.length"><summary>Derniers trades clôturés</summary>
          <div v-for="t in evaluation.trades" :key="t.id">{{ t.symbol }} · {{ dt(t.exit_ts) }} · {{ signed(t.realized_pnl, 'EUR') }} · {{ pct(t.net_return) }}</div>
        </details>
        <h3>Validation des modèles</h3>
        <p>{{ evaluation.verdict }}</p>
        <div v-for="r in evaluation.comparisons" :key="r.version">
          {{ r.version }} / {{ r.reference }} · {{ r.verdict }}
          <span v-if="r.interval"> · écart quotidien de {{ pct(r.interval[0]) }} à {{ pct(r.interval[1]) }}</span>
        </div>
        <details v-if="evaluation.models.length"><summary>Versions enregistrées</summary>
          <div v-for="m in evaluation.models" :key="m.version">{{ m.version }} · {{ m.status }}</div>
        </details>
      </section>
      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card">
          <div class="card-head"><h2>Intelligence artificielle</h2><span class="spacer"></span><a href="#/ai" class="small">Détails →</a></div>
          <div v-for="a in agents" :key="a.name" class="card tight" style="margin-bottom:10px;background:var(--bg-2)">
            <div class="inline" style="flex-wrap:wrap">
              <span class="dot" :class="a.running ? 'ok' : a.status==='préparation' ? 'warn' : ''"></span>
              <b>{{ a.label }}</b>
              <span class="badge paper">FICTIF</span>
              <span class="spacer" style="flex:1"></span>
              <span class="muted small">{{ a.running ? 'en marche' : a.status }}</span>
              <button v-if="!a.running && a.status!=='préparation'" class="small primary" @click="startAgent(a)">Démarrer</button>
              <button v-else-if="a.running" class="small" @click="stopAgent(a)">Arrêter</button>
            </div>
            <div v-if="a.message" class="small muted" style="margin-top:6px">{{ a.message }}</div>
            <div v-if="a.running" class="small muted" style="margin-top:6px">Unité de temps {{ a.timeframe }} · {{ a.stats.entries }} entrée(s) · modèle : {{ a.model ? a.model + (a.model_trial ? ' (à l’essai)' : '') : 'aucun (exploration)' }}</div>
          </div>
          <div v-if="readiness" style="margin-top:6px">
            <div class="inline small"><b>Fiabilité prouvée en argent fictif</b><span class="spacer" style="flex:1"></span><span class="num">{{ readiness.score }} %</span></div>
            <div class="progress" :class="{ok: readiness.unlocked}" style="margin-top:6px"><div :style="{width: readiness.score + '%'}"></div></div>
            <div class="small muted" style="margin-top:6px">{{ readiness.unlocked ? 'Toutes les preuves sont réunies (en argent fictif).' : 'L’IA n’a pas encore fait toutes ses preuves. Tout reste en argent fictif.' }}</div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Capital — {{ current?.name }}</h2><span class="spacer"></span>
            <div class="seg"><button v-for="d in [1,7,30,90]" :key="d" :class="{on: days===d}" @click="days=d">{{ d }} j</button></div></div>
          <LineChart v-if="equity.length && equity[0].data.length > 1" :series="equity" height="small" />
          <div v-else class="empty">La courbe apparaîtra après quelques minutes de fonctionnement.</div>
        </div>
      </div>

      <div class="grid cols-2">
      <div class="stack">
      <ShieldCard />
      <ContextCard />
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
      </div>
    </template>
  </div>`,
};
