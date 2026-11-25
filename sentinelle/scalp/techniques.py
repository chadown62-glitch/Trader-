"""Familles techniques et de régime encore absentes du catalogue, testées contre le momentum simple (C022, C023, C025,
C031, C034, C038).

Chaque famille est causale (ligne d : données jusqu'à la clôture de d, utilisées à l'ouverture de d + 1), paramétrée a
priori, et testée comme filtre de B0 (momentum sur 28 jours) avec l'outil d'ablation (C095), Holm sur le lot :
- C022 participation : part des membres de l'univers dont le rendement sur 28 jours est positif ≥ 50 % ;
- C023 force de tendance : ADX de Wilder sur 14 jours ≥ 25 ;
- C025 rupture de régime : écart entre la moyenne des rendements du BTC sur 20 et sur 120 jours supérieur à 2 erreurs
  types ; aucune entrée pendant les 10 jours qui suivent une rupture ;
- C031 pivots confirmés : clôture au-dessus du dernier sommet confirmé (plus haut de 7 jours, confirmé 3 jours après) ;
- C034 retest de cassure : dans les 5 jours qui suivent une cassure du plus haut de 20 jours, un plus bas revient à 1 % du
  niveau cassé et la clôture reste au-dessus ; signal actif 10 jours ;
- C038 divergence RSI : exclusion des divergences baissières (nouveau plus haut de clôture sur 20 jours, RSI de 14 jours
  sous son plus haut de la même fenêtre).
Le volume delta cumulé (C044) et la pression des transactions agressives (C043) sont déjà la famille F6 de signaux.py,
non significative après correction (#147, #149) : non redéveloppés.
Mesure seulement : rien n'entre dans une décision.

Lancement : python -m sentinelle.scalp.techniques
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

from . import ablation, moteur, recherche, signaux

ADX_N, ADX_MIN = 14, 25.0
BREADTH_MIN = 0.5
BREAK_SHORT, BREAK_LONG, BREAK_Z, BREAK_PAUSE = 20, 120, 2.0, 10
PIVOT_K = 3
RETEST_WITHIN, RETEST_TOL, RETEST_HOLD = 5, 0.01, 10
RSI_N, DIV_WINDOW = 14, 20


def wilder(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(h: pd.DataFrame, lo: pd.DataFrame, c: pd.DataFrame, n: int = ADX_N) -> pd.DataFrame:
    """ADX de Wilder (force de tendance, sans direction)."""
    up, down = h.diff(), -lo.diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()]).groupby(level=0).max().reindex(c.index)
    atr = wilder(tr, n)
    pdi, mdi = 100 * wilder(plus, n) / atr, 100 * wilder(minus, n) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
    return wilder(dx, n)


def rsi(c: pd.DataFrame, n: int = RSI_N) -> pd.DataFrame:
    d = c.diff()
    gain, loss = wilder(d.clip(lower=0.0), n), wilder(-d.clip(upper=0.0), n)
    out = 100 - 100 / (1 + gain / loss.replace(0.0, np.nan))
    return out.mask((loss == 0) & (gain > 0), 100.0)  # aucune baisse sur la fenêtre : RSI de 100


def breadth(c: pd.DataFrame, member: pd.DataFrame) -> pd.Series:
    """Part des membres de l'univers dont le rendement sur 28 jours est positif."""
    up = (c / c.shift(28) - 1) > 0
    n = member.sum(axis=1)
    return (up & member).sum(axis=1) / n.replace(0, np.nan)


def regime_break(btc: pd.Series) -> pd.Series:
    """Rupture : la moyenne courte des rendements s'écarte de la moyenne longue de plus de BREAK_Z erreurs types."""
    r = np.log(btc).diff()
    z = (r.rolling(BREAK_SHORT).mean() - r.rolling(BREAK_LONG).mean()) / (r.rolling(BREAK_LONG).std() / np.sqrt(BREAK_SHORT))
    return z.abs() > BREAK_Z


def recent(mask: pd.Series | pd.DataFrame, days: int) -> pd.Series | pd.DataFrame:
    """Vrai si l'évènement a eu lieu aujourd'hui ou dans les « days − 1 » jours précédents."""
    return mask.astype(float).rolling(days, min_periods=1).max().astype(bool)


