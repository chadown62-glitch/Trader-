"""Module C et laboratoire — sélection d'alpha sur des memecoins Solana, coûts DEX complets, contrôle causal du veto, stress.

Principes :
- jeu de données CAUSAL : à la minute ``i``, une règle ne voit que les bougies ``<= i`` ; l'exécution a lieu à l'ouverture
  d'une minute suivante (une de plus par transaction échouée) ;
- découpage par date de création des pools : 60 % entraînement, 20 % développement, 20 % RÉSERVÉ (liste hachée, aucun
  calcul de stratégie dessus ici) ;
- coûts : frais du DEX, impact de prix (produit constant), frais du réseau, rente du compte de jeton (immobilisée,
  rendue à la clôture), transactions échouées, MEV : hypothèses déclarées, jamais présentées comme mesurées ;
- le filtre de sécurité mesuré à la collecte est POSTÉRIEUR aux trades passés : un backtest « filtré » est optimiste et
  non causal ; seul le contrôle en avant (décision à la collecte, issue lue plus tard) évalue le veto honnêtement ;
- passer le veto n'est pas un signal de gain.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass

import numpy as np

from . import guardian

ORDER_USD = 11.0  # ≈ 10 €, le minimum d'ordre du hub
DEX_FEES = {  # frais de pool DÉCLARÉS par plateforme (hypothèses ; inconnu → 1 % par prudence)
    "pump-fun": 0.0125, "pumpswap": 0.0030, "raydium": 0.0025, "raydium-clmm": 0.0025, "raydium-launchlab": 0.0100,
    "meteora": 0.0025, "meteora-dbc": 0.0100, "meteora-damm-v2": 0.0050, "orca": 0.0030,
}
DEFAULT_DEX_FEE = 0.0100
NETWORK = {"base_lamports": 5_000, "priority_lamports": 10_000, "tip_lamports": 100_000, "rent_lamports": 2_039_280}
HYPOTHESES = {"failed_tx_probability": 0.10, "mev_bps": 30.0, "sol_usd_fallback": 140.0}
SPLIT = (0.6, 0.2, 0.2)
COLLAPSE = 0.10  # prix ≤ 10 % du prix de référence : effondrement (« rug » probable)


# -- coûts --------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Costs:
    dex_fee: float
    mev: float
    network_usd_per_swap: float
    rent_usd: float
    failed_p: float

    @classmethod
    def for_pool(cls, pool: dict, sol_usd: float, *, fee_mult: float = 1.0, net_mult: float = 1.0, failed_p: float | None = None) -> "Costs":
        lam = (NETWORK["base_lamports"] + NETWORK["priority_lamports"] + NETWORK["tip_lamports"]) * net_mult
        return cls(DEX_FEES.get(pool.get("dex") or "", DEFAULT_DEX_FEE) * fee_mult, HYPOTHESES["mev_bps"] / 1e4, lam / 1e9 * sol_usd,
                   NETWORK["rent_lamports"] / 1e9 * sol_usd, HYPOTHESES["failed_tx_probability"] if failed_p is None else failed_p)


def impact(order_usd: float, reserve_usd: float | None) -> float:
    """Impact d'un ordre sur un pool à produit constant (moitié de la réserve de chaque côté)."""
    if not reserve_usd or reserve_usd <= 0:
        return 1.0
    side = reserve_usd / 2
    return order_usd / (side + order_usd)


def reserve_at(price: float, pool: dict, mode: str) -> float | None:
    """Réserve à un instant passé : inconnue historiquement. « central » : √(prix relatif) × réserve actuelle (≤ 3×) ;
    « prudent » : jamais plus que la réserve actuelle (un pool vidé depuis pénalise aussi les trades d'avant)."""
    now, p_now = pool.get("reserve_usd"), pool.get("price_usd")
    if not now or not p_now or price <= 0:
        return now
    est = min(3 * now, now * math.sqrt(price / p_now))
    return min(now, est) if mode == "prudent" else est


