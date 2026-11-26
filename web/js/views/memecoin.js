import { api, on } from '../api.js';
import { money, signed, cls, pct, plainPct, dt, store } from '../store.js';

const { ref, computed, onMounted, onBeforeUnmount } = Vue;
const root = '/api/dmh/memecoin';
const strategies = { sniper: 'Sniper', momentum: 'Momentum', dip_hunter: 'Dip Hunter', whale_tail: 'Whale Tail' };
const reasons = { break_even_stop: 'Stop au seuil de rentabilité', time_exit: 'Durée maximale', signal: 'Entrée', take_profit: 'Prise de profit', stop_loss: 'Stop loss', trailing_stop: 'Stop suiveur' };
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
    const now = ref(Date.now() / 1000);
    let clock;
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
      clock = setInterval(() => { now.value = Date.now() / 1000; }, 1000);
      refresh(); refreshRadar(); refreshFunnel();
      radarTimer = setInterval(() => { refreshRadar(); refreshFunnel(); if (!store.ws && !document.hidden) refresh(); }, 60000);
      off.push(on('memecoin_session', refreshFunnel));
      document.addEventListener('visibilitychange', refreshRadar);
    });
    onBeforeUnmount(() => { live = false; clearInterval(clock); clearInterval(radarTimer); document.removeEventListener('visibilitychange', refreshRadar); off.forEach(unsub => unsub()); clearTimeout(timer); });
    const tab = ref('Live');
    const learning = computed(() => {
      const data = analytics.value?.learning;
      return data?.asset_domain === 'memecoin' && data.paper_only === true ? data : null;
    });
    const learningRows = computed(() => Object.entries(strategies).map(([key, name]) => {
      const row = learning.value?.strategies?.[key];
      return { key, name, multiplier: row?.multiplier ?? 1, score: row?.score, closed: row?.closed_trades,
        window: row?.window_trades, unlocked: row?.upside_unlocked, reason: row?.reason };
    }));
    const suggestions = computed(() => (learning.value?.recommendations || []).filter(r =>
      r.status === 'suggested' && r.samples >= 20 && Number.isFinite(r.suggested_threshold)));
    const decimal = new Intl.NumberFormat('fr-FR', {minimumFractionDigits: 2, maximumFractionDigits: 2});

    const matchesSession = computed(() => analytics.value && session.value &&
      (analytics.value.revision == null || analytics.value.revision === session.value.revision));
    const lots = computed(() => matchesSession.value ? analytics.value.recent_closed_positions || [] : []);
    const holdings = computed(() => (matchesSession.value ? analytics.value.open_positions : null) ??
      Object.values(session.value?.positions || {}).map(p => ({
        mint: p.mint, strategy: p.strategy, quantity: p.qty, cost_basis_usd: p.basis, value_usd: p.mark,
        exit_policy: p.exit_policy, opened_at: p.opened_at, marked_at: p.marked_at, peak_ratio: p.high,
        exits: p.stage, realized_pnl_usd: p.pnl, stale: session.value.stale_marks?.includes(p.mint),
        unrealized_pnl_usd: Number.isFinite(p.mark) && Number.isFinite(p.basis) ? p.mark - p.basis : null,
        unrealized_pnl_pct: p.basis > 0 && Number.isFinite(p.mark) ? 100 * (p.mark / p.basis - 1) : null
      })));
    function tokenLabel(mint) {
      const mark = matchesSession.value ? analytics.value.tokens?.[mint] : null;
      const radarToken = radar.value?.tokens?.find(t => t.mint === mint && t.source === 'live');
      const symbol = mark?.symbol || radarToken?.symbol;
      return [mark?.name || radarToken?.name, symbol === mint ? null : symbol].filter(Boolean)
        .filter((n, i, arr) => arr.indexOf(n) === i).join(' · ') || mint;
    }
    function duration(start, end) {
      if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end < start) return 'Indisponible';
      const seconds = Math.floor(end - start);
      if (seconds < 60) return seconds + ' s';
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return minutes + ' min';
      const hours = Math.floor(minutes / 60);
      return hours < 24 ? hours + ' h ' + (minutes % 60) + ' min' : Math.floor(hours / 24) + ' j ' + (hours % 24) + ' h';
    }
    function quoteState(p) {
      if (p.stale) return 'Périmée au dernier cycle';
      const age = now.value - p.quote_received_at;
      if (!Number.isFinite(p.quote_received_at) || age < 0 || !Number.isFinite(analytics.value?.quote_max_age_s))
        return 'Fraîcheur inconnue';
      return (age <= analytics.value.quote_max_age_s ? 'Reçue il y a ' : 'Périmée · reçue il y a ') + duration(p.quote_received_at, now.value);
    }
    function eur(value) {
      return Number.isFinite(value?.amount) ? signed(value.amount, 'EUR') : 'EUR indisponible';
    }
    function pendingExit(p) {
      return session.value?.pending_orders?.find(o => o.kind === 'sell' && o.key === p.strategy + '/' + p.mint);
    }
    const price = value => Number.isFinite(value) ? value.toLocaleString('fr-FR', {maximumSignificantDigits: 6}) + ' $' : '—';
    function meanProfit(key) {
      const stats = analytics.value.strategies[key];
      if (Number.isFinite(stats.avg_profit_pct)) return stats.avg_profit_pct;
      const rows = lots.value.filter(p => p.strategy === key);
      if (!rows.length || rows.length !== stats.closed_trades) return null;
      const returns = rows.map(p => p.profit_pct ?? (p.initial_usd > 0 && Number.isFinite(p.pnl_usd) ? 100 * p.pnl_usd / p.initial_usd : null));
      return returns.every(Number.isFinite) ? returns.reduce((a,b) => a+b, 0) / rows.length : null;
    }
    function factor(key) {
      const f = analytics.value?.strategy_profit_factors?.[key];
      return f?.ratio?.toLocaleString('fr-FR', {maximumFractionDigits: 2}) ?? (f?.only_gains ? '∞' : '—');
    }
    function ladder(p) {
      const policy = p.exit_policy || session.value?.positions?.[p.strategy + '/' + p.mint]?.exit_policy;
      const tiers = policy === 'calibrated-v1' ? [1.25, 3, 6, 10] : policy === 'legacy' ? [1.5, 2] : [];
      return tiers.map((level, i) => {
        const sold = p.tier_reached >= level || Number.isInteger(p.exits) && p.exits > i;
        const observed = sold || Number.isFinite(p.peak_ratio) && p.peak_ratio >= level;
        return {level, observed, label: sold ? 'Vente exécutée' : observed ? 'Seuil observé' : 'Seuil à surveiller'};
      });
    }
    function protectiveStop(p) {
      const policy = p.exit_policy || session.value?.positions?.[p.strategy + '/' + p.mint]?.exit_policy;
      if (!['calibrated-v1', 'legacy'].includes(policy)) return 'Politique de stop inconnue';
      if (p.exits > 0) return policy === 'calibrated-v1' ? 'Stop suiveur et plancher de rentabilité actifs' : 'Stop suiveur actif';
      return policy === 'calibrated-v1' ? 'Stop actif à −20 %' : 'Stop actif à −17 %';
    }
    const chart = computed(() => session.value && equityChart(session.value));
    const activity = computed(() => (session.value?.activity || []).slice().reverse());
    const maximum = computed(() => Math.max(0.001, ...(session.value?.token_ranking || []).map(row => Math.abs(row[1]))));
    return { now, tokenLabel, duration, quoteState, eur, pendingExit, protectiveStop, tab, learning, learningRows, suggestions, decimal, lots, holdings, price, meanProfit, factor, ladder, analytics, analyticsError, radar, radarError, funnel, funnelError, stages, decisions, session, worker, interval, amount, currency, busy, error, flash, act, chart, activity, maximum,
      strategies, reasons, store, dt, cls, money, signed, pct, plainPct };
  },
  template: `
  <section class="mc">
    <header class="page-head"><h1>Session Memecoin</h1><span class="badge paper">Simulation</span>
      <span class="spacer"></span><span>{{ worker?.stopping ? 'Pause en cours' : worker?.active ? 'En cours' : 'En pause' }} · {{ worker?.cycles ?? 0 }} cycles</span></header>
    <p v-if="error || worker?.error" role="alert" class="note danger">{{ error || worker.error }}</p>
    <p v-if="!session" class="muted">Chargement de la session…</p>
    <template v-else>
      <nav class="mc-tabs" aria-label="Rubriques Memecoin">
        <button v-for="name in ['Live','Positions','Radar','Stratégies']" :key="name" :aria-pressed="tab===name" @click="tab=name">{{ name }}</button>
      </nav>
      <div v-if="tab==='Live'" class="mc-live">
      <section class="card mc-head">
        <div><div class="muted">Valeur du sous-portefeuille</div><strong class="mc-equity">{{ money(session.equity, 'USD') }}</strong>
          <span v-if="session.equity != null" :class="cls(session.equity - session.initial_capital)">{{ signed(session.equity - session.initial_capital, '$') }} · {{ pct(session.equity / session.initial_capital - 1) }}</span>
          <span v-else>Valorisation en attente · P&amp;L aux derniers cours disponibles</span>
          <p class="muted">Session fictive indépendante du portefeuille principal · comptabilité en USD.</p>
          <div class="muted">Liquidités · {{ money(session.cash, '$') }}</div>
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
      </div>
      <section v-if="tab==='Positions'" class="mc-holdings">
        <h2>Actifs détenus</h2><p v-if="!holdings.length" class="card empty">Aucune position ouverte.</p>
        <article v-for="p in holdings" :key="p.strategy+p.mint" class="card mc-position">
          <header><div><small>{{ strategies[p.strategy] }}</small><h3>{{ tokenLabel(p.mint) }}</h3>
            <small v-if="tokenLabel(p.mint)!==p.mint" style="overflow-wrap:anywhere">Mint Solana · {{ p.mint }}</small></div>
            <b :class="cls(p.unrealized_pnl_pct)">{{ pct(p.unrealized_pnl_pct, 2, true) }}</b></header>
          <p class="muted">{{ quoteState(p) }} · Dernière valorisation {{ dt(p.marked_at) }}</p>
          <p>Durée de détention · {{ duration(p.opened_at, now) }}</p>
          <dl><div><dt>Entrée, frais inclus</dt><dd>{{ price(p.quantity > 0 ? p.cost_basis_usd / p.quantity : null) }}</dd></div>
            <div><dt>Dernier cours</dt><dd>{{ price(p.price_usd) }}</dd></div>
            <div><dt>Dernière valeur nette</dt><dd>{{ money(p.value_usd,'$') }}</dd></div>
            <div><dt>P&amp;L réalisé</dt><dd>{{ signed(p.realized_pnl_usd,'USD') }} · {{ eur(p.realized_eur) }}</dd></div>
            <div><dt>P&amp;L latent à la marque</dt><dd>{{ signed(p.unrealized_pnl_usd,'USD') }} · {{ eur(p.unrealized_eur) }}</dd></div></dl>
          <details v-if="p.realized_eur || p.unrealized_eur"><summary>Change du P&amp;L USD en EUR</summary>
            <small v-for="leg in [...(p.realized_eur?.legs||[]), ...(p.unrealized_eur?.legs||[])]" style="display:block;overflow-wrap:anywhere">
              {{ dt(leg.event_at) }} · {{ signed(leg.usd,'USD') }} · 1 USD = {{ leg.rate.toLocaleString('fr-FR',{maximumSignificantDigits:8}) }} EUR · {{ leg.source }} ({{ dt(leg.quote_at) }})</small></details>
          <p>{{ protectiveStop(p) }}</p>
          <p v-if="pendingExit(p)" class="warn">Sortie décidée · {{ reasons[pendingExit(p).detail] || 'motif inconnu' }} · exécution en attente depuis {{ dt(pendingExit(p).decided_at) }}</p>
          <div class="mc-ladder" aria-label="Paliers de prise de profit">
            <span v-for="tier in ladder(p)" :key="tier.level" :class="{reached:tier.observed}">
              ×{{ tier.level.toLocaleString('fr-FR') }} · {{ tier.label }}</span>
          </div>
        </article>
      </section>
      <section v-if="tab==='Stratégies'" class="card mc-performance"><h2>Performance &amp; Stratégies</h2>
        <p v-if="analyticsError" role="alert" class="note warn">{{ analyticsError }}</p>
        <p v-else-if="!analytics" class="muted">Chargement du bilan…</p>
        <p v-else-if="analytics.status === 'empty'" class="empty">Aucune session memecoin enregistrée.</p>
        <template v-else>
          <p v-if="analytics.status === 'stale'" class="note warn">Valorisation en attente · derniers cours disponibles.</p>
          <div class="mc-axis"><span>Taux de réussite global</span><b>{{ plainPct(analytics.win_rate) }}</b></div>
          <div class="mc-axis"><span>Profit factor</span><b>{{ analytics.profit_factor?.toLocaleString('fr-FR', {maximumFractionDigits: 2})
            ?? (analytics.gross_profit > 0 ? '∞ (aucune perte clôturée)' : '—') }}</b></div>
          <div class="mc-axis"><span>Drawdown maximal observé</span><b>{{ analytics.max_drawdown_pct?.toLocaleString('fr-FR', {maximumFractionDigits: 2}) }} %</b></div>
          <table class="mc-strategies"><thead><tr><th>Stratégie</th><th>Profit moyen (%)</th><th>Win rate</th><th>Profit factor</th><th>P&amp;L net</th></tr></thead>
            <tbody><tr v-for="(name,key) in strategies" :key="key"><th scope="row">{{ name }}</th>
              <td :class="cls(meanProfit(key))">{{ pct(meanProfit(key),2,true) }}</td><td>{{ plainPct(analytics.strategies[key].win_rate) }}</td>
              <td>{{ factor(key) }}</td><td :class="cls(analytics.strategies[key].pnl)">{{ signed(analytics.strategies[key].pnl,'$') }}</td></tr></tbody></table>
          <h3>Dernières positions clôturées</h3><p v-if="!lots.length" class="muted">Aucune clôture enregistrée.</p>
          <div v-for="(p,i) in lots" :key="i" class="mc-closed"><time>{{ p.closed_at ? dt(p.closed_at) : '—' }}</time>
            <span :title="p.mint">{{ strategies[p.strategy] }} · {{ tokenLabel(p.mint) }} · {{ duration(p.opened_at,p.closed_at) }}</span>
            <b :class="cls(p.pnl_usd)">Réalisé {{ signed(p.pnl_usd,'$') }} · {{ eur(p.realized_eur) }} · {{ pct(p.profit_pct,2,true) }}</b>
            <small>Sortie exécutée · {{ reasons[p.exit_reason] || p.exit_reason || 'motif inconnu' }}</small>
            <details v-if="p.realized_eur"><summary>Change des sorties</summary>
              <small v-for="leg in p.realized_eur.legs" style="display:block;overflow-wrap:anywhere">{{ dt(leg.event_at) }} · {{ signed(leg.usd,'USD') }} · 1 USD = {{ leg.rate.toLocaleString('fr-FR',{maximumSignificantDigits:8}) }} EUR · {{ leg.source }} ({{ dt(leg.quote_at) }})</small></details></div>
        </template>
      </section>
      <section v-if="tab==='Stratégies' && analytics" class="card mc-learning" aria-labelledby="mc-learning-heading">
        <h2 id="mc-learning-heading">Apprentissage &amp; Calibration</h2>
        <div class="mc-learning-grid">
          <article v-for="row in learningRows" :key="row.key" :data-strategy="row.key">
            <div class="mc-axis"><h3>{{ row.name }}</h3>
              <strong class="num" :class="{up: row.multiplier > 1, warn: row.multiplier < 1}" aria-label="Multiplicateur d’allocation">
                {{ decimal.format(row.multiplier) }}x</strong></div>
            <p>Score d’efficacité · <b>{{ row.score == null ? '—' : decimal.format(row.score) }}</b>
              <small v-if="row.window > 0" class="muted"> sur {{ row.window }} clôtures récentes</small></p>
            <template v-if="row.closed != null">
              <small>{{ row.closed }} clôtures · {{ row.unlocked ? 'hausse autorisée' : 'hausse à partir de ' + learning.min_trades + ' clôtures' }}</small>
              <progress :value="Math.min(row.closed, learning.min_trades)" :max="learning.min_trades" :aria-label="'Progression des clôtures · '+row.name"></progress>
            </template>
            <p class="muted">{{ row.reason || 'Données d’apprentissage indisponibles. Allocation neutre affichée.' }}</p>
          </article>
        </div>
        <h3>Recommandations de seuils d’entrée</h3>
        <small>Simulation uniquement — non appliqué automatiquement</small>
        <ul v-if="suggestions.length">
          <li v-for="r in suggestions" :key="r.feature"><b>{{ r.label }}</b> · {{ r.current_threshold.toLocaleString('fr-FR') }} → {{ r.suggested_threshold.toLocaleString('fr-FR') }}
            <p class="muted">{{ r.explanation }}</p></li>
        </ul>
        <p v-else-if="!learning" class="muted">Aucune donnée de calibration disponible.</p>
        <p v-else-if="learning.closed_trades < 20" class="muted">En attente de 20 clôtures avec conditions d’entrée enregistrées.</p>
        <p v-else class="muted">Aucun ajustement proposé sur les observations exploitables.</p>
      </section>
      <section v-if="tab==='Radar'" class="card mc-funnel"><div class="card-head"><h2>Pools établis &amp; flotte d'agents</h2><span class="spacer"></span><small>24 h</small></div>
        <p v-if="funnelError" class="note warn">{{ funnelError }}</p>
        <p v-if="!funnel && !funnelError" class="muted">Chargement de l'entonnoir…</p>
        <template v-if="funnel">
          <div class="mc-axis"><span>Pools vus</span><b>{{ funnel.tokens }}</b></div>
          <div v-for="(n, stage) in funnel.refused_by_stage" :key="stage" class="mc-axis"><span>Refusés · {{ stages[stage] || stage }}</span><b>{{ n }}</b></div>
          <div class="mc-axis"><span>Retenus</span><b>{{ funnel.accepted }}</b></div>
          <div class="mc-axis"><span>Liquidité médiane des pools vus</span><b>{{ money(funnel.median_liquidity_usd, 'USD') }}</b></div>
          <details class="mc-rules"><summary>Critères des pools établis</summary><small class="muted">Liquidité ≥ {{ money(funnel.thresholds.min_liquidity_usd, 'USD') }}, pool d'au moins une heure, dernière heure
            ≥ {{ money(funnel.thresholds.min_volume_h1_usd, 'USD') }} avec {{ funnel.thresholds.min_trades_h1 }} achats et ventes, aller-retour Jupiter
            ≤ {{ plainPct(funnel.thresholds.max_round_trip_loss) }}, puis veto anti-arnaque.</small></details>
          <h3>Candidats retenus</h3><p v-if="!funnel.candidates.length" class="muted">Aucun pool établi retenu sur la période.</p>
          <div v-for="c in funnel.candidates" :key="c.mint" class="mc-axis"><strong :title="c.mint">{{ c.name || c.mint }}</strong><small>{{ c.dex_id }}</small>
            <span>{{ money(c.liquidity_usd, 'USD') }} · {{ money(c.volume_h1_usd, 'USD') }} sur 1 h · aller-retour {{ plainPct(c.round_trip_loss) }}</span></div>
          <h3>Décisions de la flotte</h3><p v-if="!funnel.fleet.recent.length" class="muted">Aucune entrée examinée par la flotte.</p>
          <div v-for="(d, i) in funnel.fleet.recent" :key="i" class="mc-axis"><time>{{ dt(d.ts) }}</time><small :title="d.mint">{{ d.mint.slice(0, 6) }}…</small>
            <b>{{ decisions[d.decision] || d.decision }}</b><span>{{ d.why[0] }}</span></div>
        </template>
      </section>
      <section v-if="tab==='Radar'" class="card"><div class="card-head"><h2>Radar Solana &amp; Top Smart Wallets</h2>
        <span v-if="radar" class="spacer"></span><small v-if="radar">{{ dt(radar.observed_at) }}</small></div>
        <p v-if="radarError" class="note warn">{{ radarError }}</p>
        <p v-if="!radar && !radarError" class="muted">Collecte des observations publiques…</p>
        <template v-if="radar">
          <p v-if="radar.errors?.length" class="muted">Couverture publique partielle.</p>
          <div v-for="t in radar.tokens" :key="t.mint || 'demo'" class="mc-axis">
            <strong>{{ t.name ? t.name + ' · ' : '' }}{{ t.symbol }}</strong><small>{{ t.source === 'live' ? 'Public' : 'Exemple simulé' }}</small>
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
      <div v-if="tab==='Live'" class="mc-grid"><article v-for="(name,key) in strategies" :key="key" class="card" :class="{'mc-flash': flash === key}">
        <h2>{{ name }}</h2><strong :class="cls(session.strategies[key].pnl)">{{ signed(session.strategies[key].pnl,'USD') }}</strong>
        <div class="muted">{{ session.strategies[key].trades }} entrées · {{ session.tracked_wallet_count }} wallets qualifiés suivis (session)</div>
      </article></div>
      <div v-if="tab==='Live'" class="mc-grid"><section class="card"><h2>Activité récente</h2><p v-if="!activity.length" class="muted">Aucune exécution simulée.</p>
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
