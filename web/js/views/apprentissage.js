// Apprentissage : champion, challenger, comparaison sur les mêmes situations, historique des promotions,
// preuve « modèle figé contre ré-entraîné », journal des décisions et limites statistiques.
import { api, on, safe } from '../api.js';
import { store, confirmBox, money, cls, dt, ago, rr, STRATEGY_LABELS } from '../store.js';

const { ref, computed, onMounted, onBeforeUnmount } = Vue;
const nf = (d) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
const pc = (x, d = 0) => (x === null || x === undefined) ? '—' : nf(d).format(x * 100) + ' %';
const STATUS = { champion: ['Approuvé pour le trading fictif', 'ok'], challenger: ['Challenger : à l’épreuve, ne trade pas', 'warn'],
  trial: ['À l’essai : taille réduite, argent fictif', 'warn'] };
const EVENT_CLASS = { 'promu': 'ok', 'promu manuellement': 'ok', 'rétabli': 'ok', "à l'épreuve": 'warn', "à l'essai": 'warn',
  'rejeté': 'err', 'rétrogradé': '', 'empreinte invalide': 'err' };
const VERDICT = { meilleur: ['Le nouveau fait mieux', 'ok'], 'moins bon': ['Le nouveau fait moins bien', 'err'],
  'non départagés': ['Pas de différence prouvée', 'warn'], insuffisant: ['Pas encore assez de données', ''] };