# -- données ------------------------------------------------------------------------------------------------------------
def dense(candles: list[list[float]]) -> np.ndarray | None:
    """Grille minute continue [ts, o, h, l, c, v] : une minute sans échange garde le dernier prix et un volume nul."""
    if len(candles) < 2:
        return None
    a = np.array(candles, float)
    t0, t1 = int(a[0, 0]), int(a[-1, 0])
    n = (t1 - t0) // 60000 + 1
    if n > 5000:
        return None
    out = np.zeros((n, 6))
    out[:, 0] = t0 + 60000 * np.arange(n)
    idx = ((a[:, 0] - t0) // 60000).astype(int)
    last = a[0, 4]
    pos = dict(zip(idx.tolist(), range(len(a))))
    for i in range(n):
        if i in pos:
            r = a[pos[i]]
            out[i, 1:] = r[1:6]
            last = r[4]
        else:
            out[i, 1:5] = last
    return out


def split(pools: list[dict]) -> dict[str, list[str]]:
    ordered = sorted((p for p in pools if p.get("created_ms")), key=lambda p: (p["created_ms"], p["pool"]))
    n = len(ordered)
    a, b = int(round(SPLIT[0] * n)), int(round((SPLIT[0] + SPLIT[1]) * n))
    return {"train": [p["pool"] for p in ordered[:a]], "dev": [p["pool"] for p in ordered[a:b]], "reserved": [p["pool"] for p in ordered[b:]]}


# -- règles (module C), paramètres fixés d'avance -----------------------------------------------------------------------
def _ema(x: np.ndarray, n: int) -> np.ndarray:
    k, out = 2 / (n + 1), np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + k * (x[i] - out[i - 1])
    return out


def signals(name: str, g: np.ndarray) -> np.ndarray:
    """Booléen par minute : la règle veut entrer à la clôture de cette minute (causal : bougies ``<= i`` seulement)."""
    o, h, low, c, v = g[:, 1], g[:, 2], g[:, 3], g[:, 4], g[:, 5]
    n = len(c)
    out = np.zeros(n, bool)
    if name == "trend_ema":
        e5, e20 = _ema(c, 5), _ema(c, 20)
        out[1:] = (e5[1:] > e20[1:]) & (e5[:-1] <= e20[:-1])
        out[:60] = False
        return out
    for i in range(60, n):
        if name in ("momentum_15", "fast_exit"):
            base = v[i - 35:i - 5].sum() / 30 * 5
            out[i] = c[i] / c[i - 15] - 1 > 0.10 and v[i - 4:i + 1].sum() > 2 * max(base, 1e-9)
        elif name == "breakout_60":
            med = np.median(v[i - 60:i])
            out[i] = c[i] > h[i - 60:i].max() and v[i] > 3 * max(med, 1e-9) and v[i] > 0
        elif name == "reversal_30":
            peak = h[i - 60:i].max()
            med = np.median(v[i - 60:i])
            out[i] = c[i] <= 0.7 * peak and c[i] > o[i] and v[i] > 2 * max(med, 1e-9)
    return out


EXITS = {  # prise de bénéfice, stop, durée maximale (minutes), sortie suiveuse, sortie sur croisement
    "momentum_15": {"tp": 0.20, "sl": 0.10, "max": 60},
    "breakout_60": {"trail": 0.10, "max": 60},
    "trend_ema": {"sl": 0.15, "max": 120, "cross": True},
    "reversal_30": {"tp": 0.15, "sl": 0.15, "max": 45},
    "fast_exit": {"tp": 0.08, "sl": 0.05, "max": 15},
}
STRATEGIES = tuple(EXITS)
COOLDOWN = 30


def simulate_pool(name: str, g: np.ndarray, pool: dict, sol_usd: float, *, mode: str = "central", latency: int = 0,
                  fee_mult: float = 1.0, net_mult: float = 1.0, failed_p: float | None = None, rng: np.random.Generator) -> list[dict]:
    cost = Costs.for_pool(pool, sol_usd, fee_mult=fee_mult, net_mult=net_mult, failed_p=failed_p)
    sig = signals(name, g)
    ex = EXITS[name]
    o, h, low, c = g[:, 1], g[:, 2], g[:, 3], g[:, 4]
    e5, e20 = (_ema(c, 5), _ema(c, 20)) if ex.get("cross") else (None, None)
    trades, i, n = [], 60, len(c)
    while i < n - 1:
        if not sig[i]:
            i += 1
            continue
        fails_in = int(rng.random() < cost.failed_p) + latency
        e = i + 1 + fails_in
        if e >= n:
            break
        px_in = o[e]
        res_in = reserve_at(px_in, pool, mode)
        imp_in = impact(ORDER_USD, res_in)
        tokens = ORDER_USD * (1 - cost.dex_fee) * (1 - imp_in) * (1 - cost.mev) / px_in
        peak, exit_i, px_out, why = px_in, None, None, None
        for k in range(e, min(n, e + ex["max"] + 1)):
            if ex.get("sl") and low[k] <= px_in * (1 - ex["sl"]):
                exit_i, why = k, "stop"
                px_out = min(px_in * (1 - ex["sl"]), c[k])  # traversée du stop : la clôture si elle est plus basse (prudent)
                break
            if ex.get("tp") and h[k] >= px_in * (1 + ex["tp"]):
                exit_i, px_out, why = k, px_in * (1 + ex["tp"]), "objectif"
                break
            peak = max(peak, c[k])
            if ex.get("trail") and c[k] <= peak * (1 - ex["trail"]):
                exit_i, px_out, why = k, c[k], "suiveuse"
                break
            if ex.get("cross") and k > e and e5[k] < e20[k]:
                exit_i, px_out, why = k, c[k], "croisement"
                break
        if exit_i is None:
            exit_i = min(n - 1, e + ex["max"])
            px_out, why = (o[exit_i] if exit_i < n - 1 else c[exit_i]), ("durée" if exit_i < n - 1 else "fin des données")
        fails_out = int(rng.random() < cost.failed_p)
        if fails_out and exit_i + 1 < n:  # sortie échouée : une minute plus tard, au prix d'alors
            exit_i += 1
            px_out = min(px_out, o[exit_i]) if why == "stop" else o[exit_i]
        gross_out = tokens * px_out
        imp_out = impact(gross_out, reserve_at(px_out, pool, mode))
        proceeds = gross_out * (1 - cost.dex_fee) * (1 - imp_out) * (1 - cost.mev)
        network = cost.network_usd_per_swap * (2 + fails_in - latency + fails_out)
        net = proceeds - ORDER_USD - network
        trades.append({"pool": pool["pool"], "entry": int(e), "entry_ms": int(g[e, 0]), "exit": int(exit_i), "minutes": int(exit_i - e), "why": why,
                       "gross_return": float(px_out / px_in - 1), "net_usd": float(net), "net_return": float(net / ORDER_USD),
                       "fees_usd": float(ORDER_USD * cost.dex_fee + gross_out * cost.dex_fee),
                       "impact_usd": float(ORDER_USD * imp_in + gross_out * (1 - cost.dex_fee) * imp_out),
                       "network_usd": float(network), "mev_usd": float((ORDER_USD + gross_out) * cost.mev), "rent_usd_immobilized": cost.rent_usd})
        i = exit_i + COOLDOWN
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t["net_return"] for t in trades])
    usd = np.array([t["net_usd"] for t in trades])
    wins, losses = usd[usd > 0], usd[usd <= 0]
    boot = np.random.default_rng(56).choice(r, size=(2000, len(r))).mean(axis=1)
    return {"trades": len(trades), "pools": len({t["pool"] for t in trades}), "win_rate": float((r > 0).mean()),
            "mean_net_return": float(r.mean()), "median_net_return": float(np.median(r)),
            "mean_ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))], "total_net_usd": float(usd.sum()),
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
            "mean_gross_return": float(np.mean([t["gross_return"] for t in trades])),
            "mean_costs_usd": {k: float(np.mean([t[k] for t in trades])) for k in ("fees_usd", "impact_usd", "network_usd", "mev_usd")},
            "exits": {w: sum(1 for t in trades if t["why"] == w) for w in sorted({t["why"] for t in trades})}}


