"""Agent d'actualités à faible délai : dépêches datées, classées et archivées, avec le délai publication → réception.

Sources publiques, interrogées à intervalle poli et en requête conditionnelle (ETag, Last-Modified) :
- annonces de Binance : nouvelles cotations (catégorie 48) et retraits (catégorie 161), datées à la milliseconde ;
- déclarations officielles des entreprises cotées : formulaires 8-K de la SEC (fil Atom « current ») ;
- communiqués de la Réserve fédérale ; fils de presse crypto (Cointelegraph, The Block, Decrypt) et marchés (MarketWatch).
Chaque dépêche est conservée une seule fois (empreinte), avec son heure de publication et son heure de réception,
dans une base SQLite en ajout seul : c'est l'archive à la date qui permettra, plus tard, de mesurer l'effet réel des
actualités sans regard vers le futur. Le texte d'une dépêche est une donnée, jamais une instruction.

Classement déterministe et immédiat (mots-clés, sans modèle de langue) ; une dépêche grave et négative déclenche un
veto d'entrée sur les actifs cités (24 heures), ou sur tout le marché crypto pour un événement systémique (6 heures).
Aucun ordre : l'agent alerte et bloque des entrées fictives, il n'achète ni ne vend.

Une passe : python -m sentinelle.scalp.flash --once
Veille bornée (lancée par le propriétaire) : python -m sentinelle.scalp.flash --watch --minutes 30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from ..ai.coins import detect_assets

UA = "Mozilla/5.0 (compatible; Sentinelle-simulation/1.0; research)"
BINANCE = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId={c}&pageNo=1&pageSize=20"
SOURCES = {
    "binance_cotations": {"kind": "binance", "url": BINANCE.format(c=48), "interval": 30},
    "binance_retraits": {"kind": "binance", "url": BINANCE.format(c=161), "interval": 30},
    "sec_8k": {"kind": "feed", "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom", "interval": 60},
    "fed": {"kind": "feed", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "interval": 120},
    "cointelegraph": {"kind": "feed", "url": "https://cointelegraph.com/rss", "interval": 60},
    "theblock": {"kind": "feed", "url": "https://www.theblock.co/rss.xml", "interval": 60},
    "decrypt": {"kind": "feed", "url": "https://decrypt.co/feed", "interval": 60},
    "marketwatch": {"kind": "feed", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "interval": 60},
}
CRYPTO = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "LINK", "AVAX", "LTC", "DOT", "SUI", "TRX", "NEAR", "APT", "DOGE",
          "PEPE", "WIF", "BONK", "SHIB", "FLOKI", "USDT", "USDC"}
EXTRA = {"WIF": r"dogwifhat|\$?WIF\b", "BONK": r"\bbonk\b", "FLOKI": r"\bfloki\b"}
FUNDS = {"SPY": r"s&p 500|wall street|stocks? (?:fall|rise|rall|slump|drop)", "IEF": r"treasur|bond yields?",
         "TLT": r"treasur|bond yields?", "GLD": r"\bgold\b", "DBC": r"\boil\b|commodit|crude", "VNQ": r"real estate|reit",
         "EEM": r"emerging markets?|china stocks", "EFA": r"europe(?:an)? stocks|japan(?:ese)? stocks|developed markets"}
# (type, gravité, sens) : gravité « grave » → veto ; sens -1 négatif, +1 positif, 0 neutre.
RULES = [
    ("retrait", "grave", -1, r"delist|removal of spot trading|will remove|cease trading|retrait de la cotation"),
    ("piratage", "grave", -1, r"\bhack|exploit|drained|stolen|breach|attacker|piratage"),
    ("faillite", "grave", -1, r"bankrupt|insolven|chapter 11|halts? withdrawals|suspends? withdrawals|faillite|item 1\.03"),
    ("décrochage", "grave", -1, r"depeg|de-peg|loses? (?:its )?peg"),
    ("justice", "importante", -1, r"\bsues?\b|lawsuit|charged|indict|fraud|investigation|\bban(?:s|ned)?\b|poursuit"),
    ("dirigeant", "importante", 0, r"item 5\.02|\bceo\b.{0,40}(?:fired|ousted|resign|steps down|departure|returns|reinstated)|departure of (?:directors|officers)"),
    ("cotation", "importante", 1, r"will list|will add|new listing|launchpool|launches .* trading|nouvelle cotation"),
    ("macro", "importante", 0, r"\bfomc\b|rate decision|federal funds|interest rates?|\bcpi\b|inflation|payrolls|jobs report|powell"),
    ("résultats", "normale", 0, r"item 2\.02|earnings|quarterly results|résultats trimestriels"),
    ("fusion", "importante", 0, r"item 1\.01|item 2\.01|acquisition|acquire|merger|takeover"),
]
SYSTEMIC = r"binance|coinbase|tether|usdt|usdc|stablecoin|exchange"
VETO_ASSET_MS, VETO_MARKET_MS = 24 * 3_600_000, 6 * 3_600_000


def classify(title: str, summary: str = "") -> dict:
    text = f"{title} {summary}"
    low = text.lower()
    event, severity, direction = "autre", "normale", 0
    for name, sev, sign, pattern in RULES:
        if re.search(pattern, low):
            event, severity, direction = name, sev, sign
            break
    assets = set(detect_assets(text, CRYPTO))
    assets |= {a for a, p in EXTRA.items() if re.search(p, text, re.I)}
    funds = {f for f, p in FUNDS.items() if re.search(p, low)}
    # Tout le marché : décision macroéconomique, ou incident grave d'un acteur systémique (plateforme, stablecoin).
    # Un retrait de cotation ne vise que les cryptos citées, jamais tout le marché.
    market_wide = event == "macro" or (event in ("piratage", "faillite", "décrochage") and re.search(SYSTEMIC, low) is not None)
    return {"event": event, "severity": severity, "direction": direction, "assets": sorted(assets),
            "funds": sorted(funds), "market_wide": bool(market_wide)}


def _ms(text) -> int | None:
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text)
        return int(d.timestamp() * 1000) if d.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def parse(kind: str, body: bytes) -> list[dict]:
    """Dépêches d'une réponse : titre, lien, heure de publication (ms UTC) ; tout texte reste une donnée."""
    out = []
    if kind == "binance":
        payload = json.loads(body)
        for cat in (payload.get("data") or {}).get("catalogs") or []:
            for a in cat.get("articles") or []:
                out.append({"title": str(a.get("title", ""))[:300], "summary": "",
                            "link": f"https://www.binance.com/en/support/announcement/{a.get('code', '')}",
                            "published_ms": int(a["releaseDate"]) if a.get("releaseDate") else None})
        return out
    import feedparser
    feed = feedparser.parse(body)
    if not feed.version or len(feed.entries) > 2000:
        raise ValueError("flux RSS ou Atom illisible")
    for e in feed.entries[:200]:
        title = re.sub(r"\s+", " ", str(e.get("title", ""))).strip()[:300]
        if not title:
            continue
        out.append({"title": title, "summary": re.sub(r"<[^>]+>", " ", str(e.get("summary", "")))[:600],
                    "link": str(e.get("link", ""))[:500],
                    "published_ms": _ms(e.get("published")) or _ms(e.get("updated"))})
    return out


