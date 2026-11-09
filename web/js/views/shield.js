// Carte « Bouclier temps réel » : pauses en cours (volatilité, liquidations, actualité) et mesures
import { store, ago } from '../store.js';

const { computed } = Vue;

const hm = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' });

export const ShieldCard = {
  setup() {
    const sh = computed(() => store.overview?.shield || null);
    const pauses = computed(() => {
      const s = sh.value;
      if (!s) return [];
      const out = Object.entries(s.volatility || {}).map(([sym, v]) => ({ key: 'v' + sym, text: v.reason, until: v.until }));
      if (s.cascade) out.push({ key: 'cascade', text: s.cascade.reason, until: s.cascade.until });
      if (s.news) out.push({ key: 'news', text: s.news, until: null });
      return out;
    });
    const tracked = computed(() => Object.keys(sh.value?.tracked || {}).length);
    const demo = computed(() => store.overview?.settings_summary?.source === 'synthetic');
    const warming = computed(() => Object.values(sh.value?.tracked || {}).some(n => n < 30));
    const musd = (x) => (x === null || x === undefined) ? '—' : new Intl.NumberFormat('fr-FR', { maximumFractionDigits: x < 10 ? 1 : 0 }).format(x) + ' M$';
    const until = (ts) => ts ? 'jusqu’à ' + hm.format(new Date(ts * 1000)) : '';
    return { sh, pauses, tracked, warming, demo, musd, until, ago };
  },
  template: `
  <div class="card">
    <div class="card-head"><h2>Bouclier temps réel</h2><span class="spacer"></span>
      <span v-if="sh" class="badge" :class="!sh.enabled ? '' : pauses.length ? 'warn' : 'ok'">{{ !sh.enabled ? 'désactivé' : pauses.length ? 'entrées en pause' : 'actif' }}</span></div>
    <div v-if="!sh" class="muted small">Chargement…</div>
    <template v-else>
      <div v-if="!sh.enabled" class="muted small">Le bouclier est désactivé (Réglages → Bouclier). Les protections du gestionnaire de risque restent actives.</div>
      <template v-else>
        <div v-if="pauses.length" class="note warn small" style="margin-bottom:10px">
          <div v-for="p in pauses" :key="p.key">⏸ {{ p.text }} <span class="faint">{{ until(p.until) }}</span></div>
          <div class="faint" style="margin-top:4px">Aucune nouvelle position pendant la pause. Les positions ouvertes gardent leurs stops.</div>
        </div>
        <div v-else class="note ok small" style="margin-bottom:10px">Aucun danger détecté : les entrées sont autorisées.</div>
        <dl class="kv small">
          <dt>Liquidations forcées (15 min)</dt>
          <dd v-if="demo" class="muted">non suivies en démo (marché simulé)</dd>
          <dd v-else>{{ musd(sh.metrics?.liquidations_15m_musd) }} <span class="faint" v-if="sh.metrics?.liquidations_ref_musd !== null && sh.metrics?.liquidations_ref_musd !== undefined">(habituel : {{ musd(sh.metrics.liquidations_ref_musd) }})</span></dd>
          <template v-if="!demo"><dt>Dernière vérification</dt><dd>{{ sh.last_liquidation_check ? ago(sh.last_liquidation_check) : 'en cours (lecture des dernières 24 h)' }}</dd></template>
          <dt>Surveillance minute par minute</dt><dd>{{ tracked }} crypto(s){{ warming ? ' — mise en route (30 min d’observation nécessaires)' : '' }}</dd>
        </dl>
      </template>
      <p class="faint small" style="margin-bottom:0">Le bouclier n’achète ni ne vend : il empêche seulement d’ouvrir une position au mauvais moment (mouvement brutal, carnet d’ordres vide, vague de liquidations, actualité majeure).</p>
    </template>
  </div>`,
};
