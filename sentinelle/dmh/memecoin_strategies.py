"""Local paper-only rules. Returns are learned exclusively from completed memecoin positions."""

import math

from .tokenrisk import assess_candidate
from .tokens import TokenId

NAMES = ("sniper", "momentum", "dip_hunter", "whale_tail")


def evaluate(client, asset, point, whale):
    guard = assess_candidate(client, TokenId("solana", asset["symbol"]), point["liquidity_usd"])
    if not guard["allowed"]:
        return [], guard
    previous = asset.get("last")
    signals = []
    if 0 <= point["pool_age_minutes"] <= 3:
        signals.append("sniper")
    if previous:
        if (
            previous["volume_usd_per_min_m5"]
            and previous["buy_sell_count_ratio_m5"]
            and (point["volume_usd_per_min_m5"] or 0) > 1.5 * previous["volume_usd_per_min_m5"]
            and (point["buy_sell_count_ratio_m5"] or 0) > max(1, previous["buy_sell_count_ratio_m5"])
        ):
            signals.append("momentum")
        if (
            0.65 <= point["price"] / asset["peak"] <= 0.80
            and point["liquidity_usd"] >= 0.95 * asset["peak_liquidity"]
            and (point["volume_usd_per_min_m5"] or 0) > 0
        ):
            signals.append("dip_hunter")
    if whale:
        signals.append("whale_tail")
    return signals, guard


class MemecoinIsolatedLearner:
    asset_domain = "memecoin"

    def __init__(self, completed):
        scores, self.fractions = {}, {}
        for name in NAMES:
            returns = [x["return"] for x in completed if x["strategy"] == name][-30:]
            wins = [r for r in returns if r > 0]
            losses = [-r for r in returns if r < 0]
            growth = sum(math.log(max(0.001, 1 + r)) for r in returns) / (4 + len(returns))
            scores[name] = math.exp(max(-3, min(3, 4 * growth)))
            # Shrink small samples with one win and one loss of equal size.
            p = (len(wins) + 1) / (len(returns) + 2)
            odds = ((sum(wins) + 0.2) / (len(wins) + 1)) / ((sum(losses) + 0.2) / (len(losses) + 1))
            self.fractions[name] = min(0.10, max(0.0, 0.25 * (p - (1 - p) / odds))) if returns else 0.04
        self.weights = {name: score / sum(scores.values()) for name, score in scores.items()}
