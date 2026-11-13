import { api } from '../api.js';
const { ref, onMounted } = Vue;
export default {
  setup() {
    const report = ref(null), error = ref('');
    const load = async () => { try { report.value = await api.get('/api/evidence'); error.value = ''; } catch(e) { error.value = e.message; } };
    const number = v => v == null ? 'Non mesuré' : Number(v).toLocaleString('fr-FR', {maximumFractionDigits: 6});
    onMounted(load);
    return { report, error, load, number };
  },
  template: `<section class="view mission-control">
    <h1>Mission Control</h1>
    <p class="muted">Observatoire des preuves · argent fictif · lecture seule</p>
    <div class="note warn">Aucun avantage indépendant démontré. Une abstention n'est pas un profit réalisé.</div>
    <button @click="load">Actualiser</button> <a href="/api/evidence/export" download>Exporter les preuves datées</a>
    <p v-if="error" class="note danger">{{error}}</p>
    <template v-if="report">
      <p>État : {{ report.status }} · {{ new Date(report.generated_at*1000).toISOString() }}</p>
      <div v-for="(group, category) in report.categories" :key="category" class="card" style="margin-top:12px">
        <h2>{{ {synthetic_fixture:'Fixtures synthétiques', historical_public:'Historiques authentiques', prospective_recorded:'Prospective prouvée'}[category] }}</h2>
        <p>{{group.reports.length}} rapport(s) classé(s). Aucune agrégation entre catégories.</p>
        <div style="overflow-x:auto"><table><thead><tr><th>Jour UTC</th><th>Rapports</th></tr></thead><tbody>
          <tr v-for="d in group.daily"><td>{{d.utc_day}}</td><td>{{d.reports}}</td></tr>
        </tbody></table></div>
      </div>
      <div v-if="report.historical_baselines" class="card" style="margin-top:12px">
        <h2>Comparaisons historiques descriptives</h2><p>Périodes déjà explorées, coûts OHLC approximés ; aucune promotion.</p>
        <div v-for="period in report.historical_baselines.results" style="overflow-x:auto">
          <h3>{{period.start}} → {{period.end_exclusive}} (exclu)</h3>
          <table><thead><tr><th>Bras isolé</th><th>Capital €</th><th>Trades</th><th>Frais €</th><th>Drawdown %</th></tr></thead>
          <tbody><tr v-for="(arm,name) in period.arms"><td>{{name}}</td><td>{{number(arm.metrics.capital_eur)}}</td>
          <td>{{arm.metrics.n_trades}}</td><td>{{number(arm.metrics.fees_eur)}}</td><td>{{number(arm.metrics.max_drawdown*100)}}</td></tr></tbody></table>
        </div>
      </div>
      <div v-if="report.wallet" class="card" style="margin-top:12px">
        <h2>Grand livre partagé · 30 EUR fictifs</h2>
        <p>Manuel et IA : résultat non attribué à un modèle.</p>
        <p>Réalisé net : {{number(report.wallet.realized_net_eur)}} € · frais inclus : {{number(report.wallet.position_fees_eur)}} €</p>
        <p>Dernier capital archivé : {{number(report.wallet.last_equity_snapshot?.equity)}} € · âge : {{number(report.wallet.snapshot_age_seconds)}} s</p>
        <p>Valorisation actuelle : {{number(report.wallet.current_mtm_eur)}} · rapprochement : {{report.wallet.ledger_reconciled ? 'conforme' : 'écart à examiner'}}</p>
        <p>{{report.wallet.fills}} fills · {{report.wallet.open_positions}} positions ouvertes</p>
      </div>
      <div class="card" style="margin-top:12px"><h2>Continuité observée</h2>
        <p>Superviseur : {{report.supervisor?.status || 'non démarré / aucune preuve'}}</p>
        <p>Temps CPU mesuré : {{number(report.supervisor?.measured_tick_cpu_seconds)}} s · disponibilité continue : non mesurée</p>
        <p>Coût de fonctionnement : {{number(report.operating_cost_eur)}} € · vitesse d'apprentissage : {{number(report.learning_speed)}}</p>
      </div>
      <p v-for="e in report.errors" class="note danger">{{e}}</p>
      <p v-for="limit in report.limits" class="muted small">{{limit}}</p>
    </template>
  </section>`
};
