// Carte « Contexte de marché » : Fear & Greed, indices, taux de financement, annonces macro
import { api, safe } from '../api.js';
import { pct, dt, ago } from '../store.js';

const { ref, onMounted, onBeforeUnmount } = Vue;

export const ContextCard = {
  setup() {
    const ctx = ref(null);
    const load = async () => { ctx.value = await safe(api.get('/api/context')); };
    const refresh = async () => { ctx.value = await safe(api.post('/api/context/refresh')); };
    let timer;
    onMounted(() => { load(); timer = setInterval(load, 300000); });
    onBeforeUnmount(() => clearInterval(timer));
    const fgColor = (v) => v < 25 ? 'var(--down)' : v < 45 ? 'var(--warn)' : v <= 55 ? 'var(--muted)' : v <= 75 ? 'var(--up)' : 'var(--accent)';
    const cls = (x) => x > 0 ? 'up' : x < 0 ? 'down' : '';
    return { ctx, refresh, fgColor, cls, pct, dt, ago };
  },
  template: `
  <div class="card">
    <div class="card-head"><h2>Contexte de marché</h2><span class="spacer"></span>
      <span class="faint small" v-if="ctx?.updated">mis à jour {{ ago(ctx.updated) }}</span>
      <button class="ghost small" @click="refresh" title="Actualiser">↻</button></div>
    <div v-if="!ctx || (!ctx.fear_greed && !Object.keys(ctx.indices || {}).length)" class="muted small">Données de contexte pas encore disponibles.</div>
    <template v-else>
      <div v-if="ctx.fear_greed" class="inline" style="gap:14px;margin-bottom:10px">
        <div class="big" :style="{color: fgColor(ctx.fear_greed.value)}">{{ ctx.fear_greed.value }}</div>
        <div><b>Fear & Greed : {{ ctx.fear_greed.label }}</b><div class="small muted">il y a 7 jours : {{ ctx.fear_greed.week_ago }} / 100</div></div>
      </div>
      <div class="progress" v-if="ctx.fear_greed" style="margin-bottom:12px"><div :style="{width: ctx.fear_greed.value + '%', background: fgColor(ctx.fear_greed.value)}"></div></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Indice</th><th class="num">1 jour</th><th class="num">5 jours</th></tr></thead>
        <tbody><tr v-for="(v, k) in ctx.indices" :key="k"><td>{{ v.label }}</td>
          <td class="num" :class="cls(v.change_1d)">{{ pct(v.change_1d) }}</td><td class="num" :class="cls(v.change_5d)">{{ pct(v.change_5d) }}</td></tr></tbody>
      </table></div>
      <div v-if="Object.keys(ctx.funding || {}).length" style="margin-top:10px" class="small">
        <b>Taux de financement (marchés à levier)</b>
        <div class="chips" style="margin-top:5px"><span v-for="(v, k) in ctx.funding" :key="k" class="badge" :title="v.comment">{{ k }} {{ (v.rate * 100).toFixed(4) }} %</span></div>
      </div>
    </template>
    <div style="margin-top:10px" class="small">
      <b>Annonces à venir</b> <span class="faint">(pas de nouvelle position autour)</span>
      <div v-if="!(ctx?.macro || []).length" class="muted">Aucune annonce enregistrée (Réglages → Calendrier macro).</div>
      <div v-for="e in (ctx?.macro || [])" :key="e.ts">{{ dt(e.ts) }} — {{ e.label }}</div>
    </div>
    <p class="faint small" style="margin-bottom:0">Informations de contexte : elles ne déclenchent aucun trade.</p>
  </div>`,
};
