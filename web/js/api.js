// Client de l'API Sentinelle + connexion temps réel (WebSocket)
import { store, toast } from './store.js';

export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.status = status; this.data = data; }
}

// Erreurs de validation (422) : jamais le texte brut du validateur (en anglais), toujours un message en français qui
// nomme le champ. Un message déjà rédigé par le serveur (« Value error, … ») est repris tel quel.
const RULES = {
  missing: () => 'valeur obligatoire',
  string_too_short: (c) => `au moins ${c.min_length} caractères`,
  string_too_long: (c) => `au plus ${c.max_length} caractères`,
  too_short: (c) => `au moins ${c.min_length} élément(s)`,
  too_long: (c) => `au plus ${c.max_length} élément(s)`,
  greater_than: (c) => `supérieur à ${c.gt}`,
  greater_than_equal: (c) => `au moins ${c.ge}`,
  less_than: (c) => `inférieur à ${c.lt}`,
  less_than_equal: (c) => `au plus ${c.le}`,
  int_parsing: () => 'nombre entier attendu', int_type: () => 'nombre entier attendu', int_from_float: () => 'nombre entier attendu',
  float_parsing: () => 'nombre attendu', float_type: () => 'nombre attendu', finite_number: () => 'nombre fini attendu',
  bool_parsing: () => 'oui ou non attendu', bool_type: () => 'oui ou non attendu',
  string_type: () => 'texte attendu', dict_type: () => 'objet attendu', list_type: () => 'liste attendue',
  literal_error: () => 'valeur non autorisée', enum: () => 'valeur non autorisée',
  string_pattern_mismatch: () => 'format invalide', json_invalid: () => 'requête mal formée', extra_forbidden: () => 'champ non autorisé',
};
export function validationMessage(d) {
  if (!d || typeof d !== 'object') return String(d);
  const field = (d.loc || []).filter((x) => !['body', 'query', 'path'].includes(x)).join('.');
  const text = ['value_error', 'assertion_error'].includes(d.type)
    ? String(d.msg || '').replace(/^(Value|Assertion) error, /, '') || 'valeur invalide'
    : (RULES[d.type] || (() => 'valeur invalide'))(d.ctx || {});
  return field ? `« ${field} » : ${text}` : text;
}

async function request(method, path, body) {
  const opts = { method, headers: { 'X-Sentinelle': '1' }, credentials: 'same-origin' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError('Serveur injoignable : Sentinelle est-il lancé ?', 0);
  }
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth/')) store.auth.authenticated = false;
    let msg = (data && data.detail) || `Erreur ${res.status}`;
    if (Array.isArray(msg)) msg = 'Saisie refusée : ' + msg.map(validationMessage).join(' ; ');
    throw new ApiError(msg, res.status, data);
  }
  return data;
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b = {}) => request('POST', p, b),
  put: (p, b = {}) => request('PUT', p, b),
  patch: (p, b = {}) => request('PATCH', p, b),
  del: (p) => request('DELETE', p),
};

// Appel avec message d'erreur automatique
export async function safe(promise, successMsg) {
  try {
    const r = await promise;
    if (successMsg) toast(successMsg, 'success');
    return r;
  } catch (e) {
    toast(e.message || String(e), 'error');
    return null;
  }
}

const listeners = new Map();
export function on(topic, fn) {
  if (!listeners.has(topic)) listeners.set(topic, new Set());
  listeners.get(topic).add(fn);
  return () => listeners.get(topic).delete(fn);
}

let ws = null;
let retry = 1000;
export function connectWs() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/api/ws`);
  ws.onopen = () => { store.ws = true; retry = 1000; };
  ws.onclose = () => {
    store.ws = false;
    if (store.auth.authenticated) setTimeout(connectWs, retry);
    retry = Math.min(retry * 2, 15000);
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    const fns = listeners.get(msg.topic);
    if (fns) fns.forEach(fn => { try { fn(msg.data, msg); } catch (e) { console.error(e); } });
    const all = listeners.get('*');
    if (all) all.forEach(fn => fn(msg.data, msg));
  };
}
export function closeWs() { if (ws) { ws.onclose = null; ws.close(); ws = null; store.ws = false; } }
