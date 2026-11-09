"""Canaux Telegram de signaux : lecture en OBSERVATION et mesure de fiabilité.

Sentinelle lit les messages des canaux choisis avec VOTRE compte Telegram
(API officielle, identifiants créés sur my.telegram.org), reconnaît les
signaux (crypto, sens, entrée, stop, objectifs) et calcule ce que chaque
signal aurait réellement rapporté, frais compris, à partir des prix du marché.

Beaucoup de canaux affichent leurs gains et effacent leurs pertes : ici, la
fiabilité est mesurée par Sentinelle elle-même. Les signaux ne sont JAMAIS
tradés automatiquement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time

import numpy as np

from ..ai.metrics import trade_metrics

log = logging.getLogger("sentinelle.signals")

LONG_WORDS = r"\b(LONG|BUY|ACHAT|ACHETER|ACHETEZ|BULLISH)\b"
SHORT_WORDS = r"\b(SHORT|SELL|VENTE|VENDRE|VENDEZ|BEARISH)\b"
QUOTES = ("USDT", "USDC", "USD", "BUSD", "FDUSD")
NOT_SYMBOLS = {"TP", "SL", "LONG", "SHORT", "BUY", "SELL", "ENTRY", "STOP", "TARGET", "TARGETS", "LEVERAGE", "CROSS",
               "ISOLATED", "SPOT", "FUTURES", "SIGNAL", "VIP", "ACHAT", "VENTE", "OBJECTIF", "ZONE", "USDT", "USDC", "USD"}


def parse_number(raw: str) -> float | None:
    s = (raw or "").strip().rstrip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"[1-9]\d{0,2}(,\d{3})+", s) else s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _numbers_after(pattern: str, text: str) -> list[float]:
    """Nombres qui suivent un mot-clé ; une zone « a - b » donne son milieu."""
    out = []
    for m in re.finditer(pattern + r"\s*[:=>]?\s*(\d[\d.,]*)(?:\s*-\s*(\d[\d.,]*))?", text):
        a = parse_number(m.group(1))
        b = parse_number(m.group(2)) if m.group(2) else None
        if a and b:
            a = (a + b) / 2
        if a:
            out.append(a)
    return out


TARGET_KW = r"(?:\bTP|\bTARGETS?|\bOBJECTIFS?|\bTAKE[\s-]*PROFIT)(?:\s*\d(?![\d.,]))?"
OTHER_KW = r"(?:STOP[\s-]*LOSS|STOPLOSS|\bSL\b|\bSTOP\b|ENTRY|ENTR[EÉ]E|LEVERAGE|LEVIER|\n)"


def _targets(text: str) -> list[float]:
    """Objectifs : tous les nombres qui suivent un mot-clé (« TP1 », « Targets 140 135 »…)."""
    out = []
    for m in re.finditer(TARGET_KW, text):
        rest = text[m.end():]
        stop = re.search(OTHER_KW + "|" + TARGET_KW, rest)
        segment = rest[: stop.start()] if stop else rest
        for num in re.findall(r"\d[\d.,]*", segment):
            v = parse_number(num)
            if v:
                out.append(v)
    return out


def parse_signal(text: str) -> dict:
    """Extrait un signal d'un message. `parsed` vaut True seulement si tout est cohérent."""
    t = (text or "").upper().replace("–", "-")
    out = {"parsed": False, "symbol": None, "side": None, "entry": None, "stop": None, "targets": []}
    m = re.search(r"#?\b([A-Z0-9]{2,10})\s*/?\s*(" + "|".join(QUOTES) + r")\b", t)
    if m and m.group(1) not in NOT_SYMBOLS:
        out["symbol"] = m.group(1)
    else:
        for tag in re.findall(r"#([A-Z0-9]{2,10})\b", t):
            if tag not in NOT_SYMBOLS:
                out["symbol"] = tag
                break
    if re.search(LONG_WORDS, t):
        out["side"] = "long"
    elif re.search(SHORT_WORDS, t):
        out["side"] = "short"
    entries = _numbers_after(r"(?:ENTRY(?:\s*ZONE)?|ENTR[EÉ]E|BUY\s*ZONE|PRIX\s*D'ENTR[EÉ]E|ENTRY\s*PRICE|@)", t)
    stops = _numbers_after(r"(?:STOP[\s-]*LOSS|STOPLOSS|\bSL\b|\bSTOP\b)", t)
    targets = _targets(t)
    out["entry"] = entries[0] if entries else None
    out["stop"] = stops[0] if stops else None
    out["targets"] = targets
    e, s_, tg = out["entry"], out["stop"], out["targets"]
    if out["symbol"] and out["side"] and e and s_ and tg:
        if out["side"] == "long":
            out["targets"] = sorted(x for x in tg if x > e)
            ok = s_ < e and out["targets"]
        else:
            out["targets"] = sorted((x for x in tg if x < e), reverse=True)
            ok = s_ > e and out["targets"]
        out["parsed"] = bool(ok)
    return out


