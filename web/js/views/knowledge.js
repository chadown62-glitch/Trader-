// Fiches cryptos : ce que Sentinelle sait de chaque crypto suivie (historique complet, risques, lien au Bitcoin, stratégies)
import { api, on, safe } from '../api.js';
import { store, money, dt, ago, date, STRATEGY_LABELS } from '../store.js';

const { ref, computed, onMounted, onBeforeUnmount } = Vue;

const nf = (min, max) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: min, maximumFractionDigits: max });
const pc = (x, d = 1) => (x === null || x === undefined) ? '—' : nf(d, d).format(x * 100) + ' %';
const spc = (x, d = 1) => (x === null || x === undefined) ? '—' : (x > 0 ? '+' : '') + nf(d, d).format(x * 100) + ' %';
const num = (x, d = 2) => (x === null || x === undefined) ? '—' : nf(d, d).format(x);
const sr = (x) => (x === null || x === undefined) ? '—' : (x > 0 ? '+' : '') + nf(2, 2).format(x) + ' R';
const big = (x) => {
  if (x === null || x === undefined) return '—';
  if (x >= 1e9) return nf(1, 1).format(x / 1e9) + ' Md';
  if (x >= 1e6) return nf(1, 1).format(x / 1e6) + ' M';
  if (x >= 1e3) return nf(0, 0).format(x / 1e3) + ' k';
  return nf(0, 0).format(x);
};

