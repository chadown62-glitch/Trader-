"""OMEGA TRUTH / MIRROR — taux de fausses validations d'un programme « deux stratégies par actif », avant toute recherche réelle.

Objectif du propriétaire (26 novembre 2026) : au moins deux stratégies rentables et robustes par actif. Avec des centaines
d'essais, combien de stratégies seraient « validées » par pur hasard, et quelle règle de validation garde ce risque sous
contrôle tout en retrouvant un vrai avantage ? Expérience synthétique seulement : aucune donnée de marché, aucune source.

Monde nul : huit actifs indépendants (volatilités annuelles proches de BTC, ETH, SOL, XMR, or, argent, Nasdaq-100,
S&P 500), rendements journaliers GARCH(1, 1) à innovations de Student (4 degrés), espérance nulle, 2 600 jours.
Monde positif : même chose, avec un vrai avantage de momentum sur le premier actif seulement (espérance du lendemain
κ·σ_t·signe(rendement des 20 derniers jours)), d'ampleur fixée par le ratio de Sharpe annuel brut visé de la stratégie.

Grille fixée a priori, 45 variantes par actif, positions longues ou nulles décidées à la clôture t, appliquées à t + 1 :
15 signaux (momentum 10/20/50/100/200 j ; croisements de moyennes 5/20, 10/50, 20/100, 50/200 ; cassure 20/55/100 j avec
sortie sur le plus bas de la moitié de la fenêtre ; retour à la moyenne z < −1/−1,5/−2 avec sortie à z > 0) × 3 sorties
(aucune, stop fixe −10 % depuis l'entrée, stop suiveur −15 % depuis le plus haut). Coûts par changement de position :
15 pb pour les cryptos, 5 pb pour les autres.

Sélection : sur les 70 % premiers jours (apprentissage), les deux meilleures variantes par actif (ratio de Sharpe net).
Validation (six règles, de la plus naïve à la plus stricte) :
- V1 « backtest favorable » : Sharpe net annuel ≥ 0,5 sur tout l'historique (toutes variantes) ;
- V2 : les deux meilleures en apprentissage ont un Sharpe positif sur les 30 % derniers jours (hors échantillon) ;
- V3 : test unilatéral de Newey-West (10 retards) sur le hors-échantillon, p < 0,05, sans correction ;
- V4 : V3 avec correction de Holm sur les 16 tests du programme (2 par actif) ;
- V5 : Sharpe dégonflé (45 essais par actif) ≥ 0,95 sur tout l'historique, et Sharpe positif hors échantillon ;
- V6 : V4, et toujours positive avec des coûts doublés, et positive dans au moins 2 des 3 tiers du hors-échantillon.
Mesures : nombre moyen de validations, probabilité d'au moins une fausse validation (FWER), puissance sur le monde positif.

Lancement : python -m sentinelle.scalp.mirror
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from . import essais

ASSETS = {"BTC": (0.65, "crypto"), "ETH": (0.80, "crypto"), "SOL": (1.00, "crypto"), "XMR": (0.80, "crypto"),
          "OR": (0.15, "autre"), "ARGENT": (0.28, "autre"), "NASDAQ100": (0.24, "autre"), "SP500": (0.18, "autre")}
COST = {"crypto": 0.0015, "autre": 0.0005}
DAYS, TRAIN_SHARE, YEAR = 2600, 0.70, 252
GARCH = (0.08, 0.90)  # α, β ; ω fixé par la volatilité visée
STUDENT_DF = 4
MOM, MA, BREAK, MEANREV = (10, 20, 50, 100, 200), ((5, 20), (10, 50), (20, 100), (50, 200)), (20, 55, 100), (1.0, 1.5, 2.0)
EXITS = ("aucune", "stop_fixe", "stop_suiveur")
STOP_FIXED, STOP_TRAIL = 0.10, 0.15
TOP, ALPHA, NW_LAGS, DSR_MIN = 2, 0.05, 10, 0.95
NULL_REPS, POWER_REPS, EFFECTS, SEED = 300, 100, (0.5, 1.0, 1.5), 20261126
RULES = ("V1", "V2", "V3", "V4", "V5", "V6")


def simulate(rng: np.random.Generator, n_assets: int = len(ASSETS), days: int = DAYS, effect_sharpe: float = 0.0) -> np.ndarray:
    """Rendements logarithmiques journaliers (jours × actifs). L'avantage éventuel ne concerne que le premier actif."""
    vols = np.array([v for v, _ in ASSETS.values()])[:n_assets] / math.sqrt(YEAR)
    a, b = GARCH
    omega = vols ** 2 * (1 - a - b)
    z = rng.standard_t(STUDENT_DF, (days, n_assets)) / math.sqrt(STUDENT_DF / (STUDENT_DF - 2))
    kappa = effect_sharpe / (math.sqrt(0.5) * math.sqrt(YEAR))  # Sharpe annuel brut ≈ κ·√0,5·√252 pour une position longue la moitié du temps
    r = np.zeros((days, n_assets))
    var = vols ** 2
    for t in range(days):
        if t:
            var = omega + a * r[t - 1] ** 2 + b * var
        sd = np.sqrt(var)
        r[t] = sd * z[t]
        if effect_sharpe and t >= 20:
            r[t, 0] += kappa * sd[0] * np.sign(r[t - 20:t, 0].sum())
    return r


