// Trader : graphique temps réel, ticket d'ordre avec calculatrice de risque, avis de l'IA, positions
import { api, on, safe } from '../api.js';
import { CandleChart } from '../charts.js';
import { store, fxLine, toast, confirmBox, money, signed, price, qty, pct, cls, dt, rr, STRATEGY_LABELS, accountLabel } from '../store.js';

const { ref, reactive, computed, onMounted, onBeforeUnmount, watch } = Vue;
const TF_MS = { '1m': 60e3, '5m': 300e3, '15m': 900e3, '30m': 1800e3, '1h': 3600e3, '4h': 14400e3, '1d': 86400e3 };

export default {
  components: { CandleChart },
  setup() {
    const chart = ref(null);
    const symbol = ref(store.markets.watchlist[0] || 'BTC/USDC');
    const tf = ref('15m');
    const candles = ref([]);
    const loading = ref(false);
    const detail = ref(null);
    const trades = ref([]);
    const coach = ref(null);
    const preview = ref(null);
    const busy = ref(false);
    const accounts = computed(() => (store.overview?.accounts || []).filter(a => a.connected !== false && a.kind === 'paper' && a.tradable !== false));
    const accountId = ref(null);
    const account = computed(() => accounts.value.find(a => a.id === accountId.value));
    const t = reactive({ side: 'buy', type: 'market', mode: 'quote', amount: null, limit: null, sl: null, tp: null, useRisk: false, riskPct: 0.5 });

    const last = computed(() => store.markets.prices[symbol.value] ?? (candles.value.length ? candles.value[candles.value.length - 1][4] : null));
    const dayChange = computed(() => {
      const c = candles.value;
      if (!c.length || !last.value) return null;
      const target = c[c.length - 1][0] - 86400e3;
      const ref0 = c.find(x => x[0] >= target) || c[0];
      return last.value / ref0[1] - 1;
    });

    const pickAccount = () => {
      if (accountId.value && accounts.value.some(a => a.id === accountId.value)) return;
      const manual = accounts.value.find(a => a.role === 'main') || accounts.value.find(a => a.role === 'manual') || accounts.value[0];
      accountId.value = manual?.id ?? null;
    };
    watch(accounts, pickAccount, { immediate: true });

    const loadCandles = async () => {
      loading.value = true;
      const r = await safe(api.get(`/api/candles?symbol=${encodeURIComponent(symbol.value)}&tf=${tf.value}&limit=500`));
      loading.value = false;
      if (r) { candles.value = r.candles; setTimeout(() => chart.value && chart.value.fit(), 50); }
    };
    const loadDetail = async () => {
      if (!accountId.value) return;
      detail.value = await safe(api.get(`/api/accounts/${accountId.value}`));
    };
    const loadTrades = async () => {
      if (!accountId.value) return;
      trades.value = (await safe(api.get(`/api/trades?account_id=${accountId.value}&limit=200`))) || [];
    };
    const loadCoach = async () => { coach.value = await safe(api.get(`/api/coach?symbol=${encodeURIComponent(symbol.value)}`)); };

    const offTick = on('tick', (d) => {
      if (d.symbol !== symbol.value || !candles.value.length) return;
      const step = TF_MS[tf.value];
      const bucket = Math.floor(d.ts / step) * step;
      const arr = candles.value;
      const lastC = arr[arr.length - 1];
      let c;
      if (lastC[0] === bucket) {
        c = [lastC[0], lastC[1], Math.max(lastC[2], d.price), Math.min(lastC[3], d.price), d.price, lastC[5]];
        arr[arr.length - 1] = c;
      } else if (bucket > lastC[0]) {
        c = [bucket, lastC[4], Math.max(lastC[4], d.price), Math.min(lastC[4], d.price), d.price, 0];
        arr.push(c);
      } else return;
      chart.value && chart.value.updateLast(c);
    });
    const offPos = [on('position_opened', loadDetail), on('position_closed', () => { loadDetail(); loadTrades(); }), on('position_updated', loadDetail)];
    let timer = null, coachTimer = null;
    onMounted(() => { loadCandles(); loadDetail(); loadTrades(); loadCoach(); timer = setInterval(loadDetail, 4000); coachTimer = setInterval(loadCoach, 60000); });
    onBeforeUnmount(() => { offTick(); offPos.forEach(f => f()); clearInterval(timer); clearInterval(coachTimer); });
    watch([symbol, tf], loadCandles);
    watch(symbol, () => { loadCoach(); preview.value = null; });
    watch(accountId, () => { loadDetail(); loadTrades(); });

    const positionsHere = computed(() => (detail.value?.positions || []));
    const lines = computed(() => {
      const out = [];
      positionsHere.value.filter(p => p.symbol === symbol.value && p.status === 'open').forEach(p => {
        out.push({ price: p.entry_price, color: '#60a5fa', title: 'Entrée' });
        if (p.stop_price) out.push({ price: p.stop_price, color: '#f43f5e', title: 'Stop' });
        if (p.target_price) out.push({ price: p.target_price, color: '#22c55e', title: 'Objectif' });
      });
      if (t.sl && t.side === 'buy') out.push({ price: Number(t.sl), color: 'rgba(244,63,94,.6)', title: 'Stop (ticket)', style: 3 });
      if (t.tp && t.side === 'buy') out.push({ price: Number(t.tp), color: 'rgba(34,197,94,.6)', title: 'Objectif (ticket)', style: 3 });
      return out;
    });
    const markers = computed(() => {
      const out = [];
      const first = candles.value.length ? candles.value[0][0] : 0;
      trades.value.filter(x => x.symbol === symbol.value).forEach(x => {
        if (x.entry_ts * 1000 >= first) out.push({ time: x.entry_ts * 1000, position: 'belowBar', color: '#60a5fa', shape: 'arrowUp', text: x.origin === 'ai' ? 'IA' : 'Achat' });
        if (x.exit_ts && x.exit_ts * 1000 >= first) out.push({ time: x.exit_ts * 1000, position: 'aboveBar', color: x.realized_pnl >= 0 ? '#22c55e' : '#f43f5e', shape: 'arrowDown', text: (x.realized_pnl >= 0 ? '+' : '') + x.realized_pnl.toFixed(2) });
      });
      positionsHere.value.filter(p => p.symbol === symbol.value && p.entry_ts && p.entry_ts * 1000 >= first).forEach(p => {
        out.push({ time: p.entry_ts * 1000, position: 'belowBar', color: '#60a5fa', shape: 'arrowUp', text: p.origin === 'ai' ? 'IA' : 'Achat' });
      });
      return out;
    });

    // -- ticket d'ordre --
    const base = computed(() => symbol.value.split('/')[0]);
    const quoteCcy = computed(() => symbol.value.split('/')[1]);
    // monnaie du compte (euros pour le portefeuille principal) : c'est elle que l'on dépense
    const cashCcy = computed(() => detail.value?.quote || quoteCcy.value);
    const fxRate = computed(() => (cashCcy.value !== quoteCcy.value ? detail.value?.fx?.rate : 1) || 1);
    const cash = computed(() => detail.value?.available ?? detail.value?.cash ?? 0);
    const holding = computed(() => detail.value?.balances?.[base.value] ?? 0);
    const refPrice = computed(() => (t.type === 'limit' && t.limit ? Number(t.limit) : last.value));
    const roundPx = (x) => { const d = x >= 1000 ? 2 : x >= 1 ? 4 : 6; return Number(x.toFixed(d)); };
    const setSl = (p) => { if (refPrice.value) t.sl = roundPx(refPrice.value * (1 - p / 100)); };
    const setTp = (p) => { if (refPrice.value) t.tp = roundPx(refPrice.value * (1 + p / 100)); };
    const setPortion = (p) => {
      if (t.side === 'buy') { t.mode = 'quote'; t.amount = Math.floor(cash.value * p / 100 * 0.995 * 100) / 100; }
      else { t.mode = 'base'; t.amount = Number((holding.value * p / 100).toPrecision(8)); }
      t.useRisk = false;
    };
    const riskComputed = computed(() => {
      if (!t.useRisk || t.side !== 'buy' || !t.sl || !refPrice.value || Number(t.sl) >= refPrice.value) return null;
      const eq = detail.value?.equity || 0;
      const riskAmount = eq * t.riskPct / 100;
      const q = riskAmount / ((refPrice.value - Number(t.sl)) * fxRate.value);
      return { qty: q, value: q * refPrice.value, riskAmount };
    });
    const body = () => {
      const b = { account_id: accountId.value, symbol: symbol.value, side: t.side, type: t.type };
      if (t.type === 'limit') b.price = Number(t.limit) || null;
      if (riskComputed.value) b.amount = riskComputed.value.qty;
      else if (t.mode === 'quote') b[cashCcy.value !== quoteCcy.value ? 'cash_amount' : 'quote_amount'] = Number(t.amount) || null;
      else b.amount = Number(t.amount) || null;
      if (t.side === 'buy') { b.stop_loss = t.sl ? Number(t.sl) : null; b.take_profit = t.tp ? Number(t.tp) : null; }
      return b;
    };
    let pvTimer = null;
    watch([() => ({ ...t }), accountId, symbol, last], () => {
      clearTimeout(pvTimer);
      pvTimer = setTimeout(async () => {
        const b = body();
        if (!b.account_id || (!b.amount && !b.quote_amount && !b.cash_amount)) { preview.value = null; return; }
        try { preview.value = await api.post('/api/orders/preview', b); } catch (e) { preview.value = { error: e.message }; }
      }, 350);
    }, { deep: true });

    const submit = async () => {
      const b = body();
      if (t.side === 'buy' && !t.sl) {
        const ok = await confirmBox({ title: 'Achat sans stop-loss', message: 'Sans stop-loss, votre perte n’est pas limitée. Continuer quand même ?', confirmLabel: 'Acheter sans stop' });
        if (!ok) return;
      }
      busy.value = true;
      const r = await safe(api.post('/api/orders', b));
      busy.value = false;
      if (r) {
        toast(t.side === 'buy' ? 'Ordre d’achat exécuté' + (r.position?.status === 'pending' ? ' (en attente du prix limite)' : '') : 'Ordre de vente envoyé', 'success');
        (r.warnings || []).forEach(w => toast(w, 'warn', 8000));
        t.amount = null; t.sl = null; t.tp = null; t.limit = null; loadDetail(); loadTrades();
      }
    };
    const closePos = async (p) => {
      const ok = await confirmBox({ title: `Fermer la position ${p.symbol}`, message: `Vente de ${qty(p.qty)} au prix du marché (résultat latent ${signed(p.pnl)}).`, confirmLabel: 'Fermer' });
      if (!ok) return;
      await safe(api.post(`/api/positions/${p.id}/close`), 'Position fermée');
      loadDetail(); loadTrades();
    };
    const editing = ref(null);
    const editPos = (p) => { editing.value = { id: p.id, symbol: p.symbol, stop: p.stop_price, target: p.target_price }; };
    const saveEdit = async () => {
      const e = editing.value;
      const r = await safe(api.patch(`/api/positions/${e.id}`, { stop: e.stop ? Number(e.stop) : null, target: e.target ? Number(e.target) : null, clear_target: !e.target }), 'Protections mises à jour');
      if (r) { editing.value = null; loadDetail(); }
    };
    const cancelOrder = async (o) => { await safe(api.del(`/api/orders/${o.id}?account_id=${accountId.value}`), 'Ordre annulé'); loadDetail(); };
    const PURPOSE = { entry: 'Achat', stop: 'Stop-loss', target: 'Objectif', exit: 'Sortie', manual: 'Manuel' };

    return { store, chart, symbol, tf, candles, loading, detail, coach, preview, busy, accounts, accountId, account, t, last, dayChange,
      lines, markers, base, quoteCcy, cashCcy, cash, holding, fxLine, setSl, setTp, setPortion, riskComputed, submit, closePos, editing, editPos, saveEdit,
      cancelOrder, positionsHere, PURPOSE, money, signed, price, qty, pct, cls, dt, rr, STRATEGY_LABELS, accountLabel };
  },
  template: `
  <div>
    <div class="page-head"><h1>Trader</h1>
      <div class="symbol-tabs"><button v-for="s in store.markets.watchlist" :key="s" :class="{on: s===symbol}" @click="symbol=s">{{ s.split('/')[0] }}</button></div>
      <span class="spacer"></span>
      <select v-model="accountId" style="width:auto;min-width:200px">
        <option v-for="a in accounts" :key="a.id" :value="a.id">{{ accountLabel(a) }}</option>
      </select>
      <span v-if="account" class="badge paper">ARGENT FICTIF</span>
    </div>
    <div class="trade-layout">
      <div class="col">
        <div class="card c-chart" style="margin-bottom:14px">
          <div class="card-head">
            <div class="ticker"><h2 style="margin:0">{{ symbol }}</h2><span class="px">{{ price(last) }}</span>
              <span v-if="dayChange !== null" class="num" :class="cls(dayChange)">{{ pct(dayChange) }} <span class="faint small">24 h</span></span></div>
            <span class="spacer"></span>
            <div class="seg"><button v-for="x in ['1m','5m','15m','1h','4h','1d']" :key="x" :class="{on: tf===x}" @click="tf=x">{{ x }}</button></div>
          </div>
          <CandleChart ref="chart" :candles="candles" :markers="markers" :lines="lines" />
          <div class="legend" style="margin-top:6px"><span><i style="background:#60a5fa"></i>Entrée</span><span><i style="background:#f43f5e"></i>Stop-loss</span><span><i style="background:#22c55e"></i>Objectif</span><span class="faint">Graphiques TradingView Lightweight Charts™</span></div>
        </div>

        <div class="card c-pos" style="margin-bottom:14px">
          <div class="card-head"><h2>Positions ouvertes</h2><span class="spacer"></span><span class="muted small" v-if="detail">Capital {{ money(detail.equity, detail.quote) }} · disponible {{ money(detail.available ?? detail.cash, detail.quote) }}</span></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Crypto</th><th>Origine</th><th class="num">Quantité</th><th class="num">Entrée</th><th class="num">Prix</th><th class="num">Résultat</th><th class="num">Stop</th><th class="num">Objectif</th><th></th></tr></thead>
            <tbody>
              <tr v-for="p in positionsHere" :key="p.id">
                <td><b>{{ p.symbol }}</b><div class="faint small" v-if="p.strategy">{{ STRATEGY_LABELS[p.strategy] }}</div></td>
                <td><span class="badge" :class="p.origin==='ai' ? 'paper' : ''">{{ p.origin==='ai' ? 'IA' : 'Manuel' }}</span><div class="faint small" v-if="p.status==='pending'">en attente</div></td>
                <td class="num">{{ qty(p.qty) }}</td><td class="num">{{ price(p.entry_price) }}</td><td class="num">{{ price(p.price) }}</td>
                <td class="num" :class="cls(p.pnl)">{{ signed(p.pnl) }}<div class="small">{{ p.r !== null && p.r !== undefined ? rr(p.r) : pct(p.pnl_pct/100) }}</div></td>
                <td class="num">{{ price(p.stop_price) }}</td><td class="num">{{ price(p.target_price) }}</td>
                <td class="nowrap"><button class="small" @click="editPos(p)" :disabled="p.status!=='open'">Modifier</button> <button class="small danger" @click="closePos(p)">Fermer</button></td>
              </tr>
              <tr v-if="!positionsHere.length"><td colspan="9" class="empty">Aucune position ouverte sur ce compte.</td></tr>
            </tbody>
          </table></div>
          <div v-if="editing" class="card tight" style="margin-top:10px;background:var(--bg-2)">
            <b>Modifier les protections — {{ editing.symbol }}</b>
            <div class="row" style="margin-top:8px">
              <div><label>Stop-loss</label><input type="number" step="any" v-model="editing.stop"></div>
              <div><label>Objectif (vide = aucun)</label><input type="number" step="any" v-model="editing.target"></div>
            </div>
            <div class="btn-row" style="margin-top:8px"><button class="primary small" @click="saveEdit">Enregistrer</button><button class="ghost small" @click="editing=null">Annuler</button></div>
          </div>
        </div>

        <div class="card c-orders">
          <div class="card-head"><h2>Ordres en attente</h2></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Crypto</th><th>Type</th><th>Rôle</th><th class="num">Quantité</th><th class="num">Prix</th><th>Créé</th><th></th></tr></thead>
            <tbody>
              <tr v-for="o in (detail?.orders || [])" :key="o.id">
                <td>{{ o.symbol }}</td><td>{{ o.side==='buy' ? 'Achat' : 'Vente' }} {{ o.type==='market' ? 'marché' : o.type==='limit' ? 'limite' : 'stop' }}</td>
                <td>{{ PURPOSE[o.purpose] || o.purpose }}</td>
                <td class="num">{{ qty(o.amount - o.filled) }}</td><td class="num">{{ price(o.price || o.stop_price) }}</td><td>{{ dt(o.created_at) }}</td>
                <td><button class="small" @click="cancelOrder(o)">Annuler</button></td>
              </tr>
              <tr v-if="!(detail?.orders || []).length"><td colspan="7" class="empty">Aucun ordre en attente.</td></tr>
            </tbody>
          </table></div>
        </div>
      </div>

      <div class="col">
        <div class="card ticket c-ticket" style="margin-bottom:14px">
          <div class="seg side" style="width:100%;margin-bottom:12px">
            <button style="flex:1" :class="{on: t.side==='buy', 'buy-on': t.side==='buy'}" @click="t.side='buy'">Acheter</button>
            <button style="flex:1" :class="{on: t.side==='sell', 'sell-on': t.side==='sell'}" @click="t.side='sell'">Vendre</button>
          </div>
          <div class="field"><label>Type d'ordre</label>
            <div class="seg"><button :class="{on: t.type==='market'}" @click="t.type='market'">Au marché</button><button :class="{on: t.type==='limit'}" @click="t.type='limit'">À cours limité</button></div></div>
          <div v-if="t.type==='limit'" class="field"><label>Prix limite</label><div class="input-suffix"><input type="number" step="any" v-model="t.limit" :placeholder="price(last)"><span>{{ quoteCcy }}</span></div></div>
          <div v-if="t.side==='buy'" class="check" style="padding-top:0"><input type="checkbox" id="userisk" v-model="t.useRisk"><label for="userisk" style="margin:0;color:var(--text)">Calculer la quantité selon le risque (recommandé)</label></div>
          <div v-if="t.useRisk && t.side==='buy'" class="field"><label>Risque accepté si le stop est touché</label>
            <div class="input-suffix"><input type="number" step="0.1" min="0.1" max="5" v-model.number="t.riskPct"><span>% du capital</span></div>
            <div class="hint" v-if="!t.sl">Indiquez un stop-loss ci-dessous pour calculer la quantité.</div>
            <div class="hint" v-else-if="riskComputed">→ {{ qty(riskComputed.qty) }} {{ base }} (≈ {{ money(riskComputed.value, quoteCcy) }}), perte max ≈ {{ money(riskComputed.riskAmount, cashCcy) }}</div>
          </div>
          <div v-else class="field"><label>Montant</label>
            <div class="row"><div class="input-suffix" style="flex:2"><input type="number" step="any" min="0" v-model="t.amount"><span>{{ t.mode==='quote' ? cashCcy : base }}</span></div>
              <div class="seg" style="flex:1"><button :class="{on: t.mode==='quote'}" @click="t.mode='quote'">{{ cashCcy }}</button><button :class="{on: t.mode==='base'}" @click="t.mode='base'">{{ base }}</button></div></div>
            <div class="pill-row"><button v-for="p in [10,25,50,100]" :key="p" class="small" @click="setPortion(p)">{{ p }} %</button>
              <span class="faint small" style="align-self:center">{{ t.side==='buy' ? 'du disponible (' + money(cash, cashCcy) + ')' : 'de vos ' + base + ' (' + qty(holding) + ')' }}</span></div>
          </div>
          <template v-if="t.side==='buy'">
            <div class="field"><label>Stop-loss (vente automatique en cas de baisse)</label><div class="input-suffix"><input type="number" step="any" v-model="t.sl" placeholder="recommandé"><span>{{ quoteCcy }}</span></div>
              <div class="pill-row"><button v-for="p in [1,2,5,10]" :key="p" class="small" @click="setSl(p)">−{{ p }} %</button></div></div>
            <div class="field"><label>Objectif de gain (optionnel)</label><div class="input-suffix"><input type="number" step="any" v-model="t.tp"><span>{{ quoteCcy }}</span></div>
              <div class="pill-row"><button v-for="p in [2,4,8,15]" :key="p" class="small" @click="setTp(p)">+{{ p }} %</button></div></div>
          </template>
          <div v-if="preview" class="preview">
            <div v-if="preview.error" class="down">{{ preview.error }}</div>
            <template v-else>
              <div class="line"><span class="muted">Quantité</span><span class="num">{{ qty(preview.qty) }} {{ base }}</span></div>
              <div class="line"><span class="muted">Montant</span><span class="num">≈ {{ money(preview.value, quoteCcy) }}<span v-if="preview.fx"> = {{ money(preview.value_cash, preview.currency) }}</span></span></div>
              <div class="line"><span class="muted">Frais de la plateforme</span><span class="num">≈ {{ money(preview.fee, preview.currency) }}</span></div>
              <div class="line" v-if="preview.fx_cost"><span class="muted">Coût de conversion</span><span class="num">≈ {{ money(preview.fx_cost, preview.currency) }}</span></div>
              <div class="line"><span class="muted">{{ t.side==='buy' ? 'Total débité' : 'Total reçu' }}</span><span class="num"><b>≈ {{ money(preview.total_cash, preview.currency) }}</b></span></div>
              <div class="line" v-if="preview.risk_amount"><span class="muted">Perte max (stop)</span><span class="num">{{ money(preview.risk_amount, preview.currency) }} ({{ preview.risk_pct?.toFixed(2) }} %)</span></div>
              <div v-if="preview.fx" class="faint small" style="margin-top:4px">Taux appliqué : {{ fxLine(preview.fx) }}</div>
              <div v-for="w in preview.warnings" :key="w" class="warn small" style="margin-top:4px">⚠ {{ w }}</div>
            </template>
          </div>
          <button class="block" :class="t.side==='buy' ? 'buy' : 'sell'" :disabled="busy || !preview || !!preview.error" @click="submit">
            {{ t.side==='buy' ? 'Acheter' : 'Vendre' }} {{ base }}</button>
          <p class="faint small center" style="margin-bottom:0">Argent fictif, prix réels : entraînez-vous sans risque.</p>
        </div>

        <div class="card c-coach">
          <div class="card-head"><h3>✦ Avis de l'IA sur {{ base }}</h3></div>
          <div v-if="!coach" class="muted small">Chargement…</div>
          <div v-else-if="!coach.available" class="muted small">{{ coach.message }}</div>
          <template v-else>
            <dl class="kv">
              <dt>Climat {{ base }}</dt><dd>{{ coach.regime }}</dd>
              <dt>Climat du marché (BTC)</dt><dd>{{ coach.btc_regime }}</dd>
              <dt>RSI</dt><dd>{{ coach.rsi?.toFixed(0) }}</dd>
              <dt>Amplitude moyenne</dt><dd>{{ coach.atr_pct?.toFixed(2) }} % par bougie</dd>
              <dt>Tendance 200 jours</dt><dd :class="cls(coach.trend_200d)">{{ coach.trend_200d !== null ? pct(coach.trend_200d/100) : '—' }}</dd>
            </dl>
            <div style="margin-top:10px">
              <div v-if="!coach.signals.length" class="small muted">Aucune stratégie ne donne de signal d'achat sur la dernière bougie {{ coach.timeframe }}.</div>
              <div v-for="s in coach.signals" :key="s.strategy" class="note info small" style="margin-top:6px">
                <b>{{ s.label }}</b> : stop {{ price(s.stop) }}, objectif {{ price(s.target) }}
                <span v-if="s.p_win !== undefined"><br>IA : {{ (s.p_win*100).toFixed(0) }} % de chances, espérance {{ rr(s.expected_r) }}</span>
                <span v-else><br>Aucun modèle validé : signal non évalué.</span>
              </div>
            </div>
            <p class="faint small" style="margin-bottom:0;margin-top:8px">Indication seulement : ce n'est pas un conseil financier.</p>
          </template>
        </div>
      </div>
    </div>
  </div>`,
};
