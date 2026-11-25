"""OMEGA priorité A — HIVE (apprentissage mutualisé), GENOME (spécialisation par actif), BORDER (généralisation hors domaine).

Question : un modèle appris sur tous les actifs (collectif) prédit-il mieux, ou moins bien, qu'un modèle propre à chaque actif
(individuel), et un mélange (hybride) fait-il mieux que les deux ? Le gain se généralise-t-il dans le temps et à des actifs
exclus de l'apprentissage ? Quels actifs subissent un transfert négatif ?

Contrôle synthétique (reconstruction de l'expérience HIVE décrite par le propriétaire ; le registre OMEGA V3 n'est pas
accessible) : une famille d'actifs aux comportements cohérents et une famille aux comportements divergents. La mesure doit
retrouver le gain de la mutualisation sur la première et le transfert négatif sur la seconde.

Marché : 42 cryptos candidates, décision hebdomadaire à la clôture du dimanche, membres de l'univers à la date.
- Cible : rendement du lundi au dimanche suivant (clôture d → clôture d + 7) positif.
- 11 caractéristiques causales (connues à la clôture de d) : rendements sur 1, 7, 28 et 90 jours, volatilité sur 20 jours
  et rapport 20/120 jours, volume relatif 7/90 jours, part des achats au marché sur 7 jours, distance au plus haut de 28
  jours, rendements du BTC sur 7 et 28 jours.
- Modèle : régression logistique (C = 0,5), standardisation apprise sur l'échantillon d'apprentissage seul.
- Versions : individuelle (un modèle par actif, au moins 52 semaines d'apprentissage, sinon taux propre de l'actif),
  collective (un modèle pour tous), hybride (logit = w · individuel + (1 − w) · collectif, w = n / (n + 104), n = semaines
  d'apprentissage de l'actif), référence simple (taux de semaines positives appris).
- Walk-forward annuel 2020 → 2026 : apprentissage sur les semaines dont la cible est connue avant l'année jugée (embargo de
  8 jours), jugement de l'année.
- BORDER : actifs répartis en 4 groupes fixes (ordre alphabétique, rang modulo 4) ; le modèle collectif appris sans le groupe
  juge ce groupe (actifs non vus) ; écart avec le modèle collectif qui les a vus.
- Transfert négatif : par actif, perte collective − perte individuelle ; lien (rang) avec la divergence de l'actif
  (1 − corrélation de ses rendements hebdomadaires avec la moyenne des membres).
Mesures : perte logarithmique moyenne (négatif = meilleur), appariée par prédiction ; intervalles et p bilatérales par
rééchantillonnage des semaines (les prédictions d'une même semaine ne sont pas indépendantes) ; Holm sur les 8 tests.
Descriptif : portefeuille long-only des 5 probabilités les plus hautes au-dessus de 0,5, 0,125 % par côté.
Mesure seulement : rien n'entre dans une décision sans approbation humaine.

Lancement : python -m sentinelle.scalp.hive
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from . import essais, flux, moteur, recherche, signaux

FEATURES = ["r1", "r7", "r28", "r90", "vol20", "vol_ratio", "volume_z", "taker", "dist_high", "btc7", "btc28"]
HORIZON, EMBARGO, MIN_OWN, SHRINK, FOLDS = 7, 8, 52, 104, 4
TEST_YEARS = list(range(2020, 2027))
C_REG = 0.5
DRAWS, SEED = 2000, 0
TOP_K, COST = 5, 0.00125
EPS = 1e-6


# --- Caractéristiques et échantillons --------------------------------------------------------------------------------

def features(p: dict) -> dict[str, pd.DataFrame]:
    """Caractéristiques causales par jour et par actif (connues à la clôture du jour)."""
    c, h, v, tb = p["c"], p["h"], p["v"], p["taker_buy"]
    lc = np.log(c)
    lr = lc.diff()
    vol20 = lr.rolling(20, min_periods=20).std()
    btc = lc["BTCUSDT"]
    spread = lambda s: pd.DataFrame(np.repeat(s.to_numpy()[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns)  # noqa: E731
    return {"r1": lc - lc.shift(1), "r7": lc - lc.shift(7), "r28": lc - lc.shift(28), "r90": lc - lc.shift(90),
            "vol20": vol20, "vol_ratio": vol20 / lr.rolling(120, min_periods=120).std(),
            "volume_z": np.log(v.rolling(7, min_periods=7).mean() / v.rolling(90, min_periods=90).mean()),
            "taker": tb.rolling(7, min_periods=7).sum() / v.rolling(7, min_periods=7).sum() - 0.5,
            "dist_high": c / h.rolling(28, min_periods=28).max() - 1,
            "btc7": spread(btc - btc.shift(7)), "btc28": spread(btc - btc.shift(28))}


def samples(p: dict, member: pd.DataFrame, feats: dict[str, pd.DataFrame], first: int) -> pd.DataFrame:
    """Une ligne par dimanche et par membre : caractéristiques, rendement des 7 jours suivants, cible."""
    c = p["c"]
    ahead = np.log(c.shift(-HORIZON) / c)
    days = [d for d in c.index if d >= first and flux.weekday([d])[0] == 6]
    rows = []
    for d in days:
        for s in c.columns:
            if not member.at[d, s]:
                continue
            x = [feats[f].at[d, s] for f in FEATURES]
            y = ahead.at[d, s]
            if np.all(np.isfinite(x)) and np.isfinite(y):
                rows.append([d, s, *x, y])
    out = pd.DataFrame(rows, columns=["day", "asset", *FEATURES, "ret"])
    out["y"] = (out["ret"] > 0).astype(int)
    return out


def year_of(day) -> np.ndarray:
    return pd.to_datetime(np.asarray(day, dtype=np.int64) * moteur.DAY_MS, unit="ms").year.to_numpy()


def split(frame: pd.DataFrame, year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apprentissage : semaines dont la cible est connue avant l'année (embargo) ; jugement : semaines de l'année."""
    start = moteur._day(f"{year}-01-01")
    train = frame[frame["day"] + EMBARGO <= start - 1]
    test = frame[year_of(frame["day"]) == year]
    return train, test