export default {
  setup() {
    const list = ref(null);
    const selected = ref(null);
    const detail = ref(null);
    const job = computed(() => Object.values(store.jobs).filter(j => j.kind === 'knowledge').sort((a, b) => b.started_at - a.started_at)[0]);
    const running = computed(() => job.value?.status === 'running');
    const load = async () => {
      list.value = await safe(api.get('/api/knowledge'));
      if (!selected.value && list.value?.items?.length) open(list.value.items[0].symbol);
      else if (selected.value) open(selected.value);
    };
    const open = async (symbol) => {
      selected.value = symbol;
      detail.value = await safe(api.get('/api/knowledge/detail?symbol=' + encodeURIComponent(symbol)));
    };
    const refresh = async () => { await safe(api.post('/api/knowledge/refresh'), 'Mise à jour des fiches lancée (téléchargement de l’historique complet)'); };
    const offs = [on('job', (j) => { if ((j.kind === 'knowledge' || j.kind === 'train') && j.status === 'done') load(); })];
    onMounted(load);
    onBeforeUnmount(() => offs.forEach(f => f()));
    const quote = computed(() => store.settings?.general?.quote || '');
    const missing = computed(() => (list.value?.items || []).some(i => i.missing));
    const strategies = computed(() => {
      const s = detail.value?.strategies || {};
      const en = detail.value?.enabled_strategies || [];
      return Object.entries(s).map(([k, v]) => ({ key: k, ...v, enabled: en.includes(k) })).sort((a, b) => (b.enabled - a.enabled) || ((b.mean_r ?? -9) - (a.mean_r ?? -9)));
    });
    const verdict = (s) => s.n < 20 ? ['trop peu de cas', ''] : s.proven ? ['avantage net', 'ok'] : (s.ci_high !== null && s.ci_high < 0) ? ['perdante', 'err'] : ['rien de prouvé', 'warn'];
    const regimeCls = (r) => r === 'bull' ? 'ok' : r === 'bear' ? 'err' : '';
    return { store, list, selected, detail, job, running, open, refresh, quote, missing, strategies, verdict, regimeCls,
      pc, spc, num, sr, big, money, dt, ago, date, STRATEGY_LABELS };
  },
  template: `
  <div>
    <div class="page-head"><h1>Fiches cryptos</h1><span class="spacer"></span>
      <span v-if="list?.updated" class="faint small hide-mobile">mises à jour {{ ago(list.updated) }}</span>
      <button :disabled="running" @click="refresh">↻ Mettre à jour</button></div>
    <div class="note info small" style="margin-bottom:14px">
      Tout ce que Sentinelle sait de chaque crypto suivie, calculé sur <b>tout son historique</b> : ampleur normale de ses mouvements, pires chutes, lien avec le Bitcoin,
      liquidité, et résultats passés de chaque stratégie, frais compris. Ces fiches servent à <b>comprendre et doser le risque</b>.
      Elles ne servent pas à choisir les trades : il a été mesuré que les résultats passés d'une stratégie sur une crypto ne prédisent presque rien de ses résultats futurs.
    </div>
    <div v-if="job && job.status !== 'done'" class="card tight" style="margin-bottom:14px">
      <div class="small muted">{{ job.status==='running' ? job.message : 'Échec : ' + job.error }}</div>
      <div class="progress" style="margin-top:6px"><div :style="{width: (job.progress*100)+'%'}"></div></div>
    </div>
    <div v-if="!list" class="card empty">Chargement…</div>
    <template v-else>
      <div v-if="missing && !running" class="note warn small" style="margin-bottom:14px">Certaines fiches n'ont pas encore été calculées. Cliquez sur « Mettre à jour » (une à deux minutes la première fois), ou lancez un entraînement de l'IA.</div>
      <div class="card" style="margin-bottom:14px">
        <div class="table-wrap"><table>
          <thead><tr><th>Crypto</th><th>Nature</th><th class="num">Mouvement d'un jour normal</th><th class="num">Pire baisse historique</th><th class="num">Lien au Bitcoin</th><th>Climat</th>
            <th class="num">Volume par jour</th><th class="num">Tendance (historique)</th></tr></thead>
          <tbody><tr v-for="i in list.items" :key="i.symbol" style="cursor:pointer" :style="i.symbol===selected ? 'background:rgba(59,130,246,.08)' : ''" @click="open(i.symbol)">
            <td><b>{{ i.symbol.split('/')[0] }}</b> <span class="muted small">{{ i.name }}</span></td>
            <td class="small">{{ i.kind }}</td>
            <template v-if="i.missing || i.insufficient"><td colspan="6" class="muted small">{{ i.missing ? 'fiche à calculer' : 'historique trop court' }}</td></template>
            <template v-else>
              <td class="num">{{ pc(i.typical_day) }}</td><td class="num down">{{ spc(i.max_drawdown, 0) }}</td>
              <td class="num">{{ i.corr_btc === null || i.corr_btc === undefined ? '—' : num(i.corr_btc) + ' (× ' + num(i.beta_btc) + ')' }}</td>
              <td><span class="badge" :class="regimeCls(i.regime)">{{ i.regime_label }}</span></td>
              <td class="num">{{ big(i.daily_volume) }} {{ quote }}</td>
              <td class="num" :class="(i.trend?.trend_follow?.mean_r || 0) > 0 ? 'up' : 'down'">{{ sr(i.trend?.trend_follow?.mean_r) }} <span class="faint small">({{ i.trend?.trend_follow?.n || 0 }})</span></td>
            </template>
          </tr></tbody>
        </table></div>
        <p class="faint small" style="margin-bottom:0">Lien au Bitcoin : corrélation sur un an, et entre parenthèses l'ampleur des mouvements par rapport à ceux du Bitcoin. Tendance : résultat moyen par trade du « suivi de tendance » sur tout l'historique (nombre de trades).</p>
      </div>

      <template v-if="detail && !detail.missing && !detail.insufficient">
        <div class="grid cols-2" style="margin-bottom:14px">
          <div class="card">
            <div class="card-head"><h2>{{ detail.info.name }} <span class="muted">({{ detail.base }})</span></h2><span class="spacer"></span><span class="badge">{{ detail.info.kind }}</span></div>
            <p class="small">{{ detail.info.what }}</p>
            <dl class="kv small">
              <dt>Ce qui fait bouger son prix</dt><dd>{{ detail.info.drivers }}</dd>
              <dt>Risques propres</dt><dd>{{ detail.info.risks }}</dd>
            </dl>
            <p class="faint small" style="margin-bottom:0">Informations générales arrêtées {{ detail.knowledge_date }} ; elles ne remplacent pas l'actualité.</p>
          </div>
          <div class="card">
            <h2>Lecture des chiffres</h2>
            <ul class="small" style="margin:0 0 0 18px;padding:0"><li v-for="(l, k) in detail.reading" :key="k" style="margin-bottom:4px">{{ l }}</li></ul>
          </div>
        </div>
        <div class="grid cols-3" style="margin-bottom:14px">
          <div class="card">
            <h3>Volatilité et risques extrêmes</h3>
            <dl class="kv small">
              <dt>Jour normal</dt><dd>± {{ pc(detail.volatility.typical_day) }}</dd>
              <dt>Bougie normale ({{ detail.timeframe }})</dt><dd>{{ pc(detail.volatility.typical_candle) }}</dd>
              <dt>Volatilité annuelle</dt><dd>{{ pc(detail.volatility.annual, 0) }} (90 derniers jours : {{ pc(detail.volatility.recent_90d, 0) }})</dd>
              <dt>Pire journée</dt><dd class="down">{{ spc(detail.extremes.worst_day.return) }} <span class="faint">le {{ date(detail.extremes.worst_day.ts / 1000) }}</span></dd>
              <dt>Pire semaine</dt><dd class="down">{{ spc(detail.extremes.worst_week?.return) }}</dd>
              <dt>Meilleure journée</dt><dd class="up">{{ spc(detail.extremes.best_day.return) }}</dd>
              <dt>Journées à −10 % ou pire</dt><dd>{{ detail.extremes.days_down_10 }} ({{ num(detail.extremes.down_10_per_year, 1) }} par an)</dd>
              <dt>Plus forte baisse</dt><dd class="down">{{ spc(detail.extremes.max_drawdown.depth, 0) }}</dd>
              <dt>Depuis son plus haut</dt><dd>{{ spc(detail.price.from_ath, 0) }} <span class="faint">(plus haut le {{ date(detail.price.ath_ts / 1000) }})</span></dd>
              <dt v-if="detail.volatility_rank">Rang de volatilité</dt><dd v-if="detail.volatility_rank">{{ detail.volatility_rank.rank }}ᵉ sur {{ detail.volatility_rank.of }} cryptos suivies</dd>
            </dl>
          </div>
          <div class="card">
            <h3>Marché et liquidité</h3>
            <dl class="kv small">
              <dt>Historique analysé</dt><dd>{{ num(detail.history.years, 1) }} ans, depuis le {{ date(detail.history.first / 1000) }}</dd>
              <dt>Climat actuel</dt><dd><span class="badge" :class="regimeCls(detail.regime.current)">{{ detail.regime.current_label }}</span> depuis {{ num(detail.regime.days_in_current, 0) }} j</dd>
              <dt>Temps passé en climat haussier</dt><dd>{{ pc(detail.regime.share?.bull, 0) }} (baissier : {{ pc(detail.regime.share?.bear, 0) }})</dd>
              <template v-if="detail.btc">
                <dt>Corrélation au Bitcoin</dt><dd>{{ num(detail.btc.corr_1y) }} sur un an ({{ num(detail.btc.corr_all) }} sur tout l'historique)</dd>
                <dt>Quand le Bitcoin perd 5 % en un jour</dt><dd class="down">{{ spc(detail.btc.when_btc_drops_5?.mean) }} en moyenne</dd>
                <dt>Quand il gagne 5 %</dt><dd class="up">{{ spc(detail.btc.when_btc_rises_5?.mean) }} en moyenne</dd>
              </template>
              <dt>Volume par jour (30 j)</dt><dd>{{ big(detail.liquidity.daily_volume_30d) }} {{ detail.quote }}<span v-if="detail.liquidity_rank" class="faint"> ({{ detail.liquidity_rank.rank }}ᵉ sur {{ detail.liquidity_rank.of }})</span></dd>
              <template v-if="detail.book"><dt>Écart achat/vente</dt><dd>{{ num(detail.book.spread_bps / 100, 3) }} % ; {{ big(detail.book.depth_quote) }} {{ detail.quote }} disponibles à moins de 0,5 %</dd></template>
              <template v-if="detail.rules"><dt>Ordre minimal</dt><dd>{{ detail.rules.min_notional }} {{ detail.quote }} ; frais {{ pc(detail.rules.taker_fee, 2) }} par ordre</dd></template>
            </dl>
          </div>
          <div class="card">
            <h3>Rendement par année</h3>
            <div class="table-wrap"><table><tbody>
              <tr v-for="y in detail.price.by_year" :key="y.year"><td>{{ y.year }}</td><td class="num" :class="y.return > 0 ? 'up' : 'down'">{{ spc(y.return, 0) }}</td></tr>
            </tbody></table></div>
            <p class="faint small" style="margin-bottom:0">Depuis le début de l'historique : {{ spc(detail.price.total_return, 0) }}{{ detail.price.cagr !== null ? ' (' + spc(detail.price.cagr, 0) + ' par an)' : '' }}. Première et dernière années incomplètes.</p>
          </div>
        </div>
        <div class="card">
          <h2>Stratégies de l'IA sur {{ detail.base }} (tout l'historique, frais compris)</h2>
          <div class="table-wrap"><table>
            <thead><tr><th>Stratégie</th><th class="num">Trades</th><th class="num">Gagnants</th><th class="num">Résultat moyen</th><th class="num">Fourchette probable</th><th class="num">Deux dernières années</th><th class="num">Durée moyenne</th><th>Lecture</th></tr></thead>
            <tbody><tr v-for="s in strategies" :key="s.key">
              <td>{{ s.label }} <span v-if="s.enabled" class="badge ok">activée</span></td><td class="num">{{ s.n }}</td><td class="num">{{ pc(s.win_rate, 0) }}</td>
              <td class="num" :class="(s.mean_r || 0) > 0 ? 'up' : 'down'">{{ sr(s.mean_r) }}</td>
              <td class="num small">{{ s.ci_low === null ? '—' : sr(s.ci_low) + ' à ' + sr(s.ci_high) }}</td>
              <td class="num">{{ sr(s.recent_mean_r) }} <span class="faint small">({{ s.recent_n }})</span></td><td class="num">{{ num(s.hold_days, 1) }} j</td>
              <td><span class="badge" :class="verdict(s)[1]">{{ verdict(s)[0] }}</span></td>
            </tr></tbody>
          </table></div>
          <p class="faint small" style="margin-bottom:0">R = multiple du risque pris (−1 R : le stop a été touché). « Avantage net » : la fourchette probable (95 %) est entièrement positive.
            Un bon résultat passé sur une crypto ne garantit rien : mesuré sur 33 cryptos, il ne prédit presque rien du résultat suivant.</p>
        </div>
      </template>
      <div v-else-if="detail && detail.missing" class="card">
        <h2>{{ detail.info.name }}</h2><p class="small">{{ detail.info.what }}</p>
        <p class="muted small">Statistiques pas encore calculées : cliquez sur « Mettre à jour ».</p>
      </div>
    </template>
  </div>`,
};