def confirmed_pivot_high(h: pd.DataFrame, k: int = PIVOT_K) -> pd.DataFrame:
    """Dernier sommet confirmé : le plus haut du jour t − k est le maximum de [t − 2k, t] ; connu au jour t seulement."""
    window = h.rolling(2 * k + 1, min_periods=2 * k + 1).max()
    is_pivot = h.shift(k) == window
    return h.shift(k).where(is_pivot).ffill()


def retest(h: pd.DataFrame, lo: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
    """Cassure du plus haut de 20 jours (jours précédents), puis retour d'un plus bas à 1 % du niveau dans les 5 jours,
    clôture au-dessus : signal actif RETEST_HOLD jours."""
    level = h.rolling(20, min_periods=20).max().shift(1)
    broke = c > level
    lvl = level.where(broke).ffill(limit=RETEST_WITHIN)
    since = recent(broke, RETEST_WITHIN + 1) & ~broke
    touched = since & (lo <= lvl * (1 + RETEST_TOL)) & (c > lvl)
    return recent(touched, RETEST_HOLD)


def bearish_divergence(c: pd.DataFrame, r: pd.DataFrame, w: int = DIV_WINDOW) -> pd.DataFrame:
    """Nouveau plus haut de clôture sur w jours alors que le RSI reste sous son plus haut des w jours précédents."""
    new_high = c >= c.rolling(w, min_periods=w).max()
    return new_high & (r < r.shift(1).rolling(w - 1, min_periods=w - 1).max())


def families(p: dict, member: pd.DataFrame) -> dict[str, pd.DataFrame]:
    h, lo, c = p["h"], p["l"], p["c"]
    spread = lambda s: pd.DataFrame(np.repeat(s.to_numpy()[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns)  # noqa: E731
    return {"C022 participation ≥ 50 %": spread(breadth(c, member) >= BREADTH_MIN) & c.notna(),
            "C023 ADX(14) ≥ 25": adx(h, lo, c) >= ADX_MIN,
            "C025 pas d'entrée 10 jours après une rupture du BTC": spread(~recent(regime_break(c["BTCUSDT"]).fillna(False), BREAK_PAUSE)) & c.notna(),
            "C031 au-dessus du dernier sommet confirmé": c > confirmed_pivot_high(h),
            "C034 retest de cassure": retest(h, lo, c),
            "C038 sans divergence RSI baissière": ~bearish_divergence(c, rsi(c)) & c.notna()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Familles techniques et de régime (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/techniques-v2.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    btc = p["c"]["BTCUSDT"]
    base_signal = fam["F1_momentum"]
    base = signaux.net(base_signal, member, p, r, signaux.COST)[0].loc[first:]
    new = families(p, member)
    rows = [ablation.compare(base, signaux.net(base_signal & f.fillna(False), member, p, r, signaux.COST)[0].loc[first:],
                             label=f"{name} en filtre de B0", costs="0,125 %/côté", btc_close=btc) for name, f in new.items()]
    rows = ablation.adjust(rows)
    share = {name: round(float((f & member).loc[first:].sum().sum() / max(member.loc[first:].sum().sum(), 1)), 4) for name, f in new.items()}
    out = {"schema": "scalp-techniques-v2", "status": "mesure sur historique déjà consulté ; paramètres fixés a priori ; aucune intégration",
           "capacites": ["C022", "C023", "C025", "C031", "C034", "C038"], "deja_couvertes": {"C043": "F6_volume_pression (#146)", "C044": "F6_volume_pression (#146)"},
           "part_des_jours_membres_ou_la_famille_est_vraie": share, "ablations": rows, "seconds": round(time.time() - t0)}
    for x in rows:
        print(f"{x['ablation'][:56]:56} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem IC [{x['ic95_pct'][0]:+.4f} ; {x['ic95_pct'][1]:+.4f}] "
              f"Holm {x['p_holm']:.3f} | baisse {x['sans']['max_drawdown_pct']:.1f} → {x['avec']['max_drawdown_pct']:.1f} % | part {share[x['ablation'].replace(' en filtre de B0', '')]}")
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "techniques-v2",
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"], "pire_baisse": [x["sans"]["max_drawdown_pct"], x["avec"]["max_drawdown_pct"]]}
                                               for x in rows}}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
