// Contexte macro : journal local en lecture seule. Aucun bouton ne déclenche de trading ni de collecte.
import { api, safe } from '../api.js';
import { utc, label } from '../store.js';

const { ref, onMounted } = Vue;

const SOURCE_NAMES = {
  forex_factory: 'Forex Factory (calendrier)', fed: 'Réserve fédérale américaine (Fed)', bls: 'BLS (emploi et prix, États-Unis)',
  bls_cpi_index: 'BLS : indice des prix CPI', bea: 'BEA (croissance, États-Unis)', ecb: 'Banque centrale européenne',
  eurostat: 'Eurostat', coindesk: 'CoinDesk (actualités crypto)',
};
const STATES = { fresh: 'À jour', missing: 'Jamais reçue', unavailable_or_stale: 'Indisponible ou périmée' };
const STATE_CLASS = { fresh: 'ok', missing: 'warn', unavailable_or_stale: 'err' };
const IMPACT = { High: 'Impact fort', Medium: 'Impact moyen', Low: 'Impact faible', Holiday: 'Jour férié', Unknown: 'Impact inconnu' };
const TIME_QUALITY = { explicit_offset: 'heure confirmée', uncertain: 'heure incertaine' };
const REGIMES = { unknown_not_supplied: 'inconnu (aucune mesure fournie)' };