def simulate_signal(sig: dict, candles, fee: float = 0.001, slip: float = 0.0005, horizon_hours: int = 72) -> dict | None:
    """Résultat d'un signal à partir de bougies 1 h (ts, open, high, low, close). None si pas encore connu."""
    ts_ms = sig["ts"] * 1000
    t = candles["ts"].to_numpy()
    i = int(np.searchsorted(t, ts_ms, side="right")) - 1  # bougie contenant le signal
    if i < 0 or i >= len(t) - 1:
        return None
    o, h, l, c = (candles[k].to_numpy() for k in ("open", "high", "low", "close"))
    long = sig["side"] == "long"
    entry = c[i] * ((1 + slip) if long else (1 - slip))  # entrée au marché à la réception du signal
    stop, target = sig["stop"], sig["targets"][0]
    if (long and (entry <= stop or entry >= target)) or (not long and (entry >= stop or entry <= target)):
        return {"outcome": "dépassé", "outcome_r": None, "exit_ts": sig["ts"]}
    risk = abs(entry - stop)
    end = min(len(t), i + 1 + horizon_hours)
    exit_px, outcome, j = None, None, None
    for j in range(i + 1, end):
        if long:
            if l[j] <= stop:
                exit_px, outcome = min(stop, o[j]) * (1 - slip), "stop"
            elif h[j] >= target:
                exit_px, outcome = target, "objectif"
        else:
            if h[j] >= stop:
                exit_px, outcome = max(stop, o[j]) * (1 + slip), "stop"
            elif l[j] <= target:
                exit_px, outcome = target, "objectif"
        if exit_px is not None:
            break
    if exit_px is None:
        if end - (i + 1) < horizon_hours:
            return None  # pas encore assez de recul
        j = end - 1
        exit_px, outcome = c[j], "durée"
    pnl = (exit_px * (1 - fee) - entry * (1 + fee)) if long else (entry * (1 - fee) - exit_px * (1 + fee))
    return {"outcome": outcome, "outcome_r": pnl / risk, "exit_ts": float(t[j] / 1000 + 3600)}


