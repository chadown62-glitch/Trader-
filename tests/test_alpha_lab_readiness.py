"""Mission 52 — préparation d'ALPHA FACTORY v2 : contrat de coûts #50 et modèle de reliquats (données SYNTHÉTIQUES)."""
import json
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.lab import CAPITAL, costs, data, registry
from sentinelle.alpha.lab.simulate import simulate
from sentinelle.alpha.lab.strategies import BY_NAME, CANDIDATES, Spec
from sentinelle.alpha.lab.synthetic import synthetic_panel
from sentinelle.alpha.lab.simulate import Features
from tests.test_alpha_lab import flat_panel
from tools import alpha_lab

ROOT = Path(__file__).resolve().parent.parent
PROPOSAL = ROOT / "research" / "cost-optimizer-v1" / "alpha-lab-cost-hypotheses-proposal.json"
CONTRACT = ROOT / "research" / "cost-optimizer-v1" / "cost-contract-v1.json"
HYP = costs.load(PROPOSAL)
MESURE, TENSION = HYP.scenario("mesure"), HYP.scenario("tension")
RULES = {"BTC": {"step": 1e-5, "min_notional": 5.0}, "ETH": {"step": 1e-4, "min_notional": 5.0}}


def toggler(ctx, p):
    """Achète BTC un jour sur deux et sort le lendemain (beaucoup d'allers-retours, donc de reliquats)."""
    return {"BTC": 1.0} if len(ctx.panel.days) % 2 == 0 else {}


TOGGLE = Spec("toggle", "test", "t", toggler, {}, "daily", "btc", "t", 1)


def btc_panel(n=60, price=60_000.0):
    return flat_panel({"BTC": np.full(n, price), "ETH": np.full(n, 1_650.0)})


# -- contrat de coûts #50 -------------------------------------------------------------------------------------------------
def test_the_cost_contract_of_pr50_is_readable_by_the_lab_and_matches_its_published_values():
    rep = alpha_lab.costs_check(PROPOSAL, ROOT / "research" / "alpha-factory-v2" / registry.COST_FILE)
    assert rep["compatible"] and rep["adopted"] is True and "cost_contract_v1" in rep["proposal"]["source_of_truth"]
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    conv, conv_s = c["conversion"]["per_order"]["value"], c["conversion"]["per_order_stress"]["value"]
    assert MESURE.fx_bps == pytest.approx(conv, abs=1e-3) and TENSION.fx_bps == pytest.approx(conv_s, abs=1e-3)
    majors = c["classes"]["majors_usdt"]
    assert MESURE.half_spread_major_bps == pytest.approx(majors["half_spread"]["value"], abs=1e-3)
    assert MESURE.fee_bps == pytest.approx(majors["fee_taker"]["value"])
    assert MESURE.round_trip("BTC") * 1e4 == pytest.approx(43.757, abs=0.01)  # 2 × 21,88 pb : valeur publiée par #50
    assert TENSION.round_trip("X") * 1e4 == pytest.approx(75.906, abs=0.01)
    assert MESURE.min_notional == 5.0 and tuple(MESURE.majors) == ("BTC", "ETH")


def test_the_cost_contract_of_pr50_is_adopted_byte_for_byte_with_the_coordination_decisions():
    """Mission 54 : le fichier de coûts du laboratoire EST la proposition #50 (même empreinte) ; la décision est tracée."""
    lab = ROOT / "research" / "alpha-factory-v2"
    adopted = lab / registry.COST_FILE
    assert adopted.read_bytes() == PROPOSAL.read_bytes() and costs.load(adopted).sha256 == HYP.sha256
    decision = json.loads((lab / registry.ADOPTION_FILE).read_text(encoding="utf-8"))
    assert decision["adopted_sha256"] == HYP.sha256 and decision["adopted_file"] == registry.COST_FILE
    assert decision["cost_contract"]["sha256"] == (ROOT / "research" / "cost-optimizer-v1" / "cost-contract-v1.sha256").read_text().split()[0]
    assert "jamais être extrapolés à une plateforme dont les commissions sont différentes" in decision["scope"]
    assert any("NON VÉRIFIÉE" in d for d in decision["decisions"])
    old = costs.load(lab / "cost-hypotheses-v1.json")  # l'ancien fichier provisoire reste, pour l'historique
    assert old.version == "1.0.0-provisoire" and decision["replaces"]["sha256"] == old.sha256


