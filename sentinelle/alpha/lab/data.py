"""Panneau journalier multi-actifs, lu hors ligne, et univers daté (« ce qui était cotable ce jour-là »).

Règles de construction, fixées avant toute mesure :

- une colonne = une série CONTINUE de journées d'un actif. Un jour manquant (cotation suspendue,
  symbole réutilisé pour un autre jeton) coupe la série en deux colonnes : une position ne
  traverse jamais un trou, et la suite repart comme une nouvelle cotation ;
- les jetons adossés (monnaies stables, or, versions « enveloppées »), les jetons à effet de
  levier intégré et les actions ou fonds « tokenisés » sont exclus par règle : ils ne portent pas
  le risque étudié, ou portent un levier interdit. Un filtre causal de volatilité écarte en plus
  de l'univers tout actif resté quasi immobile pendant 90 jours (monnaie stable non répertoriée) ;
- ``Panel.view(t)`` découpe physiquement les tableaux : une stratégie qui reçoit la vue du jour
  ``t`` ne détient aucune donnée postérieure ;
- l'univers d'un jour ne dépend que du passé : ancienneté de cotation et volume médian récent.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DAY_MS = 86_400_000
PEGGED = frozenset({
    "USDC", "BUSD", "TUSD", "PAX", "USDP", "DAI", "FDUSD", "UST", "USTC", "SUSD", "USDS", "USDSB", "USD1", "USDE", "XUSD", "BFUSD",
    "PYUSD", "USDD", "GUSD", "EUR", "EURI", "AEUR", "GBP", "AUD", "BKRW", "BVND", "IDRT", "BIDR", "NGN", "RUB", "TRY", "UAH", "BRL",
    "ZAR", "VAI", "PAXG", "XAUT", "WBTC", "WBETH", "BETH", "BNSOL", "USDSOLD", "RLUSD", "RWUSD",
})
TOKENIZED_GROUP = "TRD_GRP_261"  # groupe du catalogue public propre aux actions et fonds « tokenisés » (dont des fonds à levier)
PEG_VOLATILITY = 0.005  # volatilité quotidienne sur 90 jours en dessous de laquelle un actif est tenu pour adossé (règle causale)
LEVERAGED = re.compile(r"(UP|DOWN|BULL|BEAR)$")
NOT_LEVERAGED = frozenset({"JUP", "SYRUP"})  # noms qui finissent par hasard comme un jeton à levier
MAJORS = ("BTC", "ETH")


def excluded(base: str) -> str | None:
    if base in PEGGED:
        return "pegged"
    if LEVERAGED.search(base) and base not in NOT_LEVERAGED:
        return "leveraged"
    return None


@dataclass(frozen=True)
class Panel:
    days: np.ndarray  # jour UTC d'ouverture de chaque bougie (nombre de jours depuis 1970)
    assets: tuple[str, ...]  # nom de colonne : actif, ou actif#2 pour une série reprise après un trou
    open: np.ndarray  # (jours, actifs) ; NaN hors cotation
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    qvol: np.ndarray  # volume en devise de cotation
    provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        n, k = len(self.days), len(self.assets)
        for a in (self.open, self.high, self.low, self.close, self.qvol):
            if a.shape != (n, k):
                raise ValueError("Panneau incohérent")
        if n > 1 and not np.all(np.diff(self.days) == 1):
            raise ValueError("La grille des jours doit être continue")
        if len(set(self.assets)) != k:
            raise ValueError("Colonnes en double")

    def __len__(self) -> int:
        return len(self.days)

    def view(self, t: int) -> "Panel":
        """Ce qui est connu à la clôture du jour d'indice ``t`` : les ``t + 1`` premières lignes, rien d'autre."""
        if not 0 <= t < len(self.days):
            raise IndexError(t)
        cut = slice(0, t + 1)
        return Panel(self.days[cut], self.assets, self.open[cut], self.high[cut], self.low[cut], self.close[cut], self.qvol[cut], {})

    def col(self, asset: str) -> int:
        return self.assets.index(asset)

    def index_of(self, date: str) -> int:
        """Indice du jour ``AAAA-MM-JJ`` (UTC)."""
        day = int(np.datetime64(date, "D").astype(int))
        i = int(day - self.days[0])
        if not 0 <= i < len(self.days):
            raise KeyError(date)
        return i

    def date(self, t: int) -> str:
        return str(np.datetime64(int(self.days[t]), "D"))

    def base(self, asset: str) -> str:
        return asset.split("#")[0]


def from_series(series: dict[str, dict], provenance: dict | None = None) -> Panel:
    """``series`` : actif -> {days, open, high, low, close, qvol} ; coupe chaque série à ses trous."""
    cols: dict[str, dict] = {}
    for name in sorted(series):
        s = series[name]
        days = np.asarray(s["days"], dtype=np.int64)
        if len(days) == 0:
            continue
        if np.any(np.diff(days) <= 0):
            raise ValueError(f"Jours non croissants pour {name}")
        breaks = np.flatnonzero(np.diff(days) != 1) + 1
        for k, idx in enumerate(np.split(np.arange(len(days)), breaks)):
            cols[name if k == 0 else f"{name}#{k + 1}"] = {f: np.asarray(s[f], dtype=float)[idx] for f in ("open", "high", "low", "close", "qvol")} | {"days": days[idx]}
    if not cols:
        raise ValueError("Aucune série")
    first = min(int(c["days"][0]) for c in cols.values())
    last = max(int(c["days"][-1]) for c in cols.values())
    grid = np.arange(first, last + 1, dtype=np.int64)
    names = tuple(cols)
    mats = {f: np.full((len(grid), len(names)), np.nan) for f in ("open", "high", "low", "close", "qvol")}
    for j, name in enumerate(names):
        c = cols[name]
        lo = int(c["days"][0] - first)
        for f in mats:
            mats[f][lo:lo + len(c["days"]), j] = c[f]
    return Panel(grid, names, mats["open"], mats["high"], mats["low"], mats["close"], mats["qvol"], provenance or {})


def load_archive(root: Path, *, end_ms: int | None = None) -> Panel:
    """Lit une archive produite par ``tools.alpha_lab_fetch`` : lecture seule, empreintes vérifiées, exclusions par règle."""
    root = Path(root)
    payload = (root / "manifest.json").read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != (root / "manifest.sha256").read_text().strip():
        raise ValueError("Manifeste de l'archive altéré")
    manifest = json.loads(payload)
    if manifest.get("kind") != "alpha-lab-daily-archive" or manifest.get("interval") != "1d" or not manifest.get("complete"):
        raise ValueError("Archive inattendue ou incomplète")
    info_raw = (root / "exchangeInfo.json").read_bytes()
    if hashlib.sha256(info_raw).hexdigest() != manifest["exchange_info_sha256"]:
        raise ValueError("Catalogue de la plateforme altéré")
    tokenized = {s["symbol"] for s in json.loads(info_raw)["symbols"]
                 if any(TOKENIZED_GROUP in group for group in s.get("permissionSets", []))}
    series, skipped, invalid = {}, {}, 0
    for symbol, meta in sorted(manifest["symbols"].items()):
        reason = "tokenized_security" if symbol in tokenized else excluded(meta["base"])
        if reason or not meta["bars"]:
            skipped[symbol] = reason or "empty"
            continue
        raw = (root / "klines" / f"{symbol}.csv.gz").read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise ValueError(f"Fichier altéré : {symbol}")
        rows = [ln.split(",") for ln in gzip.decompress(raw).decode("utf-8").splitlines()[1:]]
        a = np.array([[float(x) for x in r[:7]] for r in rows])
        if end_ms is not None:
            a = a[a[:, 0] + DAY_MS <= end_ms]
        ok = (a[:, 1:5] > 0).all(axis=1) & (a[:, 2] >= np.maximum(a[:, 1], a[:, 4])) & (a[:, 3] <= np.minimum(a[:, 1], a[:, 4])) & (a[:, 6] >= 0)
        invalid += int((~ok).sum())
        a = a[ok]  # une bougie incohérente est écartée (elle coupe la série), jamais corrigée
        if len(a):
            series[meta["base"] if meta["base"] not in series else symbol] = {
                "days": (a[:, 0] // DAY_MS).astype(np.int64), "open": a[:, 1], "high": a[:, 2], "low": a[:, 3], "close": a[:, 4], "qvol": a[:, 6]}
    prov = {"archive_manifest_sha256": digest, "endpoint": manifest["endpoint"], "quote": manifest["quote"], "end_ms": manifest["end_ms"],
            "symbols_in_archive": len(manifest["symbols"]), "series_loaded": len(series), "excluded": {r: sum(1 for v in skipped.values() if v == r)
                                                                                                 for r in sorted(set(skipped.values()))},
            "invalid_bars_dropped": invalid}
    return from_series(series, prov)


def quality(panel: Panel) -> dict:
    """Constat sur les données (aucun rendement de stratégie) : nombre d'actifs cotés par an, séries reprises, fins de cotation."""
    present = np.isfinite(panel.close)
    years = {}
    for t in range(0, len(panel), 1):
        y = panel.date(t)[:4]
        years.setdefault(y, []).append(int(present[t].sum()))
    ended = [a for j, a in enumerate(panel.assets) if not present[-1, j]]
    with np.errstate(invalid="ignore", divide="ignore"):
        r = panel.close[1:] / panel.close[:-1] - 1
    return {"days": len(panel), "first": panel.date(0), "last": panel.date(len(panel) - 1), "columns": len(panel.assets),
            "resumed_series": sum(1 for a in panel.assets if "#" in a), "ended_before_last_day": len(ended),
            "listed_per_year": {y: {"min": min(v), "max": max(v)} for y, v in years.items()},
            "daily_moves_above_100pct": int(np.nansum(r > 1.0)), "daily_moves_below_minus_50pct": int(np.nansum(r < -0.5))}


def load_rules(root: Path) -> tuple[dict[str, dict], dict]:
    """Règles de marché (pas de quantité, montant minimal) par actif, lues dans le catalogue ARCHIVÉ de la plateforme.

    Le catalogue est vérifié par son empreinte (manifeste de l'archive). Limite connue : ce sont les filtres du jour de
    l'archive, appliqués au passé (les pas ont pu changer). Renvoie (règles par actif, provenance).
    """
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    raw = (root / "exchangeInfo.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != manifest["exchange_info_sha256"]:
        raise ValueError("Catalogue de la plateforme altéré")
    rules: dict[str, dict] = {}
    for s in json.loads(raw)["symbols"]:
        if s.get("quoteAsset") != manifest.get("quote", "USDT"):
            continue
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("LOT_SIZE") or {}
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        try:
            step = float(lot["stepSize"])
            mn = float(notional.get("minNotional", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if step > 0:
            rules[s["baseAsset"]] = {"symbol": s["symbol"], "step": step, "min_notional": mn}
    return rules, {"exchange_info_sha256": digest, "assets": len(rules),
                   "limit": "filtres du jour de l'archive appliqués à tout l'historique"}
