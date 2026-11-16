"""Candidats du laboratoire : règles simples de la littérature, paramètres fixés avant toute mesure.

Chaque candidat est une fonction pure ``(contexte) -> poids cibles`` :
- poids >= 0 (achat seulement), somme <= 1 (aucun levier) ; le reste est conservé en liquidités ;
- le contexte ne contient que les journées déjà closes (``Context`` est construit sur une vue
  tronquée) ; l'ordre est exécuté à l'ouverture suivante par le simulateur ;
- aucun paramètre n'est ajusté sur les données : un seul jeu par règle, celui de sa source.

Six familles : momentum, value (substitut par les prix), suivi de tendance, cassure, retour à la
moyenne, spécificités crypto. Les références simples (abstention, détention) sont définies à part
et ne sont jamais des candidats.

« Value » sans données fondamentales : seul un substitut par les prix est testable hors ligne
(retournement de long terme, comme Asness, Moskowitz et Pedersen pour les actifs sans bilan).
Les mesures de valeur propres aux blockchains (activité du réseau) ne sont pas dans les archives :
elles ne sont pas simulées.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .data import MAJORS, PEG_VOLATILITY, Panel

FAMILIES = ("momentum", "value", "trend", "breakout", "mean_reversion", "crypto")
MIN_AGE, TOP, VOLUME_WINDOW, PEG_WINDOW = 91, 20, 30, 90  # univers daté : ancienneté, nombre d'actifs, fenêtres de liquidité et de volatilité


@dataclass(frozen=True)
class Context:
    """Ce qu'une stratégie peut voir à la clôture du jour ``t`` (dernière ligne de ``panel``)."""
    panel: Panel  # vue tronquée : aucune ligne postérieure
    age: np.ndarray  # jours consécutifs de cotation de chaque colonne
    medvol: np.ndarray  # volume médian des 30 derniers jours
    vol90: np.ndarray  # volatilité quotidienne des 90 derniers jours (filtre des actifs adossés)
    held: dict = field(default_factory=dict)  # actif -> {"days": jours de détention, "entry": prix d'entrée, "value": valeur à la clôture}
    exclude: frozenset = frozenset()  # actifs retirés de l'univers (études de généralisation)
    majors: tuple = MAJORS  # paire d'actifs des règles « majeurs » (remplacée dans les études de généralisation)

    @property
    def t(self) -> int:
        return len(self.panel) - 1

    @property
    def weekday(self) -> int:
        """Jour de la semaine de la bougie qui vient de se clore (lundi = 0)."""
        return int((self.panel.days[-1] + 3) % 7)

    def col(self, base: str) -> int | None:
        """Colonne actuellement cotée d'un actif (la série en cours, s'il y a eu une reprise)."""
        best = None
        for j, name in enumerate(self.panel.assets):
            if self.panel.base(name) == base and self.age[j] > 0:
                best = j
        return best

    def universe(self, *, min_age: int = 90, top: int = 20) -> list[int]:
        """Actifs éligibles aujourd'hui : assez anciens, puis les plus liquides ; ne lit que le passé, ordre déterministe."""
        with np.errstate(invalid="ignore"):
            live = (self.age >= max(min_age, PEG_WINDOW + 1)) & np.isfinite(self.medvol) & (self.vol90 >= PEG_VOLATILITY)
        ok = [int(j) for j in np.flatnonzero(live)
              if self.panel.base(self.panel.assets[j]) not in self.exclude]
        ok.sort(key=lambda j: (-self.medvol[j], self.panel.assets[j]))
        return ok[:top]

    def ret(self, cols: list[int], lookback: int, skip: int = 0) -> np.ndarray:
        """Rendement de ``t - lookback`` à ``t - skip`` ; NaN si l'historique manque."""
        c = self.panel.close
        if len(c) <= lookback:
            return np.full(len(cols), np.nan)
        return c[-1 - skip, cols] / c[-1 - lookback, cols] - 1

    def sma(self, cols: list[int], n: int) -> np.ndarray:
        c = self.panel.close
        if len(c) < n:
            return np.full(len(cols), np.nan)
        return c[-n:, cols].mean(axis=0)  # NaN si une journée manque dans la fenêtre

    def vol(self, cols: list[int], n: int) -> np.ndarray:
        """Volatilité quotidienne des ``n`` derniers rendements."""
        c = self.panel.close
        if len(c) < n + 1:
            return np.full(len(cols), np.nan)
        w = c[-n - 1:, cols]
        return (w[1:] / w[:-1] - 1).std(axis=0, ddof=1)

    def name(self, j: int) -> str:
        return self.panel.assets[j]


