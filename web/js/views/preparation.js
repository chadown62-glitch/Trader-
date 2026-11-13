// Préparation de l'observation prospective : page en lecture seule.
// Volontairement AUCUN bouton de démarrage : l'observation gelée exige une approbation humaine
// explicite, en dehors du panel (mission #30). Le seul bouton relance le contrôle, sans rien modifier.
import { api, safe } from '../api.js';
import { utc } from '../store.js';

const { ref, computed, onMounted } = Vue;
const MARKS = { ok: '✓', blocked: '✕', untested: '?', warning: '!', info: 'i' };
const WORDS = { ok: 'OK', blocked: 'Bloquant', untested: 'Non testé', warning: 'Attention', info: 'Information' };

export default {
  setup() {
    const rep = ref(null);
    const loading = ref(false);
    const load = async () => { loading.value = true; rep.value = await safe(api.get('/api/observation/preflight')); loading.value = false; };
    onMounted(load);
    const blocking = computed(() => (rep.value?.checks || []).filter(c => c.blocking && (c.status === 'blocked' || c.status === 'untested')));
    const others = computed(() => (rep.value?.checks || []).filter(c => !(c.blocking && (c.status === 'blocked' || c.status === 'untested'))));
    const isoUtc = s => (s ? utc(Date.parse(s) / 1000) : 'Inconnue');
    return { rep, loading, load, blocking, others, utc, isoUtc, MARKS, WORDS };
  },
  template: `<div class="wrap-anywhere page-stack">
  <div class="page-head"><h1>Préparation de l'observation</h1><span class="spacer"></span><button @click="load" :disabled="loading">Refaire le contrôle</button></div>
  <div class="note info">Cette page vérifie si TRADER AI peut commencer l'<b>observation prospective</b> : le modèle gelé observe le marché réel
    pendant la période réservée, avec le portefeuille de <b>30 € fictifs</b>, sans aucun ordre réel. Elle ne démarre rien et ne modifie rien.</div>
  <div v-if="!rep" class="card empty">{{ loading ? 'Contrôle en cours…' : 'Contrôle indisponible.' }}</div>
  <template v-else>
    <section class="card" v-if="rep.states"><h2>État du superviseur</h2>
      <p>{{rep.states.bounded_runner}} · {{rep.states.continuous}} · {{rep.states.windows}}</p>
      <p v-if="rep.supervisor">Collecteur : {{rep.supervisor.status || 'non démarré'}} · actif : {{rep.supervisor.active ? 'oui' : 'non'}}.
        Origine : {{rep.supervisor.origin || 'aucune'}}. Clôtures observées : {{rep.supervisor.observed || 0}} ; manquantes : {{rep.supervisor.missing_slots ?? 'non mesurées'}}.
        La couverture passée ne sera jamais reconstruite par des prédictions tardives.</p>
    </section>
    <section class="card">
      <div class="verdict">
        <span class="big-badge" :class="rep.verdict === 'READY' ? 'ready' : 'blocked'" data-verdict>{{ rep.verdict === 'READY' ? 'PRÊT (READY)' : 'BLOQUÉ (BLOCKED)' }}</span>
        <div style="flex:1;min-width:220px">
          <p style="margin-top:0"><b v-if="rep.verdict === 'READY'">Tous les prérequis vérifiables sont remplis. Le démarrage reste une décision humaine, hors de ce panel.</b>
            <b v-else>L'observation ne peut pas commencer : {{ blocking.length }} point(s) bloquant(s) ci-dessous.</b></p>
          <p class="faint small" style="margin-bottom:0">Contrôlé le {{ utc(rep.checked_at) }} · capital {{ rep.capital_eur }} € fictifs · cryptos {{ rep.watchlist.join(', ') }} · bougies {{ rep.timeframe }}</p>
        </div>
      </div>
    </section>

    <section class="card" v-if="blocking.length"><h2>Ce qui bloque</h2>
      <ul class="checklist"><li v-for="c in blocking" :key="c.key" :class="c.status" :data-check="c.key">
        <span class="mark" :title="WORDS[c.status]">{{ MARKS[c.status] }}</span>
        <div><b>{{ c.label }}</b> <span class="badge" :class="c.status === 'blocked' ? 'err' : 'warn'">{{ WORDS[c.status] }}</span><div class="small muted">{{ c.detail }}</div></div></li></ul>
    </section>

    <section class="card"><h2>Autres contrôles</h2>
      <ul class="checklist"><li v-for="c in others" :key="c.key" :class="c.status" :data-check="c.key">
        <span class="mark" :title="WORDS[c.status]">{{ MARKS[c.status] }}</span>
        <div><b>{{ c.label }}</b> <span class="badge" :class="c.status === 'ok' ? 'ok' : c.status === 'warning' ? 'warn' : ''">{{ WORDS[c.status] }}</span><div class="small muted">{{ c.detail }}</div></div></li></ul>
    </section>

    <div class="grid cols-2">
      <section class="card"><h2>Politique gelée (protocole v2)</h2>
        <template v-if="rep.frozen.v2.ok">
          <dl class="kv">
            <dt>État</dt><dd>{{ rep.frozen.v2.launch_status === 'PREPARED_NOT_STARTED' ? 'Préparé, non démarré' : rep.frozen.v2.launch_status }}</dd>
            <dt>Période réservée</dt><dd>{{ isoUtc(rep.frozen.v2.start) }} → {{ isoUtc(rep.frozen.v2.end) }}</dd>
            <dt>Enregistré le</dt><dd>{{ isoUtc(rep.frozen.v2.registered_at) }}</dd>
            <dt>Critères</dt><dd>au moins {{ rep.frozen.v2.criteria?.minimum_closed_trades }} trades clôturés et {{ rep.frozen.v2.criteria?.minimum_days }} jours ; aucune réutilisation de l'historique pour une promotion</dd>
          </dl>
          <p class="hash">Protocole : {{ rep.frozen.v2.protocol_sha256 }}</p>
          <p class="hash">Modèle : {{ rep.frozen.v2.model_sha256 }}</p>
          <p class="hash">Réglages : {{ rep.frozen.v2.settings_sha256 }}</p>
        </template>
        <p v-else class="note danger">Intégrité refusée : {{ rep.frozen.v2.error }}</p>
        <p class="faint small">La préparation v1 est {{ rep.frozen.v1.invalid ? 'marquée invalide et écartée' : 'NON marquée invalide' }}.</p>
      </section>
      <section class="card"><h2>Observation macro parallèle (v3)</h2>
        <template v-if="rep.frozen.v3.ok">
          <dl class="kv">
            <dt>Mode</dt><dd>observation seule, aucun ordre ni filtre sur le portefeuille</dd>
            <dt>Fenêtre</dt><dd>{{ utc(rep.frozen.v3.observation_start) }} → {{ utc(rep.frozen.v3.observation_end) }}</dd>
            <dt>Bras comparés</dt><dd>{{ (rep.frozen.v3.arms || []).join(', ') }}</dd>
          </dl>
          <p class="hash">Protocole : {{ rep.frozen.v3.protocol_sha256 }}</p>
          <p class="hash">Référence v2 : {{ rep.frozen.v3.baseline_protocol_sha256 }}</p>
        </template>
        <p v-else class="note danger">Intégrité refusée : {{ rep.frozen.v3.error }}</p>
      </section>
    </div>

    <section class="card"><h2>Dernier diagnostic de l'installation</h2>
      <p v-if="!rep.diagnostic" class="muted">Aucun rapport de diagnostic trouvé. Sur Windows 11, lancez <code>tools\\diagnostic_windows.ps1</code> (voir docs/WINDOWS.md) : le rapport apparaîtra ici.</p>
      <dl v-else class="kv">
        <dt>Rapport</dt><dd>{{ rep.diagnostic.file }} · {{ utc(rep.diagnostic.generated_at) }}</dd>
        <dt>Système</dt><dd>{{ rep.diagnostic.platform }} · {{ rep.diagnostic.windows_actually_tested ? 'Windows réellement testé' : 'pas Windows : ne valide pas Windows' }}</dd>
        <dt>Installation</dt><dd>{{ rep.diagnostic.installation_ok ? 'fonctionnelle' : 'problèmes détectés' }}</dd>
        <dt>Verdict d'alors</dt><dd>{{ rep.diagnostic.observation_verdict || 'inconnu' }}</dd>
      </dl>
    </section>

    <section class="card"><h2>Limites connues</h2>
      <ul class="small"><li v-for="l in rep.limitations" :key="l">{{ l }}</li></ul>
      <p v-for="e in rep.errors" :key="e" class="note warn small">{{ e }}</p>
      <p class="faint small" style="margin-bottom:0">Le démarrage de l'observation n'est pas possible depuis le panel : il exige une approbation humaine explicite et le lanceur de la mission #30.</p>
    </section>
  </template>
  </div>`,
};
