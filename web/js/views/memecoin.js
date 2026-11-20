import { api, on } from '../api.js';
import { money, signed, cls, pct, plainPct, dt, store } from '../store.js';

const { ref, computed, onMounted, onBeforeUnmount } = Vue;
const root = '/api/dmh/memecoin';
const strategies = { sniper: 'Sniper', momentum: 'Momentum', dip_hunter: 'Dip Hunter', whale_tail: 'Whale Tail' };
const reasons = { signal: 'Entrée', take_profit: 'Prise de profit', stop_loss: 'Stop loss', trailing_stop: 'Stop suiveur' };
const stages = { liquidity: 'Liquidité sous 20 000 $', age: 'Pool de moins d\u2019une heure', activity: 'Dernière heure inactive',
  round_trip: 'Revente non confirmée (Jupiter)', guardian: 'Veto anti-arnaque' };
const decisions = { 'PROPOSITION_SIMULÉE': 'Entrée fictive autorisée', ATTENDRE: 'Attente', VETO: 'Veto', REFUS: 'Refus du risque',
  ABSTENTION: 'Abstention' };

// Break the stroke at missing valuations. Clip the same geometry at the starting balance.
function equityChart(session) {
  const initial = session.initial_capital, curve = session.equity_curve.slice(-480);
  const values = [initial, ...curve.map(p => p.equity).filter(Number.isFinite)];
  const pad = Math.max(initial * 0.0001, (Math.max(...values) - Math.min(...values)) * 0.12);
  const hi = Math.max(...values) + pad, lo = Math.min(...values) - pad;
  const y = v => 140 * (hi - v) / (hi - lo);
  let path = '', connected = false;
  const first = curve[0]?.ts ?? 0, elapsed = Math.max(1, (curve.at(-1)?.ts ?? first) - first);
  for (const point of curve) {
    if (!Number.isFinite(point.equity)) { connected = false; continue; }
    const x = 4 + 592 * (point.ts - first) / elapsed;
    path += (connected ? ' L' : ' M') + x + ' ' + y(point.equity);
    if (!connected) path += ' l0.1 0';
    connected = true;
  }
  return { path, baseline: y(initial), first, last: curve.at(-1)?.ts, hi, lo };
}

