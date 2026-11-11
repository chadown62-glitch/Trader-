// Historique des trades + statistiques + export CSV (déclaration fiscale)
import { api, safe } from '../api.js';
import { store, money, signed, price, qty, plainPct, rr, cls, dt, duration, STRATEGY_LABELS } from '../store.js';

const { ref, computed, onMounted, watch } = Vue;

export default {
  setup() {
    const accounts = computed(() => store.overview?.accounts || []);
    const accountId = ref(null);
    const origin = ref('');
    const trades = ref([]);
    const stats = ref(null);
    const open = ref(null);
    const load = async () => {
      if (accountId.value === null && accounts.value.length) accountId.value = accounts.value[0].id;
      if (accountId.value === null) return;
      trades.value = (await safe(api.get(`/api/trades?account_id=${accountId.value}&limit=1000${origin.value ? '&origin=' + origin.value : ''}`))) || [];
      stats.value = await safe(api.get(`/api/stats?account_id=${accountId.value}`));
    };
    onMounted(load);
    watch([accountId, origin], load);
    watch(accounts, () => { if (accountId.value === null) load(); });
    const csvUrl = computed(() => `/api/trades.csv?account_id=${accountId.value ?? ''}`);
    const fiscalYear = ref('');
    const fiscalUrl = computed(() => `/api/fiscal.csv?account_id=${accountId.value ?? ''}${fiscalYear.value ? '&year=' + fiscalYear.value : ''}`);
    const fiscal = ref([]);
    const loadFiscal = async () => { if (accountId.value !== null) fiscal.value = (await safe(api.get(`/api/fiscal/summary?account_id=${accountId.value}`))) || []; };
    watch(accountId, loadFiscal);
    onMounted(loadFiscal);
    const s = computed(() => (origin.value === 'ai' ? stats.value?.ai : origin.value === 'manual' ? stats.value?.manual : stats.value?.all) || {});
    return { accounts, accountId, origin, trades, stats, s, open, csvUrl, fiscalYear, fiscalUrl, fiscal, money, signed, price, qty, plainPct, rr, cls, dt, duration, STRATEGY_LABELS };
  },
  template: `
  <div>
    <div class="page-head"><h1>Historique</h1><span class="spacer"></span>
      <select v-model="accountId" style="width:auto"><option v-for="a in accounts" :key="a.id" :value="a.id">{{ a.name }}{{ a.kind==='paper' ? '' : ' (ancien compte réel)' }}</option></select>
      <div class="seg"><button :class="{on: origin===''}" @click="origin=''">Tous</button><button :class="{on: origin==='ai'}" @click="origin='ai'">IA</button><button :class="{on: origin==='manual'}" @click="origin='manual'">Manuels</button></div>
      <a class="btn" :href="csvUrl">⤓ Export CSV</a>
    </div>
    <div class="card" style="margin-bottom:14px">
      <div class="stats-row">
        <div class="stat"><span class="label">Trades</span><span class="value">{{ s.n ?? 0 }}</span></div>
        <div class="stat"><span class="label">Taux de réussite</span><span class="value">{{ s.win_rate !== null && s.win_rate !== undefined ? plainPct(s.win_rate) : '—' }}</span></div>
        <div class="stat"><span class="label">Résultat moyen</span><span class="value" :class="cls(s.mean_r)">{{ rr(s.mean_r) }}</span><span class="sub muted" v-if="s.ci_low !== null && s.ci_low !== undefined">95 % : {{ rr(s.ci_low) }} à {{ rr(s.ci_high) }}</span></div>
        <div class="stat"><span class="label">Profit factor</span><span class="value">{{ s.profit_factor ? s.profit_factor.toFixed(2) : '—' }}</span></div>
        <div class="stat"><span class="label">Gain moyen / perte moyenne</span><span class="value small">{{ rr(s.avg_win) }} / {{ rr(s.avg_loss) }}</span></div>
        <div class="stat"><span class="label">Résultat net</span><span class="value" :class="cls(stats?.pnl)">{{ signed(origin==='ai' ? stats?.ai?.pnl : origin==='manual' ? stats?.manual?.pnl : stats?.pnl) }}</span></div>
        <div class="stat"><span class="label">Frais payés</span><span class="value">{{ money(stats?.fees) }}</span></div>
      </div>
      <p class="faint small" style="margin-bottom:0">Le taux de réussite seul ne veut rien dire : c'est le résultat moyen (en R, frais compris) qui doit être positif sur un grand nombre de trades.</p>
    </div>
    <div class="card" style="margin-bottom:14px">
      <div class="card-head"><h2>Déclaration fiscale</h2><span class="spacer"></span>
        <select v-model="fiscalYear" style="width:auto"><option value="">Toutes les années</option><option v-for="y in fiscal" :key="y.year" :value="y.year">{{ y.year }}</option></select>
        <a class="btn" :href="fiscalUrl">⤓ Toutes les opérations (CSV)</a></div>
      <div class="table-wrap"><table><thead><tr><th>Année</th><th class="num">Trades clôturés</th><th class="num">Résultat réalisé</th><th class="num">Frais</th></tr></thead>
        <tbody><tr v-for="y in fiscal" :key="y.year"><td>{{ y.year }}</td><td class="num">{{ y.trades }}</td><td class="num" :class="cls(y.pnl)">{{ signed(y.pnl) }}</td><td class="num">{{ money(y.fees) }}</td></tr>
          <tr v-if="!fiscal.length"><td colspan="4" class="empty">Aucune opération clôturée.</td></tr></tbody></table></div>
      <p class="faint small" style="margin-bottom:0">Le fichier liste chaque achat et chaque vente (date, quantité, prix, frais), à transmettre à votre expert-comptable. Les règles françaises (calcul global du portefeuille, formulaire 2086, comptes 3916-bis) ne sont pas appliquées automatiquement. Ceci n'est pas un conseil fiscal.</p>
    </div>
    <div class="card">
      <div class="table-wrap"><table>
        <thead><tr><th>Sortie</th><th>Crypto</th><th>Origine</th><th>Stratégie</th><th class="num">Quantité</th><th class="num">Entrée</th><th class="num">Sortie</th><th class="num">Résultat</th><th class="num">R</th><th>Motif</th><th>Durée</th></tr></thead>
        <tbody>
          <template v-for="t in trades" :key="t.id">
            <tr @click="open = open===t.id ? null : t.id" style="cursor:pointer">
              <td class="nowrap">{{ dt(t.exit_ts) }}</td><td><b>{{ t.symbol }}</b></td>
              <td><span class="badge" :class="t.origin==='ai' ? 'paper' : ''">{{ t.origin==='ai' ? (t.mode==='exploration' ? 'IA (explo.)' : t.mode==='essai' ? 'IA (essai)' : 'IA') : 'Manuel' }}</span></td>
              <td>{{ STRATEGY_LABELS[t.strategy] || '—' }}</td><td class="num">{{ qty(t.initial_qty) }}</td>
              <td class="num">{{ price(t.entry_price) }}</td><td class="num">{{ price(t.exit_price) }}</td>
              <td class="num" :class="cls(t.realized_pnl)">{{ signed(t.realized_pnl) }}</td><td class="num" :class="cls(t.pnl_r)">{{ rr(t.pnl_r) }}</td>
              <td class="small">{{ t.reason_close }}</td><td class="nowrap small">{{ duration(t.exit_ts - t.entry_ts) }}</td>
            </tr>
            <tr v-if="open===t.id"><td colspan="11" class="small muted" style="background:var(--bg-2)">
              <b>Pourquoi ce trade :</b> {{ t.reason_open || '—' }}<br>
              Frais : {{ money(t.fees) }} · Stop initial : {{ price(t.initial_stop) }} · Objectif : {{ price(t.target_price) }}
              <span v-if="t.p_win !== null"> · Probabilité annoncée : {{ Math.round(t.p_win*100) }} %</span>
            </td></tr>
          </template>
          <tr v-if="!trades.length"><td colspan="11" class="empty">Aucun trade clôturé sur ce compte.</td></tr>
        </tbody>
      </table></div>
    </div>
  </div>`,
};
