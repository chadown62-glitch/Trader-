"""Moteur court terme (SPEC.md) : IA principale et pupitre de risque du profil prudent.
Abstention par défaut, apprentissage sur le seul passé et discipline du pupitre. Hors ligne."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import campaign, desk
from sentinelle.scalp.brain import MIN_TRAIN, PrincipalAI
from sentinelle.scalp.features import FEATURES
from sentinelle.scalp.trade import STOP, TARGET


def cand(i, symbol="AUSDC", net=0.01, risk=0.01, setup="pullback", hours=1):
    t = 1_000_000_000_000 + i * 3_600_000
    return {"decided_at": t, "exit_ms": t + hours * 3_600_000, "symbol": symbol, "setup": setup, "risk_pct": risk,
            "net_return": net, "r": net / risk, "outcome": TARGET if net > 0 else STOP}


def test_desk_sizes_by_risk_without_leverage_and_limits_positions():
    rows = pd.DataFrame([cand(0, "A", hours=10), cand(0, "A", hours=10), cand(0, "B", hours=10), cand(0, "C", hours=10),
                         cand(0, "D", hours=10)])
    out = desk.run(rows, np.ones(len(rows), bool), 0, 10**13)
    assert len(out["trades"]) == 3
    assert out["refusals"] == {"position déjà ouverte sur cette crypto": 1, "nombre maximal de positions": 1}
    assert out["trades"][0]["notional"] == pytest.approx(0.30 * 1000)  # 0,5 % / 1 % = 50 % plafonné à 30 %
    small = desk.run(pd.DataFrame([cand(0, risk=0.04, net=0.08)]), np.ones(1, bool), 0, 10**13)
    assert small["trades"][0]["notional"] == pytest.approx(0.005 * 1000 / 0.04)


def test_desk_stops_after_daily_loss_and_pauses_after_three_losses():
    # Deux stops franchis en ouverture (−1,64 R, soit −0,82 % du capital chacun) : −1,63 % dans la journée.
    losses = pd.DataFrame([cand(i, symbol=f"S{i}", net=-0.0655, risk=0.04) for i in range(6)])
    out = desk.run(losses, np.ones(len(losses), bool), 0, 10**13)
    assert len(out["trades"]) == 2 and out["refusals"] == {"perte maximale du jour atteinte": 4}
    # Trois petites pertes (sortie à 5 h) : pause de 12 heures, l'alerte de 10 h est refusée, celle de 20 h est prise.
    spaced = pd.DataFrame([cand(i * 2, symbol=f"S{i}", net=-0.01, risk=0.04) for i in range(3)]
                          + [cand(10, symbol="Z", net=0.02, risk=0.04), cand(20, symbol="W", net=0.02, risk=0.04)])
    out = desk.run(spaced, np.ones(len(spaced), bool), 0, 10**14)
    assert [t["symbol"] for t in out["trades"]] == ["S0", "S1", "S2", "W"]
    assert out["refusals"] == {"pause après 3 pertes d'affilée": 1}


def synthetic_alerts(n, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    win = rng.random(n) < 1 / (1 + np.exp(-2.5 * x))
    rows = []
    for i in range(n):
        r = 2.0 if win[i] else -1.0
        row = cand(i, symbol=f"S{i % 5}", net=r * 0.01, risk=0.01)
        row.update({k: 0.0 for k in FEATURES}, cost_r=0.22, thesis="Plan.", trend_1h=float(x[i]))
        rows.append(row)
    return pd.DataFrame(rows)


def test_principal_ai_abstains_without_enough_history_and_learns_a_real_edge():
    few = PrincipalAI().fit(synthetic_alerts(MIN_TRAIN - 1))
    assert not few.ready and not few.judge(synthetic_alerts(20, 1))["take"].any()
    data = synthetic_alerts(3000)
    ai = PrincipalAI().fit(data.iloc[:2000])
    test = data.iloc[2000:].drop(columns=["r", "net_return", "outcome", "exit_ms"])  # l'issue n'est pas visible
    v = ai.judge(test)
    taken = data.iloc[2000:]["r"][v["take"].to_numpy()]
    assert 0 < len(taken) < 1000 and taken.mean() > data.iloc[2000:]["r"].mean() + 0.3
    assert v["reasoning"].str.contains("IA principale").all()


def test_month_test_trains_only_on_outcomes_known_before_the_month():
    data = synthetic_alerts(3000)
    start = int(data["decided_at"].iloc[2600])
    month = pd.Timestamp(start, unit="ms").strftime("%Y-%m")
    seen = {}
    original = PrincipalAI.fit

    def spy(self, train):
        seen["max_exit"] = int(train["exit_ms"].max())
        return original(self, train)

    PrincipalAI.fit = spy
    try:
        campaign.month_test(data, {}, month)
    finally:
        PrincipalAI.fit = original
    from sentinelle.scalp.data import month_bounds
    assert seen["max_exit"] < month_bounds(month)[0] - campaign.EMBARGO_MS