export default {
  setup() {
    const session = ref(null), worker = ref(null), busy = ref(false), error = ref('');
    const amount = ref(30), currency = ref('USD'), interval = ref(15), flash = ref('');
    let seq = 0, statusSeq = 0, timer, live = true, initialized = false;
    const radar = ref(null), radarError = ref('');
    const funnel = ref(null), funnelError = ref('');
    let funnelPending = false;
    async function refreshFunnel() {
      if (!live || funnelPending || document.hidden) return;
      funnelPending = true;
      try {
        const data = await api.get(root + '/funnel');
        if (live && data.asset_domain === 'memecoin') { funnel.value = data; funnelError.value = ''; }
      } catch (e) { if (live) funnelError.value = e.message; }
      finally { funnelPending = false; }
    }
    let radarPending = false, radarTimer;
    async function refreshRadar() {
      if (!live || radarPending || document.hidden) return;
      radarPending = true;
      try {
        const data = await api.get(root + '/radar');
        if (live && data.asset_domain === 'memecoin') { radar.value = data; radarError.value = ''; }
      } catch (e) { if (live) radarError.value = e.message; }
      finally { radarPending = false; }
    }
    const off = [];
    function accept(s) {
      if (!live || s?.asset_domain !== 'memecoin' || s.revision < (session.value?.revision ?? 0)) return;
      session.value = s;
      if (!initialized || s.revision > 0) {
        amount.value = s.deposit.amount; currency.value = s.deposit.currency; initialized = true;
      }
    }
    const analytics = ref(null), analyticsError = ref('');
    async function refreshAnalytics(id) {
      try {
        const data = await api.get(root + '/analytics');
        if (!live || id !== seq) return;
        if (data.asset_domain !== 'memecoin') throw new Error('Domaine analytique inattendu');
        analytics.value = data;
        analyticsError.value = '';
      } catch (e) {
        if (live && id === seq) { analyticsError.value = e.message; analytics.value = null; }
      }
    }
    async function refresh() {
      const id = ++seq, version = statusSeq;
      refreshAnalytics(id);
      try {
        const [s, w] = await Promise.all([api.get(root + '/session'), api.get(root + '/worker/status')]);
        if (!live || id !== seq) return;
        accept(s);
        if (version === statusSeq) { worker.value = w; if (w.active) interval.value = w.interval_seconds; }
      } catch (e) { if (live && id === seq) error.value = e.message; }
    }
    async function act(action) {
      if (busy.value) return;
      busy.value = true; error.value = '';
      try {
        const args = { mode: 'paper' };
        if (action !== 'stop' && !session.value.revision) Object.assign(args, { initial_capital: amount.value, currency: currency.value });
        if (action === 'start') args.interval_seconds = interval.value;
        const result = await api.post(root + (action === 'tick' ? '/session/tick' : '/worker/' + action), args);
        if (action === 'tick') accept(result);
        await refresh();
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    onMounted(() => {
      off.push(on('hello', refresh), on('memecoin_session', e => { if (e.asset_domain === 'memecoin') refresh(); }),
        on('memecoin_worker', w => { if (w.asset_domain === 'memecoin') { statusSeq++; worker.value = w; interval.value = w.interval_seconds; } }),
        on('memecoin_fill', f => {
          if (f.asset_domain !== 'memecoin') return;
          flash.value = f.strategy; clearTimeout(timer); timer = setTimeout(() => flash.value = '', 3500);
        }));
      refresh(); refreshRadar(); refreshFunnel();
      radarTimer = setInterval(() => { refreshRadar(); refreshFunnel(); }, 60000);
      off.push(on('memecoin_session', refreshFunnel));
      document.addEventListener('visibilitychange', refreshRadar);
    });
    onBeforeUnmount(() => { live = false; clearInterval(radarTimer); document.removeEventListener('visibilitychange', refreshRadar); off.forEach(unsub => unsub()); clearTimeout(timer); });
    const chart = computed(() => session.value && equityChart(session.value));
    const activity = computed(() => (session.value?.activity || []).slice().reverse());
    const maximum = computed(() => Math.max(0.001, ...(session.value?.token_ranking || []).map(row => Math.abs(row[1]))));
    return { analytics, analyticsError, radar, radarError, funnel, funnelError, stages, decisions, session, worker, interval, amount, currency, busy, error, flash, act, chart, activity, maximum,
      strategies, reasons, store, dt, cls, money, signed, pct, plainPct };
  },
  template: `
  <section class="mc">
    <header class="page-head"><h1>Session Memecoin</h1><span class="badge paper">Simulation</span>
      <span class="spacer"></span><span>{{ worker?.stopping ? 'Pause en cours' : worker?.active ? 'En cours' : 'En pause' }} · {{ worker?.cycles ?? 0 }} cycles</span></header>
    <p v-if="error || worker?.error" role="alert" class="note danger">{{ error || worker.error }}</p>
    <p v-if="!session" class="muted">Chargement de la session…</p>
    <template v-else>
      <section class="card mc-head">
        <div><div class="muted">Valeur du sous-portefeuille</div><strong class="mc-equity">{{ money(session.equity, 'USD') }}</strong>
          <span v-if="session.equity != null" :class="cls(session.equity - session.initial_capital)">{{ pct(session.equity / session.initial_capital - 1) }}</span>
          <span v-else>Valorisation en attente · P&amp;L aux derniers cours disponibles</span>
          <div>Capital de départ · {{ money(session.deposit.amount, session.deposit.currency) }}<span v-if="session.deposit.currency !== 'USD'"> ({{ money(session.initial_capital, 'USD') }})</span></div>
          <span v-if="activity.length" class="badge">Dernière exécution · {{ strategies[activity[0].strategy] }} · {{ reasons[activity[0].reason] }}</span>
        </div>
        <form @submit.prevent="act('start')" class="mc-controls">
          <div class="btn-row" v-if="!session.revision && !worker?.active" aria-label="Capital de départ en dollars">
            <button v-for="n in [30,100,1000]" :key="n" type="button" :disabled="busy" :aria-pressed="amount === n && currency === 'USD'"
              @click="amount = n; currency = 'USD'">{{ n.toLocaleString('fr-FR') }} $</button>
          </div>
          <label>Capital fictif<input type="number" v-model.number="amount" min="10" max="1000000" step="any" required :disabled="busy || !!session.revision"></label>
          <label>Devise<select v-model="currency" :disabled="busy || !!session.revision"><option>USD</option><option>EUR</option></select></label>
          <label>Intervalle (s)<input type="number" v-model.number="interval" min="15" max="3600" required :disabled="busy || worker?.active"></label>
          <div class="btn-row"><button type="submit" class="primary" :disabled="busy || worker?.active">Démarrer</button>
            <button type="button" @click="act('stop')" :disabled="busy || !worker?.active">Pause</button>
            <button type="button" @click="act('tick')" :disabled="busy || worker?.active">Cycle manuel</button></div>
        </form>
      </section>
      <section class="card"><div class="card-head"><h2>Courbe du capital</h2><span class="spacer"></span><small>{{ store.ws ? 'WebSocket connecté' : 'WebSocket déconnecté' }}</small></div>
        <p v-if="!chart.first" class="empty">Le premier cycle alimentera la courbe.</p>
        <template v-else><svg class="mc-chart" viewBox="0 0 600 140" preserveAspectRatio="none" role="img" aria-label="Capital simulé en dollars, vert au-dessus du capital initial et rouge en dessous">
          <defs><clipPath id="mc-up"><rect width="600" :height="chart.baseline"/></clipPath><clipPath id="mc-down"><rect :y="chart.baseline" width="600" height="140"/></clipPath></defs>
          <line x1="0" x2="600" :y1="chart.baseline" :y2="chart.baseline" stroke="currentColor" stroke-dasharray="5 6"/>
          <path v-for="side in ['up','down']" :key="side" :d="chart.path" :clip-path="'url(#mc-'+side+')'" :class="side" stroke="currentColor" fill="none" stroke-width="3" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
        </svg><div class="mc-axis"><small>{{ dt(chart.first) }}</small><small>Référence {{ money(session.initial_capital, 'USD') }}</small><small>{{ dt(chart.last) }}</small></div></template>
      </section>
      <section class="card"><h2>Performance &amp; Stratégies</h2>
        <p v-if="analyticsError" role="alert" class="note warn">{{ analyticsError }}</p>
        <p v-else-if="!analytics" class="muted">Chargement du bilan…</p>
        <p v-else-if="analytics.status === 'empty'" class="empty">Aucune session memecoin enregistrée.</p>
        <template v-else>
          <p v-if="analytics.status === 'stale'" class="note warn">Valorisation en attente · derniers cours disponibles.</p>
          <div class="mc-axis"><span>Taux de réussite global</span><b>{{ plainPct(analytics.win_rate) }}</b></div>
          <div class="mc-axis"><span>Profit factor</span><b>{{ analytics.profit_factor?.toLocaleString('fr-FR', {maximumFractionDigits: 2})
            ?? (analytics.gross_profit > 0 ? '∞ (aucune perte clôturée)' : '—') }}</b></div>
          <div class="mc-axis"><span>Drawdown maximal observé</span><b>{{ analytics.max_drawdown_pct?.toLocaleString('fr-FR', {maximumFractionDigits: 2}) }} %</b></div>
          <table><thead><tr><th>Stratégie</th><th>P&amp;L net</th><th>Réussite sur clôtures</th></tr></thead>
            <tbody><tr v-for="(name,key) in strategies" :key="key"><th scope="row">{{ name }}</th>
              <td :class="cls(analytics.strategies[key].pnl)">{{ signed(analytics.strategies[key].pnl,'USD') }}</td>
              <td>{{ plainPct(analytics.strategies[key].win_rate) }}</td></tr></tbody></table>
        </template>
      </section>
      <section class="card mc-funnel"><div class="card-head"><h2>Pools établis &amp; flotte d'agents</h2><span class="spacer"></span><small>24 h</small></div>
        <p v-if="funnelError" class="note warn">{{ funnelError }}</p>
        <p v-if="!funnel && !funnelError" class="muted">Chargement de l'entonnoir…</p>
        <template v-if="funnel">
          <div class="mc-axis"><span>Pools vus</span><b>{{ funnel.tokens }}</b></div>
          <div v-for="(n, stage) in funnel.refused_by_stage" :key="stage" class="mc-axis"><span>Refusés · {{ stages[stage] || stage }}</span><b>{{ n }}</b></div>
          <div class="mc-axis"><span>Retenus</span><b>{{ funnel.accepted }}</b></div>
          <div class="mc-axis"><span>Liquidité médiane des pools vus</span><b>{{ money(funnel.median_liquidity_usd, 'USD') }}</b></div>
          <small class="muted">Critères : liquidité ≥ {{ money(funnel.thresholds.min_liquidity_usd, 'USD') }}, pool d'au moins une heure, dernière heure
            ≥ {{ money(funnel.thresholds.min_volume_h1_usd, 'USD') }} avec {{ funnel.thresholds.min_trades_h1 }} achats et ventes, aller-retour Jupiter
            ≤ {{ plainPct(funnel.thresholds.max_round_trip_loss) }}, puis veto anti-arnaque. Simulation seulement.</small>
          <h3>Candidats retenus</h3><p v-if="!funnel.candidates.length" class="muted">Aucun pool établi retenu sur la période.</p>
          <div v-for="c in funnel.candidates" :key="c.mint" class="mc-axis"><strong :title="c.mint">{{ c.name || c.mint }}</strong><small>{{ c.dex_id }}</small>
            <span>{{ money(c.liquidity_usd, 'USD') }} · {{ money(c.volume_h1_usd, 'USD') }} sur 1 h · aller-retour {{ plainPct(c.round_trip_loss) }}</span></div>
          <h3>Décisions de la flotte</h3><p v-if="!funnel.fleet.recent.length" class="muted">Aucune entrée examinée par la flotte.</p>
          <div v-for="(d, i) in funnel.fleet.recent" :key="i" class="mc-axis"><time>{{ dt(d.ts) }}</time><small :title="d.mint">{{ d.mint.slice(0, 6) }}…</small>
            <b>{{ decisions[d.decision] || d.decision }}</b><span>{{ d.why[0] }}</span></div>
        </template>
      </section>
      <section class="card"><div class="card-head"><h2>Radar Solana &amp; Top Smart Wallets</h2>
        <span v-if="radar" class="spacer"></span><small v-if="radar">{{ dt(radar.observed_at) }}</small></div>
        <p v-if="radarError" class="note warn">{{ radarError }}</p>
        <p v-if="!radar && !radarError" class="muted">Collecte des observations publiques…</p>
        <template v-if="radar">
          <p v-if="radar.errors?.length" class="muted">Couverture publique partielle.</p>
          <div v-for="t in radar.tokens" :key="t.mint || 'demo'" class="mc-axis">
            <strong>{{ t.symbol }}</strong><small>{{ t.source === 'live' ? 'Public' : 'Exemple simulé' }}</small>
            <span>{{ money(t.liquidity_usd,'USD') }} de liquidité · {{ pct(t.change_5m_pct, 2, true) }} sur 5 min</span>
            <span>{{ {passed:'Guardian accepté',veto:'Guardian refusé',unknown:'Risque inconnu'}[t.risk] }} · RugCheck {{ t.rug_score ?? '—' }}</span>
            <small>{{ t.eligible_strategies.map(k => strategies[k]).join(', ') || 'Aucune stratégie éligible' }}</small>
          </div><p v-if="!radar.tokens?.length">Aucun candidat observé.</p>
          <h3>Wallets qualifiés · 72 h</h3>
          <p v-if="!radar.smart_wallets?.length" class="muted">Aucun wallet qualifié sur la période.</p>
          <div v-for="w in radar.smart_wallets" :key="w.address" class="mc-axis"><code :title="w.address">{{ w.address.slice(0,6) }}…{{ w.address.slice(-5) }}</code>
            <span>{{ w.score_72h_sol.toFixed(4) }} SOL pondérés · {{ plainPct(w.win_rate) }} gagnants · aucun dump signalé</span></div>
        </template>
      </section>
      <div class="mc-grid"><article v-for="(name,key) in strategies" :key="key" class="card" :class="{'mc-flash': flash === key}">
        <h2>{{ name }}</h2><strong :class="cls(session.strategies[key].pnl)">{{ signed(session.strategies[key].pnl,'USD') }}</strong>
        <div class="muted">{{ session.strategies[key].trades }} entrées · {{ session.tracked_wallet_count }} wallets qualifiés suivis (session)</div>
      </article></div>
      <div class="mc-grid"><section class="card"><h2>Activité récente</h2><p v-if="!activity.length" class="muted">Aucune exécution simulée.</p>
        <ol class="mc-activity"><li v-for="(f,i) in activity" :key="i"><time>{{ dt(f.ts) }}</time><span>{{ strategies[f.strategy] }} · {{ reasons[f.reason] }}<small :title="f.mint">{{ f.mint }}</small></span><b :class="cls(f.pnl)">{{ f.side === 'sell' ? signed(f.pnl,'USD') : 'Achat' }}</b></li></ol></section>
        <section class="card"><h2>Bilan de session</h2><p>{{ session.closed_trades }} positions clôturées</p>
          <div class="mc-axis"><span>Taux de réussite</span><b>{{ plainPct(session.win_rate) }}</b></div>
          <div class="mc-axis"><span>Gain net moyen par position clôturée</span><b>{{ signed(session.mean_closed_pnl,'USD') }}</b></div>
          <h3>Performance par token</h3><p v-if="!session.token_ranking.length" class="muted">Aucun token en portefeuille.</p>
          <div v-for="[mint,pnl] in session.token_ranking" :key="mint" class="mc-token"><span :title="mint">{{ mint }}</span><b :class="cls(pnl)">{{ signed(pnl,'USD') }}</b>
            <i :class="cls(pnl)" :style="{width:(100*Math.abs(pnl)/maximum)+'%'}"></i></div>
        </section></div>
      <details v-if="session.errors.length" class="card"><summary>Sources indisponibles ({{ session.errors.length }})</summary><p v-for="(e,i) in session.errors" :key="i">{{ typeof e === 'string' ? e : (e.reason || e.wallets || e) }}</p></details>
    </template>
  </section>`
};