Weights = dict[str, float]


def _pick(ctx: Context, cols: list[int], score: np.ndarray, n: int, *, highest: bool) -> list[int]:
    ok = [(float(s), ctx.name(j), j) for j, s in zip(cols, score) if np.isfinite(s)]
    ok.sort(key=lambda x: ((-x[0]) if highest else x[0], x[1]))
    return [j for _, _, j in ok[:n]]


def _equal(ctx: Context, cols: list[int], share: float) -> Weights:
    return {ctx.name(j): share for j in cols}


def _majors(ctx: Context) -> list[int]:
    return [j for j in (ctx.col(b) for b in ctx.majors) if j is not None]


def _broad(ctx: Context, need: int) -> list[int]:
    """Univers liquide daté : cotation depuis ``MIN_AGE`` jours au moins (et assez d'historique pour la règle), ``TOP`` plus liquides."""
    return ctx.universe(min_age=max(MIN_AGE, need), top=TOP)


# ----------------------------------------------------------------------------- momentum


def mom_ts(ctx: Context, p: dict) -> Weights:
    """Momentum en série temporelle : détenir un actif majeur si son rendement passé est positif."""
    cols = _majors(ctx)
    r = ctx.ret(cols, p["lookback"])
    return {ctx.name(j): 1 / len(ctx.majors) for j, x in zip(cols, r) if np.isfinite(x) and x > 0}


def mom_xs(ctx: Context, p: dict) -> Weights:
    """Momentum transversal : les ``n`` meilleurs rendements passés de l'univers liquide."""
    cols = _broad(ctx, p["lookback"] + 2)
    return _equal(ctx, _pick(ctx, cols, ctx.ret(cols, p["lookback"]), p["n"], highest=True), 1 / p["n"])


def mom_dual(ctx: Context, p: dict) -> Weights:
    """Momentum double : les meilleurs de l'univers, seulement si leur propre rendement est positif."""
    cols = _broad(ctx, p["lookback"] + 2)
    r = ctx.ret(cols, p["lookback"])
    best = _pick(ctx, cols, r, p["n"], highest=True)
    pos = {j for j, x in zip(cols, r) if np.isfinite(x) and x > 0}
    return _equal(ctx, [j for j in best if j in pos], 1 / p["n"])


# ----------------------------------------------------------------------------- value (substitut par les prix)


def value_reversal(ctx: Context, p: dict) -> Weights:
    """« Bon marché » = plus faibles rendements de long terme, mois le plus récent exclu."""
    cols = _broad(ctx, p["lookback"] + 2)
    return _equal(ctx, _pick(ctx, cols, ctx.ret(cols, p["lookback"], p["skip"]), p["n"], highest=False), 1 / p["n"])


def value_momentum(ctx: Context, p: dict) -> Weights:
    """Combinaison value + momentum : moyenne des deux rangs (Asness, Moskowitz, Pedersen)."""
    cols = _broad(ctx, p["lookback"] + 2)
    v, m = ctx.ret(cols, p["lookback"], p["skip"]), ctx.ret(cols, p["momentum"])
    ok = [k for k in range(len(cols)) if np.isfinite(v[k]) and np.isfinite(m[k])]
    if not ok:
        return {}
    rank_v = {k: r for r, k in enumerate(sorted(ok, key=lambda k: (v[k], ctx.name(cols[k]))))}  # bon marché d'abord
    rank_m = {k: r for r, k in enumerate(sorted(ok, key=lambda k: (-m[k], ctx.name(cols[k]))))}  # fort d'abord
    best = sorted(ok, key=lambda k: (rank_v[k] + rank_m[k], ctx.name(cols[k])))[:p["n"]]
    return _equal(ctx, [cols[k] for k in best], 1 / p["n"])


# ----------------------------------------------------------------------------- suivi de tendance


def trend_sma(ctx: Context, p: dict) -> Weights:
    """Détenir un actif majeur quand sa clôture est au-dessus de sa moyenne longue (Faber)."""
    cols = _majors(ctx)
    sma, c = ctx.sma(cols, p["n"]), ctx.panel.close[-1, cols]
    return {ctx.name(j): 1 / len(ctx.majors) for j, s, x in zip(cols, sma, c) if np.isfinite(s) and x > s}