class Archive:
    """Archive SQLite en ajout seul : une dépêche est enregistrée une fois, jamais modifiée."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, source TEXT, title TEXT, link TEXT,
                published_ms INTEGER, received_ms INTEGER, baseline INTEGER, event TEXT, severity TEXT,
                direction INTEGER, assets TEXT, funds TEXT, market_wide INTEGER);
            CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, etag TEXT, modified TEXT, polled_ms INTEGER,
                status TEXT);
            CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON items BEGIN SELECT RAISE(ABORT, 'archive en ajout seul'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON items BEGIN SELECT RAISE(ABORT, 'archive en ajout seul'); END;
        """)

    def known_source(self, name) -> bool:
        return self.db.execute("SELECT 1 FROM items WHERE source=? LIMIT 1", (name,)).fetchone() is not None

    def add(self, source: str, item: dict, received_ms: int, baseline: bool) -> dict | None:
        key = hashlib.sha256(f"{source}|{item['link']}|{item['title']}".encode()).hexdigest()
        c = classify(item["title"], item.get("summary", ""))
        cur = self.db.execute("INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (key, source, item["title"], item["link"], item["published_ms"], received_ms, int(baseline),
                               c["event"], c["severity"], c["direction"], json.dumps(c["assets"]), json.dumps(c["funds"]),
                               int(c["market_wide"])))
        self.db.commit()
        return {**item, **c, "source": source, "received_ms": received_ms, "baseline": baseline} if cur.rowcount else None

    def vetoes(self, now_ms: int) -> dict:
        """Entrées bloquées à l'instant « now » : par actif, et pour tout le marché crypto."""
        # La fenêtre part de l'heure de publication : une dépêche ancienne lue tardivement ne bloque rien.
        rows = self.db.execute("SELECT title, COALESCE(published_ms, received_ms) AS t, assets, market_wide FROM items "
                               "WHERE severity='grave' AND direction<0 AND received_ms <= ? "
                               "AND COALESCE(published_ms, received_ms) BETWEEN ? AND ?",
                               (now_ms, now_ms - VETO_ASSET_MS, now_ms)).fetchall()
        assets, market = {}, []
        for title, published, raw, wide in rows:
            if wide and published >= now_ms - VETO_MARKET_MS:
                market.append(title)
            for a in json.loads(raw):
                assets.setdefault(a, []).append(title)
        return {"assets": assets, "market": market}

    def latency(self) -> dict:
        rows = self.db.execute("SELECT source, (received_ms - published_ms) / 1000.0 FROM items WHERE baseline=0 "
                               "AND published_ms IS NOT NULL AND received_ms >= published_ms").fetchall()
        out = {}
        for s, lat in rows:
            out.setdefault(s, []).append(lat)
        return {s: {"n": len(v), "median_s": sorted(v)[len(v) // 2], "max_s": max(v)} for s, v in out.items()}


def poll(archive: Archive, client: httpx.Client, now_ms: int, sources=SOURCES, force=False, clock=time.time) -> list[dict]:
    """Une passe sur les sources dont l'intervalle est écoulé ; renvoie les dépêches nouvelles."""
    fresh = []
    for name, spec in sources.items():
        row = archive.db.execute("SELECT etag, modified, polled_ms FROM sources WHERE name=?", (name,)).fetchone()
        etag, modified, polled = row if row else (None, None, None)
        if not force and polled and now_ms - polled < spec["interval"] * 1000:
            continue
        headers = {"User-Agent": UA}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        baseline = not archive.known_source(name)  # première lecture : l'existant n'a pas de délai mesurable
        try:
            r = client.get(spec["url"], headers=headers, timeout=20)
            status = str(r.status_code)
            if r.status_code == 200:
                received = int(clock() * 1000)  # heure réelle de réception, pour mesurer le délai
                for item in parse(spec["kind"], r.content):
                    new = archive.add(name, item, received, baseline)
                    if new:
                        fresh.append(new)
                etag, modified = r.headers.get("ETag"), r.headers.get("Last-Modified")
        except (httpx.HTTPError, ValueError) as e:
            status = f"erreur : {type(e).__name__}"
        archive.db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)", (name, etag, modified, now_ms, status))
        archive.db.commit()
    return fresh


def main(argv=None):
    ap = argparse.ArgumentParser(description="Agent d'actualités à faible délai (aucun ordre)")
    ap.add_argument("--db", default="data/actualites-flash.sqlite")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--minutes", type=float, default=30.0)
    args = ap.parse_args(argv)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    archive = Archive(args.db)
    stop = time.time() + (args.minutes * 60 if args.watch else 0)
    with httpx.Client(follow_redirects=True) as client:
        while True:
            now = int(time.time() * 1000)
            for d in poll(archive, client, now, force=args.once):
                if not d["baseline"]:
                    lat = (d["received_ms"] - d["published_ms"]) / 1000 if d["published_ms"] else None
                    print(f"[{d['source']}] {d['event']} ({d['severity']}) {d['title'][:110]} | actifs {d['assets'] or d['funds']} "
                          f"| délai {lat:.0f} s" if lat is not None else f"[{d['source']}] {d['title'][:110]}")
            if not args.watch or time.time() >= stop:
                break
            time.sleep(5)
    status = dict(archive.db.execute("SELECT name, status FROM sources").fetchall())
    count = dict(archive.db.execute("SELECT source, COUNT(*) FROM items GROUP BY source").fetchall())
    print("sources :", {k: f"{status.get(k)} ({count.get(k, 0)} dépêches)" for k in SOURCES})
    print("délais mesurés (dépêches apparues pendant la veille) :", archive.latency() or "aucune encore")
    print("vetos en cours :", archive.vetoes(int(time.time() * 1000)))


if __name__ == "__main__":
    main()
