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


def events(settings: dict, auto: list[dict] | None = None) -> list[tuple[float, str]]:
    """Annonces saisies à la main + annonces importées automatiquement (calendrier économique)."""
    out = [e for e in (parse_event(x) for x in settings["macro"]["events"]) if e]
    if settings["macro"].get("auto_calendar", True):
        out += [(float(e["ts"]), e["label"]) for e in (auto or [])]
    return sorted(set(out))


def blackout(settings: dict, now: float, auto: list[dict] | None = None) -> tuple[float, str] | None:
    """Renvoie l'événement en cours de fenêtre d'exclusion, sinon None."""
    if not settings["macro"]["enabled"]:
        return None
    win = settings["macro"]["blackout_minutes"] * 60
    for ts, label in events(settings, auto):
        if ts - win <= now <= ts + win:
            return ts, label
    return None


def upcoming(settings: dict, now: float, limit: int = 8, auto: list[dict] | None = None) -> list[dict]:
    details = {(float(e["ts"]), e["label"]): e for e in (auto or [])}
    out = []
    for ts, label in events(settings, auto):
        if ts >= now - 3600:
            e = details.get((ts, label), {})
            out.append({"ts": ts, "label": label, "forecast": e.get("forecast"), "previous": e.get("previous"),
                        "source": "calendrier" if e else "manuel"})
    return out[:limit]
