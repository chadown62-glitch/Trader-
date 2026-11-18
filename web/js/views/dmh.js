// Hub de marché (mission 51) : marchés publics, préparation, prévisualisation, simulateur, observatoire.
// SIMULATION — ARGENT FICTIF : aucun ordre réel, aucune transaction diffusée, aucune clé, aucun portefeuille.
import { api, safe } from '../api.js';
import { toast, utc } from '../store.js';

const { ref, reactive, computed, onMounted } = Vue;
const eur = x => (x === null || x === undefined || Number.isNaN(x)) ? '—' : `${Number(x).toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} €`;
const bps = x => (x === null || x === undefined) ? '—' : `${Number(x).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} pb`;
const ms = t => (t ? utc(t / 1000) : '—');
const newId = () => 'ui-' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : String(Date.now()) + Math.random().toString(16).slice(2));

export default {
  setup() {
    const tab = ref('marche');
    const status = ref(null);
    const board = ref(null);
    const pv = ref(null);
    const busy = ref(false);
    const positions = ref(null);
    const history = ref(null);
    const obs = ref(null);
    const chat = reactive({ message: '', draft: null, error: '' });
    const form = reactive({ asset: 'BTC', notional_eur: 10, edge_bps: null, networks: ['solana', 'base', 'ethereum'], prefer_limit: false,
                            horizon: '', entry: '', exit: '' });
    const limitsDraft = reactive({});

    const loadStatus = async () => { status.value = await safe(api.get('/api/dmh/status')); Object.assign(limitsDraft, status.value?.limits || {}); };
    const loadBoard = async () => { busy.value = true; board.value = await safe(api.get('/api/dmh/markets?assets=BTC,ETH,SOL')); busy.value = false; };
    const loadPositions = async () => { positions.value = await safe(api.get('/api/dmh/positions')); };
    const loadHistory = async () => { history.value = await safe(api.get('/api/dmh/history?limit=100')); };
    const loadObs = async () => { obs.value = await safe(api.get('/api/dmh/observatory')); };
    onMounted(async () => { await loadStatus(); await loadPositions(); });

    const preview = async () => {
      if (form.edge_bps === null || form.edge_bps === '') { toast("Indiquez l'avantage brut attendu (hypothèse) : sans lui, l'opération n'est jamais rentable", 'error'); return; }
      busy.value = true;
      pv.value = await safe(api.post('/api/dmh/preview', { mode: 'paper', side: 'buy', asset: form.asset, notional_eur: Number(form.notional_eur),
        edge_bps: Number(form.edge_bps), networks: form.networks, prefer_limit: form.prefer_limit }));
      busy.value = false; tab.value = 'preview';
    };
    const previewSell = async (p) => {
      busy.value = true;
      pv.value = await safe(api.post('/api/dmh/preview', { mode: 'paper', side: 'sell', holding_asset: p.asset, holding_location: p.location, fraction: 1 }));
      busy.value = false; tab.value = 'preview';
    };
    const confirm = async () => {
      if (!pv.value) return;
      if (!window.confirm('Confirmer cette opération FICTIVE (simulation, argent fictif) ?')) return;
      busy.value = true;
      const r = await safe(api.post('/api/dmh/orders', { mode: 'paper', preview_id: pv.value.id, request_id: newId(), confirm: true }), 'Opération fictive traitée');
      busy.value = false;
      if (r) { toast(`État : ${r.state}`, r.state === 'REJECTED' ? 'error' : 'success'); await loadPositions(); await loadHistory(); await loadStatus(); tab.value = 'simulateur'; }
    };
    const halt = async () => { await safe(api.post('/api/dmh/halt', { mode: 'paper', reason: 'arrêt immédiat depuis le panel' }), 'Nouvelles opérations fictives arrêtées'); await loadStatus(); };
    const resume = async () => { await safe(api.post('/api/dmh/resume', { mode: 'paper' }), 'Opérations fictives relancées'); await loadStatus(); };
    const refreshOpen = async () => { await safe(api.post('/api/dmh/open-orders/refresh', { mode: 'paper' }), 'Ordres ouverts mis à jour'); await loadHistory(); await loadPositions(); };
    const reconcile = async () => { const r = await safe(api.post('/api/dmh/reconcile', { mode: 'paper' })); if (r) toast(r.ok ? 'Portefeuille réconcilié' : `Incohérences : ${r.problems.join(' ; ')}`, r.ok ? 'success' : 'error'); await loadStatus(); };
    const saveLimits = async () => { const r = await safe(api.put('/api/dmh/limits', { mode: 'paper', limits: { ...limitsDraft } }), 'Limites enregistrées'); if (r) await loadStatus(); };
    const ask = async () => {
      chat.error = ''; chat.draft = null; busy.value = true;
      const r = await safe(api.post('/api/dmh/strategy', { mode: 'paper', message: chat.message }));
      busy.value = false;
      if (!r) return;
      if (!r.available) { chat.error = r.error; return; }
      chat.draft = r.draft;
    };
    const useDraft = () => { const d = chat.draft; if (!d) return; form.asset = d.asset; form.notional_eur = d.capital_eur; form.edge_bps = d.objective_bps;
      form.horizon = d.horizon; form.entry = d.entry_conditions; form.exit = d.exit_conditions; tab.value = 'strategie'; };
    const open = (t) => { tab.value = t; if (t === 'marche' && !board.value) loadBoard(); if (t === 'simulateur') { loadPositions(); loadHistory(); } if (t === 'observatoire') loadObs(); };
    const routes = computed(() => pv.value ? [pv.value.route, ...(pv.value.alternatives || [])].filter(Boolean) : []);
    return { tab, status, board, pv, busy, positions, history, obs, chat, form, limitsDraft, routes, eur, bps, ms,
             loadBoard, preview, previewSell, confirm, halt, resume, refreshOpen, reconcile, saveLimits, ask, useDraft, open };
  },
  template: `<div class="wrap-anywhere page-stack dmh">
  <div class="sim-banner" data-sim-banner>SIMULATION — ARGENT FICTIF · aucun ordre réel, aucune transaction diffusée, aucune clé</div>
  <div class="page-head"><h1>Hub de marché</h1><span class="spacer"></span>
    <button class="danger" @click="halt" data-halt>Arrêt immédiat des nouvelles opérations fictives</button></div>
  <div v-if="status?.halted" class="note warn">Opérations fictives ARRÊTÉES : {{ status.halted.reason }} <button @click="resume">Relancer</button></div>
  <div v-if="status?.desync" class="note warn">Portefeuille désynchronisé : {{ status.desync.reason }}. Nouvelles opérations bloquées. <button @click="reconcile">Réconcilier</button></div>
  <div class="tabs">
    <button v-for="t in [['marche','Marchés'],['strategie','Stratégie'],['preview','Prévisualisation'],['simulateur','Simulateur'],['observatoire','Observatoire'],['limites','Limites']]"
      :key="t[0]" :class="{active: tab===t[0]}" @click="open(t[0])">{{ t[1] }}</button>
  </div>

  <section v-if="tab==='marche'" class="card">
    <div class="card-head"><h2>Marchés publics (lecture seule)</h2><span class="spacer"></span><button @click="loadBoard" :disabled="busy">Relire</button></div>
    <p class="small muted">Données publiques des plateformes et des réseaux, horodatées (UTC) et hachées. Une source indisponible est affichée comme telle : aucun prix n'est inventé.</p>
    <div v-if="!board" class="empty">{{ busy ? 'Lecture…' : 'Cliquer sur « Relire ».' }}</div>
    <div v-else class="table-wrap"><table>
      <thead><tr><th>Actif</th><th>Source</th><th>État</th><th>Achat</th><th>Vente</th><th>Demi-écart</th><th>Profondeur (10 niv.)</th><th>Âge</th><th>Commission</th></tr></thead>
      <tbody><tr v-for="(r, i) in board.books" :key="i" :class="{muted: r.venue==='consensus'}">
        <td>{{ r.asset }}</td><td>{{ r.venue }}</td><td><span class="badge" :class="r.status==='DISPONIBLE'||r.status==='OK' ? 'ok' : 'warn'">{{ r.status }}</span> <span class="small faint">{{ r.reason || '' }}</span></td>
        <td>{{ r.bid ?? (r.mid ? 'milieu ' + r.mid.toFixed(2) : '—') }}</td><td>{{ r.ask ?? '—' }}</td><td>{{ r.half_spread_bps !== undefined ? bps(r.half_spread_bps) : '' }}</td>
        <td>{{ r.depth_eur_10 ? eur(r.depth_eur_10) : '' }}</td><td>{{ r.age_ms !== undefined ? (r.age_ms/1000).toFixed(1)+' s' : '' }}</td>
        <td>{{ r.fee_taker_bps !== undefined ? bps(r.fee_taker_bps) + ' (' + r.fee_level + ')' : '' }}</td></tr></tbody></table></div>
    <div v-if="board" class="grid cols-3" style="margin-top:12px">
      <div v-for="(n, k) in board.networks" :key="k" class="card"><b>{{ k }}</b> <span class="badge" :class="n.status==='DISPONIBLE'?'ok':'warn'">{{ n.status }}</span>
        <div class="small muted" v-if="n.base_fee_gwei !== undefined">gaz de base {{ n.base_fee_gwei.toPrecision(3) }} gwei · priorité {{ n.priority_p50_gwei.toPrecision(3) }} gwei</div>
        <div class="small muted" v-if="n.rent_token_account_sol !== undefined">dépôt d'un compte de token {{ n.rent_token_account_sol }} SOL (récupérable)</div>
        <div class="small faint" v-if="n.reason">{{ n.reason }}</div></div>
    </div>
  </section>

  <section v-if="tab==='strategie'" class="card">
    <h2>Préparer une stratégie fictive</h2>
    <div class="note info">L'avantage brut attendu est une <b>hypothèse</b> : aucune stratégie n'a encore démontré de rentabilité après frais.
      Sans avantage annoncé, le hub s'abstient (le résultat net serait le coût changé de signe).</div>
    <div class="grid cols-3">
      <div class="field"><label>Actif</label><select v-model="form.asset"><option v-for="a in (status?.assets || ['BTC','ETH','SOL'])" :key="a">{{ a }}</option></select></div>
      <div class="field"><label>Capital fictif engagé (€)</label><input type="number" min="1" max="15" step="0.5" v-model.number="form.notional_eur"></div>
      <div class="field"><label>Avantage brut attendu (pb, hypothèse)</label><input type="number" step="1" v-model.number="form.edge_bps" placeholder="ex. 80"></div>
      <div class="field"><label>Horizon</label><input v-model="form.horizon" placeholder="ex. 3 jours"></div>
      <div class="field"><label>Condition d'entrée</label><input v-model="form.entry"></div>
      <div class="field"><label>Condition de sortie</label><input v-model="form.exit"></div>
    </div>
    <div class="pill-row"><label v-for="n in ['solana','base','ethereum']" :key="n"><input type="checkbox" :value="n" v-model="form.networks"> {{ n }}</label>
      <label><input type="checkbox" v-model="form.prefer_limit"> préférer un ordre à cours limité (amélioration non démontrée)</label></div>
    <div class="btn-row"><button class="primary" @click="preview" :disabled="busy">{{ busy ? 'Calcul…' : 'Prévisualiser (aucune exécution)' }}</button></div>
    <h3>Discuter avec l'IA locale</h3>
    <p class="small muted">Le cerveau de l'IA reste sur cet ordinateur. Sa réponse est un brouillon : elle ne peut ni exécuter, ni changer les limites, ni passer en argent réel.</p>
    <textarea v-model="chat.message" rows="3" placeholder="ex. Je veux tester un achat de SOL sur quelques jours"></textarea>
    <div class="btn-row"><button @click="ask" :disabled="busy || !chat.message">Demander un brouillon</button></div>
    <div v-if="chat.error" class="note warn">{{ chat.error }}</div>
    <div v-if="chat.draft" class="card"><b>Brouillon de l'IA (à relire)</b>
      <p>{{ chat.draft.asset }} · {{ eur(chat.draft.capital_eur) }} · objectif {{ bps(chat.draft.objective_bps) }} (hypothèse) · {{ chat.draft.horizon }}</p>
      <p class="small">{{ chat.draft.explanation }}</p><button @click="useDraft">Reprendre dans le formulaire</button></div>
  </section>

  <section v-if="tab==='preview'" class="card">
    <h2>Prévisualisation</h2>
    <div v-if="!pv" class="empty">Aucune prévisualisation.</div>
    <template v-else>
      <div class="note" :class="pv.decision==='ROUTE' && pv.verdict.allowed ? 'info' : 'warn'" data-decision>
        <b>{{ pv.decision==='ROUTE' && pv.verdict.allowed ? 'Opération possible' : (pv.decision==='ABSTENTION' ? 'ABSTENTION' : 'REFUS de sécurité') }}</b> — {{ pv.explanation }}</div>
      <ul v-if="pv.verdict.vetoes.length" class="small"><li v-for="v in pv.verdict.vetoes" :key="v.code + v.message"><b>{{ v.layer }}</b> : {{ v.message }}</li></ul>
      <ul v-if="pv.verdict.warnings.length" class="small muted"><li v-for="v in pv.verdict.warnings" :key="v.code">{{ v.message }}</li></ul>
      <div class="table-wrap"><table>
        <thead><tr><th>Route</th><th>Aller-retour</th><th>Fourchette</th><th>Net (hypothèse)</th><th>Remplissage</th><th>Reliquat</th><th>Preuve</th><th>Motif</th></tr></thead>
        <tbody><tr v-for="r in routes" :key="r.id" :class="{muted: !r.eligible}">
          <td>{{ r.label }}</td><td>{{ r.totals ? bps(r.totals.cost_bps) + ' · ' + eur(r.totals.cost_eur) : '—' }}</td>
          <td>{{ r.totals ? bps(r.totals.cost_low_bps) + ' – ' + bps(r.totals.cost_high_bps) : '' }}</td>
          <td>{{ r.net ? eur(r.net.net_eur) : '—' }}</td><td>{{ r.totals ? Math.round(r.totals.entry_fill_probability*100) + ' %' : '' }}</td>
          <td>{{ r.totals ? eur(r.totals.dust_eur) : '' }}</td><td>{{ r.totals ? r.totals.level : '' }}</td><td class="small">{{ (r.reasons || []).join(' ; ') || (r.notes || []).join(' ; ') }}</td></tr></tbody></table></div>
      <details v-if="pv.route"><summary>Détail des frais de la route retenue</summary>
        <div v-for="(l, i) in [...pv.route.entry, ...(pv.route.exit || [])]" :key="i" class="small"><b>{{ l.step }}</b>
          <ul><li v-for="c in l.costs" :key="c.name">{{ c.name }} : {{ eur(c.eur) }} ({{ eur(c.low) }} – {{ eur(c.high) }}) · {{ c.level }} · <span class="faint">{{ c.source }}</span></li></ul></div></details>
      <div class="btn-row"><button class="primary" @click="confirm" :disabled="busy || pv.decision!=='ROUTE' || !pv.verdict.allowed" data-confirm>Confirmer l'opération fictive</button>
        <span class="small faint">Valable jusqu'à {{ ms(pv.expires_ms) }} · données {{ pv.mode }}</span></div>
    </template>
  </section>

  <section v-if="tab==='simulateur'" class="card">
    <div class="card-head"><h2>Positions fictives</h2><span class="spacer"></span><button @click="refreshOpen">Mettre à jour les ordres ouverts</button><button @click="reconcile">Réconcilier</button></div>
    <p v-if="positions" class="small">Euros fictifs : {{ eur(positions.cash_total) }} (disponibles : {{ eur(positions.cash_available) }})</p>
    <div v-if="positions && !positions.positions.length" class="empty">Aucune position.</div>
    <div v-else-if="positions" class="table-wrap"><table><thead><tr><th>Actif</th><th>Lieu</th><th>Quantité</th><th>Coût</th><th>Valeur prudente</th><th>Latent (fictif)</th><th></th></tr></thead>
      <tbody><tr v-for="p in positions.positions" :key="p.asset + p.location"><td>{{ p.logical }}</td><td>{{ p.location }}</td><td>{{ p.qty }}</td><td>{{ eur(p.cost_eur) }}</td>
        <td>{{ eur(p.value_eur) }} <span class="small faint">{{ p.mark_method || '' }}</span></td><td>{{ eur(p.unrealized_eur) }}</td>
        <td><button @click="previewSell(p)" :disabled="!p.final">{{ p.final ? 'Vendre (fictif)' : 'Finalité en attente' }}</button></td></tr></tbody></table></div>
    <h3>Historique</h3>
    <div v-if="history" class="table-wrap"><table><thead><tr><th>Date</th><th>Sens</th><th>Actif</th><th>État</th><th>Mode</th><th>Notes</th></tr></thead>
      <tbody><tr v-for="o in history.operations" :key="o.id"><td>{{ ms(o.created_ms) }}</td><td>{{ o.side }}</td><td>{{ o.asset }}</td><td>{{ o.state }}</td><td>{{ o.evidence_mode }}</td>
        <td class="small">{{ ((o.result || {}).notes || (o.result || {}).reasons || []).join(' ; ') }}</td></tr></tbody></table></div>
  </section>

  <section v-if="tab==='observatoire'" class="card">
    <div class="card-head"><h2>Observatoire</h2><span class="spacer"></span><button @click="open('observatoire')">Relire</button></div>
    <div v-if="obs" class="kpi-grid">
      <div class="card"><div class="small muted">Performance nette (fictive)</div><b>{{ eur(obs.net_performance_eur) }}</b></div>
      <div class="card"><div class="small muted">Frais payés en simulation</div><b>{{ eur(obs.fees_paid_eur) }}</b></div>
      <div class="card"><div class="small muted">Économies estimées</div><b>{{ eur(obs.savings_estimated.total_eur) }}</b><div class="small faint">{{ obs.savings_estimated.note }}</div></div>
      <div class="card"><div class="small muted">Économies validées</div><b>{{ eur(obs.savings_validated.total_eur) }}</b><div class="small faint">{{ obs.savings_validated.note }}</div></div>
    </div>
    <div v-if="obs"><h3>Opérations</h3><p class="small">{{ Object.entries(obs.operations).map(([k,v]) => k + ' : ' + v).join(' · ') || 'aucune' }}</p>
      <h3>Rejets de sécurité</h3><ul class="small"><li v-for="(n, k) in obs.rejections" :key="k">{{ k }} — {{ n }}</li></ul>
      <h3>Qualité des données</h3><div class="table-wrap"><table><thead><tr><th>Source</th><th>Appels</th><th>Erreurs</th><th>Coupe-circuit</th><th>Latence médiane</th></tr></thead>
        <tbody><tr v-for="(h, k) in obs.data_quality.hosts" :key="k"><td>{{ k }}</td><td>{{ h.calls }}</td><td>{{ h.errors }}</td><td>{{ h.circuit_open ? 'ouvert' : 'fermé' }}</td><td>{{ h.latency_ms_median ? Math.round(h.latency_ms_median) + ' ms' : '—' }}</td></tr></tbody></table></div></div>
  </section>

  <section v-if="tab==='limites'" class="card">
    <h2>Limites de risque</h2>
    <p class="small muted">Réglables ici, mais jamais au-delà des plafonds absolus du code (ni l'IA ni l'API ne peuvent les dépasser).</p>
    <div v-if="status" class="grid cols-3"><div v-for="(v, k) in status.limits" :key="k" class="field"><label>{{ k }} <span class="faint">(plafond {{ status.absolute[k] }})</span></label>
      <input type="number" step="any" v-model.number="limitsDraft[k]"></div></div>
    <div class="btn-row"><button class="primary" @click="saveLimits">Enregistrer</button></div>
  </section>
</div>`,
};
