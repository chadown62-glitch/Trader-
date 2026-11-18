"""Contrat d'exécution unique : l'étiquetage des exemples, la simulation accélérée (backtest)
et le trading en argent fictif appliquent les mêmes règles, avec les mêmes formules.

Les neuf règles (détail et exemples dans docs/EXECUTION.md) :

1. **Disponibilité du signal.** Un signal calculé sur la bougie i n'existe qu'à sa clôture
   (`signal_time`). Aucune décision n'utilise une donnée postérieure à cet instant.
2. **Premier prix disponible.** L'ordre est exécuté au premier prix connu APRÈS la décision :
   l'ouverture de la bougie i+1 sur l'historique, le premier relevé de prix en direct.
   Jamais à la clôture de la bougie du signal, qui a servi à décider.
3. **Délai et contraintes.** L'ordre part à la décision. Sans prix récent, sans taux de change
   récent ou sous les minimums de la plateforme, il est refusé.
4. **Ordres au marché et à cours limité.** Au marché : premier prix disponible, aggravé du
   glissement (et du demi-écart du carnet en direct). À cours limité : au prix limite, ou à
   l'ouverture si elle est plus favorable ; sans glissement.
5. **Frais et glissement.** Frais sur le montant de chaque ordre. Glissement sur les ordres au
   marché et sur les stops.
6. **Trous de cotation et stops.** Un stop s'exécute au pire du prix du stop et de l'ouverture,
   aggravé du glissement.
7. **Stop et objectif dans la même bougie.** Le stop est supposé touché en premier.
8. **Exécution partielle ou refus.** Un ordre est exécuté en entier ou refusé (limite connue).
   Si le premier prix disponible est déjà au niveau du stop ou en dessous, l'entrée est abandonnée.
9. **Données manquantes, anciennes ou incohérentes.** Une bougie invalide (`valid_bar`) ne
   déclenche ni décision ni exécution. En direct, pas d'entrée sur des prix périmés.

Sortie à la durée maximale et relèvement du stop : décidés à la clôture d'une bougie, ils
s'appliquent à partir de la bougie suivante (sortie à son ouverture, nouveau stop actif dès
cette bougie).
"""

from __future__ import annotations

import math


def signal_time(bar_ts: int, tf_ms: int) -> int:
    """Instant (ms) où le signal de la bougie ouverte à `bar_ts` devient disponible : sa clôture."""
    return int(bar_ts) + int(tf_ms)


def valid_bar(o: float, h: float, l: float, c: float) -> bool:
    """Bougie exploitable : prix finis et positifs, plus haut et plus bas cohérents avec l'ouverture et la clôture."""
    try:
        if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
            return False
    except TypeError:
        return False
    return h >= max(o, c) and l <= min(o, c)


def market_fill(price: float, side: str, slip: float, half_spread: float = 0.0) -> float:
    """Prix d'exécution d'un ordre au marché à partir du premier prix disponible."""
    worse = slip + max(0.0, half_spread)
    return price * (1 + worse) if side == "buy" else price * (1 - worse)


def entry_possible(first_price: float, stop: float | None) -> bool:
    """Une entrée est abandonnée si le premier prix disponible (avant glissement) est déjà au niveau du stop ou en dessous."""
    return stop is None or first_price > stop


def stop_fill(stop: float, open_: float, side: str, slip: float) -> float:
    """Exécution d'un stop touché pendant une bougie : au pire du stop et de l'ouverture, glissement compris."""
    if side == "sell":
        return min(stop, open_) * (1 - slip)
    return max(stop, open_) * (1 + slip)


def stop_hit(stop: float, high: float, low: float, side: str) -> bool:
    return low <= stop if side == "sell" else high >= stop


def limit_fill(limit: float, open_: float, side: str) -> float:
    """Exécution d'un ordre à cours limité touché pendant une bougie : au prix limite, ou à l'ouverture si elle est meilleure."""
    if side == "sell":
        return open_ if open_ > limit else limit
    return open_ if open_ < limit else limit


def limit_hit(limit: float, high: float, low: float, side: str) -> bool:
    return high >= limit if side == "sell" else low <= limit
