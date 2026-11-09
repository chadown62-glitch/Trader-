// Client de l'API Sentinelle + connexion temps réel (WebSocket)
import { store, toast } from './store.js';

export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.status = status; this.data = data; }
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
    if (Array.isArray(msg)) msg = msg.map(d => d.msg || JSON.stringify(d)).join(' ; ');
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