# -- contrôle en avant du veto ------------------------------------------------------------------------------------------
def forward_check(pools: list[dict], verdicts: dict, follow_ohlcv: dict, sol_usd: float, decided_ms: dict | None = None) -> dict:
    """Décision à la collecte (données d'alors), issue lue plus tard : part des effondrements et rendement après coûts."""
    groups: dict[str, list] = {}
    for p in pools:
        v = verdicts.get(p["pool"])
        f = follow_ohlcv.get(p["pool"])
        if not v or not f or not f["candles"]:
            continue
        t0 = max(p["observed_ms"], (decided_ms or {}).get(p["pool"], 0))  # instant de la décision du veto
        before = [c for c in f["candles"] if c[0] <= t0]
        after = [c for c in f["candles"] if c[0] > t0]
        if not before or not after:
            continue
        ref, last = before[-1][4], after[-1][4]
        low = min(c[3] for c in after)
        cost = Costs.for_pool(p, sol_usd)
        rt = 2 * cost.dex_fee + 2 * cost.mev + impact(ORDER_USD, p.get("reserve_usd")) * 2 + 2 * cost.network_usd_per_swap / ORDER_USD
        groups.setdefault(v["status"], []).append({"collapsed": low <= COLLAPSE * ref, "ret": last / ref - 1, "net": last / ref - 1 - rt,
                                                    "minutes": (after[-1][0] - t0) / 60000})
    out = {}
    for s, rows in groups.items():
        out[s] = {"tokens": len(rows), "collapsed_share": float(np.mean([r["collapsed"] for r in rows])),
                  "median_return": float(np.median([r["ret"] for r in rows])), "mean_net_after_costs": float(np.mean([r["net"] for r in rows])),
                  "share_net_positive": float(np.mean([r["net"] > 0 for r in rows])),
                  "median_minutes_after": float(np.median([r["minutes"] for r in rows]))}
    return out


