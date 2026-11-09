"""Calendrier des annonces macroéconomiques (Fed, inflation US...).

Autour de ces annonces, les prix bougent brutalement : l'IA n'ouvre aucune
nouvelle position pendant la fenêtre d'exclusion. Les dates sont saisies dans
les réglages (heure UTC) : vérifiez-les sur federalreserve.gov et bls.gov.
"""

from __future__ import annotations

import calendar
import time


def parse_event(text: str) -> tuple[float, str] | None:
    """« 2026-12-09 19:00 Fed » -> (horodatage UTC, libellé). None si invalide."""
    parts = text.strip().split(" ", 2)
    if len(parts) < 2:
        return None
    try:
        st = time.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return float(calendar.timegm(st)), (parts[2].strip() if len(parts) > 2 else "Annonce")


def events(settings: dict) -> list[tuple[float, str]]:
    out = [e for e in (parse_event(x) for x in settings["macro"]["events"]) if e]
    return sorted(out)


def blackout(settings: dict, now: float) -> tuple[float, str] | None:
    """Renvoie l'événement en cours de fenêtre d'exclusion, sinon None."""
    if not settings["macro"]["enabled"]:
        return None
    win = settings["macro"]["blackout_minutes"] * 60
    for ts, label in events(settings):
        if ts - win <= now <= ts + win:
            return ts, label
    return None


def upcoming(settings: dict, now: float, limit: int = 5) -> list[dict]:
    return [{"ts": ts, "label": label} for ts, label in events(settings) if ts >= now - 3600][:limit]