def trend_cross(ctx: Context, p: dict) -> Weights:
    """Détenir un actif majeur quand sa moyenne courte est au-dessus de sa moyenne longue."""
    cols = _majors(ctx)
    fast, slow = ctx.sma(cols, p["fast"]), ctx.sma(cols, p["slow"])
    return {ctx.name(j): 1 / len(ctx.majors) for j, f, s in zip(cols, fast, slow) if np.isfinite(s) and f > s}


# ----------------------------------------------------------------------------- cassure (canaux de Donchian)


def _channel(ctx: Context, j: int, n: int, field: str) -> float:
    """Plus haut (ou plus bas) des ``n`` journées PRÉCÉDENTES, journée courante exclue."""
    a = getattr(ctx.panel, field)
    if len(a) < n + 1:
        return np.nan
    w = a[-n - 1:-1, j]
    return float(w.max() if field == "high" else w.min()) if np.isfinite(w).all() else np.nan


def _slots(ctx: Context, p: dict, cols: list[int], enter: Callable[[int], bool], leave: Callable[[int, dict], bool],
           priority: Callable[[int], float]) -> Weights:
    """Portefeuille à emplacements : on garde ce qui n'a pas de signal de sortie, on remplit les emplacements libres."""
    index = {ctx.name(j): j for j in range(len(ctx.panel.assets))}
    keep = [a for a, info in sorted(ctx.held.items(), key=lambda kv: (-kv[1].get("value", 0.0), kv[0])) if a in index and not leave(index[a], info)]
    keep = keep[:p["slots"]]  # un reliquat invendable (sous le minimum d'ordre) ne prend jamais un emplacement de plus
    free = p["slots"] - len(keep)
    fresh = sorted((j for j in cols if ctx.name(j) not in ctx.held and enter(j)), key=lambda j: (priority(j), ctx.name(j)))
    return {a: 1 / p["slots"] for a in keep + [ctx.name(j) for j in fresh[:max(0, free)]]}


def breakout(ctx: Context, p: dict) -> Weights:
    """Entrée sur clôture au-dessus du plus haut des ``entry`` jours précédents ; sortie sous le plus bas des ``exit`` jours."""
    cols = _majors(ctx) if p["universe"] == "majors" else _broad(ctx, p["entry"] + 2)
    close = ctx.panel.close[-1]

    def enter(j: int) -> bool:
        hi = _channel(ctx, j, p["entry"], "high")
        return bool(np.isfinite(hi) and close[j] > hi)

    def leave(j: int, info: dict) -> bool:
        lo = _channel(ctx, j, p["exit"], "low")
        return bool(np.isfinite(lo) and close[j] < lo)

    return _slots(ctx, p, cols, enter, leave, lambda j: -ctx.medvol[j] if np.isfinite(ctx.medvol[j]) else 0.0)


# ----------------------------------------------------------------------------- retour à la moyenne


def _rsi(close: np.ndarray, n: int) -> float:
    """RSI de Wilder sur les 60 dernières clôtures (assez pour que la récurrence ait convergé)."""
    w = close[-61:]
    if len(w) < n + 2 or not np.isfinite(w).all():
        return np.nan
    d = np.diff(w)
    up, dn = float(np.maximum(d[:n], 0).mean()), float(np.maximum(-d[:n], 0).mean())
    for x in d[n:]:
        up, dn = (up * (n - 1) + max(x, 0.0)) / n, (dn * (n - 1) + max(-x, 0.0)) / n
    return 100.0 if dn == 0 and up > 0 else 50.0 if dn == 0 else 100 - 100 / (1 + up / dn)


def meanrev_rsi(ctx: Context, p: dict) -> Weights:
    """Connors : achat d'un repli brutal (RSI court très bas) dans une tendance longue haussière ; sortie rapide."""
    cols = _broad(ctx, p["trend"] + 2)
    close = ctx.panel.close
    rsi = {j: _rsi(close[:, j], p["rsi"]) for j in cols}
    sma = dict(zip(cols, ctx.sma(cols, p["trend"])))

    def enter(j: int) -> bool:
        return bool(np.isfinite(rsi[j]) and rsi[j] < p["below"] and np.isfinite(sma[j]) and close[-1, j] > sma[j])

    def leave(j: int, info: dict) -> bool:
        fast = ctx.sma([j], p["exit_sma"])[0]
        return bool(info["days"] >= p["max_days"] or (np.isfinite(fast) and close[-1, j] > fast))

    return _slots(ctx, p, cols, enter, leave, lambda j: rsi[j])