# --- Modèles ---------------------------------------------------------------------------------------------------------

class Logit:
    """Régression logistique standardisée sur l'apprentissage ; repli sur le taux appris si une seule classe."""

    def fit(self, X: np.ndarray, y: np.ndarray) -> "Logit":
        self.rate = float(np.clip(y.mean(), EPS, 1 - EPS)) if len(y) else 0.5
        self.model = None
        if len(np.unique(y)) == 2:
            self.mu, self.sd = X.mean(axis=0), X.std(axis=0)
            self.sd[self.sd == 0] = 1.0
            self.model = LogisticRegression(C=C_REG, max_iter=2000).fit((X - self.mu) / self.sd, y)
        return self

    def proba(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            return np.full(len(X), self.rate)
        return self.model.predict_proba((X - self.mu) / self.sd)[:, 1]


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def hybrid(p_ind: np.ndarray, p_pool: np.ndarray, n_own: np.ndarray) -> np.ndarray:
    """Mélange fixé a priori : poids de l'individuel n / (n + SHRINK)."""
    w = n_own / (n_own + SHRINK)
    return 1 / (1 + np.exp(-(w * logit(p_ind) + (1 - w) * logit(p_pool))))


def judge_year(train: pd.DataFrame, test: pd.DataFrame, folds: dict[str, int]) -> pd.DataFrame:
    """Probabilités des quatre versions et du collectif sans le groupe de l'actif, pour les semaines jugées."""
    X, y = train[FEATURES].to_numpy(), train["y"].to_numpy()
    Xt = test[FEATURES].to_numpy()
    pool = Logit().fit(X, y)
    out = test[["day", "asset", "ret", "y"]].copy()
    out["p_base"] = pool.rate
    out["p_pool"] = pool.proba(Xt)
    p_ind, n_own = np.empty(len(test)), np.zeros(len(test))
    for a, idx in test.groupby("asset").indices.items():
        own = train[train["asset"] == a]
        n_own[idx] = len(own)
        if len(own) >= MIN_OWN:
            p_ind[idx] = Logit().fit(own[FEATURES].to_numpy(), own["y"].to_numpy()).proba(Xt[idx])
        else:
            p_ind[idx] = float(np.clip(own["y"].mean(), EPS, 1 - EPS)) if len(own) else pool.rate
    out["p_ind"], out["n_own"] = p_ind, n_own
    out["p_hyb"] = hybrid(p_ind, out["p_pool"].to_numpy(), n_own)
    out["p_unseen"] = np.nan
    group = test["asset"].map(folds).to_numpy()
    tgroup = train["asset"].map(folds).to_numpy()
    for g in range(FOLDS):
        sel = group == g
        if sel.any():
            m = Logit().fit(X[tgroup != g], y[tgroup != g])
            out.loc[out.index[sel], "p_unseen"] = m.proba(Xt[sel])
    return out


def folds_of(assets) -> dict[str, int]:
    return {a: k % FOLDS for k, a in enumerate(sorted(assets))}


# --- Mesures ---------------------------------------------------------------------------------------------------------

def logloss(p, y) -> np.ndarray:
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def week_bootstrap(diff: np.ndarray, weeks: np.ndarray, draws: int = DRAWS, seed: int = SEED) -> dict:
    """Écart moyen et p bilatérale en rééchantillonnant les semaines (grappes) ; statistique : moyenne des pertes."""
    keys, inv = np.unique(weeks, return_inverse=True)
    sums, counts = np.bincount(inv, weights=diff), np.bincount(inv)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(keys), (draws, len(keys)))
    means = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    m = float(diff.mean())
    centred = means - means.mean()
    return {"ecart": m, "ic95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "p_bilaterale": float((1 + (np.abs(centred) >= abs(m)).sum()) / (draws + 1)), "n": int(len(diff))}


def negative_transfer(frame: pd.DataFrame, divergence: pd.Series) -> dict:
    """Par actif : perte collective − individuelle ; corrélation de rang avec la divergence, p par permutation."""
    d = frame.assign(delta=logloss(frame["p_pool"], frame["y"]) - logloss(frame["p_ind"], frame["y"])).groupby("asset")["delta"].agg(["mean", "size"])
    d = d[d["size"] >= 26].join(divergence.rename("divergence"), how="inner").dropna()
    rho = float(d["mean"].rank().corr(d["divergence"].rank()))
    rng = np.random.default_rng(SEED)
    perm = np.array([pd.Series(rng.permutation(d["mean"].to_numpy())).rank().corr(d["divergence"].rank().reset_index(drop=True)) for _ in range(DRAWS)])
    p = float((1 + (np.abs(perm) >= abs(rho)).sum()) / (DRAWS + 1))
    return {"actifs": int(len(d)), "actifs_en_transfert_negatif": int((d["mean"] > 0).sum()), "spearman_divergence": rho,
            "p_bilaterale": p, "par_actif": {a: round(float(v), 5) for a, v in d["mean"].items()}}


def divergence_of(p: dict, member: pd.DataFrame, first: int) -> pd.Series:
    """1 − corrélation des rendements hebdomadaires de l'actif avec la moyenne des membres (descriptif, pas une entrée)."""
    c = p["c"].loc[first:]
    week = c.iloc[::7]
    r = np.log(week / week.shift(1))
    m = member.loc[first:].iloc[::7].reindex(columns=r.columns, fill_value=False)
    mean = r.where(m).mean(axis=1)
    return pd.Series({a: 1 - r[a].where(m[a]).corr(mean) for a in r.columns})


def portfolio(frame: pd.DataFrame, col: str) -> dict:
    """Descriptif : chaque semaine, les 5 plus hautes probabilités au-dessus de 0,5, poids égaux, 0,125 % par côté."""
    weekly, prev = [], set()
    for _day, g in frame.groupby("day"):
        pick = g[g[col] > 0.5].nlargest(TOP_K, col)
        now = set(pick["asset"])
        turnover = len(now ^ prev) / max(TOP_K, 1)
        gross = float(np.mean(np.exp(pick["ret"]) - 1)) * len(pick) / TOP_K if len(pick) else 0.0
        weekly.append(gross - COST * turnover)
        prev = now
    w = np.array(weekly)
    eq = np.cumprod(1 + w)
    dd = float((1 - eq / np.maximum.accumulate(eq)).max())
    return {"semaines": int(len(w)), "geometrique_hebdo_pct": float(100 * (eq[-1] ** (1 / len(w)) - 1)),
            "pire_baisse_pct": 100 * dd, "semaines_positives": float((w > 0).mean())}


# --- Contrôle synthétique --------------------------------------------------------------------------------------------

def synthetic(seed: int = SEED, assets: int = 8, n_train: int = 80, n_test: int = 400, k: int = 6) -> dict:
    """Familles cohérente (coefficients communs + bruit faible) et divergente (coefficients propres et opposés) : perte de
    l'individuel, du collectif et de l'hybride sur des données fraîches."""
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 1, k)
    out = {}
    for family in ("coherente", "divergente"):
        betas = [common + rng.normal(0, 0.1, k) if family == "coherente" else rng.normal(0, 1, k) * rng.choice([-1, 1], k)
                 for _ in range(assets)]
        tr, te = [], []
        for a, b in enumerate(betas):
            for n, sink in ((n_train, tr), (n_test, te)):
                X = rng.normal(0, 1, (n, k))
                y = (rng.random(n) < 1 / (1 + np.exp(-X @ b))).astype(int)
                sink.append((a, X, y))
        pool = Logit().fit(np.vstack([X for _, X, _ in tr]), np.concatenate([y for _, _, y in tr]))
        loss = {"individuel": [], "collectif": [], "hybride": []}
        for (_a, X, y), (_, Xt, yt) in zip(tr, te):
            pi, pp = Logit().fit(X, y).proba(Xt), pool.proba(Xt)
            ph = hybrid(pi, pp, np.full(len(Xt), n_train))
            for name, pr in (("individuel", pi), ("collectif", pp), ("hybride", ph)):
                loss[name].append(logloss(pr, yt).mean())
        out[family] = {k_: float(np.mean(v)) for k_, v in loss.items()}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA A — apprentissage mutualisé, individuel et hybride (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/omega-hive-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    synth = synthetic()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    p = signaux.panel(loaded)
    member = signaux.members(p)
    first = moteur._day(signaux.FIRST)
    frame = samples(p, member, features(p), first)
    folds = folds_of(frame["asset"].unique())
    judged = pd.concat([judge_year(*split(frame, y), folds) for y in TEST_YEARS], ignore_index=True)
    ll = {k: logloss(judged[f"p_{k}"], judged["y"]) for k in ("base", "ind", "pool", "hyb", "unseen")}
    weeks = judged["day"].to_numpy()
    pairs = {"collectif − individuel": ("pool", "ind"), "hybride − individuel": ("hyb", "ind"), "hybride − collectif": ("hyb", "pool"),
             "collectif − référence": ("pool", "base"), "individuel − référence": ("ind", "base"), "hybride − référence": ("hyb", "base"),
             "BORDER collectif sans l'actif − collectif avec": ("unseen", "pool")}
    tests = {name: week_bootstrap(ll[a] - ll[b], weeks) for name, (a, b) in pairs.items()}
    transfer = negative_transfer(judged, divergence_of(p, member, first))
    tests["transfert négatif : rang (perte collective − individuelle, divergence)"] = {"ecart": transfer["spearman_divergence"], "p_bilaterale": transfer["p_bilaterale"], "n": transfer["actifs"]}
    for t, ph in zip(tests.values(), essais.holm([t["p_bilaterale"] for t in tests.values()])):
        t["p_holm"], t["significatif_holm_5pct"] = ph, bool(ph < 0.05)
    by_year = {int(y): {k: float(ll[k][year_of(weeks) == y].mean()) for k in ("base", "ind", "pool", "hyb", "unseen")} for y in TEST_YEARS}
    out = {"schema": "scalp-omega-hive-v1", "status": "mesure hors échantillon (walk-forward annuel) sur historique déjà consulté ; aucune intégration",
           "omega": ["HIVE", "GENOME", "BORDER"], "capacites": ["C085", "C087", "C093", "C026"],
           "parametres": {"caracteristiques": FEATURES, "horizon_jours": HORIZON, "embargo_jours": EMBARGO, "semaines_minimales_individuel": MIN_OWN,
                          "retrait_hybride": SHRINK, "groupes_border": FOLDS, "annees_jugees": TEST_YEARS, "C": C_REG},
           "synthetique": synth, "predictions": int(len(judged)), "semaines": int(len(np.unique(weeks))), "actifs": int(judged["asset"].nunique()),
           "taux_semaines_positives": float(judged["y"].mean()),
           "perte_log_moyenne": {k: float(v.mean()) for k, v in ll.items()}, "par_annee": by_year, "tests": tests,
           "transfert_negatif": transfer,
           "portefeuille_descriptif": {k: portfolio(judged, f"p_{k}") for k in ("ind", "pool", "hyb")},
           "seconds": round(time.time() - t0)}
    print("synthétique :", json.dumps(synth))
    print({k: out[k] for k in ("predictions", "semaines", "actifs", "taux_semaines_positives", "perte_log_moyenne")})
    for k, t in tests.items():
        print(f"{k:70} {t['ecart']:+.5f} p {t['p_bilaterale']:.3f} Holm {t['p_holm']:.3f}")
    print("transfert négatif :", transfer["actifs_en_transfert_negatif"], "actifs sur", transfer["actifs"])
    print("portefeuilles :", json.dumps(out["portefeuille_descriptif"]))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-hive-v1", "synthetique": synth,
                                 "perte_log_moyenne": out["perte_log_moyenne"],
                                 "tests": {k: {"ecart": t["ecart"], "p_holm": t["p_holm"]} for k, t in tests.items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