# -- stress -------------------------------------------------------------------------------------------------------------
def stress_scenarios(sol_usd: float) -> dict:
    """Scénarios synthétiques : ce que coûte un retrait de liquidité, une taxe, une sortie bloquée, une panne de source."""
    rng = np.random.default_rng(1)
    base = np.cumprod(np.r_[1.0, 1 + rng.normal(0.008, 0.004, 199)])  # tendance régulière : un signal momentum vers la minute 99
    t = np.arange(200) * 60000.0
    vol = np.full(200, 1_000.0)
    vol[95:100] = 5_000.0
    pool = {"pool": "synthetique", "dex": "pumpswap", "reserve_usd": 60_000.0, "price_usd": float(base[-1])}

    def grid(px):
        return np.column_stack([t, np.r_[px[0], px[:-1]], px * 1.002, px * 0.998, px, vol])

    def run(px, **kw):
        kw.setdefault("failed_p", 0.0)
        tr = simulate_pool("momentum_15", grid(px), pool, sol_usd, rng=np.random.default_rng(0), **kw)
        return summarize(tr)

    rug = base.copy()
    rug[108:] = rug[107] * 0.03  # liquidité retirée : −97 % en une minute, pendant la position
    out = {"référence (tendance régulière)": run(base), "retrait de liquidité pendant la position": run(rug),
           "frais du réseau ×100 (congestion)": run(base, net_mult=100.0), "frais du DEX ×3": run(base, fee_mult=3.0),
           "toutes les transactions échouent une fois": run(base, failed_p=1.0), "retard de 3 minutes": run(base, latency=3)}
    drained = dict(pool, reserve_usd=600.0)
    tr = simulate_pool("momentum_15", grid(base), drained, sol_usd, failed_p=0.0, rng=np.random.default_rng(0))
    out["liquidité ÷100 (600 $)"] = summarize(tr)
    taxed = guardian.assess({"pool": "x", "mint": "x", "observed_ms": 0, "reserve_usd": 60_000.0},
                            {"mint_info": {"exists": True, "transfer_fee": True}, "rugcheck": {"risks": []}, "round_trip": {"loss": 0.02},
                             "dexscreener": None})
    out["taxe de transfert activée (Token-2022)"] = {"guardian": taxed["status"], "reasons": taxed["veto"]}
    blind = guardian.assess({"pool": "x", "mint": "x", "observed_ms": 0, "reserve_usd": 60_000.0}, None)
    out["toutes les sources de sécurité en panne"] = {"guardian": blind["status"], "reasons": blind["missing"]}
    return out