export default {
  setup() {
    const st = ref(null);
    const journal = ref(null);
    const filter = ref('');
    const open = ref(null);
    const busy = ref(false);
    const load = async () => {
      st.value = await safe(api.get('/api/ai/champion'));
      journal.value = await safe(api.get('/api/ai/journal?limit=60' + (filter.value ? '&decision=' + encodeURIComponent(filter.value) : '')));
    };
    onMounted(load);
    const offs = [on('model', load), on('training_done', load), on('trade_closed', load),
      on('job', (j) => { if (['proof', 'train', 'bench'].includes(j.kind) && j.status !== 'running') load(); })];
    const timer = setInterval(load, 60000);
    onBeforeUnmount(() => { offs.forEach(f => f()); clearInterval(timer); });

    const proofJob = computed(() => Object.values(store.jobs).filter(j => j.kind === 'proof').sort((a, b) => b.started_at - a.started_at)[0]);
    const startProof = async () => { busy.value = true; await safe(api.post('/api/ai/proof', {}), 'Mesure lancée : elle peut durer plusieurs minutes'); busy.value = false; };
    const rollback = async () => {
      const ok = await confirmBox({ title: 'Revenir au champion précédent', confirmLabel: 'Revenir en arrière', danger: true,
        message: 'Le champion actuel sera retiré (il est conservé) et le précédent redeviendra le modèle utilisé par l’IA, en argent fictif.' });
      if (ok && await safe(api.post('/api/ai/rollback', { confirm: true }), 'Champion précédent rétabli')) load();
    };
    const setFilter = (f) => { filter.value = f; load(); };
    const cmpLine = (c) => c && c.diff !== null && c.diff !== undefined
      ? `${rr(c.diff, 3)} par situation (intervalle ${rr(c.ci_low, 3)} à ${rr(c.ci_high, 3)})` : '—';
    const progress = (c) => {
      if (!c || !c.required) return null;
      return { decisive: Math.min(1, c.n_diff / c.required.decisive), blocks: Math.min(1, c.blocks / c.required.blocks) };
    };
    const cur = computed(() => store.overview?.portfolio?.currency || '');
    return { st, journal, filter, open, busy, proofJob, startProof, rollback, setFilter, cmpLine, progress, cur, STATUS, EVENT_CLASS, VERDICT,
      pc, nf, money, cls, dt, ago, rr, STRATEGY_LABELS };
  },
  template: `
  <div>
    <div class="page-head"><h1>Apprentissage</h1></div>
    <div v-if="!st" class="card empty">Chargement…</div>
    <template v-else>
      <div class="note info" style="margin-bottom:14px">
        <b>État de l'IA :</b> {{ st.state }}.
        <span v-if="st.acting"> Modèle utilisé : <code>{{ st.acting }}</code>.</span>
        Tout se passe en argent fictif.
      </div>

      <div class="grid cols-3" style="margin-bottom:14px">
        <div v-for="key in ['champion', 'challenger', 'trial']" :key="key" class="card">
          <div class="card-head"><h2>{{ key === 'champion' ? 'Champion' : key === 'challenger' ? 'Challenger' : 'À l’essai' }}</h2><span class="spacer"></span>
            <span v-if="st[key]" class="badge" :class="STATUS[key][1]">{{ STATUS[key][1] === 'ok' ? 'approuvé' : key === 'challenger' ? 'à l’épreuve' : 'à l’essai' }}</span></div>
          <template v-if="st[key]">
            <div><code>{{ st[key].version }}</code></div>
            <div class="small muted" style="margin-top:4px">{{ STATUS[key][0] }}</div>
            <dl class="kv small" style="margin-top:8px">
              <dt>Créé</dt><dd>{{ dt(st[key].created_at) }}</dd>
              <dt v-if="st[key].promoted_at">Promu</dt><dd v-if="st[key].promoted_at">{{ dt(st[key].promoted_at) }}</dd>
              <dt>Validation</dt><dd>{{ st[key].metrics.verdict }}<span v-if="st[key].metrics.holdout_mean_r !== null && st[key].metrics.holdout_mean_r !== undefined">,
                {{ rr(st[key].metrics.holdout_mean_r) }} sur {{ st[key].metrics.holdout_n }} trades jamais vus</span></dd>
              <dt>Recette</dt><dd>{{ st[key].params.recipe || '—' }}<span v-if="st[key].params.alpha">, confiance exigée {{ pc(1 - st[key].params.alpha, 2) }}</span></dd>
              <dt>Données vues</dt><dd>{{ st[key].params.train_end ? 'jusqu’au ' + dt(st[key].params.train_end / 1000) : 'non précisé (ancien modèle)' }}</dd>
              <dt>Empreinte</dt><dd><code class="small">{{ st[key].sha256 || '—' }}</code></dd>
            </dl>
          </template>
          <div v-else class="muted small">{{ key === 'champion' ? 'Aucun modèle n’a encore été validé sur des données jamais vues.' : key === 'challenger' ? 'Aucun nouveau modèle à l’épreuve.' : 'Aucun modèle à l’essai.' }}</div>
        </div>
      </div>

      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card">
          <h2>Champion contre challenger</h2>
          <div v-if="!st.comparison" class="muted small">Pas de comparaison en cours (il faut un champion et un challenger).</div>
          <template v-else>
            <div class="inline" style="gap:8px;margin-bottom:8px"><span class="badge" :class="VERDICT[st.comparison.verdict][1]">{{ VERDICT[st.comparison.verdict][0] }}</span></div>
            <p class="small" style="margin-top:0">{{ st.comparison.explanation }}</p>
            <dl class="kv small">
              <dt>Situations vécues, dénouées</dt><dd>{{ st.comparison.n }} (dont {{ st.comparison.n_diff }} où ils décident différemment ; {{ st.comparison.pending }} en attente d'issue)</dd>
              <dt>Différence</dt><dd>{{ cmpLine(st.comparison) }}</dd>
              <dt>Champion / challenger</dt><dd>{{ rr(st.comparison.sum_a) }} / {{ rr(st.comparison.sum_b) }} au total</dd>
            </dl>
            <div v-if="progress(st.comparison)" class="small" style="margin-top:8px">
              <div>Situations décisives : {{ st.comparison.n_diff }} / {{ st.comparison.required.decisive }}</div>
              <div class="progress" :class="{ok: progress(st.comparison).decisive >= 1}"><div :style="{width: progress(st.comparison).decisive * 100 + '%'}"></div></div>
              <div style="margin-top:6px">Semaines couvertes : {{ st.comparison.blocks }} / {{ st.comparison.required.blocks }}</div>
              <div class="progress" :class="{ok: progress(st.comparison).blocks >= 1}"><div :style="{width: progress(st.comparison).blocks * 100 + '%'}"></div></div>
            </div>
            <p v-if="st.comparison.historical" class="faint small" style="margin-bottom:0">Sur l'historique, au moment de l'entraînement : {{ st.comparison.historical.explanation }}</p>
          </template>
        </div>
        <div class="card">
          <h2>Surveillance du champion</h2>
          <div v-if="!st.health" class="muted small">Pas de champion à surveiller.</div>
          <template v-else>
            <dl class="kv small">
              <dt>En direct depuis sa promotion</dt><dd>{{ st.health.live.n }} situation(s) retenue(s) et dénouée(s)<span v-if="st.health.live.mean_r !== null">, {{ rr(st.health.live.mean_r) }} en moyenne</span></dd>
              <dt>Attendu (validation)</dt><dd>{{ rr(st.health.expected_mean_r) }}</dd>
              <dt>Ressemblance au marché d'apprentissage</dt><dd v-if="st.health.drift && st.health.drift.mean_psi !== null">
                indice {{ nf(2).format(st.health.drift.mean_psi) }} <span class="badge" :class="st.health.drift.alert ? 'err' : 'ok'">{{ st.health.drift.alert ? 'marché différent' : 'comparable' }}</span></dd>
                <dd v-else>pas encore mesurable</dd>
            </dl>
            <div v-for="n in st.health.notes" :key="n" class="note warn small" style="margin-top:8px">{{ n }}</div>
          </template>
          <div v-if="st.monitoring" class="note small" style="margin-top:10px">
            L'ancien champion <code>{{ st.monitoring.previous }}</code> reste suivi jusqu'au {{ dt(st.monitoring.until) }}.
            <b>{{ VERDICT[st.monitoring.comparison.verdict][0] }}</b> — {{ st.monitoring.comparison.explanation }}
          </div>
          <button v-if="st.monitoring" class="small danger" style="margin-top:10px" @click="rollback">Revenir au champion précédent…</button>
        </div>
      </div>

      <div class="grid cols-2" style="margin-bottom:14px">
        <div class="card">
          <div class="card-head"><h2>Ré-apprendre aide-t-il ?</h2><span class="spacer"></span>
            <button class="small" :disabled="busy || proofJob?.status === 'running'" @click="startProof">Mesurer sur l'historique</button></div>
          <p class="small muted" style="margin-top:0">Un modèle est figé au milieu de l'historique ; un autre est ré-entraîné à chaque période avec tout ce qui est connu.
            Les deux décident ensuite sur des périodes qu'aucun n'a vues, et on compare leurs résultats situation par situation.</p>
          <div v-if="proofJob && proofJob.status === 'running'" class="small">{{ proofJob.message }}<div class="progress" style="margin-top:6px"><div :style="{width: proofJob.progress * 100 + '%'}"></div></div></div>
          <template v-if="st.proof">
            <div class="inline" style="gap:8px"><span class="badge" :class="VERDICT[st.proof.verdict][1]">{{ st.proof.verdict === 'meilleur' ? 'Ré-apprendre fait mieux' : st.proof.verdict === 'moins bon' ? 'Le modèle figé fait mieux' : VERDICT[st.proof.verdict][0] }}</span>
              <span class="faint small">mesuré {{ ago(st.proof.ts) }}, {{ st.proof.n_candidates }} situations, {{ (st.proof.symbols || []).length }} cryptos</span></div>
            <p class="small">{{ st.proof.explanation }}</p>
            <div class="table-wrap" v-if="st.proof.steps.length"><table>
              <thead><tr><th>Période</th><th class="num">Situations</th><th class="num">Figé</th><th class="num">Ré-entraîné</th></tr></thead>
              <tbody><tr v-for="s in st.proof.steps" :key="s.start"><td class="small">{{ dt(s.start / 1000).slice(0, 8) }} → {{ dt(s.end / 1000).slice(0, 8) }}</td>
                <td class="num">{{ s.n }}</td><td class="num" :class="cls(s.frozen)">{{ rr(s.frozen) }}</td><td class="num" :class="cls(s.retrained)">{{ rr(s.retrained) }}</td></tr></tbody></table></div>
          </template>
          <div v-else-if="!proofJob" class="muted small">Pas encore mesuré.</div>
        </div>
        <div class="card">
          <h2>Journal des expériences</h2>
          <dl class="kv small">
            <dt>Entraînements</dt><dd>{{ st.experiments.trainings }} (dont {{ st.experiments.validated }} validé(s))</dd>
            <dt>Configurations différentes</dt><dd>{{ st.experiments.distinct_training_configs }} sur {{ st.experiments.window_days }} jours</dd>
            <dt>Confiance exigée</dt><dd><b>{{ nf(2).format(st.experiments.confidence_pct) }}&nbsp;%</b></dd>
            <dt v-if="st.bench">Banc d'essai</dt><dd v-if="st.bench">taux d'apprentissage {{ pc(st.bench.score) }}, {{ st.bench.false_discoveries }} fausse(s) découverte(s) sur {{ st.bench.noise_runs }} marchés de bruit</dd>
          </dl>
          <p class="faint small" style="margin-bottom:0">{{ st.experiments.note }}</p>
        </div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h2>Règles de promotion (fixées d'avance)</h2>
        <ol class="small" style="margin:0 0 0 18px;padding:0">
          <li v-for="r in st.rules" :key="r" style="margin-bottom:4px">{{ r }}</li>
        </ol>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h2>Historique des modèles</h2>
        <div class="table-wrap" style="max-height:360px;overflow:auto"><table>
          <thead><tr><th>Date</th><th>Modèle</th><th>Événement</th><th>Raison</th></tr></thead>
          <tbody><tr v-for="e in st.history" :key="e.id"><td class="nowrap small">{{ dt(e.ts) }}</td><td><code class="small">{{ e.version }}</code></td>
            <td><span class="badge" :class="EVENT_CLASS[e.event] || ''">{{ e.label }}</span></td><td class="small muted">{{ e.reason }}</td></tr>
            <tr v-if="!st.history.length"><td colspan="4" class="empty">Aucun modèle enregistré pour l'instant.</td></tr></tbody></table></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <div class="card-head"><h2>Journal des décisions</h2><span class="spacer"></span>
          <div class="seg"><button :class="{on: filter===''}" @click="setFilter('')">Toutes</button><button :class="{on: filter==='pris'}" @click="setFilter('pris')">Prises</button>
            <button :class="{on: filter==='refusé'}" @click="setFilter('refusé')">Refusées</button></div></div>
        <p class="small muted" style="margin-top:0">Chaque situation examinée par l'IA, avec le plan écrit <b>avant</b> l'issue, ce qui s'est passé ensuite,
          la cause la plus probable et ce que le modèle actuel déciderait aujourd'hui.</p>
        <div v-if="!journal || !journal.rows.length" class="empty">Aucune décision enregistrée pour l'instant.</div>
        <div class="table-wrap" v-else><table>
          <thead><tr><th>Date</th><th>Crypto</th><th>Stratégie</th><th>Décision</th><th class="num">Probabilité</th><th class="num">Issue</th><th>Cause probable</th></tr></thead>
          <tbody>
            <template v-for="r in journal.rows" :key="r.id">
              <tr @click="open = open === r.id ? null : r.id" style="cursor:pointer">
                <td class="nowrap small">{{ dt(r.ts) }}</td><td>{{ r.symbol }}</td><td class="small">{{ STRATEGY_LABELS[r.strategy] || r.strategy }}</td>
                <td><span class="badge" :class="r.decision === 'pris' ? 'paper' : ''">{{ r.decision }}</span></td>
                <td class="num">{{ pc(r.p_win) }}</td>
                <td class="num" :class="cls(r.outcome.r)">{{ r.outcome.r !== null ? rr(r.outcome.r) : r.outcome.status }}<span v-if="r.outcome.theoretical && r.outcome.r !== null" class="faint small"> (théorique)</span></td>
                <td class="small">{{ r.review ? r.review.label : '—' }}</td>
              </tr>
              <tr v-if="open === r.id"><td colspan="7" class="small" style="background:var(--bg-2)">
                <div><b>Raison de la décision :</b> {{ r.reason }}</div>
                <template v-if="r.plan && r.plan.raisons">
                  <div style="margin-top:6px"><b>Plan écrit avant l'issue :</b></div>
                  <ul style="margin:4px 0 0 18px;padding:0"><li v-for="x in r.plan.raisons" :key="x">{{ x }}</li></ul>
                  <div style="margin-top:4px">Stop {{ nf(2).format(r.plan.stop.prix) }} ({{ nf(1).format(r.plan.stop.distance_pct) }} %) · objectif
                    {{ r.plan.objectif.prix ? nf(2).format(r.plan.objectif.prix) + ' (gain/risque ' + nf(1).format(r.plan.objectif.rapport_gain_risque) + ')' : 'stop suiveur' }}
                    · frais {{ rr(r.plan.frais.en_r) }} · durée max. {{ nf(1).format(r.plan.duree_max.jours) }} j</div>
                  <div style="margin-top:4px">Scénarios :
                    <span v-for="s in r.plan.scenarios" :key="s.nom" style="margin-right:10px">{{ s.nom }} {{ pc(s.probabilite) }}<span v-if="s.resultat_r !== null"> ({{ rr(s.resultat_r) }})</span></span>
                    <span v-if="r.plan.probabilite.marge" class="faint">— marge d'erreur ± {{ pc(r.plan.probabilite.marge) }}</span></div>
                  <div v-if="r.plan.taille" style="margin-top:4px">Taille : {{ money(r.plan.taille.montant, r.plan.taille.monnaie) }}, risque {{ money(r.plan.taille.risque, r.plan.taille.monnaie) }}
                    <span v-if="r.plan.taille.portee_au_minimum">(portée au minimum de la plateforme)</span></div>
                </template>
                <div v-if="r.review" style="margin-top:6px"><b>Pourquoi :</b> {{ r.review.detail }}</div>
                <div v-if="r.outcome.market_move !== null" class="faint">Bitcoin pendant le trade : {{ nf(1).format(r.outcome.market_move * 100) }} %</div>
                <div v-if="r.today" style="margin-top:6px"><b>Aujourd'hui :</b> {{ r.today.text }}</div>
              </td></tr>
            </template>
          </tbody></table></div>
      </div>

      <div v-if="journal && journal.lessons" class="grid cols-2" style="margin-bottom:14px">
        <div class="card">
          <h2>Pourquoi les trades ont gagné ou perdu</h2>
          <div class="table-wrap"><table><thead><tr><th>Cause la plus probable</th><th class="num">Gains</th><th class="num">Pertes</th></tr></thead>
            <tbody><tr v-for="c in journal.lessons.causes" :key="c.cause"><td>{{ c.label }}</td><td class="num">{{ c.gains }}</td><td class="num">{{ c.pertes }}</td></tr>
              <tr v-if="!journal.lessons.causes.length"><td colspan="3" class="empty">Aucun trade dénoué pour l'instant.</td></tr></tbody></table></div>
          <p class="faint small" style="margin-bottom:0">« Issue conforme aux probabilités » : un résultat attendu au vu des chances annoncées. Ce n'est pas une erreur à corriger.</p>
        </div>
        <div class="card">
          <h2>Mémoire de marché</h2>
          <div class="table-wrap"><table><thead><tr><th></th><th class="num">Cas</th><th class="num">Gagnants</th><th class="num">Moyenne</th><th class="num">Moyenne prudente</th></tr></thead>
            <tbody><tr v-for="g in journal.lessons.memory" :key="g.kind + g.key"><td>{{ g.kind === 'crypto' ? g.key : 'climat ' + g.key }}</td><td class="num">{{ g.n }}</td>
              <td class="num">{{ pc(g.win_rate) }}</td><td class="num" :class="cls(g.mean_r)">{{ rr(g.mean_r) }}</td><td class="num" :class="cls(g.prudent_mean_r)">{{ rr(g.prudent_mean_r) }}</td></tr>
              <tr v-if="!journal.lessons.memory.length"><td colspan="5" class="empty">Pas encore de données.</td></tr></tbody></table></div>
          <p class="faint small" style="margin-bottom:0">{{ journal.lessons.note }}</p>
        </div>
      </div>

      <div class="card">
        <h2>Limites statistiques</h2>
        <ul class="small" style="margin:0 0 0 18px;padding:0;color:var(--muted)">
          <li>Un modèle n'est jamais jugé sur les données qui ont servi à l'entraîner ou à choisir ses réglages.</li>
          <li>Les trades proches dans le temps se ressemblent (le marché bouge d'un bloc) : l'incertitude est calculée par blocs de temps, ce qui la rend plus large et plus honnête.</li>
          <li>Plus on essaie de configurations, plus on risque d'en valider une par chance : la confiance exigée monte avec le nombre d'essais.</li>
          <li>Avec 30 € et peu de trades, la plupart des comparaisons resteront longtemps « pas encore assez de données » : c'est voulu.</li>
          <li>Aucun résultat simulé ne garantit un résultat réel.</li>
        </ul>
      </div>
    </template>
  </div>`,
};