# -- modèle de reliquats --------------------------------------------------------------------------------------------------
def test_without_rules_the_simulator_is_unchanged():
    p = btc_panel()
    a = simulate(p, TOGGLE, MESURE, start=5, end=50)
    b = simulate(p, TOGGLE, MESURE, start=5, end=50, rules=None)
    assert np.array_equal(a.equity, b.equity) and a.dust_lost == 0 and a.dust_events == 0


def test_dust_is_created_by_step_rounding_and_fee_in_the_received_asset_and_counted_as_lost():
    p = btc_panel()
    res = simulate(p, TOGGLE, MESURE, start=5, end=51, rules=RULES)  # dernière journée sans position : pas de sortie estimée
    assert res.exposure[-1] == 0
    step_value = 1e-5 * 60_000.0
    assert res.dust_events > 5 and res.trades > 10
    per_event = res.dust_lost / res.dust_events
    assert 0 < per_event <= step_value + 1e-9  # jamais plus d'un pas par reliquat
    # conservation : capital = valeur finale + coûts payés + reliquats perdus (prix constant, aucune autre perte)
    assert CAPITAL == pytest.approx(res.equity[-1] + res.fees + res.spread_slippage + res.dust_lost, abs=1e-4)
    plain = simulate(p, TOGGLE, MESURE, start=5, end=51)
    assert res.equity[-1] < plain.equity[-1]  # le modèle de reliquats est plus prudent


def test_fine_steps_make_the_dust_model_converge_to_the_plain_model():
    p = btc_panel()
    fine = {"BTC": {"step": 1e-12, "min_notional": 5.0}}
    a = simulate(p, TOGGLE, MESURE, start=5, end=50, rules=fine)
    b = simulate(p, TOGGLE, MESURE, start=5, end=50)
    assert a.equity[-1] == pytest.approx(b.equity[-1], rel=2e-4)  # même coût à l'arrondi près (commission dans l'actif reçu)
    assert a.dust_lost < 1e-6


def test_rounding_never_creates_cash_or_negative_positions_on_a_random_panel():
    p = synthetic_panel(5, kind="noise", days=500)
    feats = Features(p)
    rules = {p.base(a): {"step": 10 ** -k, "min_notional": 5.0} for a, k in zip(p.assets, [2, 3, 4, 5, 6] * 20)}
    for spec in CANDIDATES[:4]:
        res = simulate(p, spec, MESURE, start=430, end=499, features=feats, rules=rules)
        assert np.all(np.isfinite(res.equity)) and res.equity[-1] >= 0 and res.dust_lost >= 0
        assert np.all(res.exposure >= -1e-12) and np.all(res.exposure <= 1 + 1e-9)


def test_an_order_below_the_minimum_after_rounding_is_refused():
    p = flat_panel({"BTC": np.full(30, 600_000.0)})  # un pas de 0,00001 BTC vaut 6 € : 30 € → arrondis
    rules = {"BTC": {"step": 1e-4, "min_notional": 5.0}}  # un pas vaut 60 € : impossible avec 30 €
    res = simulate(p, BY_NAME["hold_btc"], MESURE, start=5, end=25, rules=rules)
    assert res.trades == 0 and any("minimum" in k for k in res.blocked) and res.equity[-1] == pytest.approx(CAPITAL)


def test_the_plan_declares_the_dust_model_and_the_rules_fingerprint():
    plan = registry.build_plan(root=ROOT, data={"archive_manifest_sha256": "0" * 64}, hyp=HYP, other_pairs=[], registered_at="t",
                               registered_at_ms=0, rules_meta={"exchange_info_sha256": "a" * 64, "assets": 2, "limit": "x"})
    assert plan["execution"]["dust_model"] and plan["execution"]["rules"]["exchange_info_sha256"] == "a" * 64


def test_load_rules_reads_the_archived_catalogue_with_its_fingerprint(tmp_path):
    info = {"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
                         "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.00001000"}, {"filterType": "NOTIONAL", "minNotional": "5.0"}]},
                        {"symbol": "BTCEUR", "baseAsset": "BTC", "quoteAsset": "EUR", "filters": []}]}
    raw = json.dumps(info).encode()
    import hashlib
    (tmp_path / "exchangeInfo.json").write_bytes(raw)
    (tmp_path / "manifest.json").write_text(json.dumps({"exchange_info_sha256": hashlib.sha256(raw).hexdigest(), "quote": "USDT"}))
    rules, meta = data.load_rules(tmp_path)
    assert rules == {"BTC": {"symbol": "BTCUSDT", "step": 1e-5, "min_notional": 5.0}} and meta["assets"] == 1
    (tmp_path / "exchangeInfo.json").write_bytes(raw + b" ")
    with pytest.raises(ValueError):
        data.load_rules(tmp_path)
