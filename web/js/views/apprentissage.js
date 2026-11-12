// Selective #26 integration. Read-only; no training, promotion or rollback buttons.
import {api,safe} from '../api.js';
const {ref,onMounted}=Vue;
export default {
 setup(){
  const status=ref(null),journal=ref(null),filter=ref(''),open=ref(null);
  const load=async()=>{status.value=await safe(api.get('/api/ai/champion'));
   journal.value=await safe(api.get('/api/ai/journal?limit=60&decision='+encodeURIComponent(filter.value)));};
  const val=v=>v===null||v===undefined?'Non disponible':String(v);
  const date=t=>t===null||t===undefined?'Inconnue':new Date(t*1000).toISOString();
  const percent=v=>v===null||v===undefined?'Non disponible':(100*v).toFixed(2)+' %';
  onMounted(load);return{status,journal,filter,open,load,val,date,percent};
 },
 template:`<div style="overflow-wrap:anywhere">
 <div class="page-head"><h1>Apprentissage</h1><button @click="load">Relire les journaux</button></div>
 <div class="note warn">Observation parallèle. Aucune promotion ni rétrogradation automatique. Protocoles prospectifs v2 et v3 conservés.</div>
 <template v-if="status"><p><b>{{status.status}}</b> · Horizon {{status.timeframe}}</p>
 <div class="grid cols-3"><section v-for="role in ['champion','challenger','trial']" :key="role" class="card">
 <h2>{{role==='trial'?'À l’essai':role==='champion'?'Champion':'Challenger'}}</h2>
 <template v-if="status[role]"><code>{{status[role].version}}</code><p>Statut registre : {{status[role].status}} · {{status[role].integrity}}</p>
 <p>Créé {{date(status[role].created_at)}} · Promu {{date(status[role].promoted_at)}}</p>
 <p>Recette {{status[role].params.recipe||'ancienne version'}} · seuil {{val(status[role].params.threshold)}} R</p>
 <p>Fin des données vues {{date(status[role].params.trained_until/1000||null)}}</p>
 <p>SHA-256 : {{status[role].sha256||'Historique sans empreinte, non vérifié'}}</p></template>
 <p v-else>Aucun modèle de ce statut enregistré.</p></section></div>
 <section class="card"><h2>Comparaisons préenregistrées</h2><p v-if="!status.comparisons.length">Aucune comparaison prospective enregistrée. Aucun résultat inventé.</p>
 <article v-for="c in status.comparisons" :key="c.protocol_sha"><p>{{c.origin}} · {{c.protocol_sha}}</p>
 <p>{{c.resolved}} observations communes résolues ; {{c.pending}} en attente ; {{c.observed_days}} jours observés.</p>
 <p>{{c.verdict}} : {{c.explanation}}</p><div class="table-wrap"><table><thead><tr><th>Modèle</th><th>AUC</th><th>Brier</th><th>PnL portefeuille</th></tr></thead>
 <tbody><tr v-for="(d,role) in c.diagnostics" :key="role"><td>{{role}}</td><td>{{val(d.auc)}}</td><td>{{val(d.brier)}}</td><td>Non évalué</td></tr></tbody></table></div></article></section>
 <section class="card"><h2>Règles et verrous</h2><ul><li v-for="rule in status.rules" :key="rule">{{rule}}</li></ul>
 <p v-for="lock in status.policy_locks" :key="lock.protocol_sha">{{lock.reason}} · jusqu’au {{date(lock.end)}}</p></section>
 <section class="card"><h2>Historique des modèles</h2><div class="table-wrap"><table><thead><tr><th>Date UTC</th><th>Version</th><th>Événement</th><th>Raison</th></tr></thead>
 <tbody><tr v-for="e in status.history" :key="e.id"><td>{{date(e.ts)}}</td><td>{{e.version}}</td><td>{{e.event}}</td><td>{{e.reason}}</td></tr>
 <tr v-if="!status.history.length"><td colspan="4">Aucun événement enregistré.</td></tr></tbody></table></div></section>
 <section class="card"><h2>Journal des expériences</h2><p>{{status.experiments.total}} essais enregistrés, y compris résultats négatifs et interruptions. {{status.experiments.note}}</p>
 <div class="table-wrap"><table><thead><tr><th>Date UTC</th><th>Type</th><th>Configuration SHA</th><th>État</th></tr></thead><tbody>
 <tr v-for="e in status.experiments.rows" :key="e.id"><td>{{date(e.ts)}}</td><td>{{e.kind}}</td><td>{{e.config_hash}}</td><td>{{e.result?.status||'en cours ou interrompu'}}</td></tr></tbody></table></div></section>
 </template>
 <section class="card"><h2>Journal des décisions</h2><label>Décisions <select v-model="filter" @change="load"><option value="">Toutes</option><option>pris</option><option>refusé</option><option>préparé</option></select></label>
 <p>Le plan initial précède l’ordre. Un résultat théorique de signal ne constitue pas un gain du portefeuille.</p>
 <template v-if="journal"><p v-if="!journal.rows.length">Aucune décision enregistrée.</p><div class="table-wrap"><table v-if="journal.rows.length"><thead><tr><th>Date UTC</th><th>Actif / stratégie</th><th>Décision</th><th>Probabilité</th><th>Réalisé fictif</th><th>Étiquette théorique</th></tr></thead><tbody>
 <template v-for="r in journal.rows" :key="r.id"><tr><td>{{date(r.ts)}}</td><td><button @click="open=open===r.id?null:r.id">{{r.symbol}} · {{r.strategy}}</button></td><td>{{r.decision}} : {{r.reason}}</td><td>{{percent(r.p_win)}}</td>
 <td>{{r.actual?val(r.actual.realized_pnl)+' '+r.actual.currency:'Non disponible'}}<span v-if="r.actual?.partial_realization"> (sortie partielle)</span><span v-if="r.link_missing"> · liaison manquante</span></td><td>{{val(r.theoretical.r)}} R · hors portefeuille</td></tr>
 <tr v-if="open===r.id"><td colspan="6"><p>Version {{r.model_version||'sans modèle'}} · enregistré {{date(r.recorded_at)}}</p>
 <template v-if="r.plan"><p v-for="reason in r.plan.raisons" :key="reason">{{reason}}</p><p>Entrée de référence {{r.plan.entree.reference}}, stop {{r.plan.stop.prix}}, objectif {{val(r.plan.objectif.prix)}}.</p>
 <p>Frais estimés {{r.plan.frais.en_r}} R. {{r.plan.scenario_note}} {{r.plan.probabilite.source}}</p><p v-for="s in r.plan.scenarios" :key="s.nom">{{s.nom}} : {{percent(s.probabilite)}} · {{s.description}}</p></template>
 <p v-else>Ancienne décision sans plan archivé ; aucun plan reconstruit après l’issue.</p><p v-if="r.review">{{r.review.detail}}</p></td></tr></template></tbody></table></div></template></section>
 </div>`
};
