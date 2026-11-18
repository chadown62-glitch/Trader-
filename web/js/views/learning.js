// Onglet « Capacités d'apprentissage » : banc d'essai (règles cachées, bruit pur, changement de règle) et suivi en direct
import { api, on, safe } from '../api.js';
import { store, dt, ago } from '../store.js';

const { ref, computed, onMounted, onBeforeUnmount } = Vue;

const nf0 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const LearningTab = {
  setup() {
    const bench = ref(null);
    const monitor = ref(null);
    const job = computed(() => Object.values(store.jobs).filter(j => j.kind === 'bench').sort((a, b) => b.started_at - a.started_at)[0]);
    const running = computed(() => job.value?.status === 'running');
    const load = async () => {
      const r = await safe(api.get('/api/ai/bench'));
      bench.value = r?.report || null;
      monitor.value = await safe(api.get('/api/ai/learning'));
    };
    const start = async () => { await safe(api.post('/api/ai/bench'), 'Test lancé : environ deux minutes'); };
    const off = on('job', (j) => { if (j.kind === 'bench' && j.status === 'done') load(); });
    const timer = setInterval(async () => { monitor.value = await safe(api.get('/api/ai/learning')); }, 60000);
    onMounted(load);
    onBeforeUnmount(() => { off(); clearInterval(timer); });
    const pc = (x) => (x === null || x === undefined) ? '—' : nf0.format(x * 100) + ' %';
    const r2 = (x) => (x === null || x === undefined) ? '—' : (x > 0 ? '+' : '') + nf2.format(x) + ' R';
    const grade = (x) => x === null || x === undefined ? '' : x >= 0.8 ? 'ok' : x >= 0.5 ? 'warn' : 'err';
    const learnable = computed(() => (bench.value?.rules || []).filter(r => r.learnable && r.name !== 'changement'));
    const shift = computed(() => (bench.value?.rules || []).find(r => r.name === 'changement'));
    const noise = computed(() => (bench.value?.rules || []).find(r => r.name === 'bruit'));
    const LEVELS = { mixte: 'mixte', prudent: 'simple', lineaire: 'linéaire', moyen: 'intermédiaire', riche: 'fin', comite: 'comité de trois modèles', auto: 'choisi automatiquement' };
    return { store, bench, monitor, job, running, start, pc, r2, grade, learnable, shift, noise, LEVELS, dt, ago };
  },
  template: `
  <div>
    <div class="note info small" style="margin-bottom:14px">
      <b>Comment mesurer si une IA « apprend bien » ?</b> Sur un vrai marché, personne ne connaît la bonne réponse. Ce test fabrique donc des marchés artificiels
      dans lesquels <b>une règle est cachée exprès</b>, puis mesure quelle part de cette règle l'IA retrouve sur des données qu'elle n'a jamais vues, avec exactement
      la même chaîne d'entraînement que pour le vrai marché. <b>Taux d'apprentissage de 100 %</b> : toute la règle a été retrouvée.
    </div>
    <div class="grid cols-2" style="margin-bottom:14px">
      <div class="card">
        <div class="card-head"><h2>Taux d'apprentissage</h2><span class="spacer"></span>
          <span v-if="bench" class="faint small">testé {{ ago(bench.ts) }}</span></div>
        <div v-if="bench" class="inline" style="gap:16px;margin-bottom:10px">
          <div class="big" :class="grade(bench.score) === 'ok' ? 'up' : grade(bench.score) === 'err' ? 'down' : ''">{{ pc(bench.score) }}</div>
          <div class="small muted">des règles cachées retrouvées, en moyenne sur quatre types de règles<br>(modèle {{ LEVELS[bench.recipe] || bench.recipe }}, {{ bench.sizes[bench.sizes.length - 1] }} situations d'apprentissage)</div>
        </div>
        <div v-else class="muted small" style="margin-bottom:10px">Aucun test effectué pour le moment.</div>
        <button class="primary" :disabled="running" @click="start">🧪 Tester les capacités d'apprentissage</button>
        <div v-if="job" style="margin-top:12px">
          <div class="small muted">{{ job.status==='running' ? job.message : job.status==='done' ? 'Test terminé' : 'Échec : ' + job.error }}</div>
          <div class="progress" style="margin-top:6px" :class="{ok: job.status==='done'}"><div :style="{width: (job.progress*100)+'%'}"></div></div>
        </div>
        <p class="faint small" style="margin-bottom:0">Le test dure environ deux minutes et n'utilise aucun argent ni aucune donnée de marché.</p>
      </div>
      <div class="card">
        <h2>Ce que l'IA apprend en direct</h2>
        <div v-if="!monitor" class="muted small">Chargement…</div>
        <template v-else>
          <dl class="kv small">
            <dt>Situations observées en direct</dt><dd>{{ monitor.seen }} (dont {{ monitor.resolved }} dénouées, {{ monitor.pending }} en cours)</dd>
            <dt>Modèle actif</dt><dd>{{ monitor.model ? monitor.model.version + ' — recette ' + monitor.model.level + (monitor.model.mode === 'règles seules' ? ' (règles seules)' : '') + (monitor.model.trial ? ' — à l’essai en argent fictif' : '') : 'aucun (exploration)' }}</dd>
            <dt>Ré-entraînement automatique</dt>
            <dd v-if="monitor.retraining.enabled">toutes les {{ monitor.retraining.every_hours }} h, dès {{ monitor.retraining.min_new_samples }} nouvelles situations dénouées
              ({{ monitor.retraining.new_samples }} depuis le dernier{{ monitor.retraining.last ? ', ' + ago(monitor.retraining.last) : '' }})</dd>
            <dd v-else>désactivé (Réglages → IA)</dd>
          </dl>
          <div v-if="monitor.forecast" class="note small" :class="monitor.forecast.drift ? 'warn' : 'ok'" style="margin-top:10px">
            Sur {{ monitor.forecast.n }} situations notées par l'IA puis dénouées : elle annonçait en moyenne <b>{{ pc(monitor.forecast.announced) }}</b> de chances de gain,
            il y en a eu <b>{{ pc(monitor.forecast.observed) }}</b>.
            <span v-if="monitor.forecast.drift"> L'écart est net : l'IA est trop {{ monitor.forecast.optimistic ? 'optimiste' : 'pessimiste' }} en ce moment. Le prochain ré-entraînement en tiendra compte.</span>
            <span v-else-if="monitor.forecast.n >= 30"> Ses prévisions correspondent à la réalité.</span>
            <span v-else> Trop peu de cas pour conclure (30 minimum).</span>
          </div>
          <div v-else class="muted small" style="margin-top:10px">Pas encore de prévision vérifiable : il faut un modèle (validé ou à l'essai) et des situations dénouées en direct.</div>
        </template>
      </div>
    </div>

    <div v-if="bench" class="card" style="margin-bottom:14px">
      <h2>Détail des épreuves</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Épreuve</th><th>Ce qui est caché</th><th v-for="n in bench.sizes" :key="n" class="num">{{ n }} situations</th><th class="num">Gain grâce à l'IA</th><th class="num">Tri de l'IA validé</th></tr></thead>
        <tbody>
          <tr v-for="r in learnable" :key="r.name">
            <td><b>{{ r.label }}</b></td><td class="small muted">{{ r.description }}</td>
            <td v-for="s in r.by_size" :key="s.n" class="num"><span class="badge" :class="grade(s.rate)">{{ pc(s.rate) }}</span></td>
            <td class="num">{{ r2(r.by_size[r.by_size.length - 1].model_r - r.by_size[r.by_size.length - 1].base_r) }} par trade</td>
            <td class="num">{{ r.by_size[r.by_size.length - 1].validated }} / {{ r.by_size[r.by_size.length - 1].runs }}</td>
          </tr>
        </tbody>
      </table></div>
      <p class="faint small">Chaque case est une moyenne sur {{ bench.seeds }} marchés artificiels différents. Plus l'IA dispose de situations, mieux elle apprend : un signal faible demande beaucoup d'exemples.</p>
      <div class="grid cols-2" style="margin-top:6px">
        <div v-if="noise" class="note small" :class="noise.false_discoveries === 0 ? 'ok' : 'warn'">
          <b>Bruit pur (aucune règle à trouver).</b> L'IA a prétendu avoir trouvé quelque chose dans <b>{{ noise.false_discoveries }}</b> cas sur {{ noise.runs }}.
          {{ noise.false_discoveries === 0 ? 'Elle ne s’invente pas de fausses règles.' : 'Le critère de validation tolère environ une fausse alerte sur vingt : c’est attendu, et l’entraînement en argent fictif sert de second filtre.' }}
        </div>
        <div v-if="shift" class="note small" :class="(shift.rate || 0) >= 0.5 ? 'ok' : 'warn'">
          <b>Changement de règle en cours de route.</b> Taux d'adaptation : <b>{{ pc(shift.rate) }}</b>. Le tri de l'IA s'est validé dans {{ shift.by_size[shift.by_size.length - 1].validated }} cas sur {{ shift.by_size[shift.by_size.length - 1].runs }}.
          Quand le marché change brutalement de logique, l'IA ne s'adapte pas tout de suite : elle a besoin de nouveaux exemples. Sa protection est de <b>refuser de valider son tri</b> tant que la nouvelle règle n'est pas apprise.
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Et sur le vrai marché ?</h2>
      <p class="small">Savoir apprendre ne suffit pas : encore faut-il qu'il y ait quelque chose de stable à apprendre. Mesures faites en novembre 2026 sur 33 cryptos et jusqu'à 475 000 exemples
        (détail dans <code>docs/RESULTATS.md</code>) :</p>
      <ul class="small" style="margin:0 0 0 18px;padding:0">
        <li>les prix passés contiennent <b>très peu d'information stable</b> sur la suite : même avec des centaines de milliers d'exemples, le score sur les périodes suivantes reste proche du hasard ;</li>
        <li>l'IA garde un <b>petit avantage réel</b> pour trier les signaux des stratégies : faible, et inégal d'une période à l'autre ;</li>
        <li>connaître le passé d'une crypto <b>ne prédit presque rien</b> de ses résultats futurs : les fiches par crypto servent à doser le risque, pas à choisir les trades.</li>
      </ul>
      <p class="faint small" style="margin-bottom:0">C'est pourquoi l'IA reste prudente. Cette version ne peut de toute façon passer aucun ordre réel : tout se passe en argent fictif.</p>
    </div>
  </div>`,
};
