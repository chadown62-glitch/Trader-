// Graphiques (TradingView Lightweight Charts™ — https://www.tradingview.com/)
const { ref, onMounted, onBeforeUnmount, watch } = Vue;
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
