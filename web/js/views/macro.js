// Local read-only shadow journal. No button triggers trading or collection.
import {api,safe} from '../api.js';
const {ref,onMounted}=Vue;
export default {
 setup(){const report=ref(null);const load=async()=>{report.value=await safe(api.get('/api/macro-shadow'));};
 const date=t=>t===null||t===undefined?'Inconnue':new Date(t*1000).toISOString();
 const value=v=>v===null||v===undefined?'Non disponible':String(v);
 onMounted(load);return{report,load,date,value};},
 template:`<div style="overflow-wrap:anywhere"><div class="page-head"><h1>Contexte macro et actualités</h1><button @click="load">Relire le journal local</button></div>
 <div class="note warn">Observation parallèle uniquement. Aucun ordre ni nouveau veto appliqué au portefeuille. Protocole prospectif v2 inchangé.</div>
 <template v-if="report"><p><b>{{report.status}}</b> · Analyse {{report.analysis_version}} · Connu au {{date(report.as_of)}}</p>
 <div class="card" v-for="warning in report.warnings" :key="warning">{{warning}}</div>
 <section class="card"><h2>Sources et fraîcheur</h2><div class="table-wrap"><table><thead><tr><th>Source</th><th>Statut</th><th>Dernière réception UTC</th><th>Stockage UTC</th><th>Provenance</th></tr></thead>
 <tbody><tr v-for="s in report.sources" :key="s.id"><td>{{s.id}} · {{s.official?'officielle':'secondaire'}}</td><td>{{s.state}} {{s.error||''}}</td><td>{{date(s.last_received_at)}}</td><td>{{date(s.last_stored_at)}}</td><td><a :href="s.url" target="_blank" rel="noopener noreferrer">Flux</a><small> {{s.dependency_group}}</small></td></tr></tbody></table></div></section>
 <section class="card"><h2>Calendrier : versions connues</h2><p v-if="!report.calendar.length">Aucun calendrier admissible observé. Aucun chiffre inventé.</p>
 <div class="table-wrap"><table v-if="report.calendar.length"><thead><tr><th>Événement</th><th>Prévu UTC</th><th>Forecast</th><th>Previous</th><th>Actual</th><th>Source / version</th><th>Disponible UTC</th></tr></thead><tbody>
 <tr v-for="e in report.calendar" :key="e.observation_id"><td>{{e.currency}} · {{e.title}} · {{e.impact}} · {{e.time_quality}}</td><td>{{date(e.scheduled_at)}}</td><td>{{value(e.forecast)}}</td><td>{{value(e.previous)}}</td><td>{{value(e.actual)}}</td><td>{{e.source}} / {{e.version}}</td><td>{{date(e.available_at)}}</td></tr></tbody></table></div></section>
 <section class="card"><h2>Données officielles publiées</h2><p>L’indice CPI BLS n’est pas le taux d’inflation y/y. Aucun rapprochement automatique d’unités différentes.</p><p v-if="!report.releases.length">Aucune valeur officielle reçue.</p>
 <div class="table-wrap"><table v-if="report.releases.length"><thead><tr><th>Série / période</th><th>Valeur et unité</th><th>Publication connue</th><th>Disponible UTC</th><th>Version</th></tr></thead><tbody><tr v-for="e in report.releases" :key="e.observation_id"><td>{{e.title}} · {{e.reference_period}}</td><td>{{value(e.actual)}} {{e.unit}}</td><td>{{date(e.published_at)}}</td><td>{{date(e.available_at)}}</td><td>{{e.source}} / {{e.version}}</td></tr></tbody></table></div></section>
 <section class="card"><h2>Actualités, faits et hypothèses</h2><p v-if="!report.news.length">Aucune actualité admissible reçue.</p><article v-for="n in report.news" :key="n.observation_id" style="border-bottom:1px solid var(--border);padding:12px 0;overflow-wrap:anywhere">
 <b>{{n.title}}</b><p>{{n.source}} · v{{n.version}} · disponible {{date(n.available_at)}} · publication {{date(n.published_at)}}</p>
 <p>{{n.excerpt}}</p><p>Instruction suspecte : {{n.suspicious_instruction?'oui':'non'}} · Actifs : {{n.assets.join(', ')||'non déterminés'}} · groupes de provenance (indépendance non prouvée) : {{n.independent_source_groups}} · rumeur {{n.rumor?'oui':'non'}} · contradiction {{n.contradictory?'oui':'non'}}</p>
 <p v-for="f in n.facts" :key="f">Fait de provenance : {{f}}</p><template v-if="n.analysis"><p>Analyse {{n.analysis.engine}}, disponible {{date(n.analysis.available_at)}}</p><p v-for="h in n.analysis.hypotheses" :key="h">Hypothèse : {{h}}</p></template></article></section>
 <section class="card"><h2>Décisions fictives annotées</h2><p>Régime de volatilité : {{report.advisory.volatility_regime}}. Aucun régime non observé n’est supposé.</p><p v-if="!report.decisions.length">Aucune décision annotée. La comparaison financière n’est pas encore évaluée.</p><div v-for="d in report.decisions" :key="d.decision_key"><p>{{date(d.as_of)}} · {{d.context.arms}} · Empreinte {{d.context_sha}}</p></div></section>
 </template></div>`
};
