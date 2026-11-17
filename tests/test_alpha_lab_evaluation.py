"""Mission 54 — corrections d'avant gel et analyses de sensibilité préenregistrées (données SYNTHÉTIQUES uniquement)."""
import numpy as np
import pytest

from sentinelle.alpha.lab import costs, registry
from sentinelle.alpha.lab.experiment import run_experiment, sensitivity
from sentinelle.alpha.lab.simulate import Features, simulate
from sentinelle.alpha.lab.stats import profit_factor
from sentinelle.alpha.lab.strategies import Spec
from sentinelle.alpha.lab.synthetic import synthetic_panel
from tests.test_alpha_lab import flat_panel
from tests.test_alpha_lab_experiment import plan_for
from tests.test_alpha_lab_readiness import HYP, MESURE

RULES = {"BTC": {"step": 1e-5, "min_notional": 5.0}, "X": {"step": 1.0, "min_notional": 5.0}}


def buy_from(day):
    def fn(ctx, p):
        return {"X": 1.0} if len(ctx.panel.days) - 1 >= day and ctx.col("X") is not None else {}
    return Spec(f"buy_from_{day}", "test", "t", fn, {}, "daily", "btc", "t", 1)


def buy_until(day):
    """Achète X avant ``day``, veut tout vendre ensuite (vente refusée si la position est sous le minimum)."""
    def fn(ctx, p):
        return {"X": 1.0} if len(ctx.panel.days) - 1 < day and ctx.col("X") is not None else {}
    return Spec(f"buy_until_{day}", "test", "t", fn, {}, "daily", "btc", "t", 1)


def test_a_one_day_lag_executes_the_same_decisions_one_day_later():
    p = flat_panel({"X": np.linspace(10, 20, 60), "BTC": np.full(60, 60_000.0)})
    spec = buy_from(20)
    a, b = simulate(p, spec, MESURE, start=5, end=50), simulate(p, spec, MESURE, start=5, end=50, lag=1)
    assert a.equity.tolist() == simulate(p, spec, MESURE, start=5, end=50, lag=0).equity.tolist()  # défaut = sans retard
    first = lambda r: int(np.flatnonzero(r.exposure > 0)[0])
    assert first(b) == first(a) + 1 and a.trades == b.trades == 1
    with pytest.raises(ValueError):
        simulate(p, spec, MESURE, start=5, end=50, lag=-1)


def test_the_forced_exit_haircut_only_hits_sales_imposed_by_a_delisting():
    x = np.concatenate([np.full(30, 10.0), np.full(30, np.nan)])
    p = flat_panel({"X": x[:30], "BTC": np.full(60, 60_000.0)})
    spec = buy_from(0)
    a = simulate(p, spec, MESURE, start=5, end=50)
    b = simulate(p, spec, MESURE, start=5, end=50, forced_haircut=0.5)
    assert a.forced_exits == b.forced_exits == 1
    assert b.net_pnl == pytest.approx(a.net_pnl - 0.5 * (a.net_pnl + 30), rel=1e-6)  # la sortie rapporte moitié moins
    with pytest.raises(ValueError):
        simulate(p, spec, MESURE, start=5, end=50, forced_haircut=1.0)


def test_with_market_rules_a_position_below_the_minimum_is_unsellable_hence_lost_at_the_end():
    """Défaut corrigé avant le gel : la valorisation finale vendait une position que le simulateur refuse de vendre."""
    x = np.concatenate([np.full(20, 10.0), np.full(40, 1.0)])  # chute de 90 % : 2 unités ne valent plus que ≈ 2 < 5
    p = flat_panel({"X": x, "BTC": np.full(60, 60_000.0)})
    spec = buy_until(20)
    r = simulate(p, spec, MESURE, start=5, end=50, rules=RULES)
    assert r.blocked.get("reliquat_sous_le_minimum", 0) > 0  # la vente est refusée les jours de décision…
    assert r.dust_lost == pytest.approx(1.998, abs=1e-6) and r.equity[-1] < 10  # … donc la position n'est pas comptée vendable à la fin
    legacy = simulate(p, spec, MESURE, start=5, end=50)  # sans règles : comportement d'avant, inchangé
    assert r.equity[-1] == pytest.approx(r.equity[-2] - 1.998, abs=1e-6)  # la veille, valorisée ; le dernier jour, invendable
    assert legacy.dust_lost == 0


