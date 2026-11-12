// Performances : ce que le portefeuille a réellement rapporté (net de tous les frais), par trade et par jour.
import { api, on, safe } from '../api.js';
import { store, money, signed, cls, dt, rr, STRATEGY_LABELS } from '../store.js';
import { LineChart, BarChart } from '../charts.js';

const { ref, computed, onMounted, onBeforeUnmount, watch } = Vue;
const nf = (d) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
const PERIODS = [['today', 'Aujourd’hui'], ['7d', '7 jours'], ['30d', '30 jours'], ['90d', '90 jours'], ['all', 'Depuis le lancement']];

export default {
  components: { LineChart, BarChart },
  setup() {
    const accounts = computed(() => (store.overview?.accounts || []).filter(a => a.kind === 'paper'));
    const accountId = ref(null);
    const period = ref('all');
    const rep = ref(null);
    const loading = ref(false);
    const load = async () => {
      loading.value = true;
      const q = `period=${period.value}` + (accountId.value !== null ? `&account_id=${accountId.value}` : '');
      const r = await safe(api.get('/api/performance?' + q));
      if (r) { rep.value = r; if (accountId.value === null) accountId.value = r.account.id; }
      loading.value = false;
    };
    onMounted(load);
    watch([period, accountId], (now, before) => { if (before[1] !== null || now[0] !== before[0]) load(); });
    const offs = [on('trade_closed', load), on('position_opened', load)];
    const timer = setInterval(load, 60000);
    onBeforeUnmount(() => { offs.forEach(f => f()); clearInterval(timer); });

    const cur = computed(() => rep.value?.account?.currency || '');
    const d = computed(() => rep.value?.detail);
    const s = computed(() => d.value?.summary);
    const t = computed(() => s.value?.trades || {});
    // pourcentage : deux décimales, trois quand la valeur est minuscule (rendements quotidiens d'un petit portefeuille)
    const pc = (x, digits) => (x === null || x === undefined) ? '—' : (x > 0 ? '+' : '') + nf(digits ?? (Math.abs(x) < 0.001 && x ? 3 : 2)).format(x * 100) + '\u00a0%';
    const plain = (x, digits = 0) => (x === null || x === undefined) ? '—' : nf(digits).format(x * 100) + '\u00a0%';
    const hours = (h) => (h === null || h === undefined) ? '—' : h < 48 ? nf(1).format(h) + ' h' : nf(1).format(h / 24) + ' j';
    const equitySeries = computed(() => d.value ? [{ data: d.value.equity, color: '#3b82f6' }] : []);
    const ddSeries = computed(() => d.value ? [{ data: d.value.drawdown, color: '#f43f5e' }] : []);
    const dailyBars = computed(() => (d.value?.daily || []).map(x => ({ value: x.ret * 100, muted: !x.observed,
      title: x.day + (x.observed ? '' : ' (aucun relevé)') + ' — ' + x.trades + ' trade(s)' })));
    const tradeBars = computed(() => (d.value?.trades || []).filter(x => x.ret_position !== null).map(x => ({ value: x.ret_position * 100,
      title: dt(x.exit_ts) + ' — ' + x.symbol + ' (' + (STRATEGY_LABELS[x.strategy] || (x.origin === 'manual' ? 'manuel' : x.strategy || '')) + ')' })));
    const strategyBars = computed(() => (d.value?.by_strategy || []).map(g => ({ value: g.pnl, title: g.label + ' — ' + g.n + ' trade(s)' })));
    const modelBars = computed(() => (d.value?.by_model || []).filter(g => g.mean_r !== null).map(g => ({ value: g.mean_r, title: g.label + ' — ' + g.n + ' trade(s)' })));
    const TABLES = [['by_strategy', 'Par stratégie'], ['by_symbol', 'Par crypto'], ['by_origin', 'Par origine'], ['by_regime', 'Par climat de marché à l’entrée'], ['by_horizon', 'Par durée de détention']];
    const gapOk = computed(() => Math.abs(rep.value?.result?.check_gap ?? 0) < 0.005);
    const priceSource = computed(() => {
      const src = store.overview?.settings_summary?.source;
      if (!src) return 'source inconnue';
      return src === 'synthetic' ? 'marché simulé (démonstration)' : `${src}, données publiques réelles`;
    });
    return { store, accounts, accountId, period, rep, loading, cur, d, s, t, pc, plain, hours, equitySeries, ddSeries, dailyBars, tradeBars,
      strategyBars, modelBars, TABLES, PERIODS, gapOk, priceSource, money, signed, cls, dt, rr, nf };
  },
  template: `
  <div>
    <div class="page-head"><h1>Performances</h1><span class="spacer"></span>
      <select v-if="accounts.length > 1" v-model="accountId" style="width:auto"><option v-for="a in accounts" :key="a.id" :value="a.id">{{ a.name }}</option></select>
      <div class="seg"><button v-for="p in PERIODS" :key="p[0]" :class="{on: period===p[0]}" @click="period=p[0]">{{ p[1] }}</button></div>
    </div>
    <div v-if="!rep" class="card empty">{{ loading ? 'Chargement…' : 'Aucune donnée.' }}</div>
    <template v-else>
      <div v-for="c in rep.caution" :key="c" class="note small" :class="c.startsWith('Marché simulé') ? 'danger' : c.startsWith('Argent fictif') ? 'info' : 'warn'" style="margin-bottom:8px">{{ c }}</div>
      <p class="faint small" data-nature style="margin:4px 0 0">Nature des chiffres : {{ rep.account.name }}, argent fictif ({{ money(rep.capital.initial, cur) }} au départ) ·
        prix : {{ priceSource }} · ce ne sont ni des simulations sur l’historique (page IA) ni l’observation prospective gelée (page Préparation).</p>

      <div class="card" style="margin:14px 0">
        <div class="card-head"><h2>{{ rep.account.name }}</h2><span class="badge paper">ARGENT FICTIF</span><span class="spacer"></span>
          <span class="badge" :class="gapOk ? 'ok' : 'err'" :title="'Capital − départ = réalisé + latent + frais de sortie estimés. Écart : ' + nf(4).format(rep.result.check_gap) + ' ' + cur">{{ gapOk ? 'comptes vérifiés' : 'écart comptable' }}</span></div>
        <div class="kpi-grid">
          <div class="stat"><span class="label">Capital de départ</span><span class="value">{{ money(rep.capital.initial, cur) }}</span><span class="sub muted">lancé le {{ dt(rep.launch_ts) }}</span></div>
          <div class="stat"><span class="label">Capital actuel</span><span class="value">{{ money(rep.capital.current, cur) }}</span>
            <span class="sub muted" v-if="rep.capital.available !== null">disponible {{ money(rep.capital.available, cur) }} · investi {{ money(rep.capital.invested, cur) }}</span></div>
          <div class="stat"><span class="label">Résultat net depuis le lancement</span><span class="value" :class="cls(rep.result.net)">{{ signed(rep.result.net, cur) }}</span><span class="sub" :class="cls(rep.result.net)">{{ pc(rep.result.net_pct) }}</span></div>
          <div class="stat"><span class="label">dont réalisé (ventes exécutées)</span><span class="value" :class="cls(rep.result.realized)">{{ signed(rep.result.realized, cur) }}</span></div>
          <div class="stat"><span class="label">dont latent (positions ouvertes)</span><span class="value" :class="cls(rep.result.latent)">{{ signed(rep.result.latent, cur) }}</span><span class="sub muted">{{ rep.open_positions.length }} position(s), au dernier prix</span></div>
          <div class="stat"><span class="label">Frais cumulés</span><span class="value">{{ money(rep.result.fees, cur) }}</span><span class="sub muted">plateforme et conversion, déjà déduits</span></div>
        </div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <p class="muted">Validation scientifique : <b>{{ rep.validation.verdict }}</b>. Historique : {{ rep.model_history.length }} modèle(s). Une comparaison entre trades de versions différentes ne prouve pas leur supériorité.</p>
        <h2>Par période</h2>
        <div class="table-wrap"><table>
          <thead><tr><th>Période</th><th class="num">Résultat net</th><th class="num">Rendement</th><th class="num">Trades</th><th class="num">Gagnants</th><th class="num">Baisse max.</th><th class="num">Jours (avec trade)</th><th class="num">Rendement quotidien moyen (géométrique)</th></tr></thead>
          <tbody><tr v-for="p in PERIODS" :key="p[0]" :style="period===p[0] ? 'background:var(--panel-2)' : ''" @click="period=p[0]" style="cursor:pointer">
            <td><b>{{ p[1] }}</b></td>
            <td class="num" :class="cls(rep.periods[p[0]].net)">{{ signed(rep.periods[p[0]].net, cur) }}</td>
            <td class="num" :class="cls(rep.periods[p[0]].net)">{{ pc(rep.periods[p[0]].net_pct) }}</td>
            <td class="num">{{ rep.periods[p[0]].trades.n }}</td>
            <td class="num">{{ plain(rep.periods[p[0]].trades.win_rate) }}</td>
            <td class="num">{{ plain(rep.periods[p[0]].max_drawdown, 2) }}</td>
            <td class="num">{{ rep.periods[p[0]].days }} ({{ rep.periods[p[0]].days_with_trades }})</td>
            <td class="num" :class="cls(rep.periods[p[0]].geo_daily_return)">{{ pc(rep.periods[p[0]].geo_daily_return, 3) }}</td>
          </tr></tbody></table></div>
        <p class="faint small" style="margin-bottom:0">Le résultat d'une période compte tout : trades clôturés et variation des positions encore ouvertes. Les jours sans aucun trade sont comptés dans les rendements quotidiens.</p>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h2>Trades clôturés — {{ s.label.toLowerCase() }}</h2>
        <div class="kpi-grid">
          <div class="stat"><span class="label">Nombre de trades</span><span class="value">{{ t.n }}</span><span class="sub muted">{{ t.wins }} gagnant(s), {{ t.losses }} perdant(s)</span></div>
          <div class="stat"><span class="label">Trades gagnants</span><span class="value">{{ plain(t.win_rate) }}</span></div>
          <div class="stat"><span class="label">Gain moyen / perte moyenne</span><span class="value small"><span class="up">{{ signed(t.avg_win, cur) }}</span> / <span class="down">{{ signed(t.avg_loss, cur) }}</span></span></div>
          <div class="stat"><span class="label">Espérance nette par trade</span><span class="value" :class="cls(t.expectancy)">{{ signed(t.expectancy, cur) }}</span>
            <span class="sub muted" v-if="t.mean_r !== null">{{ rr(t.mean_r) }}<span v-if="t.ci_low !== null"> (95 % : {{ rr(t.ci_low) }} à {{ rr(t.ci_high) }})</span></span></div>
          <div class="stat"><span class="label">Profit factor</span><span class="value">{{ t.profit_factor ? nf(2).format(t.profit_factor) : '—' }}</span><span class="sub muted">gains ÷ pertes</span></div>
          <div class="stat"><span class="label">Rendement net par trade</span><span class="value" :class="cls(t.mean_ret_position)">{{ pc(t.mean_ret_position) }}</span><span class="sub muted">sur le montant engagé ; médiane {{ pc(t.median_ret_position) }}</span></div>
          <div class="stat"><span class="label">Contribution moyenne d'un trade</span><span class="value" :class="cls(t.mean_contribution)">{{ pc(t.mean_contribution, 3) }}</span><span class="sub muted">du portefeuille</span></div>
          <div class="stat"><span class="label">Durée moyenne d'une position</span><span class="value">{{ hours(t.avg_duration_h) }}</span></div>
          <div class="stat"><span class="label">Baisse maximale</span><span class="value">{{ plain(s.max_drawdown, 2) }}</span><span class="sub muted">depuis un plus haut</span></div>
          <div class="stat"><span class="label">Exposition maximale</span><span class="value">{{ plain(s.max_exposure) }}</span><span class="sub muted">part du capital investie</span></div>
          <div class="stat"><span class="label">Rendement quotidien</span><span class="value" :class="cls(s.geo_daily_return)">{{ pc(s.geo_daily_return, 3) }}</span><span class="sub muted">moyenne géométrique sur {{ s.days }} jour(s)</span></div>
          <div class="stat"><span class="label">Jours en hausse / en baisse</span><span class="value small">{{ s.positive_days }} / {{ s.negative_days }}</span><span class="sub muted">meilleur {{ pc(s.best_day) }}, pire {{ pc(s.worst_day) }}</span></div>
        </div>
        <p v-if="!t.enough" class="note warn small" style="margin:12px 0 0">Moins de {{ rep.min_trades }} trades : ces moyennes ne prouvent encore rien. Regardez surtout l'intervalle à 95 % : s'il contient zéro, le résultat peut venir du hasard.</p>
      </div>

      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card"><h2>Capital dans le temps</h2>
          <LineChart v-if="equitySeries.length && equitySeries[0].data.length > 1" :series="equitySeries" height="small" />
          <div v-else class="empty">La courbe apparaîtra après quelques relevés.</div></div>
        <div class="card"><h2>Baisse depuis le plus haut</h2>
          <LineChart v-if="ddSeries.length && ddSeries[0].data.length > 1" :series="ddSeries" height="small" :area="false" />
          <div v-else class="empty">Pas encore de données.</div>
          <p class="faint small" style="margin-bottom:0">En pourcentage du plus haut atteint sur la période. Zéro = au plus haut.</p></div>
      </div>
      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card"><h2>Rendement de chaque jour</h2>
          <BarChart v-if="dailyBars.length" :bars="dailyBars" unit="%" :digits="2" />
          <p class="faint small" style="margin-bottom:0">Une barre par jour, <b>y compris les jours sans trade</b> (barre absente : 0 %). Barres claires : jours sans relevé (Sentinelle éteint), capital supposé inchangé. Jours en temps universel.</p></div>
        <div class="card"><h2>Rendement net de chaque trade</h2>
          <BarChart v-if="tradeBars.length" :bars="tradeBars" unit="%" :digits="2" />
          <div v-else class="empty">Aucun trade clôturé sur la période.</div>
          <p class="faint small" style="margin-bottom:0">Résultat net du trade divisé par le montant engagé, frais et conversion compris. Du plus ancien au plus récent.</p></div>
      </div>
      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card"><h2>Résultat par stratégie</h2>
          <BarChart v-if="strategyBars.length" :bars="strategyBars" :unit="cur === 'EUR' ? '€' : cur" :digits="2" :height="120" />
          <div v-else class="empty">Aucun trade clôturé sur la période.</div></div>
        <div class="card"><h2>Versions successives du modèle</h2>
          <BarChart v-if="modelBars.length" :bars="modelBars" unit="R" :digits="2" :height="120" />
          <div class="table-wrap" v-if="d.by_model.length"><table>
            <thead><tr><th>Modèle</th><th class="num">Trades</th><th class="num">Gagnants</th><th class="num">Moyenne</th><th class="num">Résultat</th><th>Période</th></tr></thead>
            <tbody><tr v-for="m in d.by_model" :key="m.label"><td>{{ m.label }}<span v-if="m.modes.includes('essai')" class="badge warn" style="margin-left:6px">à l'essai</span></td>
              <td class="num">{{ m.n }}</td><td class="num">{{ plain(m.win_rate) }}</td>
              <td class="num" :class="cls(m.mean_r)">{{ rr(m.mean_r) }}<div class="faint small" v-if="m.ci_low !== null">{{ rr(m.ci_low) }} à {{ rr(m.ci_high) }}</div></td>
              <td class="num" :class="cls(m.pnl)">{{ signed(m.pnl, cur) }}</td><td class="small muted">{{ dt(m.first_ts) }} → {{ dt(m.last_ts) }}</td></tr></tbody></table></div>
          <div v-else class="empty">Aucun trade de l'IA sur la période.</div>
          <p class="faint small" style="margin-bottom:0">Trades de l'IA regroupés par version du modèle, dans l'ordre d'apparition. Avec peu de trades par version, la comparaison n'est pas concluante.</p></div>
      </div>

      <div class="grid cols-2" style="margin-bottom:14px">
        <div v-for="tb in TABLES" :key="tb[0]" class="card">
          <h2>{{ tb[1] }}</h2>
          <div class="table-wrap"><table>
            <thead><tr><th></th><th class="num">Trades</th><th class="num">Gagnants</th><th class="num">Moyenne</th><th class="num">Rendement par trade</th><th class="num">Résultat</th></tr></thead>
            <tbody><tr v-for="g in d[tb[0]]" :key="String(g.key)"><td>{{ g.label }}</td><td class="num">{{ g.n }}</td><td class="num">{{ plain(g.win_rate) }}</td>
              <td class="num" :class="cls(g.mean_r)">{{ rr(g.mean_r) }}</td><td class="num" :class="cls(g.mean_ret_position)">{{ pc(g.mean_ret_position) }}</td>
              <td class="num" :class="cls(g.pnl)">{{ signed(g.pnl, cur) }}</td></tr>
              <tr v-if="!d[tb[0]].length"><td colspan="6" class="empty">Aucun trade clôturé sur la période.</td></tr></tbody></table></div>
        </div>
        <div class="card">
          <h2>Signaux refusés par l'IA</h2>
          <p class="small" style="margin-top:0">Sur la période, l'IA a examiné <b>{{ d.refusals.seen }}</b> signal(aux) : <b>{{ d.refusals.taken }}</b> pris, <b>{{ d.refusals.refused }}</b> refusé(s).</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Raison du refus</th><th class="num">Nombre</th><th class="num">Part</th><th class="num">Ce qu'ils auraient donné</th></tr></thead>
            <tbody><tr v-for="g in d.refusals.reasons" :key="g.label"><td>{{ g.label }}</td><td class="num">{{ g.n }}</td><td class="num">{{ plain(g.share) }}</td>
              <td class="num" :class="cls(g.mean_r)">{{ rr(g.mean_r) }}<span class="faint small" v-if="g.n_resolved"> ({{ g.n_resolved }} dénoué(s))</span></td></tr>
              <tr v-if="!d.refusals.reasons.length"><td colspan="4" class="empty">Aucun signal refusé sur la période.</td></tr></tbody></table></div>
          <p class="small" style="margin-bottom:0" v-if="d.refusals.refused_outcome.n_resolved || d.refusals.taken_outcome.n_resolved">
            Signaux pris : {{ rr(d.refusals.taken_outcome.mean_r) }} en moyenne ({{ d.refusals.taken_outcome.n_resolved }} dénoué(s)).
            Signaux refusés : {{ rr(d.refusals.refused_outcome.mean_r) }} en moyenne ({{ d.refusals.refused_outcome.n_resolved }} dénoué(s)).
            <span class="faint">Chaque signal refusé est suivi jusqu'à son issue : on vérifie ainsi si l'IA a eu raison de refuser.</span></p>
        </div>
      </div>

      <div class="card">
        <h2>Comment lire ces chiffres</h2>
        <ul class="small" style="margin:0 0 0 18px;padding:0;color:var(--muted)">
          <li><b>Tout est net</b> : frais de la plateforme, glissement de prix et conversion en euros sont déjà déduits.</li>
          <li><b>Réalisé</b> : trades clôturés. <b>Latent</b> : positions encore ouvertes, au dernier prix connu, frais de sortie estimés déduits.</li>
          <li><b>R</b> : multiple du risque pris. +1 R = on a gagné ce qu'on risquait de perdre ; −1 R = le stop a été touché.</li>
          <li><b>Rendement géométrique</b> : le rendement constant par jour qui aurait donné le même capital. Il n'est pas « annualisé » : aucune projection n'est faite.</li>
          <li>Aucun de ces chiffres ne prédit l'avenir, et l'argent est fictif.</li>
        </ul>
      </div>
    </template>
  </div>`,
};
