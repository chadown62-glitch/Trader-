"""Contexte de marché : indice Fear & Greed, indices boursiers, taux de financement.

Ces informations sont affichées (panel, Telegram) pour situer le climat général.
Elles ne déclenchent aucun trade : aucun avantage n'a été prouvé à leur sujet.
Chaque source est facultative : si l'une tombe en panne, les autres continuent.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

log = logging.getLogger("sentinelle.context")

FG_LABELS = {"Extreme Fear": "Peur extrême", "Fear": "Peur", "Neutral": "Neutre", "Greed": "Avidité", "Extreme Greed": "Avidité extrême"}
INDICES = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^VIX", "VIX (peur des marchés actions)"), ("DX-Y.NYB", "Dollar (DXY)"), ("GC=F", "Or")]
UA = {"User-Agent": "Mozilla/5.0 (compatible; Sentinelle/0.1)"}


def fetch_fear_greed(client: httpx.Client) -> dict:
    r = client.get("https://api.alternative.me/fng/?limit=8")
    r.raise_for_status()
    data = r.json()["data"]
    now, week = int(data[0]["value"]), int(data[-1]["value"])
    return {"value": now, "label": FG_LABELS.get(data[0]["value_classification"], data[0]["value_classification"]),
            "week_ago": week, "ts": int(data[0]["timestamp"])}


def fetch_index(client: httpx.Client, ticker: str) -> dict:
    r = client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", params={"range": "1mo", "interval": "1d"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    if len(closes) < 6:
        raise ValueError("historique insuffisant")
    last = closes[-1]
    return {"last": last, "change_1d": last / closes[-2] - 1, "change_5d": last / closes[-6] - 1}


def fetch_funding(client: httpx.Client, base: str) -> float:
    r = client.get("https://www.okx.com/api/v5/public/funding-rate", params={"instId": f"{base}-USDT-SWAP"})
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        raise ValueError("pas de contrat")
    return float(data[0]["fundingRate"])


def funding_comment(rate: float) -> str:
    if rate > 0.0003:
        return "euphorie (beaucoup d'acheteurs à levier)"
    if rate < -0.0001:
        return "pessimisme (beaucoup de vendeurs à levier)"
    return "équilibré"


class ContextService:
    name = "context"

    def __init__(self, app, transport=None, period: float = 900.0):
        self.app = app
        self.transport = transport
        self.period = period
        self.data: dict = {"fear_greed": None, "indices": {}, "funding": {}, "updated": None, "errors": {}}
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="sentinelle-contexte", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.app.settings["market"].get("context", True) and self.app.settings["market"]["source"] != "synthetic":
                self.refresh()
            self._stop.wait(self.period)

    def refresh(self) -> dict:
        errors = {}
        with httpx.Client(timeout=10.0, headers=UA, transport=self.transport, follow_redirects=True) as c:
            try:
                self.data["fear_greed"] = fetch_fear_greed(c)
            except Exception as exc:
                errors["fear_greed"] = str(exc)[:120]
            for ticker, label in INDICES:
                try:
                    self.data["indices"][ticker] = {"label": label, **fetch_index(c, ticker)}
                except Exception as exc:
                    errors[ticker] = str(exc)[:120]
            for sym in self.app.settings["general"]["watchlist"]:
                base = sym.split("/")[0]
                try:
                    rate = fetch_funding(c, base)
                    self.data["funding"][base] = {"rate": rate, "comment": funding_comment(rate)}
                except Exception as exc:
                    errors[f"funding:{base}"] = str(exc)[:120]
        self.data["errors"] = errors
        self.data["updated"] = time.time()
        return self.data

    def summary(self) -> str | None:
        fg = self.data.get("fear_greed")
        if not fg:
            return None
        parts = [f"Fear & Greed {fg['value']}/100 ({fg['label']})"]
        spx = self.data["indices"].get("^GSPC")
        if spx:
            parts.append(f"S&P 500 {spx['change_5d'] * 100:+.1f} % sur 5 jours")
        return " · ".join(parts)

    def status(self) -> dict:
        return {"updated": self.data.get("updated"), "errors": len(self.data.get("errors") or {})}

    def register_routes(self, api) -> None:
        @api.get("/api/context")
        async def context():
            return {**self.data, "macro": self._macro()}

        @api.post("/api/context/refresh")
        async def context_refresh():
            import asyncio

            await asyncio.to_thread(self.refresh)
            return {**self.data, "macro": self._macro()}

    def _macro(self) -> list[dict]:
        from ..risk.macro import upcoming

        return upcoming(self.app.settings, time.time())