def test_with_market_rules_a_delisted_position_below_the_minimum_is_lost_not_sold():
    x = np.concatenate([np.full(20, 10.0), np.full(10, 1.0)])
    p = flat_panel({"X": x, "BTC": np.full(60, 60_000.0)})
    r = simulate(p, buy_until(20), MESURE, start=5, end=50, rules=RULES)
    assert r.forced_exits == 1 and r.dust_lost == pytest.approx(1.998, abs=1e-6) and r.equity[-1] < 10


def test_profit_factor():
    assert profit_factor([1, -2, 3]) == 2.0 and profit_factor([1, 2]) is None and profit_factor([-1]) == 0.0


def test_sensitivity_is_preregistered_descriptive_and_never_changes_the_verdicts():
    plan = registry.build_plan(root=registry.Path("."), data={}, hyp=HYP, other_pairs=[], registered_at="t", registered_at_ms=1)
    sens = plan["sensitivity"]
    assert "n'entrent jamais dans la règle de décision" in sens["role"] and "NON VÉRIFIÉE" in sens["base"]
    assert [v["fee_bps"] for v in sens["variants"] if set(v) == {"name", "fee_bps", "note"}] == [25, 40, 60]
    assert {v["name"] for v in sens["variants"]} >= {"minimum_10", "retard_1_jour", "decote_sortie_forcee_25", "decote_sortie_forcee_50"}
    panel = synthetic_panel(5, kind="momentum", days=800, edge=1.5)
    base = plan_for(panel, reps=200)
    base["window"] = {"start": panel.date(430), "split": panel.date(600), "end": panel.date(799)}
    with_sens = dict(base, sensitivity=sens)
    a, b = run_experiment(panel, base, HYP), run_experiment(panel, with_sens, HYP)
    assert a["sensitivity"] is None and b["sensitivity"]["variants"].keys() == {v["name"] for v in sens["variants"]}
    for n in a["candidates"]:
        assert a["candidates"][n]["verdict"] == b["candidates"][n]["verdict"] and a["candidates"][n]["checks"] == b["candidates"][n]["checks"]
        rows = b["sensitivity"]["variants"]  # pas de monotonie exigée : une autre commission change aussi le chemin des ordres
        assert rows["commission_25"][n]["fees"] != rows["commission_60"][n]["fees"] or rows["commission_25"][n]["trades"] == 0
        be = b["sensitivity"]["break_even_fee"][n]
        if be["bps"] is not None:
            assert 0 < be["bps"] < 200
    assert set(b["sensitivity"]["variants"]["commission_25"]) >= set(base["references"])
    v = b["sensitivity"]["variants"]
    assert v["commission_60"]["hold_btc"]["net_pnl"] < v["commission_25"]["hold_btc"]["net_pnl"]  # un seul achat : plus de frais, moins de gain
    with pytest.raises(ValueError, match="mal formée"):
        sensitivity(panel, dict(base, sensitivity={**sens, "variants": [{"name": "x", "leverage": 2}]}), HYP, Features(panel),
                    [], 430, 799, None)


def test_the_three_verdicts_are_fixed_before_the_results_and_read_the_plan_s_own_checks():
    from tools import alpha_lab
    def cand(retained=False, net=1.0, stressed=1.0, both=True, rel=0.001):
        return {"verdict": "RETENU_POUR_PROTOCOLE_PROSPECTIF" if retained else "NON_RETENU", "net": {"net_pnl": net},
                "costs": {"stressed_pnl": stressed}, "checks": {"holds_in_both_periods": both}, "relative": {"interval": {"mean": rel}}}
    assert alpha_lab.verdict_of(cand(retained=True)) == "AVANTAGE_DÉMONTRÉ"
    assert alpha_lab.verdict_of(cand()) == "PROMETTEUR_NON_DÉMONTRÉ"
    for bad in (dict(net=-1), dict(stressed=-1), dict(both=False), dict(rel=-0.001), dict(rel=None)):
        assert alpha_lab.verdict_of(cand(**bad)) == "ÉCHEC"
    rep = {"candidates": {"a": cand(net=-1), "b": cand()}}
    assert alpha_lab.verdict(rep)["verdict"] == "PROMETTEUR_NON_DÉMONTRÉ"
    assert alpha_lab.verdict({"candidates": {"a": cand(net=-1)}})["verdict"] == "ÉCHEC"