# -- banc complet -------------------------------------------------------------------------------------------------------
def run(capture: list[dict], follow: list[dict] | None = None) -> dict:
    """Banc complet sur une capture (et sa seconde lecture, si elle existe)."""
    pools = [r for r in capture if r["kind"] == "pool"]
    by_pool = {p["pool"]: p for p in pools}
    ohlcv = {r["pool"]: r for r in capture if r["kind"] == "ohlcv"}
    sec = {r["mint"]: r for r in capture if r["kind"] == "security"}
    coll = next((r for r in capture if r["kind"] == "collection"), {})
    sol_usd = _sol_usd(capture + (follow or []))
    verdicts = {p["pool"]: guardian.assess(p, sec.get(p["mint"])) for p in pools if p["mint"] in sec}
    parts = split(pools)
    reserved_sha = hashlib.sha256(json.dumps(sorted(parts["reserved"])).encode()).hexdigest()
    follow_map = {r["pool"]: r for r in (follow or []) if r["kind"] == "ohlcv"}
    decided = {p["pool"]: max(p["observed_ms"], sec[p["mint"]]["observed_ms"]) for p in pools if p["mint"] in sec}
    # Bougies : la seconde lecture (vie du pool jusqu'à 16 h après la décision) quand elle existe, sinon la collecte.
    source = {pid: (follow_map.get(pid) or ohlcv.get(pid)) for pid in by_pool}
    grids = {pid: g for pid, r in source.items() if r and (g := dense(r["candles"])) is not None}
    results = {}
    later_collapsed = {pid for pid, g in grids.items() if g[:, 4].min() <= COLLAPSE * g[:, 4].max()}
    for name in STRATEGIES:
        row = {}
        for part in ("train", "dev"):
            for mode in ("central", "prudent"):
                rng = np.random.default_rng(560)
                tr = [t for pid in parts[part] if pid in grids for t in simulate_pool(name, grids[pid], by_pool[pid], sol_usd, mode=mode, rng=rng)]
                s = summarize(tr)
                if tr:
                    s["share_in_pools_that_collapsed"] = float(np.mean([t["pool"] in later_collapsed for t in tr]))
                row[f"{part}:{mode}"] = s
            # Veto CAUSAL : décision prise à la collecte, avec les seules données d'alors ; seuls comptent les trades ouverts après.
            rng = np.random.default_rng(560)
            after = [t for pid in parts[part] if pid in grids and pid in decided
                     for t in simulate_pool(name, grids[pid], by_pool[pid], sol_usd, rng=rng) if t["entry_ms"] >= decided[pid]]
            row[f"{part}:sans_veto"] = summarize(after)
            row[f"{part}:avec_veto"] = summarize([t for t in after if (verdicts.get(t["pool"]) or {}).get("status") == guardian.PASS])
            rng = np.random.default_rng(560)
            late = [t for pid in parts[part] if pid in grids for t in simulate_pool(name, grids[pid], by_pool[pid], sol_usd, latency=2, rng=rng)]
            row[f"{part}:retard_2_min"] = summarize(late)
        results[name] = row
    validation = _cost_validation(pools, sec, sol_usd)
    statuses = [v["status"] for v in verdicts.values()]
    reasons: dict[str, int] = {}
    for v in verdicts.values():
        for r in v["veto"] + v["weak"] + v["missing"]:
            key = r.split(" :")[0].split(" (")[0]
            reasons[key] = reasons.get(key, 0) + 1
    return {
        "kind": "memecoin-lab-report", "paper_only": True, "can_trade": False,
        "data": {"pools": len(pools), "with_candles": len(grids), "minutes": int(sum(len(g) for g in grids.values())),
                 "security_checked": len(verdicts), "split": {k: len(v) for k, v in parts.items()}, "reserved_list_sha256": reserved_sha,
                 "reserved_note": "20 % des pools les plus récents : aucun calcul de stratégie ici ; réservés à une évaluation préenregistrée",
                 "collection": {k: coll.get(k) for k in ("started_ms", "finished_ms", "requests", "pages")}, "sol_usd": sol_usd},
        "sources": coll.get("sources"), "quality": coll.get("quality"),
        "guardian": {"rules": guardian.RULES, "counts": {s: statuses.count(s) for s in (guardian.VETO, guardian.ABSTAIN, guardian.PASS)},
                     "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1]))},
        "forward_check": forward_check(pools, verdicts, follow_map, sol_usd,
                                       {p["pool"]: sec[p["mint"]]["observed_ms"] for p in pools if p["mint"] in sec}) if follow else None,
        "strategies": results, "cost_model_validation": validation,
        "hypotheses": {"dex_fees": DEX_FEES, "default_dex_fee": DEFAULT_DEX_FEE, "network_lamports": NETWORK, **HYPOTHESES, "order_usd": ORDER_USD},
        "stress": stress_scenarios(sol_usd),
    }


def _sol_usd(records: list[dict]) -> float:
    """Prix lu (Jupiter) ; sinon déduit des pools cotés en SOL ; sinon hypothèse déclarée."""
    direct = [r["usd"] for r in records if r["kind"] == "sol_price" and r.get("usd")]
    if direct:
        return float(statistics.median(direct))
    px = [r["price_usd"] / r["price_native"] for r in records if r["kind"] == "pool" and r.get("price_native") and r.get("price_usd")]
    return float(statistics.median(px)) if px else HYPOTHESES["sol_usd_fallback"]


def _cost_validation(pools, sec, sol_usd) -> dict:
    """Coût d'aller-retour du modèle (frais + impact) face à l'aller-retour mesuré par devis Jupiter, au même instant."""
    rows = []
    for p in pools:
        rt = (sec.get(p["mint"]) or {}).get("round_trip") or {}
        if rt.get("loss") is None or not p.get("reserve_usd"):
            continue
        probe_usd = 0.05 * sol_usd
        model = 1 - (1 - Costs.for_pool(p, sol_usd).dex_fee) ** 2 * (1 - impact(probe_usd, p["reserve_usd"])) ** 2
        rows.append((rt["loss"], model, p.get("dex")))
    if not rows:
        return {"n": 0}
    d = np.array([r[0] - r[1] for r in rows])
    return {"n": len(rows), "measured_median": float(np.median([r[0] for r in rows])), "model_median": float(np.median([r[1] for r in rows])),
            "gap_median": float(np.median(d)), "gap_p90_abs": float(np.quantile(np.abs(d), 0.9)),
            "note": "écart positif : le marché coûte plus que le modèle (le modèle est alors optimiste)"}


def markdown(r: dict) -> str:
    def p(x, d=1):
        return "—" if x is None else f"{x * 100:+.{d}f} %".replace(".", ",")

    d = r["data"]
    out = [f"Pools : {d['pools']} (bougies : {d['with_candles']}, {d['minutes']} minutes) · contrôlés : {d['security_checked']} · "
           f"découpage {d['split']} · liste réservée `{d['reserved_list_sha256'][:16]}…` · SOL ≈ {d['sol_usd']:.0f} $", "",
           "#### Veto (à la collecte)", "", "| Décision | Tokens |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in r["guardian"]["counts"].items()]
    out += ["", "Motifs les plus fréquents : " + " ; ".join(f"{k} ({v})" for k, v in list(r["guardian"]["reasons"].items())[:8]) + "."]
    if r.get("forward_check"):
        out += ["", "#### Contrôle en avant du veto (décision à la collecte, issue lue plus tard)", "",
                "| Décision | Tokens | Effondrés (−90 %) | Rendement médian | Moyenne nette après coûts | Part nette positive | Minutes après |",
                "|---|---|---|---|---|---|---|"]
        for k, v in r["forward_check"].items():
            out.append(f"| {k} | {v['tokens']} | {p(v['collapsed_share'], 0)} | {p(v['median_return'])} | {p(v['mean_net_after_costs'])} | "
                       f"{p(v['share_net_positive'], 0)} | {v['median_minutes_after']:.0f} |")
    out += ["", "#### Règles de sélection (ordre de 11 $ ≈ 10 €, coûts complets)", "",
            "| Règle | Partie | Ordres | Gagnants | Moyenne nette [IC 95 %] | Médiane | Total net ($) | Facteur de profit | Dans des pools effondrés |",
            "|---|---|---|---|---|---|---|---|---|"]
    for name, row in r["strategies"].items():
        for part, s in row.items():
            if not s.get("trades"):
                out.append(f"| {name} | {part} | 0 | — | — | — | — | — | — |")
                continue
            ci = s["mean_ci95"]
            pf = "—" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}".replace(".", ",")
            out.append(f"| {name} | {part} | {s['trades']} | {p(s['win_rate'], 0)} | {p(s['mean_net_return'])} [{p(ci[0])} ; {p(ci[1])}] | "
                       f"{p(s['median_net_return'])} | {s['total_net_usd']:+.2f} | {pf} | {p(s.get('share_in_pools_that_collapsed'), 0)} |")
    v = r["cost_model_validation"]
    if v.get("n"):
        out += ["", f"Validation du modèle de coûts sur {v['n']} allers-retours mesurés (devis Jupiter de 0,05 SOL) : mesuré {p(v['measured_median'])}, "
                f"modèle {p(v['model_median'])}, écart médian {p(v['gap_median'])} ({v['note']})."]
    out += ["", "#### Tests de stress (règle momentum_15, scénarios synthétiques)", "", "| Scénario | Résultat |", "|---|---|"]
    for k, s in r["stress"].items():
        if "guardian" in s:
            out.append(f"| {k} | veto : {s['guardian']} ({'; '.join(s['reasons'])}) |")
        elif s.get("trades"):
            out.append(f"| {k} | {s['trades']} ordre(s), moyenne nette {p(s['mean_net_return'])}, total {s['total_net_usd']:+.2f} $ |")
        else:
            out.append(f"| {k} | aucun ordre |")
    return "\n".join(out)