def meanrev_xs(ctx: Context, p: dict) -> Weights:
    """Retournement de court terme : les ``n`` plus fortes baisses de la semaine dans l'univers liquide."""
    cols = _broad(ctx, 0)
    return _equal(ctx, _pick(ctx, cols, ctx.ret(cols, p["lookback"]), p["n"], highest=False), 1 / p["n"])


# ----------------------------------------------------------------------------- spécificités crypto


def btc_gate(ctx: Context, p: dict) -> Weights:
    """Le bitcoin comme climat du marché : panier des plus liquides seulement quand le BTC est au-dessus de sa moyenne."""
    btc = ctx.col("BTC")
    if btc is None:
        return {}
    sma = ctx.sma([btc], p["n"])[0]
    if not (np.isfinite(sma) and ctx.panel.close[-1, btc] > sma):
        return {}
    return _equal(ctx, ctx.universe(min_age=MIN_AGE, top=p["basket"]), 1 / p["basket"])


def vol_managed(ctx: Context, p: dict) -> Weights:
    """Exposition au BTC inversement proportionnelle à sa volatilité récente, plafonnée à 100 % (aucun levier)."""
    btc = ctx.col("BTC")
    if btc is None:
        return {}
    daily = ctx.vol([btc], p["window"])[0]
    if not np.isfinite(daily) or daily <= 0:
        return {}
    weight = min(1.0, p["target"] / (daily * np.sqrt(365)))
    return {ctx.name(btc): round(weight / p["step"]) * p["step"]}  # paliers : évite des ordres minuscules


def weekday(ctx: Context, p: dict) -> Weights:
    """Saisonnalité hebdomadaire : n'être investi en BTC que pendant le jour indiqué (0 = lundi)."""
    btc = ctx.col("BTC")
    if btc is None or (ctx.weekday + 1) % 7 != p["day"]:
        return {}
    return {ctx.name(btc): 1.0}


def low_vol(ctx: Context, p: dict) -> Weights:
    """Anomalie de faible volatilité : les ``n`` actifs les moins volatils de l'univers liquide."""
    cols = _broad(ctx, p["window"] + 2)
    return _equal(ctx, _pick(ctx, cols, ctx.vol(cols, p["window"]), p["n"], highest=False), 1 / p["n"])


# ----------------------------------------------------------------------------- références (jamais candidates)


def hold_btc(ctx: Context, p: dict) -> Weights:
    btc = ctx.col("BTC")
    return {} if btc is None else {ctx.name(btc): 1.0}


def hold_majors(ctx: Context, p: dict) -> Weights:
    return {ctx.name(j): 1 / len(ctx.majors) for j in _majors(ctx)}


def hold_basket(ctx: Context, p: dict) -> Weights:
    return _equal(ctx, ctx.universe(min_age=MIN_AGE, top=p["basket"]), 1 / p["basket"])


def cash(ctx: Context, p: dict) -> Weights:
    return {}


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    label: str
    fn: Callable[[Context, dict], Weights]
    params: dict
    rebalance: str  # daily | weekly | monthly
    benchmark: str  # btc | majors | market : référence de même exposition
    source: str
    warmup: int  # journées d'historique nécessaires avant la première décision

    def describe(self) -> dict:
        return {"name": self.name, "family": self.family, "label": self.label, "rule": self.fn.__name__, "params": self.params,
                "rebalance": self.rebalance, "benchmark": self.benchmark, "source": self.source, "warmup": self.warmup}


