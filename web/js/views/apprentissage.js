// Apprentissage : registre des modèles, comparaisons préenregistrées et journal des décisions.
// Lecture seule : aucun bouton d'entraînement, de promotion ni de retour en arrière ici.
import { api, safe } from '../api.js';
import { utc, label } from '../store.js';

const { ref, onMounted } = Vue;

const ROLES = [['champion', 'Champion'], ['challenger', 'Challenger'], ['trial', 'À l’essai']];
const REGISTRY = { champion: 'champion', challenger: 'challenger', trial: 'à l’essai', retired: 'retiré', rejected: 'rejeté', candidate: 'candidat' };
const INTEGRITY = { verified: 'empreinte vérifiée', unverified_or_unreadable: 'empreinte non vérifiée ou fichier illisible' };
const INTEGRITY_CLASS = { verified: 'ok', unverified_or_unreadable: 'warn' };
const DIAG_ROLES = { champion: 'Champion', challenger: 'Challenger', trial: 'À l’essai', previous: 'Ancien champion' };
const EXPERIMENTS = { training: 'Entraînement', backtest: 'Simulation accélérée', bench: 'Banc d’essai', compare: 'Comparaison', proof: 'Preuve figé / ré-entraîné' };

export default {
  setup() {
    const status = ref(null);
    const journal = ref(null);
    const filter = ref('');
    const open = ref(null);
    const loading = ref(false);
    const load = async () => {
      loading.value = true;
      const [s, j] = await Promise.all([
        safe(api.get('/api/ai/champion')),
        safe(api.get('/api/ai/journal?limit=60&decision=' + encodeURIComponent(filter.value))),
      ]);
      status.value = s; journal.value = j; loading.value = false;
    };
    const val = v => (v === null || v === undefined ? 'Non disponible' : String(v));
    const percent = v => (v === null || v === undefined ? 'Non disponible' : (100 * v).toFixed(1).replace('.', ',') + ' %');
    const trainedUntil = card => { const t = card?.params?.trained_until; return t ? utc(t / 1000) : 'Inconnue (modèle ancien)'; };
    onMounted(load);
    return { status, journal, filter, open, loading, load, val, percent, trainedUntil, utc, label, ROLES, REGISTRY, INTEGRITY, INTEGRITY_CLASS, DIAG_ROLES, EXPERIMENTS };
  },
  template: `<div class="wrap-anywhere page-stack">
  <div class="page-head"><h1>Apprentissage</h1><span class="spacer"></span><button @click="load" :disabled="loading">Relire les journaux</button></div>
  <div class="note warn">Observation parallèle. Aucune promotion ni rétrogradation automatique. Protocoles prospectifs v2 et v3 conservés.
    Les chiffres de cette page sont des diagnostics de modèles, pas des gains du portefeuille de 30 € fictifs.</div>
  <div v-if="!status && !loading" class="card empty">Registre des modèles illisible. Réessayez avec « Relire les journaux ».</div>
  <template v-if="status">
    <p class="meta-line"><b>{{ status.status }}</b> · Horizon des bougies : {{ status.timeframe }}</p>
    <div class="grid cols-3">
      <section v-for="r in ROLES" :key="r[0]" class="card">
        <h2>{{ r[1] }}</h2>
        <template v-if="status[r[0]]">
          <p><code>{{ status[r[0]].version }}</code></p>
          <p><span class="badge" :class="INTEGRITY_CLASS[status[r[0]].integrity] || ''">{{ label(INTEGRITY, status[r[0]].integrity) }}</span>
            <span class="faint small"> · statut du registre : {{ label(REGISTRY, status[r[0]].status) }}</span></p>
          <p class="small">Créé le {{ utc(status[r[0]].created_at) }} · promu {{ utc(status[r[0]].promoted_at, 'jamais') }}</p>
          <p class="small">Recette {{ status[r[0]].params?.recipe || 'ancienne version' }} · seuil {{ val(status[r[0]].params?.threshold) }} R</p>
          <p class="small">Fin des données vues : {{ trainedUntil(status[r[0]]) }}</p>
          <p class="hash">SHA-256 : {{ status[r[0]].sha256 || 'historique sans empreinte, non vérifié' }}</p>
        </template>
        <p v-else class="muted">Aucun modèle de ce statut enregistré.</p>
      </section>
    </div>

    <section class="card"><h2>Comparaisons préenregistrées</h2>
      <p v-if="!status.comparisons.length">Aucune comparaison prospective enregistrée. Aucun résultat inventé.</p>
      <article v-for="c in status.comparisons" :key="c.protocol_sha" class="news-item">
        <p class="small">{{ c.origin }} · protocole <span class="hash">{{ c.protocol_sha }}</span></p>
        <p>{{ c.resolved }} observation(s) commune(s) dénouée(s) ; {{ c.pending }} en attente ; {{ c.observed_days }} jour(s) observé(s).</p>
        <p><b>{{ c.verdict }}</b> : {{ c.explanation }}</p>
        <div class="table-wrap"><table><thead><tr><th>Modèle</th><th class="num">AUC</th><th class="num">Brier</th><th>Résultat du portefeuille</th></tr></thead>
          <tbody><tr v-for="(d, role) in c.diagnostics" :key="role"><td>{{ label(DIAG_ROLES, role) }}</td><td class="num">{{ val(d.auc) }}</td><td class="num">{{ val(d.brier) }}</td><td>Non évalué</td></tr></tbody></table></div>
      </article>
    </section>

    <section class="card"><h2>Règles et verrous</h2>
      <ul><li v-for="rule in status.rules" :key="rule">{{ rule }}</li></ul>
      <p v-for="lock in status.policy_locks" :key="lock.protocol_sha" class="small">{{ lock.reason }} · jusqu’au {{ utc(lock.end) }}</p>
      <p v-if="!status.policy_locks.length" class="faint small">Aucun verrou de politique actif dans cette base (il est posé au démarrage d’une observation gelée).</p>
    </section>

    <section class="card"><h2>Historique des modèles</h2>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Version</th><th>Événement</th><th>Raison</th></tr></thead>
        <tbody><tr v-for="e in status.history" :key="e.id"><td>{{ utc(e.ts) }}</td><td><code>{{ e.version }}</code></td><td>{{ e.event }}</td><td>{{ e.reason || '—' }}</td></tr>
          <tr v-if="!status.history.length"><td colspan="4" class="empty">Aucun événement enregistré.</td></tr></tbody></table></div>
    </section>

    <section class="card"><h2>Journal des expériences</h2>
      <p>{{ status.experiments.total }} essai(s) enregistré(s), y compris résultats négatifs et interruptions. {{ status.experiments.note }}</p>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Type</th><th>Configuration (SHA)</th><th>État</th></tr></thead><tbody>
        <tr v-for="e in status.experiments.rows" :key="e.id"><td>{{ utc(e.ts) }}</td><td>{{ label(EXPERIMENTS, e.kind) }}</td><td class="hash">{{ e.config_hash }}</td><td>{{ e.result?.status || 'en cours ou interrompu' }}</td></tr>
        <tr v-if="!status.experiments.rows.length"><td colspan="4" class="empty">Aucun essai enregistré.</td></tr></tbody></table></div>
    </section>
  </template>

  <section class="card"><h2>Journal des décisions</h2>
    <div class="field" style="max-width:260px"><label for="journal-filter">Décisions affichées</label>
      <select id="journal-filter" v-model="filter" @change="load"><option value="">Toutes</option><option value="pris">Prises</option><option value="refusé">Refusées</option><option value="préparé">Préparées</option></select></div>
    <p class="small">Le plan initial est écrit avant l’ordre. Un résultat théorique de signal n’est pas un gain du portefeuille.</p>
    <p v-if="!journal" class="muted">{{ loading ? 'Chargement…' : 'Journal illisible.' }}</p>
    <template v-else>
      <p v-if="!journal.rows.length">Aucune décision enregistrée.</p>
      <div class="table-wrap" v-else><table><thead><tr><th>Date</th><th>Actif / stratégie</th><th>Décision</th><th class="num">Probabilité</th><th>Réalisé (fictif)</th><th>Étiquette théorique</th></tr></thead><tbody>
        <template v-for="r in journal.rows" :key="r.id">
          <tr><td>{{ utc(r.ts) }}</td><td><button class="small" :aria-expanded="open===r.id" @click="open = open===r.id ? null : r.id">{{ r.symbol }} · {{ r.strategy }}</button></td>
            <td>{{ r.decision }} : {{ r.reason }}</td><td class="num">{{ percent(r.p_win) }}</td>
            <td>{{ r.actual ? val(r.actual.realized_pnl) + ' ' + r.actual.currency : 'Non disponible' }}<span v-if="r.actual?.partial_realization"> (sortie partielle)</span><span v-if="r.link_missing"> · liaison manquante</span></td>
            <td>{{ val(r.theoretical.r) }} R · hors portefeuille</td></tr>
          <tr v-if="open===r.id"><td colspan="6">
            <p class="small">Modèle {{ r.model_version || 'aucun (règles seules)' }} · enregistré le {{ utc(r.recorded_at) }}</p>
            <template v-if="r.plan"><p v-for="reason in r.plan.raisons" :key="reason">{{ reason }}</p>
              <p>Entrée de référence {{ r.plan.entree.reference }}, stop {{ r.plan.stop.prix }}, objectif {{ val(r.plan.objectif.prix) }}.</p>
              <p>Frais estimés {{ r.plan.frais.en_r }} R. {{ r.plan.scenario_note }} {{ r.plan.probabilite.source }}</p>
              <p v-for="s in r.plan.scenarios" :key="s.nom">{{ s.nom }} : {{ percent(s.probabilite) }} · {{ s.description }}</p></template>
            <p v-else>Ancienne décision sans plan archivé ; aucun plan reconstruit après l’issue.</p>
            <p v-if="r.review">{{ r.review.detail }}</p></td></tr>
        </template></tbody></table></div>
    </template>
  </section>
  </div>`,
};
