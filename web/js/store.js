// État global du panel + utilitaires d'affichage (format français)
const { reactive } = Vue;

export const store = reactive({
  auth: { checked: false, authenticated: false, setupRequired: false, setupAllowed: false },
  ws: false,
  overview: null,
  settings: null,
  markets: { watchlist: [], prices: {}, available: [], info: {} },
  decisions: [],
  jobs: {},
  toasts: [],
  confirm: null,
  emergencyOpen: false,
  selectedAccount: null,
});

let toastId = 0;
export function toast(message, kind = 'info', ms = 5000) {
  const id = ++toastId;
  store.toasts.push({ id, message, kind });
  setTimeout(() => { const i = store.toasts.findIndex(t => t.id === id); if (i >= 0) store.toasts.splice(i, 1); }, ms);
}

// Fenêtre de confirmation (optionnellement avec une phrase à taper)
export function confirmBox({ title, message, confirmLabel = 'Confirmer', danger = false, phrase = null }) {
  return new Promise(resolve => {
    store.confirm = { title, message, confirmLabel, danger, phrase, typed: '', resolve };
  });
}

const nf = (min, max) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: min, maximumFractionDigits: max });
const nf2 = nf(2, 2);

export function money(x, quote = '') {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return nf2.format(x) + (quote ? ' ' + quote : '');
}
export function signed(x, quote = '') {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return (x > 0 ? '+' : '') + nf2.format(x) + (quote ? ' ' + quote : '');
}
export function price(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const a = Math.abs(x);
  if (a >= 1000) return nf(2, 2).format(x);
  if (a >= 1) return nf(2, 4).format(x);
  return nf(4, 6).format(x);
}
export function qty(x) {
  if (x === null || x === undefined) return '—';
  return nf(0, 8).format(x);
}
export function pct(x, digits = 2, alreadyPct = false) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const v = alreadyPct ? x : x * 100;
  return (v > 0 ? '+' : '') + nf(digits, digits).format(v) + ' %';
}
export function plainPct(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return nf(digits, digits).format(x * 100) + ' %';
}
export function rr(x, digits = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return (x > 0 ? '+' : '') + nf(digits, digits).format(x) + ' R';
}
export function cls(x) { return x > 0 ? 'up' : x < 0 ? 'down' : ''; }
const dtf = new Intl.DateTimeFormat('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
const df = new Intl.DateTimeFormat('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' });
export function dt(tsSeconds) { return tsSeconds ? dtf.format(new Date(tsSeconds * 1000)) : '—'; }
export function dtMs(ms) { return ms ? dtf.format(new Date(ms)) : '—'; }
export function date(tsSeconds) { return tsSeconds ? df.format(new Date(tsSeconds * 1000)) : '—'; }
export function ago(tsSeconds) {
  if (!tsSeconds) return '—';
  const s = Date.now() / 1000 - tsSeconds;
  if (s < 60) return 'à l’instant';
  if (s < 3600) return `il y a ${Math.floor(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`;
  return `il y a ${Math.floor(s / 86400)} j`;
}
export function duration(sec) {
  if (!sec && sec !== 0) return '—';
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1).replace('.', ',')} h`;
  return `${(sec / 86400).toFixed(1).replace('.', ',')} j`;
}

export const STRATEGY_LABELS = {
  trend_breakout: 'Cassure de tendance',
  pullback: 'Repli en tendance',
  mean_reversion: 'Retour à la moyenne',
  momentum: 'Élan haussier',
  trend_follow: 'Suivi de tendance',
};
export const ROLE_LABELS = { ai_training: 'Entraînement IA', manual: 'Papier', live: 'Ancien compte réel (inerte)', backtest: 'Backtest' };
export const REASON_ICONS = { pris: '🟢', refusé: '⚪', sortie: '🔵', stop_relevé: '🛡️', erreur: '🔴' };
export const FEATURE_LABELS = {
  r1: 'Rendement 1 bougie', r3: 'Rendement 3 bougies', r6: 'Rendement 6 bougies', r12: 'Rendement 12 bougies',
  r24: 'Rendement 24 bougies', r72: 'Rendement 72 bougies', vol24: 'Volatilité récente', vol_ratio: 'Volatilité relative',
  atr_pct: 'Amplitude moyenne (ATR %)', rsi14: 'RSI', rsi_slope: 'Pente du RSI', d_ema20: 'Écart à la moyenne 20',
  d_ema50: 'Écart à la moyenne 50', d_ema200: 'Écart à la moyenne 200', slope_ema20: 'Pente moyenne 20',
  slope_ema50: 'Pente moyenne 50', bb_pctb: 'Position dans les bandes de Bollinger', bb_width: 'Largeur des bandes',
  macd_hist_atr: 'MACD', don_pos: 'Position dans le canal 20', breakout_atr: 'Force de la cassure', vol_z: 'Volume inhabituel',
  body: 'Corps de la bougie', upper_wick: 'Mèche haute', lower_wick: 'Mèche basse', hour_sin: 'Heure de la journée',
  hour_cos: 'Heure de la journée', trend_50d: 'Tendance 50 jours', trend_200d: 'Tendance 200 jours',
  sma50_200: 'Moyenne 50 j / 200 j', dd_30d: 'Recul depuis le plus haut 30 j', btc_r24: 'Mouvement du BTC (24 bougies)',
  btc_trend_200d: 'Tendance de fond du BTC', cost_r: 'Poids des frais',
};

export function accountLabel(a) {
  if (!a) return '';
  return a.kind === 'paper' ? `${a.name}` : `${a.name} (ancien compte réel, inerte)`;
}
