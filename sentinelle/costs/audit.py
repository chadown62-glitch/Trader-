"""Audit des hypothèses de coûts et d'exécution déjà présentes dans le projet (lecture seule).

Chaque constat est vérifié en exécutant le code existant (fonctions pures) ou en lisant ses valeurs par
défaut : si le code change, l'audit change aussi (les tests le figent). Le jugement « optimiste /
prudent » est provisoire tant que la mesure correspondante n'a pas été faite : le rapport le relie à
la mesure qui le tranche.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..alpha.execution import CostModel as AlphaCostModel
from ..config import DEFAULT_SETTINGS
from ..execution.contract import limit_fill, limit_hit, market_fill
from ..market.markets import MarketInfo

ROOT = Path(__file__).resolve().parents[2]


def _finding(component, location, assumption, value, effect, measured_by) -> dict:
    return {"component": component, "location": location, "assumption": assumption, "value": value, "expected_effect": effect,
            "measured_by": measured_by, "level": "HYPOTHETICAL"}


def findings(root: Path = ROOT) -> list[dict]:
    paper, portfolio, shield = DEFAULT_SETTINGS["paper"], DEFAULT_SETTINGS["portfolio"], DEFAULT_SETTINGS["shield"]
    risk = DEFAULT_SETTINGS["risk"]
    alpha = AlphaCostModel()
    info = MarketInfo("X/EUR", "X", "EUR")
    frozen = json.loads((root / "research" / "prospective-v2" / "settings.json").read_text(encoding="utf-8"))
    lab_path = root / "research" / "alpha-factory-v2" / "cost-hypotheses-v1.json"
    lab = json.loads(lab_path.read_text(encoding="utf-8")) if lab_path.exists() else None
    out = [
        _finding("commission", "config.paper.fee_rate ; execution/paper.py", "taux unique pour tous les ordres, teneur ou preneur",
                 {"fee_bps": paper["fee_rate"] * 1e4}, "neutre si la grille VIP 0 s'applique (0,10 % des deux côtés)", "grille tarifaire archivée"),
        _finding("actif des frais", "execution/paper.py (_fill : fee_asset = devise de cotation)",
                 "la commission est toujours payée dans la devise de cotation", {"fee_asset": "quote"},
                 "optimiste : sur Binance sans BNB, l'achat paie en actif reçu et laisse un reliquat invendable", "inventory.round_trip sur filtres observés"),
        _finding("glissement", "config.paper.slippage_bps", "glissement fixe par ordre au marché", {"slippage_bps": paper["slippage_bps"]},
                 "à mesurer : dépend de l'actif, de l'heure et de la taille", "ruban de transactions (exécution au marché)"),
        _finding("glissement (protocole gelé v2)", "research/prospective-v2/settings.json", "glissement du protocole prospectif gelé",
                 {"slippage_bps": frozen["paper"]["slippage_bps"]}, "incohérence : 10 pb dans le protocole gelé, 5 pb par défaut", "aucune (fichier gelé, non modifié)"),
        _finding("écart achat/vente", "execution/paper.py (_market_price, spread_fn facultatif)",
                 "demi-écart ajouté seulement si un carnet est fourni, sinon 0", {"half_spread_bps_without_book": 0.0},
                 "optimiste hors carnet : l'écart n'est pas payé en simulation sur bougies", "ruban de transactions et carnets observés"),
        _finding("écart (laboratoire alpha)", "sentinelle/alpha/execution.py (CostModel)", "écart par défaut si la bougie n'en fournit pas",
                 {"default_spread_bps": alpha.default_spread_bps, "slippage_bps": alpha.slippage_bps, "fx_bps": alpha.fx_bps},
                 "à mesurer ; conversion supposée nulle", "ruban de transactions"),
        _finding("conversion de devise", "config.portfolio.fx_cost_bps ; execution/paper.py (conversion_cost)",
                 "coût fixe à chaque achat et vente sur une paire non cotée en euros, sans commission de conversion",
                 {"fx_cost_bps": portfolio["fx_cost_bps"]}, "optimiste si la conversion passe par un ordre EUR/USDT (commission + demi-écart)",
                 "ruban EUR/USDT"),
        _finding("ordre à cours limité (simulation sur bougies)", "execution/contract.py (limit_hit, limit_fill)",
                 "exécuté dès que la bougie TOUCHE le prix limite, au prix limite, sans file d'attente ni exécution partielle",
                 {"touch_fills": bool(limit_hit(100.0, 101.0, 100.0, "buy")), "fill_price_at_touch": limit_fill(100.0, 100.5, "buy")},
                 "optimiste : une touche ne prouve pas l'exécution (file d'attente)", "règles prudente/optimiste sur le ruban"),
        _finding("ordre à cours limité (simulation sur carnet)", "execution/book_execution.py + liquidity.simulate_taker",
                 "seule la partie qui traverse le carnet est exécutée, comme preneur ; aucun remplissage passif",
                 {"passive_fills": False}, "prudent pour les ordres passifs", "règle prudente sur le ruban"),
        _finding("ordre au marché", "execution/contract.py (market_fill)", "dernier prix aggravé du glissement et du demi-écart",
                 {"example_buy_100_slip5_half2": market_fill(100.0, "buy", 0.0005, 0.0002)}, "neutre si les paramètres sont mesurés",
                 "ruban de transactions"),
        _finding("pas de quantité par défaut", "market/markets.py (MarketInfo)", "pas 1e-8 et minimum 5 si les filtres ne sont pas chargés",
                 {"amount_step": info.amount_step, "min_notional": info.min_notional}, "optimiste si les filtres réels sont plus grossiers",
                 "filtres exchangeInfo observés"),
        _finding("seuil de rentabilité", "risk/manager.py (min_reward_to_fees)", "gain visé d'au moins N fois les frais estimés",
                 {"min_reward_to_fees": risk["min_reward_to_fees"]}, "dépend des frais estimés, donc des hypothèses ci-dessus", "contrat de coûts"),
        _finding("bouclier (écart maximal)", "risk/shield.py (max_spread_bps)", "entrée interdite au-delà d'un écart",
                 {"max_spread_bps": shield["max_spread_bps"]}, "protection, pas un coût", "carnets observés"),
    ]
    if lab:
        p = lab["scenarios"][lab["primary"]]
        out.append(_finding("hypothèses provisoires ALPHA FACTORY v2", "research/alpha-factory-v2/cost-hypotheses-v1.json",
                            "scénario principal provisoire, en attente de COST OPTIMIZER",
                            {"fee_bps": p["fee_bps"], "slippage_bps": p["slippage_bps"], "half_spread_bps": p["half_spread_bps"], "fx_bps": p["fx_bps"]},
                            "à remplacer par le contrat de coûts mesuré", "contrat cost_contract_v1"))
    return out
