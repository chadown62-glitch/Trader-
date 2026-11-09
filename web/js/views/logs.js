// Journal du système
import { api, safe } from '../api.js';
import { dt } from '../store.js';

const { ref, onMounted, onBeforeUnmount, watch } = Vue;
const LEVELS = { info: ['Info', ''], warning: ['Attention', 'warn'], error: ['Erreur', 'err'], critical: ['Critique', 'err'] };

export default {
  setup() {
    const level = ref('');
    const rows = ref([]);
    const load = async () => { rows.value = (await safe(api.get('/api/logs?limit=300' + (level.value ? '&level=' + level.value : '')))) || []; };
    let timer;
    onMounted(() => { load(); timer = setInterval(load, 10000); });
    onBeforeUnmount(() => clearInterval(timer));
    watch(level, load);
    return { level, rows, LEVELS, dt };
  },
  template: `
  <div>
    <div class="page-head"><h1>Journal</h1><span class="spacer"></span>
      <div class="seg"><button :class="{on: level===''}" @click="level=''">Tout</button><button v-for="(v, k) in LEVELS" :key="k" :class="{on: level===k}" @click="level=k">{{ v[0] }}</button></div></div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Date</th><th>Niveau</th><th>Catégorie</th><th>Message</th></tr></thead>
      <tbody><tr v-for="r in rows" :key="r.id"><td class="nowrap">{{ dt(r.ts) }}</td><td><span class="badge" :class="(LEVELS[r.level]||[])[1]">{{ (LEVELS[r.level]||[r.level])[0] }}</span></td><td>{{ r.category }}</td><td>{{ r.message }}</td></tr>
        <tr v-if="!rows.length"><td colspan="4" class="empty">Journal vide.</td></tr></tbody>
    </table></div></div>
  </div>`,
};
