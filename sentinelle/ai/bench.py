"""Banc d'essai de l'apprentissage.

Sur un marché réel, personne ne connaît la « bonne réponse » : impossible de dire
si l'IA a tout appris ou seulement une partie. Ce banc d'essai fabrique donc des
situations artificielles dans lesquelles **on cache nous-mêmes une règle**, puis
on mesure quelle part de cette règle l'IA retrouve sur des données qu'elle n'a
jamais vues, avec exactement la même chaîne d'entraînement que pour le vrai marché.

Taux d'apprentissage = (score de l'IA − hasard) / (score parfait − hasard), où le
score est l'aire sous la courbe ROC (0,5 = hasard). 100 % = l'IA a retrouvé toute
la règle ; 0 % = elle n'a rien appris.

Deux épreuves complètent ce taux :
- « bruit pur » : aucune règle n'existe, l'IA ne doit rien « découvrir » ;
- « changement de règle » : la règle s'inverse en cours de route, l'IA doit s'adapter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from ..config import timeframe_ms
from .features import FEATURE_COLUMNS
from .strategies import STRATEGIES
from .trainer import train_walk_forward

BENCH_STRATEGIES = ("trend_follow", "trend_breakout")
BASE_WIN_RATE = 0.38  # proche des stratégies de tendance réelles
WIN_R = 2.0  # gain d'un trade gagnant, en R (avant frais) : espérance ≈ 0 sans règle cachée


@dataclass(frozen=True)
class Rule:
    name: str
    label: str
    description: str
    fn: Callable[[pd.DataFrame, np.ndarray], np.ndarray]  # (caractéristiques centrées-réduites, position dans le temps 0..1) -> effet
    learnable: bool = True


def _simple(z: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    return z["rsi14"].to_numpy()


def _combo(z: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    both = ((z["d_ema50"].to_numpy() > 0) & (z["vol_ratio"].to_numpy() < 0)).astype(float)
    return (both - 0.25) / 0.433  # centré-réduit (la condition est vraie une fois sur quatre)


def _curve(z: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    return (1.0 - z["bb_pctb"].to_numpy() ** 2) / np.sqrt(2.0)  # meilleur au milieu, mauvais aux extrêmes


def _weak(z: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    return 0.3 * z["rsi14"].to_numpy()


def _noise(z: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    return np.zeros(len(z))


def _shift(z: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    return np.where(t < 0.6, 1.0, -1.0) * z["rsi14"].to_numpy()


RULES: dict[str, Rule] = {
    r.name: r
    for r in (
        Rule("simple", "Règle simple", "Le résultat dépend d'une seule caractéristique.", _simple),
        Rule("combinaison", "Règle combinée", "Le résultat dépend de deux conditions réunies en même temps.", _combo),
        Rule("courbe", "Règle en cloche", "Bon au milieu, mauvais aux extrêmes : relation non linéaire.", _curve),
        Rule("faible", "Signal faible", "Règle simple mais ténue, noyée dans le hasard, comme sur un vrai marché.", _weak),
        Rule("changement", "Changement de règle", "La règle s'inverse aux trois cinquièmes de l'historique : l'IA doit s'adapter.", _shift),
        Rule("bruit", "Bruit pur", "Aucune règle : l'IA ne doit rien « découvrir ».", _noise, learnable=False),
    )
}


def make_candidates(n: int, rule: str, seed: int = 0, strength: float = 1.0, timeframe: str = "4h",
                    n_symbols: int = 6, years: float = 6.0) -> tuple[pd.DataFrame, np.ndarray]:
    """Fabrique `n` situations d'achat artificielles. Renvoie (candidats, vraie probabilité de gain)."""
    rng = np.random.default_rng(seed)
    z = pd.DataFrame(rng.standard_normal((n, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    t = np.arange(n) / max(1, n - 1)
    effect = RULES[rule].fn(z, t)
    logit0 = np.log(BASE_WIN_RATE / (1 - BASE_WIN_RATE))
    p_true = 1.0 / (1.0 + np.exp(-(logit0 + strength * effect)))
    win = rng.random(n) < p_true
    cand = z.copy()
    cand["cost_r"] = rng.uniform(0.05, 0.25, n)
    strat = rng.choice(BENCH_STRATEGIES, n)
    for name in STRATEGIES:
        cand[f"s_{name}"] = (strat == name).astype(float)
    cand["strategy"] = strat
    cand["symbol"] = rng.choice([f"C{k}/USDC" for k in range(n_symbols)], n)
    tf = timeframe_ms(timeframe)
    span = years * 365 * 86_400_000
    start = 1_500_000_000_000
    start -= start % tf
    cand["decision_ts"] = (start + np.floor(t * span / tf) * tf).astype("int64")
    cand["exit_ts"] = cand["decision_ts"] + 6 * tf
    cand["outcome_r"] = np.where(win, WIN_R, -1.0) - cand["cost_r"].to_numpy()
    cand["outcome"] = np.where(win, "target", "stop")
    return cand, p_true


def learning_rate(auc_model: float | None, auc_oracle: float | None) -> float | None:
    """Part de la règle cachée retrouvée par l'IA (0 à 1)."""
    if auc_model is None or auc_oracle is None or auc_oracle <= 0.505:
        return None
    return float(min(1.0, max(0.0, (auc_model - 0.5) / (auc_oracle - 0.5))))


def run_case(rule: str, n: int, seed: int, strength: float = 1.0, timeframe: str = "4h", **train_kwargs) -> dict:
    """Une épreuve : fabrique les données, entraîne avec la chaîne réelle, compare à la vérité."""
    cand, p_true = make_candidates(n, rule, seed, strength, timeframe)
    res = train_walk_forward(cand, timeframe, with_importance=False, **train_kwargs)
    out = {"rule": rule, "n": n, "seed": seed, "verdict": res.verdict, "mode": (res.report.get("summary") or {}).get("mode")}
    hold_start = res.holdout_start
    if hold_start is None:
        return {**out, "auc": None, "auc_oracle": None, "rate": None, "model_r": None, "base_r": None, "oracle_r": None}
    hold = cand["decision_ts"].to_numpy() >= hold_start
    y = (cand.loc[hold, "outcome_r"].to_numpy() > 0).astype(int)
    auc_oracle = float(roc_auc_score(y, p_true[hold])) if len(np.unique(y)) == 2 else None
    h = res.report["holdout"]
    # ce qu'aurait donné un tri parfait : ne prendre que la meilleure moitié selon la vraie probabilité
    r_hold = cand.loc[hold, "outcome_r"].to_numpy()
    oracle_r = float(r_hold[p_true[hold] >= np.median(p_true[hold])].mean()) if hold.sum() else None
    return {**out, "auc": h["auc"], "auc_oracle": auc_oracle, "rate": learning_rate(h["auc"], auc_oracle),
            "model_r": h["model"]["mean_r"], "model_n": h["model"]["n"], "base_r": h["baseline"]["mean_r"], "oracle_r": oracle_r}


def run_bench(sizes: tuple[int, ...] = (1500, 4000), seeds: int = 3, timeframe: str = "4h",
              progress: Callable[[float, str], None] | None = None, **train_kwargs) -> dict:
    """Banc d'essai complet. Renvoie un bulletin lisible par le panel."""
    t0 = time.time()
    cases = [(r, n, s) for r in RULES for n in sizes for s in range(seeds)]
    rows = []
    for k, (rule, n, seed) in enumerate(cases):
        if progress:
            progress(k / len(cases), f"Épreuve « {RULES[rule].label} » ({n} situations)")
        rows.append(run_case(rule, n, 1000 * seed + n % 997, timeframe=timeframe, **train_kwargs))
    df = pd.DataFrame(rows)
    report = {"sizes": list(sizes), "seeds": seeds, "rules": [], "duration_s": None}
    learn_rates = []
    for name, rule in RULES.items():
        sub = df[df["rule"] == name]
        entry = {"name": name, "label": rule.label, "description": rule.description, "learnable": rule.learnable, "by_size": []}
        for n in sizes:
            g = sub[sub["n"] == n]
            item = {"n": int(n), "auc": _mean(g["auc"]), "auc_oracle": _mean(g["auc_oracle"]),
                    "validated": int((g["verdict"] == "validé").sum()), "runs": int(len(g)),
                    "model_r": _mean(g["model_r"]), "base_r": _mean(g["base_r"]), "oracle_r": _mean(g["oracle_r"])}
            if rule.learnable:
                item["rate"] = _mean(g["rate"])  # une épreuve sans réponse (trop peu de cas) est ignorée
            else:
                item["ia_validated"] = int(((g["verdict"] == "validé") & (g["mode"] == "ia")).sum())
            entry["by_size"].append(item)
        if rule.learnable:
            best = entry["by_size"][-1].get("rate")
            entry["rate"] = best
            if best is not None and name != "changement":
                learn_rates.append(best)
        rows_noise = sub if not rule.learnable else None
        if rows_noise is not None:
            entry["false_discoveries"] = int(((rows_noise["verdict"] == "validé") & (rows_noise["mode"] == "ia")).sum())
            entry["runs"] = int(len(rows_noise))
        report["rules"].append(entry)
    report["score"] = float(np.mean(learn_rates)) if learn_rates else None
    adapt = next((r for r in report["rules"] if r["name"] == "changement"), None)
    report["adaptation"] = adapt.get("rate") if adapt else None
    noise = next((r for r in report["rules"] if r["name"] == "bruit"), None)
    report["false_discoveries"] = noise.get("false_discoveries") if noise else None
    report["noise_runs"] = noise.get("runs") if noise else None
    report["duration_s"] = round(time.time() - t0, 1)
    report["ts"] = time.time()
    if progress:
        progress(1.0, "Terminé")
    return report


def _mean(s) -> float | None:
    vals = [float(x) for x in s if x is not None and np.isfinite(x)]
    return float(np.mean(vals)) if vals else None