export default {
  setup() {
    const report = ref(null);
    const loading = ref(false);
    const load = async () => { loading.value = true; report.value = await safe(api.get('/api/macro-shadow')); loading.value = false; };
    const value = v => (v === null || v === undefined ? 'Non disponible' : String(v));
    const yesNo = v => (v ? 'oui' : 'non');
    onMounted(load);
    return { report, loading, load, value, yesNo, utc, label, SOURCE_NAMES, STATES, STATE_CLASS, IMPACT, TIME_QUALITY, REGIMES };
  },
  template: `<div class="wrap-anywhere">
  <div class="page-head"><h1>Contexte macro et actualités</h1><span class="spacer"></span><button @click="load" :disabled="loading">Relire le journal local</button></div>
  <div class="note warn">Observation parallèle uniquement. Aucun ordre ni nouveau veto appliqué au portefeuille. Protocole prospectif v2 inchangé.
    Cette page relit le journal déjà enregistré sur ce PC : elle ne télécharge rien.</div>
  <div v-if="!report" class="card empty">{{ loading ? 'Chargement…' : 'Journal local illisible. Réessayez avec « Relire le journal local ».' }}</div>
  <template v-else>
    <p class="meta-line"><b>{{ report.status }}</b> · Analyse {{ report.analysis_version }} · État connu au {{ utc(report.as_of) }}</p>
    <div class="note warn" v-for="warning in report.warnings" :key="warning">{{ warning }}</div>

    <section class="card"><h2>Sources et fraîcheur</h2>
      <div class="table-wrap"><table><thead><tr><th>Source</th><th>État</th><th>Dernière réception</th><th>Enregistrée</th><th>Provenance</th></tr></thead>
      <tbody><tr v-for="s in report.sources" :key="s.id">
        <td>{{ label(SOURCE_NAMES, s.id) }}<div class="faint small">{{ s.official ? 'source officielle' : 'source secondaire' }}</div></td>
        <td><span class="badge" :class="STATE_CLASS[s.state] || ''">{{ label(STATES, s.state) }}</span><div v-if="s.error" class="faint small">{{ s.error }}</div></td>
        <td>{{ utc(s.last_received_at, 'Jamais') }}</td><td>{{ utc(s.last_stored_at, 'Jamais') }}</td>
        <td><a :href="s.url" target="_blank" rel="noopener noreferrer">Flux</a> <span class="faint small">groupe {{ s.dependency_group }}</span></td></tr></tbody></table></div>
      <p class="faint small">Une source « jamais reçue » n'a encore été lue par aucune collecte explicite. Aucune collecte permanente n'est démarrée.</p>
    </section>

    <section class="card"><h2>Calendrier : versions connues</h2>
      <p v-if="!report.calendar.length">Aucun calendrier admissible observé. Aucun chiffre inventé.</p>
      <div class="table-wrap" v-if="report.calendar.length"><table><thead><tr><th>Événement</th><th>Prévu</th><th>Prévision</th><th>Précédent</th><th>Publié</th><th>Source / version</th><th>Disponible</th></tr></thead><tbody>
      <tr v-for="e in report.calendar" :key="e.observation_id"><td>{{ e.currency }} · {{ e.title }}<div class="faint small">{{ label(IMPACT, e.impact) }} · {{ label(TIME_QUALITY, e.time_quality) }}</div></td>
        <td>{{ utc(e.scheduled_at, 'Heure inconnue') }}</td><td>{{ value(e.forecast) }}</td><td>{{ value(e.previous) }}</td><td>{{ value(e.actual) }}</td>
        <td>{{ e.source }} / {{ e.version }}</td><td>{{ utc(e.available_at) }}</td></tr></tbody></table></div>
    </section>

    <section class="card"><h2>Données officielles publiées</h2>
      <p>L’indice CPI BLS n’est pas le taux d’inflation sur un an. Aucun rapprochement automatique d’unités différentes.</p>
      <p v-if="!report.releases.length">Aucune valeur officielle reçue.</p>
      <div class="table-wrap" v-if="report.releases.length"><table><thead><tr><th>Série / période</th><th>Valeur et unité</th><th>Publication connue</th><th>Disponible</th><th>Version</th></tr></thead>
      <tbody><tr v-for="e in report.releases" :key="e.observation_id"><td>{{ e.title }} · {{ e.reference_period }}</td><td>{{ value(e.actual) }} {{ e.unit }}</td>
        <td>{{ utc(e.published_at) }}</td><td>{{ utc(e.available_at) }}</td><td>{{ e.source }} / {{ e.version }}</td></tr></tbody></table></div>
    </section>

    <section class="card"><h2>Actualités, faits et hypothèses</h2>
      <p v-if="!report.news.length">Aucune actualité admissible reçue.</p>
      <article v-for="n in report.news" :key="n.observation_id" class="news-item">
        <b>{{ n.title }}</b>
        <p class="faint small">{{ n.source }} · v{{ n.version }} · disponible {{ utc(n.available_at) }} · publication {{ utc(n.published_at) }}</p>
        <p>{{ n.excerpt }}</p>
        <p class="small">Instruction suspecte : {{ yesNo(n.suspicious_instruction) }} · Actifs : {{ n.assets.join(', ') || 'non déterminés' }} ·
          groupes de provenance (indépendance non prouvée) : {{ n.independent_source_groups }} · rumeur : {{ yesNo(n.rumor) }} · contradiction : {{ yesNo(n.contradictory) }}</p>
        <p v-for="f in n.facts" :key="f">Fait de provenance : {{ f }}</p>
        <template v-if="n.analysis"><p class="faint small">Analyse {{ n.analysis.engine }}, disponible {{ utc(n.analysis.available_at) }}</p>
          <p v-for="h in n.analysis.hypotheses" :key="h">Hypothèse (non vérifiée) : {{ h }}</p></template>
      </article>
    </section>

    <section class="card"><h2>Décisions fictives annotées</h2>
      <p>Climat de volatilité : {{ label(REGIMES, report.advisory.volatility_regime) }}. Aucun climat non observé n’est supposé.</p>
      <p v-if="!report.decisions.length">Aucune décision annotée. La comparaison financière n’est pas encore évaluée.</p>
      <div class="table-wrap" v-else><table><thead><tr><th>Date</th><th>Bras comparés</th><th>Empreinte du contexte</th></tr></thead>
        <tbody><tr v-for="d in report.decisions" :key="d.decision_key"><td>{{ utc(d.as_of) }}</td><td>{{ value(d.context.arms) }}</td><td><code>{{ d.context_sha }}</code></td></tr></tbody></table></div>
    </section>
  </template></div>`,
};