class SignalWatcher:
    name = "signals"

    def __init__(self, app):
        self.app = app
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client = None
        self._stop = threading.Event()
        self._phone_hash: str | None = None
        self.state = {"connected": False, "authorized": False, "error": None, "channels": {}, "last_resolve": None}

    @property
    def cfg(self) -> dict:
        return self.app.settings["signals"]

    # -- cycle de vie -------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._thread, name="sentinelle-signaux", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _thread(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception:
            log.exception("Lecteur de canaux arrêté")

    def _creds(self):
        s = self.app.secrets
        api_id, api_hash = s.get("telegram_api_id"), s.get("telegram_api_hash")
        if not api_id or not api_hash or not api_id.isdigit():
            return None
        return int(api_id), api_hash

    async def _client(self):
        creds = self._creds()
        if creds is None:
            raise RuntimeError("Identifiants my.telegram.org manquants (api_id, api_hash)")
        if self.client is None:
            from telethon import TelegramClient

            self.client = TelegramClient(str(self.app.data_dir / "telegram_utilisateur"), creds[0], creds[1])
        if not self.client.is_connected():
            await self.client.connect()
        self.state["connected"] = True
        return self.client

    async def _main(self) -> None:
        listening: set[str] = set()
        last_resolve = 0.0
        while not self._stop.is_set():
            try:
                news_channels = self.app.settings["news"].get("telegram_channels") or []
                if (self.cfg["enabled"] or news_channels) and self._creds():
                    client = await self._client()
                    self.state["authorized"] = await client.is_user_authorized()
                    if self.state["authorized"]:
                        wanted = (set(self.cfg["channels"]) if self.cfg["enabled"] else set()) | set(news_channels)
                        if wanted != listening:
                            await self._listen(client, wanted)
                            listening = wanted
                if time.time() - last_resolve > 600:
                    await asyncio.to_thread(self.resolve_pending)
                    last_resolve = time.time()
                self.state["error"] = None
            except Exception as exc:
                self.state["error"] = str(exc)[:200]
            await asyncio.sleep(20)

    async def _listen(self, client, channels: set[str]) -> None:
        from telethon import events

        client.remove_event_handler(self._on_message)
        entities = []
        for ch in channels:
            try:
                ent = await client.get_entity(ch.replace("https://t.me/", "").lstrip("@"))
                entities.append(ent)
                self.state["channels"][ch] = {"ok": True}
                if self.cfg["enabled"] and ch in self.cfg["channels"]:
                    async for msg in client.iter_messages(ent, limit=300):  # historique : fiabilité mesurable tout de suite
                        self.store_message(ch, msg.id, msg.date.timestamp(), msg.message or "")
            except Exception as exc:
                self.state["channels"][ch] = {"ok": False, "error": str(exc)[:150]}
        if entities:
            client.add_event_handler(self._on_message, events.NewMessage(chats=entities))

    async def _on_message(self, event) -> None:
        chat = await event.get_chat()
        configured = list(self.cfg["channels"]) + list(self.app.settings["news"].get("telegram_channels") or [])
        ids = {(getattr(chat, "username", "") or "").lower(), str(chat.id)}
        name = next((c for c in configured if c.replace("https://t.me/", "").lstrip("@").lower() in ids),
                    getattr(chat, "username", None) or str(chat.id))
        self.route_message(name, event.message.id, event.message.date.timestamp(), event.message.message or "")

    def route_message(self, channel: str, msg_id: int, ts: float, text: str) -> None:
        """Canal de signaux -> mesure de fiabilité ; canal de dépêches -> actualités analysées tout de suite."""
        if channel in (self.app.settings["news"].get("telegram_channels") or []):
            for ext in self.app.extensions:
                if getattr(ext, "name", "") == "news":
                    ext.ingest(f"Telegram {channel}", text, ts, uid=f"tg:{channel}:{msg_id}")
        if self.cfg["enabled"] and channel in self.cfg["channels"]:
            self.store_message(channel, msg_id, ts, text)

    # -- connexion (depuis le panel) ----------------------------------------------
    def _run(self, coro, timeout: float = 60):
        if self.loop is None:
            raise RuntimeError("Lecteur de canaux non démarré")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def login_start(self) -> dict:
        phone = self.app.secrets.get("telegram_phone")
        if not phone:
            raise RuntimeError("Numéro de téléphone manquant")

        async def go():
            client = await self._client()
            if await client.is_user_authorized():
                return {"authorized": True}
            sent = await client.send_code_request(phone)
            self._phone_hash = sent.phone_code_hash
            return {"authorized": False, "code_sent": True}

        return self._run(go())

    def login_code(self, code: str, password: str | None = None) -> dict:
        phone = self.app.secrets.get("telegram_phone")

        async def go():
            from telethon.errors import SessionPasswordNeededError

            client = await self._client()
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=self._phone_hash)
            except SessionPasswordNeededError:
                if not password:
                    return {"authorized": False, "need_password": True}
                await client.sign_in(password=password)
            self.state["authorized"] = await client.is_user_authorized()
            return {"authorized": self.state["authorized"]}

        return self._run(go())

    # -- stockage et mesure ---------------------------------------------------------
    def store_message(self, channel: str, msg_id: int, ts: float, text: str) -> None:
        if not text.strip():
            return
        sig = parse_signal(text)
        self.app.db.execute(
            "INSERT OR IGNORE INTO signals(channel, msg_id, ts, text, parsed, symbol, side, entry, stop, targets) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (channel, msg_id, ts, text[:2000], int(sig["parsed"]), sig["symbol"], sig["side"], sig["entry"], sig["stop"],
             json.dumps(sig["targets"])),
        )

    def resolve_pending(self) -> int:
        quote = self.app.settings["general"]["quote"]
        rows = self.app.db.query("SELECT * FROM signals WHERE parsed = 1 AND outcome IS NULL AND ts < ?", (time.time() - 3600,))
        done = 0
        cache: dict[str, object] = {}
        horizon = self.cfg["horizon_hours"]
        for r in rows:
            sym = f"{r['symbol']}/{quote}"
            if sym not in cache:
                markets = self.app.hub.markets
                if markets and sym not in markets:
                    cache[sym] = None
                else:
                    days = max(3, int((time.time() - min(x["ts"] for x in rows if x["symbol"] == r["symbol"])) / 86400) + 4)
                    try:
                        cache[sym] = self.app.history.update(sym, "1h", min(days, 400))
                    except Exception:
                        cache[sym] = None
            candles = cache[sym]
            if candles is None or len(candles) == 0:
                self.app.db.update("signals", r["id"], {"outcome": "paire indisponible", "resolved_at": time.time()})
                continue
            sig = dict(r, targets=json.loads(r["targets"] or "[]"))
            res = simulate_signal(sig, candles, horizon_hours=horizon)
            if res:
                self.app.db.update("signals", r["id"], {**res, "resolved_at": time.time()})
                done += 1
        self.state["last_resolve"] = time.time()
        return done

    def channel_stats(self) -> list[dict]:
        out = []
        for ch in sorted({r["channel"] for r in self.app.db.query("SELECT DISTINCT channel FROM signals")} | set(self.cfg["channels"])):
            rows = self.app.db.query("SELECT parsed, outcome, outcome_r FROM signals WHERE channel = ?", (ch,))
            rs = [r["outcome_r"] for r in rows if r["outcome_r"] is not None]
            out.append({"channel": ch, "messages": len(rows), "signals": sum(r["parsed"] for r in rows),
                        "resolved": len(rs), **trade_metrics(rs), **self.state["channels"].get(ch, {})})
        return out

    def status(self) -> dict:
        return {"enabled": self.cfg["enabled"], "connected": self.state["connected"], "authorized": self.state["authorized"],
                "error": self.state["error"], "creds": bool(self._creds())}

    def register_routes(self, api) -> None:
        from fastapi import HTTPException
        from pydantic import BaseModel

        class CodeBody(BaseModel):
            code: str
            password: str | None = None

        @api.get("/api/signals")
        async def signals_overview(limit: int = 100):
            rows = self.app.db.query("SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (min(limit, 500),))
            for r in rows:
                r["targets"] = json.loads(r["targets"] or "[]")
            return {"status": self.status(), "channels": self.channel_stats(), "signals": rows}

        @api.post("/api/signals/login")
        async def signals_login():
            try:
                return await asyncio.to_thread(self.login_start)
            except Exception as exc:
                raise HTTPException(400, f"Connexion impossible : {exc}") from None

        @api.post("/api/signals/code")
        async def signals_code(body: CodeBody):
            try:
                return await asyncio.to_thread(self.login_code, body.code.strip(), body.password)
            except Exception as exc:
                raise HTTPException(400, f"Code refusé : {exc}") from None

        @api.post("/api/signals/resolve")
        async def signals_resolve():
            return {"resolved": await asyncio.to_thread(self.resolve_pending)}
