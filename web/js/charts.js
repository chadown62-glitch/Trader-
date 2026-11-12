// Graphiques (TradingView Lightweight Charts™ — https://www.tradingview.com/)
const { ref, computed, onMounted, onBeforeUnmount, watch } = Vue;
const TZ = -new Date().getTimezoneOffset() * 60; // affichage à l'heure locale

const theme = {
  layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#93a0b8', fontFamily: getComputedStyle(document.body).fontFamily },
  grid: { vertLines: { color: 'rgba(35, 48, 74, .55)' }, horzLines: { color: 'rgba(35, 48, 74, .55)' } },
  rightPriceScale: { borderColor: '#23304a' },
  timeScale: { borderColor: '#23304a', timeVisible: true, secondsVisible: false },
  crosshair: { mode: 0 },
  localization: { locale: 'fr-FR', priceFormatter: (p) => { const a = Math.abs(p); return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: a >= 100 ? 2 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6 }).format(p); } },
};

export const toChartTime = (ms) => Math.floor(ms / 1000) + TZ;

function makeChart(el, extra = {}) {
  const light = document.documentElement.dataset.theme === 'light';
  const colors = light
    ? { layout: { ...theme.layout, textColor: '#4b5870' }, grid: { vertLines: { color: '#e6ebf3' }, horzLines: { color: '#e6ebf3' } },
        rightPriceScale: { borderColor: '#dfe5ef' }, timeScale: { ...theme.timeScale, borderColor: '#dfe5ef' } }
    : {};
  const chart = LightweightCharts.createChart(el, { ...theme, ...colors, ...extra, width: el.clientWidth, height: el.clientHeight });
  const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth, height: el.clientHeight }));
  ro.observe(el);
  return { chart, ro };
}

// Graphique en chandeliers avec volumes, marqueurs de trades et lignes de prix
export const CandleChart = {
  props: { candles: Array, markers: Array, lines: Array, height: { type: String, default: '' } },
  setup(props, { expose }) {
    const el = ref(null);
    let chart, ro, series, volume, priceLines = [];
    const apply = () => {
      if (!series) return;
      const data = (props.candles || []).map(c => ({ time: toChartTime(c[0]), open: c[1], high: c[2], low: c[3], close: c[4] }));
      series.setData(data);
      volume.setData((props.candles || []).map(c => ({ time: toChartTime(c[0]), value: c[5], color: c[4] >= c[1] ? 'rgba(34,197,94,.35)' : 'rgba(244,63,94,.35)' })));
      applyMarkers();
    };
    const applyMarkers = () => {
      if (!series) return;
      const ms = (props.markers || []).map(m => ({ ...m, time: toChartTime(m.time) })).sort((a, b) => a.time - b.time);
      series.setMarkers(ms);
    };
    const applyLines = () => {
      if (!series) return;
      priceLines.forEach(l => series.removePriceLine(l));
      priceLines = (props.lines || []).map(l => series.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.style ?? 2, axisLabelVisible: true, title: l.title }));
    };
    onMounted(() => {
      ({ chart, ro } = makeChart(el.value));
      series = chart.addCandlestickSeries({ upColor: '#22c55e', downColor: '#f43f5e', borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#f43f5e' });
      volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      apply(); applyLines();
      chart.timeScale().fitContent();
    });
    onBeforeUnmount(() => { ro && ro.disconnect(); chart && chart.remove(); });
    watch(() => props.candles, () => { apply(); }, { deep: false });
    watch(() => props.markers, applyMarkers, { deep: true });
    watch(() => props.lines, applyLines, { deep: true });
    const updateLast = (c) => { if (series) { series.update({ time: toChartTime(c[0]), open: c[1], high: c[2], low: c[3], close: c[4] }); } };
    const fit = () => chart && chart.timeScale().fitContent();
    expose({ updateLast, fit });
    return { el };
  },
  template: `<div ref="el" class="chart" :class="height"></div>`,
};

// Courbe(s) simple(s) : capital, résultats cumulés...
export const LineChart = {
  props: { series: Array, height: { type: String, default: 'small' }, area: { type: Boolean, default: true } },
  setup(props) {
    const el = ref(null);
    let chart, ro, lines = [];
    const draw = () => {
      if (!chart) return;
      lines.forEach(s => chart.removeSeries(s));
      lines = [];
      (props.series || []).forEach((s, i) => {
        const color = s.color || (i === 0 ? '#3b82f6' : '#93a0b8');
        const line = props.area && i === 0
          ? chart.addAreaSeries({ lineColor: color, topColor: color + '55', bottomColor: color + '05', lineWidth: 2, priceLineVisible: false })
          : chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lineStyle: s.dashed ? 2 : 0 });
        const pts = new Map();
        (s.data || []).forEach(([t, v]) => { pts.set(toChartTime(t), v); });
        line.setData([...pts.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value })));
        lines.push(line);
      });
      chart.timeScale().fitContent();
    };
    onMounted(() => { ({ chart, ro } = makeChart(el.value)); draw(); });
    onBeforeUnmount(() => { ro && ro.disconnect(); chart && chart.remove(); });
    watch(() => props.series, draw, { deep: true });
    return { el };
  },
  template: `<div ref="el" class="chart" :class="height"></div>`,
};

// Histogramme simple (HTML/CSS) : valeurs positives vers le haut, négatives vers le bas, autour d'une ligne zéro.
// Chaque barre porte une info-bulle. Lisible sur téléphone, sans dépendance.
export const BarChart = {
  props: { bars: Array, unit: { type: String, default: '' }, digits: { type: Number, default: 2 }, height: { type: Number, default: 150 } },
  setup(props) {
    const fmt = (v) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: props.digits, maximumFractionDigits: props.digits }).format(v);
    const scale = computed(() => {
      const vals = (props.bars || []).map(b => Number(b.value) || 0);
      const max = Math.max(0, ...vals), min = Math.min(0, ...vals);
      const span = (max - min) || 1;
      return { max, min, span, top: max / span * 100 };
    });
    const style = (b) => {
      const v = Number(b.value) || 0, s = scale.value;
      const h = Math.max(Math.abs(v) / s.span * 100, v ? 1.5 : 0);
      return v >= 0 ? { bottom: (100 - s.top) + '%', height: h + '%' } : { top: s.top + '%', height: h + '%' };
    };
    const tip = (b) => (b.title ? b.title + ' : ' : '') + (b.value > 0 ? '+' : '') + fmt(b.value) + (props.unit ? ' ' + props.unit : '');
    return { scale, style, tip, fmt };
  },
  template: `
  <div class="barchart" :style="{height: height + 'px'}" role="img" :aria-label="'Histogramme de ' + (bars || []).length + ' valeurs'">
    <div class="axis"><span>{{ scale.max > 0 ? '+' + fmt(scale.max) : fmt(scale.max) }}{{ unit ? ' ' + unit : '' }}</span><span v-if="scale.min < 0">{{ fmt(scale.min) }}{{ unit ? ' ' + unit : '' }}</span></div>
    <div class="plot">
      <div class="zero" :style="{top: scale.top + '%'}"></div>
      <div v-for="(b, i) in bars" :key="i" class="col" :title="tip(b)">
        <div class="bar" :class="[b.value >= 0 ? 'pos' : 'neg', b.muted ? 'muted' : '']" :style="style(b)"></div>
      </div>
    </div>
  </div>`,
};
