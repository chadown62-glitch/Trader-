// IA : entraînement (argent fictif), bulletin de fiabilité, rapports d'entraînement, simulation accélérée
import { api, on, safe } from '../api.js';
import { LineChart } from '../charts.js';
import { LearningTab } from './learning.js';
import { store, toast, confirmBox, money, signed, pct, plainPct, rr, cls, dt, dtMs, date, ago, STRATEGY_LABELS, FEATURE_LABELS, REASON_ICONS } from '../store.js';

const { ref, computed, onMounted, onBeforeUnmount, watch } = Vue;

export default {
  components: { LineChart, LearningTab },
  setup() {
    const readiness = ref(null);
    const models = ref([]);
    const report = ref(null);
    const reportVersion = ref(null);
    const trainDays = ref(2920);
    const btDays = ref(90);
    const btUseModel = ref(false);
    const btResult = ref(null);
    const stats = ref(null);
    const cands = ref(null);
    const decisions = ref([]);
    const confirmText = ref('');
    const tab = ref('training');
    const lastTrain = ref(null);
    const agents = computed(() => store.overview?.agents || []);
    const training = computed(() => agents.value.find(a => a.name === 'ia_entrainement'));
    const trainingAcc = computed(() => (store.overview?.accounts || []).find(a => a.role === 'ai_training'));
    const jobs = computed(() => Object.values(store.jobs).sort((a, b) => b.started_at - a.started_at));
    const trainJob = computed(() => jobs.value.find(j => j.kind === 'train'));
    const btJob = computed(() => jobs.value.find(j => j.kind === 'backtest'));
    const prepJob = computed(() => jobs.value.find(j => j.kind === 'prepare' && j.status === 'running'));

    const loadModels = async () => {
      models.value = (await safe(api.get('/api/ai/models'))) || [];
      const champ = models.value.find(m => m.status === 'champion') || models.value[0];
      if (champ && !reportVersion.value) openReport(champ.version);
    };
    const openReport = async (v) => { reportVersion.value = v; report.value = await safe(api.get(`/api/ai/models/${encodeURIComponent(v)}/report`)); };
    const loadReadiness = async () => { readiness.value = await safe(api.get('/api/ai/readiness')); };
    const loadStats = async () => {
      if (trainingAcc.value) stats.value = await safe(api.get(`/api/stats?account_id=${trainingAcc.value.id}`));
      cands.value = await safe(api.get('/api/candidates?limit=300'));
      decisions.value = (await safe(api.get('/api/decisions?limit=80'))) || [];
    };
    const loadJobs = async () => { (await safe(api.get('/api/jobs')) || []).forEach(j => { store.jobs[j.id] = j; }); };
    const loadLastTrain = async () => { lastTrain.value = await safe(api.get('/api/ai/last-training')); };
    onMounted(() => { loadModels(); loadReadiness(); loadStats(); loadJobs(); loadLastTrain(); });
    watch(() => trainingAcc.value?.id, loadStats);
    const offs = [
      on('training_done', () => { reportVersion.value = null; loadModels(); loadReadiness(); }),
      on('job', async (j) => {
        if (j.kind === 'backtest' && j.status === 'done') { const full = await safe(api.get(`/api/jobs/${j.id}`)); btResult.value = full?.result || null; }
        if (j.kind === 'train' && j.status === 'error') toast('Entraînement : ' + (j.error || 'échec'), 'error', 10000);
        if (j.kind === 'train' && j.status === 'done') { const full = await safe(api.get(`/api/jobs/${j.id}`)); if (full?.result) lastTrain.value = { verdict: full.result.verdict, reasons: full.result.reasons, trained_at: Date.now() / 1000 }; }
        if (j.kind === 'backtest' && j.status === 'error') toast('Simulation : ' + (j.error || 'échec'), 'error', 10000);
      }),
      on('decision', (d) => { decisions.value.unshift({ ...d, ts: Date.now() / 1000 }); decisions.value.splice(120); }),
    ];
    const timer = setInterval(() => { loadReadiness(); loadStats(); }, 30000);
    onBeforeUnmount(() => { offs.forEach(f => f()); clearInterval(timer); });

    const startTraining = async () => { await safe(api.post('/api/agents/ia_entrainement/start', {}), 'Préparation de l’IA : téléchargement de l’historique…'); };
    const stopAgent = async (name, close = false) => {
      if (close) {
        const ok = await confirmBox({ title: 'Arrêter et fermer les positions', message: 'Les positions de cette IA seront revendues au prix du marché.', confirmLabel: 'Arrêter et fermer', danger: true });
        if (!ok) return;
      }
      await safe(api.post(`/api/agents/${name}/stop`, { close_positions: close }), 'IA arrêtée');
    };
    const train = async () => { await safe(api.post('/api/ai/train', { days: trainDays.value }), 'Entraînement lancé : il peut prendre quelques minutes'); };
    const backtest = async () => { btResult.value = null; await safe(api.post('/api/ai/backtest', { days: btDays.value, use_model: btUseModel.value }), 'Simulation lancée'); };
    const promote = async (m) => { if (await safe(api.post(`/api/ai/models/${encodeURIComponent(m.version)}/promote`), 'Modèle activé')) loadModels(); };
    const retire = async (m) => { if (await safe(api.post(`/api/ai/models/${encodeURIComponent(m.version)}/retire`), 'Modèle retiré')) loadModels(); };

    const holdoutCurves = computed(() => {
      const h = report.value?.holdout;
      if (!h) return [];
      return [{ data: h.curve_model || [], color: '#3b82f6' }, { data: h.curve_baseline || [], color: '#93a0b8', dashed: true }];
    });
    const btCurves = computed(() => {
      const r = report.value;
      if (!r?.backtest_model) return [];
      return [{ data: r.backtest_model.equity || [], color: '#3b82f6' }, { data: r.backtest_baseline?.equity || [], color: '#93a0b8', dashed: true }];
    });
    const manualBtCurve = computed(() => btResult.value ? [{ data: btResult.value.equity || [], color: '#22c55e' }] : []);
    const STATUS = { champion: ['Actif', 'ok'], trial: ['À l’essai', 'warn'], challenger: ['En test', 'warn'], rejected: ['Non validé', 'err'], retired: ['Retiré', ''] };
    return { store, lastTrain, readiness, models, report, reportVersion, trainDays, btDays, btUseModel, btResult, stats, cands, decisions, confirmText, tab,
      training, trainingAcc, trainJob, btJob, prepJob, startTraining, stopAgent, train, backtest, promote, retire, openReport,
      holdoutCurves, btCurves, manualBtCurve, STATUS, money, signed, pct, plainPct, rr, cls, dt, dtMs, date, ago, STRATEGY_LABELS, FEATURE_LABELS, REASON_ICONS };
  },
  template: `
  <div>
    <div class="page-head"><h1>Intelligence artificielle</h1></div>
    <div class="note info" style="margin-bottom:14px">
      <b>Comment l'IA apprend.</b> Des stratégies repèrent des situations d'achat. L'IA apprend, sur des années d'historique, lesquelles valent vraiment la peine,
      et elle n'est validée que si elle gagne sur une période qu'elle n'a <b>jamais vue</b>. Elle s'entraîne ensuite en direct avec de l'argent fictif.
<b>Tout se passe en argent fictif</b> : cette version ne peut passer aucun ordre réel. <b>Aucun résultat n'est garanti</b> : le marché change, et l'IA peut se tromper.
    </div>
    <div class="tabs">
      <button :class="{on: tab==='training'}" @click="tab='training'">Entraînement en direct</button>
      <button :class="{on: tab==='proof'}" @click="tab='proof'">Bulletin de fiabilité</button>
      <button :class="{on: tab==='learn'}" @click="tab='learn'">Apprentissage sur l'historique</button>
      <button :class="{on: tab==='bench'}" @click="tab='bench'">Capacités d'apprentissage</button>
      <button :class="{on: tab==='sim'}" @click="tab='sim'">Simulation accélérée</button>
      <button :class="{on: tab==='journal'}" @click="tab='journal'">Journal des décisions</button>
    </div>

    <template v-if="tab==='training'">
      <div class="grid cols-2">
        <div class="card">
          <div class="card-head"><h2>IA — Entraînement</h2><span class="badge paper">ARGENT FICTIF · PRIX RÉELS</span><span class="spacer"></span>
            <span class="inline small"><span class="dot" :class="training?.running ? 'ok' : prepJob ? 'warn' : ''"></span>{{ training?.running ? 'en marche' : training?.status }}</span></div>
          <p class="muted small">L'IA surveille {{ (store.settings?.general?.watchlist || []).join(', ') }} 24 h/24 et trade sur le compte « {{ trainingAcc?.name }} ». Chaque décision est expliquée dans le journal.</p>
          <div v-if="prepJob" class="note warn small" style="margin-bottom:10px">{{ prepJob.message }}<div class="progress" style="margin-top:6px"><div :style="{width: (prepJob.progress*100)+'%'}"></div></div></div>
          <div class="btn-row" style="margin-bottom:12px">
            <button v-if="!training?.running" class="primary" :disabled="!!prepJob" @click="startTraining">▶ Démarrer l'entraînement</button>
            <template v-else><button @click="stopAgent('ia_entrainement')">⏸ Arrêter</button><button class="danger" @click="stopAgent('ia_entrainement', true)">Arrêter et fermer ses positions</button></template>
          </div>
          <div class="stats-row">
            <div class="stat"><span class="label">Capital fictif</span><span class="value">{{ money(trainingAcc?.equity) }}</span><span class="sub" :class="cls(trainingAcc?.total_pnl)">{{ signed(trainingAcc?.total_pnl) }}</span></div>
            <div class="stat"><span class="label">Trades de l'IA</span><span class="value">{{ stats?.ai?.n ?? 0 }}</span></div>
            <div class="stat"><span class="label">Taux de réussite</span><span class="value">{{ stats?.ai?.win_rate !== null && stats?.ai?.win_rate !== undefined ? plainPct(stats.ai.win_rate) : '—' }}</span></div>
            <div class="stat"><span class="label">Résultat moyen</span><span class="value" :class="cls(stats?.ai?.mean_r)">{{ rr(stats?.ai?.mean_r) }}</span></div>
          </div>
          <p class="faint small">Mode : {{ training?.model ? (training.model_trial ? 'modèle à l’essai ' + training.model + ' (prometteur mais pas encore prouvé : taille réduite de moitié, argent fictif uniquement)' : 'modèle validé ' + training.model) : 'exploration (aucun modèle : signaux de base, taille réduite de moitié)' }}.</p>
          <template v-if="training?.running && training.scan && Object.keys(training.scan).length">
            <h3 style="margin-top:12px">Ce que l'IA voit (dernière bougie {{ training.timeframe }})</h3>
            <div class="table-wrap"><table>
              <thead><tr><th>Crypto</th><th>Climat</th><th class="num">RSI</th><th class="num">Tendance 200 j</th><th>Signal</th><th>Analyse</th></tr></thead>
              <tbody><tr v-for="(v, sym) in training.scan" :key="sym">
                <td><b>{{ sym.split('/')[0] }}</b></td><td>{{ v.regime }}</td><td class="num">{{ v.rsi !== null ? v.rsi.toFixed(0) : '—' }}</td>
                <td class="num" :class="cls(v.trend_200d)">{{ v.trend_200d !== null ? pct(v.trend_200d) : '—' }}</td>
                <td>{{ v.signals.length ? v.signals.join(', ') : 'aucun' }}</td><td class="nowrap small muted">{{ dtMs(v.ts) }}</td></tr></tbody>
            </table></div>
            <p class="faint small">Climat du marché (BTC) : {{ Object.values(training.scan)[0]?.btc_regime }}. En climat baissier, l'IA reste volontairement à l'écart (en USDC).</p>
          </template>
        </div>
        <div class="card">
          <div class="card-head"><h2>Les refus de l'IA étaient-ils justes ?</h2></div>
          <p class="muted small">Chaque signal, pris ou refusé, est suivi jusqu'à son issue. Si les signaux refusés auraient perdu de l'argent, l'IA a eu raison de les refuser.</p>
          <div class="grid cols-2" v-if="cands">
            <div class="card tight" style="background:var(--bg-2)"><div class="stat"><span class="label">Signaux pris</span><span class="value" :class="cls(cands.taken.mean_r)">{{ rr(cands.taken.mean_r) }}</span><span class="sub muted">{{ cands.taken.n }} résolus</span></div></div>
            <div class="card tight" style="background:var(--bg-2)"><div class="stat"><span class="label">Signaux refusés</span><span class="value" :class="cls(cands.refused.mean_r)">{{ rr(cands.refused.mean_r) }}</span><span class="sub muted">{{ cands.refused.n }} résolus</span></div></div>
          </div>
          <div class="table-wrap" style="margin-top:10px;max-height:260px;overflow-y:auto"><table>
            <thead><tr><th>Bougie</th><th>Crypto</th><th>Stratégie</th><th>Décision</th><th class="num">IA</th><th class="num">Issue</th></tr></thead>
            <tbody><tr v-for="c in (cands?.rows || []).slice(0, 60)" :key="c.id">
              <td class="nowrap">{{ dtMs(c.ts) }}</td><td>{{ c.symbol.split('/')[0] }}</td><td>{{ STRATEGY_LABELS[c.strategy] }}</td>
              <td><span class="badge" :class="c.decision==='pris' ? 'ok' : ''">{{ c.decision }}</span></td>
              <td class="num">{{ c.p_win !== null ? Math.round(c.p_win*100) + ' %' : '—' }}</td>
              <td class="num" :class="cls(c.outcome_r)">{{ c.outcome_r !== null ? rr(c.outcome_r) : (c.outcome || 'en cours') }}</td></tr>
              <tr v-if="!(cands?.rows || []).length"><td colspan="6" class="empty">Aucun signal observé en direct pour l'instant.</td></tr></tbody>
          </table></div>
        </div>
      </div>
    </template>

    <template v-if="tab==='proof'">
      <div class="grid cols-2">
        <div class="card">
          <div class="card-head"><h2>Bulletin de fiabilité</h2><span class="badge paper">ARGENT FICTIF</span></div>
          <div class="note" :class="readiness?.unlocked ? 'ok' : 'warn'" style="margin-bottom:12px">
            <b>{{ readiness?.unlocked ? '✅ Toutes les preuves sont réunies' : '⏳ Preuves en cours' }}</b> — {{ readiness?.score ?? 0 }} %
            <div class="small" style="margin-top:4px">Ce bulletin mesure si l'IA fait ses preuves en argent fictif, sur des prix en direct. Il n'ouvre aucun mode réel.</div>
          </div>
          <div class="criteria" v-if="readiness">
            <div v-for="c in readiness.criteria" :key="c.key" class="criterion">
              <span class="ico">{{ c.ok ? '✅' : '⬜' }}</span><span>{{ c.label }}</span><span class="muted small num">{{ c.value }} / {{ c.target }}</span>
              <div class="progress" :class="{ok: c.ok}"><div :style="{width: (c.progress*100)+'%'}"></div></div>
            </div>
          </div>
        </div>
        <div class="card">
          <h2>🔒 Simulation uniquement</h2>
          <p class="small">Cette version de Sentinelle ne peut <b>ni envoyer, ni modifier, ni annuler un ordre réel</b>. Ce n'est pas un réglage :
            le code qui parlait à la plateforme d'échange a été retiré du programme.</p>
          <ul class="small" style="padding-left:18px;color:var(--muted)">
            <li>L'IA et le trading manuel utilisent uniquement de l'argent fictif, avec les vrais prix du marché.</li>
            <li>Aucune clé API de plateforme d'échange n'est demandée, lue ou utilisée.</li>
            <li>Un bulletin favorable, une phrase de confirmation ou un réglage ne peuvent rien déverrouiller.</li>
            <li>Les gains simulés ne garantissent jamais des gains réels.</li>
          </ul>
          <div v-if="store.overview?.paper_only?.legacy_accounts?.length || store.overview?.paper_only?.legacy_keys" class="note warn small">
            D'anciennes données de « portefeuille réel » existent sur ce PC : elles sont conservées mais ignorées.
            <span v-if="store.overview.paper_only.legacy_note">{{ store.overview.paper_only.legacy_note }}</span>
          </div>
        </div>
      </div>
    </template>

    <template v-if="tab==='learn'">
      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card">
          <h2>Entraîner l'IA sur l'historique</h2>
          <p class="muted small">Télécharge l'historique des prix, crée les situations d'apprentissage, valide sur une période jamais vue, puis simule le portefeuille. Durée : une à quelques minutes.</p>
          <div class="field"><label>Historique utilisé</label><select v-model.number="trainDays"><option :value="365">1 an</option><option :value="730">2 ans</option><option :value="1095">3 ans</option><option :value="1460">4 ans</option><option :value="2190">6 ans</option><option :value="2920">8 ans (historique étendu)</option></select></div>
          <button class="primary" :disabled="trainJob && trainJob.status==='running'" @click="train">🧠 Lancer l'entraînement</button>
          <div v-if="trainJob" style="margin-top:12px">
            <div class="small muted">{{ trainJob.label }} — {{ trainJob.status==='running' ? trainJob.message : trainJob.status==='done' ? 'terminé' : 'échec : ' + trainJob.error }}</div>
            <div class="progress" style="margin-top:6px" :class="{ok: trainJob.status==='done'}"><div :style="{width: (trainJob.progress*100)+'%'}"></div></div>
          </div>
          <div v-if="lastTrain" class="note small" :class="lastTrain.verdict==='validé' ? 'ok' : 'warn'" style="margin-top:12px">
            <b>Dernier entraînement ({{ dt(lastTrain.trained_at) }}) : {{ lastTrain.verdict }}</b>
            <div v-for="r in (lastTrain.reasons || [])" :key="r">{{ r }}</div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Versions du modèle</h2></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Version</th><th>Date</th><th>Verdict</th><th class="num">Contrôle</th><th>Statut</th><th></th></tr></thead>
            <tbody><tr v-for="m in models" :key="m.version" :style="m.version===reportVersion ? 'background:rgba(59,130,246,.08)' : ''">
              <td class="mono small">{{ m.version }}</td><td class="nowrap">{{ dt(m.created_at) }}</td>
              <td>{{ m.metrics?.verdict }}</td><td class="num" :class="cls(m.metrics?.holdout_mean_r)">{{ rr(m.metrics?.holdout_mean_r) }}</td>
              <td><span class="badge" :class="(STATUS[m.status]||[])[1]">{{ (STATUS[m.status]||[m.status])[0] }}</span></td>
              <td class="nowrap"><button class="small" @click="openReport(m.version)">Rapport</button>
                <button v-if="m.status!=='champion' && m.metrics?.verdict==='validé'" class="small" @click="promote(m)">Activer</button>
                <button v-if="m.status==='champion' || m.status==='trial'" class="small ghost" @click="retire(m)">Retirer</button></td></tr>
              <tr v-if="!models.length"><td colspan="6" class="empty">Aucun entraînement pour l'instant.</td></tr></tbody>
          </table></div>
        </div>
      </div>
      <div v-if="report" class="card">
        <div class="card-head"><h2>Rapport d'entraînement</h2><span class="mono small muted">{{ reportVersion }}</span><span class="spacer"></span><span class="muted small">{{ report.symbols?.join(', ') }} · {{ report.timeframe }} · {{ report.n_resolved }} situations</span></div>
        <div class="note verdict" :class="report.verdict==='validé' ? 'ok' : 'warn'" style="margin-bottom:14px">
          <span class="big-ico">{{ report.verdict==='validé' ? '✅' : '⚠️' }}</span>
          <div><b>Modèle {{ report.verdict }}{{ report.summary?.mode === 'règles seules' ? ' (règles seules : le filtre IA est désactivé)' : '' }}</b><div v-for="r in report.reasons" :key="r" class="small">{{ r }}</div>
            <div v-if="report.verdict!=='validé'" class="small" style="margin-top:4px">Conséquence : l'IA {{ report.summary?.trial ? 'utilise ce modèle « à l’essai » en argent fictif, avec une taille réduite,' : 'reste en exploration (argent fictif)' }} et ne peut pas trader en réel. C'est une protection, pas un bug.</div></div>
        </div>
        <div class="grid cols-2" style="margin-bottom:14px" v-if="report.holdout">
          <div>
            <h3>Période de contrôle jamais vue ({{ date(report.period?.holdout_start/1000) }} → {{ date(report.period?.end/1000) }})</h3>
            <div class="table-wrap"><table>
              <thead><tr><th></th><th class="num">Trades</th><th class="num">Réussite</th><th class="num">Moyenne</th><th class="num">Intervalle 95 %</th><th class="num">Profit factor</th></tr></thead>
              <tbody>
                <tr><td><b>Avec l'IA</b></td><td class="num">{{ report.holdout.model.n }}</td><td class="num">{{ plainPct(report.holdout.model.win_rate) }}</td>
                  <td class="num" :class="cls(report.holdout.model.mean_r)">{{ rr(report.holdout.model.mean_r) }}</td>
                  <td class="num small">{{ rr(report.holdout.model.ci_low) }} à {{ rr(report.holdout.model.ci_high) }}</td><td class="num">{{ report.holdout.model.profit_factor?.toFixed(2) ?? '—' }}</td></tr>
                <tr><td>Tous les signaux</td><td class="num">{{ report.holdout.baseline.n }}</td><td class="num">{{ plainPct(report.holdout.baseline.win_rate) }}</td>
                  <td class="num" :class="cls(report.holdout.baseline.mean_r)">{{ rr(report.holdout.baseline.mean_r) }}</td>
                  <td class="num small">{{ rr(report.holdout.baseline.ci_low) }} à {{ rr(report.holdout.baseline.ci_high) }}</td><td class="num">{{ report.holdout.baseline.profit_factor?.toFixed(2) ?? '—' }}</td></tr>
              </tbody></table></div>
            <p class="faint small">R = multiple du risque pris. +0,20 R = gain moyen de 20 % de la somme risquée par trade, frais compris.</p>
            <h3 style="margin-top:12px">Résultat cumulé (en R)</h3>
            <LineChart :series="holdoutCurves" height="small" :area="false" />
            <div class="legend"><span><i style="background:#3b82f6"></i>Avec l'IA</span><span><i style="background:#93a0b8"></i>Tous les signaux</span></div>
          </div>
          <div>
            <h3>Simulation du portefeuille sur la même période</h3>
            <template v-if="report.backtest_model">
              <div class="stats-row" style="margin-bottom:8px">
                <div class="stat"><span class="label">Avec l'IA</span><span class="value" :class="cls(report.backtest_model.equity_metrics.return_pct)">{{ pct(report.backtest_model.equity_metrics.return_pct/100) }}</span><span class="sub muted">baisse max {{ report.backtest_model.equity_metrics.max_dd_pct.toFixed(1) }} %</span></div>
                <div class="stat"><span class="label">Tous les signaux</span><span class="value" :class="cls(report.backtest_baseline?.equity_metrics?.return_pct)">{{ pct((report.backtest_baseline?.equity_metrics?.return_pct||0)/100) }}</span><span class="sub muted">baisse max {{ report.backtest_baseline?.equity_metrics?.max_dd_pct?.toFixed(1) }} %</span></div>
                <div class="stat"><span class="label">Acheter et garder</span><span class="value" :class="cls(report.backtest_model.buy_hold_pct)">{{ pct((report.backtest_model.buy_hold_pct||0)/100) }}</span></div>
              </div>
              <LineChart :series="btCurves" height="small" :area="false" />
              <div class="legend"><span><i style="background:#3b82f6"></i>Capital avec l'IA</span><span><i style="background:#93a0b8"></i>Tous les signaux</span></div>
            </template>
            <div v-else class="empty">Simulation indisponible.</div>
          </div>
        </div>
        <div class="grid cols-2">
          <div>
            <h3>Ce que l'IA regarde le plus</h3>
            <div v-for="f in (report.importance || [])" :key="f.feature" style="margin-bottom:6px">
              <div class="inline small"><span>{{ FEATURE_LABELS[f.feature] || f.feature }}</span><span class="spacer" style="flex:1"></span><span class="num muted">{{ (f.value*100).toFixed(1) }}</span></div>
              <div class="progress"><div :style="{width: Math.min(100, Math.max(2, f.value*1500)) + '%'}"></div></div>
            </div>
            <div v-if="!(report.importance||[]).length" class="muted small">Non disponible.</div>
          </div>
          <div>
            <h3>Par stratégie (période de contrôle)</h3>
            <div class="table-wrap"><table>
              <thead><tr><th>Stratégie</th><th class="num">IA : trades</th><th class="num">IA : moyenne</th><th class="num">Tous : trades</th><th class="num">Tous : moyenne</th></tr></thead>
              <tbody><tr v-for="(v, k) in (report.holdout?.by_strategy || {})" :key="k" v-show="v.model.n || v.baseline.n"><td>{{ STRATEGY_LABELS[k] || k }}</td>
                <td class="num">{{ v.model.n }}</td><td class="num" :class="cls(v.model.mean_r)">{{ rr(v.model.mean_r) }}</td>
                <td class="num">{{ v.baseline.n }}</td><td class="num" :class="cls(v.baseline.mean_r)">{{ rr(v.baseline.mean_r) }}</td></tr></tbody>
            </table></div>
            <h3 style="margin-top:12px">Fiabilité des probabilités</h3>
            <div class="table-wrap"><table><thead><tr><th class="num">Annoncé</th><th class="num">Réel</th><th class="num">Cas</th></tr></thead>
              <tbody><tr v-for="(c, i) in (report.holdout?.calibration || [])" :key="i"><td class="num">{{ (c.p_mean*100).toFixed(0) }} %</td><td class="num">{{ (c.win_rate*100).toFixed(0) }} %</td><td class="num">{{ c.n }}</td></tr></tbody></table></div>
          </div>
        </div>
      </div>
    </template>

    <LearningTab v-if="tab==='bench'" />

    <template v-if="tab==='sim'">
      <div class="card">
        <h2>Simulation accélérée (backtest)</h2>
        <p class="muted small">Rejoue le passé bougie par bougie avec exactement les mêmes règles que l'IA en direct (frais, glissement, gestion du risque). Les performances passées ne préjugent pas des performances futures.</p>
        <div class="row" style="max-width:640px">
          <div class="field"><label>Période</label><select v-model.number="btDays"><option :value="30">30 jours</option><option :value="90">90 jours</option><option :value="180">6 mois</option><option :value="365">1 an</option><option :value="730">2 ans</option></select></div>
          <div class="field"><label>Décisions</label><select v-model="btUseModel"><option :value="true">Avec le modèle actif</option><option :value="false">Tous les signaux (sans filtre IA)</option></select></div>
        </div>
        <button class="primary" :disabled="btJob && btJob.status==='running'" @click="backtest">▶ Lancer la simulation</button>
        <div v-if="btJob" style="margin-top:12px"><div class="small muted">{{ btJob.status==='running' ? btJob.message : btJob.status==='done' ? 'Terminé' : 'Échec : ' + btJob.error }}</div>
          <div class="progress" style="margin-top:6px"><div :style="{width: (btJob.progress*100)+'%'}"></div></div></div>
        <div v-if="btResult" style="margin-top:14px">
          <div class="stats-row" style="margin-bottom:10px">
            <div class="stat"><span class="label">Résultat</span><span class="value" :class="cls(btResult.equity_metrics.return_pct)">{{ pct(btResult.equity_metrics.return_pct/100) }}</span></div>
            <div class="stat"><span class="label">Baisse maximale</span><span class="value">{{ btResult.equity_metrics.max_dd_pct.toFixed(1) }} %</span></div>
            <div class="stat"><span class="label">Trades</span><span class="value">{{ btResult.metrics.n }}</span></div>
            <div class="stat"><span class="label">Réussite</span><span class="value">{{ plainPct(btResult.metrics.win_rate) }}</span></div>
            <div class="stat"><span class="label">Moyenne</span><span class="value" :class="cls(btResult.metrics.mean_r)">{{ rr(btResult.metrics.mean_r) }}</span></div>
            <div class="stat"><span class="label">Frais payés</span><span class="value">{{ money(btResult.fees_paid) }}</span></div>
            <div class="stat"><span class="label">Acheter et garder</span><span class="value" :class="cls(btResult.buy_hold_pct)">{{ pct((btResult.buy_hold_pct||0)/100) }}</span></div>
          </div>
          <LineChart :series="manualBtCurve" height="medium" />
          <div v-if="btResult.halted" class="note danger small" style="margin-top:8px">L'arrêt de sécurité (baisse maximale) s'est déclenché pendant la simulation.</div>
        </div>
      </div>
    </template>

    <template v-if="tab==='journal'">
      <div class="card">
        <div class="card-head"><h2>Journal des décisions</h2></div>
        <div class="feed" style="max-height:none">
          <div v-for="(d, i) in decisions" :key="i" class="feed-item" :class="d.kind">
            <div class="meta">{{ REASON_ICONS[d.kind] || '•' }} {{ dt(d.ts) }} · {{ d.symbol }} · {{ d.agent }}</div><div>{{ d.message }}</div>
          </div>
          <div v-if="!decisions.length" class="empty">Aucune décision enregistrée.</div>
        </div>
      </div>
    </template>
  </div>`,
};
