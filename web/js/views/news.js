// Actualités analysées par l'IA locale (Ollama)
import { api, on, safe } from '../api.js';
import { store, dt, ago } from '../store.js';

const { ref, onMounted, onBeforeUnmount } = Vue;

export default {
  setup() {
    const items = ref([]);
    const status = ref(null);
    const load = async () => {
      items.value = (await safe(api.get('/api/news?limit=150'))) || [];
      status.value = await safe(api.get('/api/news/status'));
    };
    const refresh = async () => { await safe(api.post('/api/news/refresh'), 'Analyse lancée : les actualités apparaîtront dans quelques instants'); setTimeout(load, 15000); };
    const enable = async () => { if (await safe(api.put('/api/settings', { news: { enabled: true } }), 'Module Actualités activé')) { store.settings.news.enabled = true; setTimeout(load, 3000); } };
    const off = on('news', load);
    const timer = setInterval(load, 60000);
    onMounted(load);
    onBeforeUnmount(() => { off(); clearInterval(timer); });
    const tone = (s) => s === null || s === undefined ? '' : s > 0.25 ? 'pos' : s < -0.25 ? 'neg' : '';
    const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
    const others = (n) => [...new Set((n.also || []).map(a => a.source).filter(s => s !== n.source))];
    return { store, items, status, refresh, enable, tone, safeUrl, others, dt, ago };
  },
  template: `
  <div>
    <div class="page-head"><h1>Actualités</h1><span class="spacer"></span>
      <button v-if="store.settings?.news?.enabled" @click="refresh">↻ Analyser maintenant</button></div>
    <div v-if="status && !status.enabled" class="note info" style="margin-bottom:14px">
      <b>Module désactivé.</b> Il lit les actualités crypto et les communiqués officiels (Fed, BCE, SEC), et en option des canaux Telegram de dépêches, puis les fait analyser par une IA <b>locale et gratuite</b> (Ollama, sur votre carte graphique).
      L'IA de trading s'en sert comme <b>garde du corps</b> : une nouvelle très négative et confirmée bloque les achats.
      <ol class="small" style="margin:8px 0 8px 18px;padding:0">
        <li>Installez Ollama : <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a></li>
        <li>Dans un terminal : <code>ollama pull {{ store.settings?.news?.model }}</code></li>
        <li>Activez le module ci-dessous.</li>
      </ol>
      <button class="primary small" @click="enable">Activer le module Actualités</button>
    </div>
    <div v-else-if="status" class="card tight" style="margin-bottom:14px">
      <div class="lights">
        <span class="light"><span class="dot" :class="status.ollama_ok ? 'ok' : 'err'"></span>Ollama {{ status.ollama_ok ? 'connecté' : 'injoignable' }}</span>
        <span class="light">Modèle : {{ status.model }}<span v-if="status.ollama_ok && status.models_installed && !status.models_installed.includes(status.model)" class="warn">&nbsp;(non installé : ollama pull {{ status.model }})</span></span>
        <span class="light">Dernière analyse : {{ ago(status.last_cycle) }}</span>
        <span v-if="status.error" class="light down">{{ status.error }}</span>
      </div>
    </div>
    <div class="card">
      <div v-if="!items.length" class="empty">Aucune actualité pour le moment.</div>
      <div v-for="n in items" :key="n.id" class="news-item">
        <div class="bar" :class="tone(n.sentiment)"></div>
        <div>
          <h4><a v-if="safeUrl(n.url)" :href="safeUrl(n.url)" target="_blank" rel="noopener noreferrer">{{ n.title }}</a><span v-else>{{ n.title }}</span></h4>
          <div class="small muted">{{ n.source }}<span v-if="others(n).length" :title="others(n).join(', ')"> + {{ others(n).length }} autre{{ others(n).length > 1 ? 's' : '' }} source{{ others(n).length > 1 ? 's' : '' }}</span> · {{ dt(n.published_ts) }}
            <template v-if="n.sentiment !== null"> · impact {{ n.sentiment > 0 ? '+' : '' }}{{ n.sentiment?.toFixed(2) }} · importance {{ Math.round((n.importance||0)*100) }} %</template>
            <template v-else-if="n.analysis && n.analysis.error"> · analyse impossible</template>
            <template v-else> · en attente d'analyse</template></div>
          <div v-if="n.analysis && n.analysis.summary_fr" class="small" style="margin-top:3px">{{ n.analysis.summary_fr }}</div>
          <div class="chips" style="margin-top:5px" v-if="(n.assets||[]).length"><span v-for="a in n.assets" :key="a" class="badge">{{ a === '*' ? 'tout le marché' : a }}</span></div>
        </div>
      </div>
    </div>
  </div>`,
};