def _rolling_mean(x: np.ndarray, n: int) -> np.ndarray:
    c = np.cumsum(np.vstack([np.zeros((1, x.shape[1])), x]), axis=0)
    out = np.full_like(x, np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _rolling_extreme(x: np.ndarray, n: int, fn) -> np.ndarray:
    """Extrême des n jours PRÉCÉDENTS (t − n à t − 1), jamais du jour t ni du futur."""
    out = np.full_like(x, np.nan)
    for t in range(n, len(x)):
        out[t] = fn(x[t - n:t], axis=0)
    return out


def _state(enter: np.ndarray, leave: np.ndarray) -> np.ndarray:
    pos = np.zeros_like(enter, dtype=bool)
    cur = np.zeros(enter.shape[1], dtype=bool)
    for t in range(len(enter)):
        cur = np.where(cur, ~leave[t], enter[t])
        pos[t] = cur
    return pos


def base_signals(logp: np.ndarray) -> dict[str, np.ndarray]:
    """Les 15 signaux (positions désirées à la clôture t), sur des log-prix (jours × actifs)."""
    out = {}
    for n in MOM:
        past = np.full_like(logp, np.nan)
        past[n:] = logp[n:] - logp[:-n]
        out[f"momentum_{n}"] = past > 0
    for f, s in MA:
        out[f"moyennes_{f}_{s}"] = _rolling_mean(logp, f) > _rolling_mean(logp, s)
    for n in BREAK:
        hi, lo = _rolling_extreme(logp, n, np.max), _rolling_extreme(logp, max(2, n // 2), np.min)
        out[f"cassure_{n}"] = _state(logp >= hi, logp <= lo)
    m, sd = _rolling_mean(logp, 20), np.sqrt(np.maximum(_rolling_mean(logp ** 2, 20) - _rolling_mean(logp, 20) ** 2, 1e-18))
    zscore = (logp - m) / sd
    for k in MEANREV:
        out[f"retour_{k:g}"] = _state(zscore < -k, zscore > 0)
    return {k: np.nan_to_num(v, nan=0).astype(bool) for k, v in out.items()}


def with_exit(signal: np.ndarray, logp: np.ndarray, kind: str) -> np.ndarray:
    """Sortie superposée : après un stop, on attend que le signal retombe puis revienne avant de rentrer."""
    if kind == "aucune":
        return signal
    pos = np.zeros_like(signal)
    holding = np.zeros(signal.shape[1], dtype=bool)
    blocked = np.zeros(signal.shape[1], dtype=bool)
    ref = np.zeros(signal.shape[1])
    for t in range(len(signal)):
        blocked &= signal[t]  # le blocage tombe quand le signal s'éteint
        enter = signal[t] & ~holding & ~blocked
        ref = np.where(enter, logp[t], ref)
        holding = (holding | enter) & signal[t]
        if kind == "stop_suiveur":
            ref = np.where(holding, np.maximum(ref, logp[t]), ref)
            hit = holding & (logp[t] - ref <= math.log(1 - STOP_TRAIL))
        else:
            hit = holding & (logp[t] - ref <= math.log(1 - STOP_FIXED))
        blocked |= hit
        holding &= ~hit
        pos[t] = holding
    return pos


def positions(r: np.ndarray) -> tuple[list[str], np.ndarray]:
    """Positions (jours × variantes × actifs) décidées à la clôture t : 15 signaux × 3 sorties, calculées en bloc."""
    logp = np.cumsum(r, axis=0)
    sigs = base_signals(logp)
    names = list(sigs)
    stack = np.stack([sigs[k] for k in names], axis=1)  # jours × signaux × actifs
    flat = stack.reshape(len(r), -1)
    lp = np.repeat(logp[:, None, :], len(names), axis=1).reshape(len(r), -1)
    blocks, labels = [], []
    for e in EXITS:
        blocks.append(with_exit(flat, lp, e).reshape(stack.shape))
        labels += [f"{n}|{e}" for n in names]
    return labels, np.concatenate(blocks, axis=1).astype(float)


def net_returns(r: np.ndarray, pos: np.ndarray, cost_scale: float = 1.0) -> np.ndarray:
    """Rendements journaliers nets : position de t appliquée au rendement de t + 1, changement payé à l'exécution."""
    costs = np.array([COST[c] for _, c in ASSETS.values()])[: r.shape[1]] * cost_scale
    gross = np.zeros_like(pos)
    gross[1:] = pos[:-1] * (np.exp(r[1:]) - 1)[:, None, :]
    turns = np.abs(np.diff(np.concatenate([np.zeros((1,) + pos.shape[1:]), pos]), axis=0))
    gross[1:] -= turns[:-1] * costs
    return gross


def annual_sharpe(x: np.ndarray) -> np.ndarray:
    sd = x.std(axis=0, ddof=1)
    return np.where(sd > 0, x.mean(axis=0) / np.where(sd > 0, sd, 1) * math.sqrt(YEAR), 0.0)


def newey_west_p(x: np.ndarray, lags: int = NW_LAGS) -> float:
    """p unilatérale de « moyenne > 0 » avec erreur type de Newey-West."""
    x = np.asarray(x, float)
    n = len(x)
    d = x - x.mean()
    s = d @ d / n
    for k in range(1, lags + 1):
        s += 2 * (1 - k / (lags + 1)) * (d[k:] @ d[:-k]) / n
    if s <= 0:
        return 1.0
    return 1 - essais.norm_cdf(x.mean() / math.sqrt(s / n))


def weekly_sr_variance(variants: np.ndarray) -> float:
    days = np.arange(len(variants))
    return float(np.var([essais.sharpe(essais.weekly(variants[:, j], days))["sr_week"] for j in range(variants.shape[1])], ddof=1))


def dsr(full: np.ndarray, variance: float, trials: int) -> float:
    """Sharpe dégonflé hebdomadaire d'une variante parmi les essais de son actif."""
    return essais.deflated_sharpe(essais.sharpe(essais.weekly(full, np.arange(len(full)))), variance, trials)["dsr"]


def validate(r: np.ndarray) -> dict:
    """Applique les six règles à un monde simulé ; renvoie, par règle, les validations par actif."""
    names, pos = positions(r)
    net, net2 = net_returns(r, pos), net_returns(r, pos, cost_scale=2.0)
    cut = int(len(r) * TRAIN_SHARE)
    n_assets = r.shape[1]
    out = {k: np.zeros(n_assets, dtype=int) for k in RULES}
    out["V1"] = (annual_sharpe(net) >= 0.5).sum(axis=0)
    chosen, pvals = [], []
    for a in range(n_assets):
        top = np.argsort(-annual_sharpe(net[:cut, :, a]))[:TOP]
        variance = weekly_sr_variance(net[:, :, a])
        for j in top:
            oos = net[cut:, j, a]
            chosen.append((a, j))
            pvals.append(newey_west_p(oos))
            sr_oos = annual_sharpe(oos[:, None])[0]
            out["V2"][a] += sr_oos > 0
            out["V3"][a] += pvals[-1] < ALPHA
            out["V5"][a] += (dsr(net[:, j, a], variance, len(names)) >= DSR_MIN) and sr_oos > 0
    holm = essais.holm(pvals)
    for (a, j), ph in zip(chosen, holm):
        if ph < ALPHA:
            out["V4"][a] += 1
            thirds = np.array_split(net[cut:, j, a], 3)
            if net2[cut:, j, a].mean() > 0 and sum(x.mean() > 0 for x in thirds) >= 2:
                out["V6"][a] += 1
    return {"validations": {k: v.tolist() for k, v in out.items()}, "variantes": len(names)}


def summarize(runs: list[dict], effect: bool) -> dict:
    out = {}
    for k in RULES:
        v = np.array([r["validations"][k] for r in runs])  # répétitions × actifs
        null_part = v[:, 1:] if effect else v
        row = {"fausses_validations_moyennes": float(null_part.sum(axis=1).mean()),
               "probabilite_au_moins_une_fausse": float((null_part.sum(axis=1) > 0).mean())}
        if effect:
            row["puissance_actif_avec_avantage"] = float((v[:, 0] > 0).mean())
        out[k] = row
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA TRUTH/MIRROR — fausses validations d'un programme multi-actifs (synthétique)")
    ap.add_argument("--out", default="research/scalp-v1/omega-mirror-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    ap.add_argument("--null-reps", type=int, default=NULL_REPS)
    ap.add_argument("--power-reps", type=int, default=POWER_REPS)
    args = ap.parse_args(argv)
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    null_runs = [validate(simulate(rng)) for _ in range(args.null_reps)]
    out = {"schema": "scalp-omega-mirror-v1", "status": "synthétique ; aucune donnée de marché ; préenregistré",
           "omega": ["TRUTH", "MIRROR", "REPLAY"], "capacites": ["C094", "C095", "C096"],
           "parametres": {"actifs": {k: {"volatilite_annuelle": v, "classe": c} for k, (v, c) in ASSETS.items()}, "jours": DAYS,
                          "apprentissage": TRAIN_SHARE, "garch": GARCH, "student": STUDENT_DF, "couts_par_changement": COST,
                          "variantes_par_actif": null_runs[0]["variantes"], "retenues_par_actif": TOP, "graine": SEED},
           "monde_nul": {"repetitions": args.null_reps, **summarize(null_runs, effect=False)}, "monde_positif": {}}
    for s in EFFECTS:
        runs = [validate(simulate(rng, effect_sharpe=s)) for _ in range(args.power_reps)]
        out["monde_positif"][f"sharpe_{s:g}"] = {"repetitions": args.power_reps, **summarize(runs, effect=True)}
    out["seconds"] = round(time.time() - t0)
    for k in RULES:
        print(k, "nul :", out["monde_nul"][k], "| positif :", {e: {x: round(y, 3) for x, y in d[k].items()} for e, d in out["monde_positif"].items()})
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-mirror-v1",
                                 "monde_nul": {k: out["monde_nul"][k] for k in RULES},
                                 "monde_positif": out["monde_positif"]}, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
