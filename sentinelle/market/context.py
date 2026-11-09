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


CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_FR = {
    "CPI": "Inflation (CPI)", "Core CPI": "Inflation sous-jacente (CPI)", "PPI": "Prix à la production (PPI)",
    "Core PPI": "Prix à la production sous-jacents", "Non-Farm Employment Change": "Emploi américain (NFP)",
    "Unemployment Rate": "Taux de chômage", "FOMC Statement": "Fed : décision de taux", "Federal Funds Rate": "Fed : taux directeur",
    "FOMC Press Conference": "Fed : conférence de presse", "Main Refinancing Rate": "BCE : taux directeur",
    "Monetary Policy Statement": "BCE : décision de politique monétaire", "ECB Press Conference": "BCE : conférence de presse",
    "Advance GDP q/q": "PIB (première estimation)", "Core PCE Price Index m/m": "Inflation PCE (indicateur préféré de la Fed)",
    "Retail Sales m/m": "Ventes au détail", "ISM Manufacturing PMI": "Indice ISM manufacturier",
}


def fetch_calendar(client: httpx.Client, countries: list[str]) -> list[dict]:
    """Annonces économiques à fort impact de la semaine (calendrier public, sans clé)."""
    from datetime import datetime

    r = client.get(CALENDAR_URL)
    r.raise_for_status()
    out = []
    for e in r.json():
        if e.get("impact") != "High" or e.get("country") not in countries:
            continue
        try:
            ts = datetime.fromisoformat(e["date"]).timestamp()
        except (KeyError, ValueError):
            continue
        base = e.get("title", "").replace(" m/m", "").replace(" y/y", "").replace(" q/q", "")
        name = CALENDAR_FR.get(e.get("title", ""), CALENDAR_FR.get(base, e.get("title", "Annonce")))
        out.append({"ts": ts, "label": f"{e['country']} : {name}", "forecast": e.get("forecast") or None,
                    "previous": e.get("previous") or None})
    return out


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
            if self.app.settings["market"]["source"] != "synthetic":
                if self.app.settings["market"].get("context", True):
                    self.refresh()
                else:
                    try:
                        with httpx.Client(timeout=10.0, headers=UA, transport=self.transport) as c:
                            self.refresh_calendar(c)
                    except Exception as exc:
                        self.data["errors"] = {"calendrier": str(exc)[:120]}
            self._stop.wait(self.period)

    def refresh_calendar(self, client: httpx.Client, force: bool = False) -> int:
        """Met à jour les annonces automatiques (au plus toutes les 3 heures, sauf demande explicite)."""
        macro = self.app.settings["macro"]
        last = float(self.app.db.get_meta("calendar_fetched", "0") or 0)
        if not macro.get("auto_calendar", True) or (not force and time.time() - last < 3 * 3600):
            return 0
        events = fetch_calendar(client, macro.get("calendar_countries") or ["USD"])
        from ..storage.db import dumps, loads

        # on garde aussi les annonces passées récentes (fenêtre d'exclusion encore en cours)
        old = [e for e in loads(self.app.db.get_meta("calendar_events"), []) if e["ts"] > time.time() - 86400]
        merged = {(e["ts"], e["label"]): e for e in old + events}
        self.app.db.set_meta("calendar_events", dumps(sorted(merged.values(), key=lambda e: e["ts"])))
        self.app.db.set_meta("calendar_fetched", str(time.time()))
        return len(events)

    def refresh(self, force_calendar: bool = False) -> dict:
        errors = {}
        with httpx.Client(timeout=10.0, headers=UA, transport=self.transport, follow_redirects=True) as c:
            try:
                self.refresh_calendar(c, force=force_calendar)
            except Exception as exc:
                errors["calendrier"] = str(exc)[:120]
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

            await asyncio.to_thread(lambda: self.refresh(force_calendar=True))
            return {**self.data, "macro": self._macro()}

    def _macro(self) -> list[dict]:
        from ..risk.macro import upcoming
        from ..storage.db import loads

        return upcoming(self.app.settings, time.time(), auto=loads(self.app.db.get_meta("calendar_events"), []))
