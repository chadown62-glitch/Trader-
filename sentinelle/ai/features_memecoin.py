"""Descriptive pool observations only; no registration with the majors' feature pipeline."""
import math


def nonnegative(value):
    if type(value) not in (float, int) or value < 0 or value > 1e300:
        return None
    return float(value) if math.isfinite(value) else None


def _field(pair, group, key):
    data = pair.get(group)
    return nonnegative(data.get(key)) if isinstance(data, dict) else None


def _ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def extract_features(pair, *, observed_ms, created_ms):
    """5-minute USD volume/minute, trade-count pressure and a balanced XYK proxy.

    The impact is a fraction of mid for a hypothetical USD notional, before fees;
    it is neither an executable quote nor a forecast. Missing denominators stay null.
    """
    liquidity = _field(pair, "liquidity", "usd")
    cap = nonnegative(pair.get("marketCap"))
    created = nonnegative(created_ms)
    reference_usd = 10.0
    volume = _field(pair, "volume", "m5")
    txns = pair.get("txns")
    counts = txns.get("m5") if isinstance(txns, dict) else None
    counts = counts if isinstance(counts, dict) else {}
    buys, sells = nonnegative(counts.get("buys")), nonnegative(counts.get("sells"))
    return {"asset_domain": "memecoin", "liquidity_usd": liquidity,
            "pool_age_minutes": (observed_ms - created) / 60000 if created and created <= observed_ms else None,
            "liquidity_to_market_cap": _ratio(liquidity, cap),
            "volume_usd_per_min_m5": volume / 5 if volume is not None else None,
            "buy_sell_count_ratio_m5": _ratio(buys, sells),
            "xyk_impact_fraction": reference_usd / (liquidity / 2 + reference_usd) if liquidity else None,
            "impact_reference_usd": reference_usd}