CANDIDATES: tuple[Spec, ...] = (
    Spec("mom_ts_28", "momentum", "Momentum 4 semaines, BTC et ETH", mom_ts, {"lookback": 28}, "weekly", "majors",
         "Moskowitz, Ooi, Pedersen (2012) ; Liu, Tsyvinski (2021) : momentum de 1 à 4 semaines sur les cryptos", 30),
    Spec("mom_xs_28_top3", "momentum", "Les 3 plus fortes hausses sur 4 semaines", mom_xs, {"lookback": 28, "n": 3}, "weekly", "market",
         "Jegadeesh, Titman (1993) ; Liu, Tsyvinski, Wu (2022) : facteur momentum des cryptos", 120),
    Spec("mom_dual_90_top3", "momentum", "Momentum double sur 3 mois", mom_dual, {"lookback": 90, "n": 3}, "weekly", "market",
         "Antonacci (2014) : momentum relatif et absolu", 120),
    Spec("value_rev_365_top3", "value", "Les 3 plus fortes baisses sur un an (mois récent exclu)", value_reversal,
         {"lookback": 365, "skip": 30, "n": 3}, "monthly", "market",
         "De Bondt, Thaler (1985) ; Asness, Moskowitz, Pedersen (2013) : retournement de long terme comme substitut de la value", 400),
    Spec("value_mom_combo_top3", "value", "Value + momentum (moyenne des rangs)", value_momentum,
         {"lookback": 365, "skip": 30, "momentum": 28, "n": 3}, "monthly", "market", "Asness, Moskowitz, Pedersen (2013)", 400),
    Spec("trend_sma_200", "trend", "Au-dessus de la moyenne 200 jours, BTC et ETH", trend_sma, {"n": 200}, "daily", "majors",
         "Faber (2007) : moyenne mobile de 10 mois", 201),
    Spec("trend_cross_50_200", "trend", "Moyenne 50 jours au-dessus de la moyenne 200 jours, BTC et ETH", trend_cross,
         {"fast": 50, "slow": 200}, "daily", "majors", "Brock, Lakonishok, LeBaron (1992) : double moyenne mobile", 201),
    Spec("breakout_20_10_slots4", "breakout", "Cassure 20 jours, sortie 10 jours, 4 emplacements", breakout,
         {"entry": 20, "exit": 10, "slots": 4, "universe": "broad"}, "daily", "market", "Donchian ; règles des Turtles, système 1 (Faith, 2007)", 120),
    Spec("breakout_55_20_majors", "breakout", "Cassure 55 jours, sortie 20 jours, BTC et ETH", breakout,
         {"entry": 55, "exit": 20, "slots": 2, "universe": "majors"}, "daily", "majors", "Règles des Turtles, système 2 (Faith, 2007)", 60),
    Spec("meanrev_rsi2_slots4", "mean_reversion", "Repli brutal en tendance haussière (RSI 2), 4 emplacements", meanrev_rsi,
         {"rsi": 2, "below": 10.0, "trend": 200, "exit_sma": 5, "max_days": 10, "slots": 4}, "daily", "market", "Connors, Alvarez (2008)", 210),
    Spec("meanrev_xs_7_bottom3", "mean_reversion", "Les 3 plus fortes baisses de la semaine", meanrev_xs, {"lookback": 7, "n": 3}, "weekly",
         "market", "Jegadeesh (1990) : retournement de court terme", 120),
    Spec("crypto_btc_gate_top5", "crypto", "Panier des 5 plus liquides quand le BTC est au-dessus de sa moyenne 100 jours", btc_gate,
         {"n": 100, "basket": 5}, "weekly", "market", "Filtre « climat du bitcoin » déjà utilisé par le projet (features.btc_regime)", 120),
    Spec("crypto_volmanaged_btc", "crypto", "BTC à exposition pilotée par la volatilité (plafond 100 %)", vol_managed,
         {"window": 30, "target": 0.40, "step": 0.25}, "weekly", "btc", "Moreira, Muir (2017) : portefeuilles pilotés par la volatilité", 35),
    Spec("crypto_weekday_btc", "crypto", "BTC le lundi seulement", weekday, {"day": 0}, "daily", "btc",
         "Caporale, Plastun (2019) : effet jour de la semaine sur le bitcoin", 2),
    Spec("crypto_lowvol_top3", "crypto", "Les 3 actifs les moins volatils", low_vol, {"window": 30, "n": 3}, "monthly", "market",
         "Ang, Hodrick, Xing, Zhang (2006) : anomalie de faible volatilité", 120),
)
REFERENCES: tuple[Spec, ...] = (
    Spec("cash", "reference", "Ne rien faire", cash, {}, "monthly", "market", "abstention", 0),
    Spec("hold_btc", "reference", "Acheter et garder le BTC", hold_btc, {}, "monthly", "btc", "détention simple", 1),
    Spec("hold_majors", "reference", "Acheter et garder BTC et ETH", hold_majors, {}, "monthly", "majors", "détention simple", 1),
    Spec("hold_basket_top5", "reference", "Panier des 5 plus liquides, revu chaque mois", hold_basket, {"basket": 5}, "monthly", "market",
         "détention diversifiée", 120),
)
BY_NAME = {s.name: s for s in CANDIDATES + REFERENCES}
