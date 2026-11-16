"""Plan de mesure des coûts, figé AVANT toute collecte historique (tirages, fenêtres, méthodes, règles).

Le plan fixe, avec une graine publique :
- les dates et les paires mesurées, classe par classe ; pour les cryptos « autres », l'actif est tiré
  dans l'univers daté d'ALPHA FACTORY v2 tel qu'il était connu la veille (liquidité seulement, aucun
  résultat de stratégie : il n'en existe aucun sur données réelles) ;
- deux instants par fichier journalier : l'ouverture (00:01 UTC, la minute qui suit l'exécution à
  l'ouverture supposée par le laboratoire) et un instant aléatoire de la journée ;
- les tailles d'ordre (5, 10 et 30 €), les deux sens, les latences, les délais d'attente ;
- les méthodes comparées, la seule comparaison confirmatoire, et la façon de passer des mesures au
  contrat de coûts (règles écrites ici, appliquées telles quelles par ``measure``).

Source historique : l'archive publique officielle (data.binance.vision), fichiers journaliers de
transactions agrégées vérifiés par leur somme de contrôle publiée.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .markets import CEX_MEMECOINS, MAJORS, classify

PLAN_ID = "cost-optimizer-v1-m1"
SEED = 20_261_116
WINDOW = ("2020-01-01", "2026-11-12")
SPLIT = "2023-06-01"  # moitié d'estimation avant, moitié de validation après (même coupure qu'ALPHA FACTORY v2)
COUNTS = {"eur_route_dates": 60, "others_usdt": 150, "memecoin_usdt": 80}
EUR_LISTED = {"BTCEUR": "2020-01-03", "ETHEUR": "2020-01-03", "SOLEUR": "2021-05-28"}  # première bougie publique observée
MIN_AGE_DAYS = 91
OPEN_OFFSET_MS = 60_000  # 00:01 UTC
BEFORE_MS, AFTER_MS = 60_000, 420_000
SIZES_EUR = (5.0, 10.0, 30.0)
LATENCIES_MS = (1_000, 5_000, 30_000)
TIMEOUTS_MS = (30_000, 60_000, 300_000)
BULK = "https://data.binance.vision/data/spot/daily/aggTrades"
DAY_MS = 86_400_000

PROBES = {"symbols": ["BTCEUR", "ETHEUR", "SOLEUR", "BTCUSDT", "ETHUSDT", "SOLUSDT", "EURUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "TRXUSDT",
                      "DOGEUSDT", "SHIBUSDT", "PEPEUSDT"],
          "rounds": 12, "round_pause_s": 20, "depth_levels": 100, "trades_lookback_ms": 120_000,
          "use": "valider l'estimateur du demi-écart par le ruban contre le carnet observé ; mesurer la profondeur et l'impact actuels"}
RULES = {
    "fees": "commission preneur du niveau VIP 0, sans remise (hypothèse : la grille n'a pas pu être archivée) ; remise BNB = variante",
    "half_spread_primary": "borne haute à 95 % de la moyenne du demi-écart estimé, échantillons d'ouverture, par classe",
    "slippage_primary": "max(0, borne haute à 95 % de la moyenne de (coût au marché à 1 s − demi-écart − commission)), ordre de 10 €",
    "half_spread_stress": "90e centile du demi-écart estimé, tous échantillons de la classe",
    "slippage_stress": "90e centile de (coût au marché à 5 s − demi-écart − commission), ordre de 30 €",
    "conversion_primary": "par ordre, comme le moteur actuel : demi-écart EUR/USDT (borne haute) + commission preneur",
    "conversion_alternative": "devise de cotation conservée : une conversion au départ et une à la fin ; risque de change rapporté à part",
    "estimator_correction": "si les sondes montrent une sous-estimation moyenne du demi-écart (borne haute de l'erreur < 0), "
                            "le demi-écart du contrat est augmenté de la valeur absolue de la borne basse de l'erreur",
    "missing_quote": "un échantillon sans estimation possible est compté (part des échantillons sans liquidité visible), jamais remplacé",
    "export_alpha_lab": "majors = classe majors_usdt ; others = maximum des classes others_usdt et memecoin_usdt ; glissement = maximum "
                        "des classes de cryptos en USDT ; conversion par ordre = coût de conversion principal ; minimum d'ordre 5",
    "dust": "reliquat d'un aller-retour de 10 € calculé avec les filtres observés et la règle « frais dans l'actif reçu » ; rapporté à part, "
            "non ajouté aux coûts (la valeur reste détenue)",
    "execution_confirmatory": "une seule comparaison confirmatoire : la variante passive (repli au marché, règle prudente) au plus petit "
                              "coût moyen sur la moitié d'estimation, contre le marché à 1 s, sur la moitié de validation ; amélioration "
                              "démontrée si la borne basse à 95 % de (coût marché − coût variante) est > 0 ; sinon le contrat garde l'ordre au marché",
}


IMPLEMENTATION = tuple(f"sentinelle/costs/{n}.py" for n in ("__init__", "evidence", "venues", "inventory", "tape", "execution", "book",
                                                                "markets", "stats", "benefit", "measure", "contract", "plan")) + (
    "sentinelle/execution/liquidity.py", "tools/cost_fetch.py", "tools/cost_optimizer.py")


def implementation_sha256(root: Path) -> dict[str, str]:
    from ..alpha.shadow.protocol import file_sha

    return {name: file_sha(Path(root) / name) for name in IMPLEMENTATION}


def _day(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) // DAY_MS


def _date(day: int) -> str:
    return datetime.fromtimestamp(day * DAY_MS / 1000, timezone.utc).strftime("%Y-%m-%d")


def daily_closes(archive: Path, symbol: str) -> dict[int, float]:
    """Clôtures journalières d'une paire dans l'archive d'ALPHA FACTORY v2 (empreinte vérifiée)."""
    manifest = json.loads((Path(archive) / "manifest.json").read_bytes())
    meta = manifest["symbols"][symbol]
    raw = (Path(archive) / "klines" / f"{symbol}.csv.gz").read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
        raise ValueError(f"Fichier altéré : {symbol}")
    out = {}
    for line in gzip.decompress(raw).decode("utf-8").splitlines()[1:]:
        f = line.split(",")
        out[int(f[0]) // DAY_MS] = float(f[4])
    return out


def build(archive: Path, *, registered_at: str, root: Path | None = None) -> dict:
    """Plan complet, déterministe pour une archive donnée."""
    from ..alpha.lab import data as labdata
    from ..alpha.lab.simulate import Features
    from ..alpha.lab.strategies import Context

    panel = labdata.load_archive(archive)
    feats = Features(panel)
    eur = daily_closes(archive, "EURUSDT")
    rng = np.random.default_rng(SEED)
    lo, hi = _day(WINDOW[0]), _day(WINDOW[1])
    days = np.arange(lo, hi + 1)
    samples = []

    def rate(day: int) -> float:
        for d in range(day - 1, day - 8, -1):  # dernier taux connu la veille
            if d in eur:
                return eur[d]
        raise KeyError(day)

    def add(cls: str, symbol: str, day: int, file_group: str):
        to_quote = 1.0 if symbol.endswith("EUR") else rate(day)
        intraday = int(rng.integers(3_600_000, 23 * 3_600_000))
        for kind, offset in (("open", OPEN_OFFSET_MS), ("intraday", intraday)):
            t = day * DAY_MS + offset
            samples.append({"id": f"{cls}-{symbol}-{_date(day)}-{kind}", "class": cls, "symbol": symbol, "day": _date(day), "kind": kind,
                            "t_ms": t, "start_ms": t - BEFORE_MS, "end_ms": t + AFTER_MS, "split": "estimation" if day < _day(SPLIT) else "validation",
                            "sizes_quote": [round(x * to_quote, 6) for x in SIZES_EUR], "eur_to_quote": to_quote, "file_group": file_group})

    route_days = sorted(rng.choice(days[days >= _day(EUR_LISTED["BTCEUR"]) + MIN_AGE_DAYS], COUNTS["eur_route_dates"], replace=False).tolist())
    for d in route_days:
        group = f"route-{_date(d)}"
        for base in ("BTC", "ETH", "SOL"):
            if d < _day(EUR_LISTED[f"{base}EUR"]) + MIN_AGE_DAYS:
                continue
            add("eur_direct", f"{base}EUR", d, group)
            add(classify(base, "USDT"), f"{base}USDT", d, group)
        add("fx_eurusdt", "EURUSDT", d, group)

    def draw(cls: str, count: int, eligible):
        got, tries = 0, 0
        while got < count and tries < count * 50:
            tries += 1
            d = int(rng.choice(days))
            t = panel.index_of(_date(d - 1)) if _date(d - 1) >= panel.date(0) else None
            if t is None:
                continue
            names = eligible(t)
            if not names:
                continue
            base = names[int(rng.integers(len(names)))]
            add(cls, f"{base}USDT", d, f"{cls}-{got}")
            got += 1
        return got

    def others(t: int) -> list[str]:
        ctx = Context(panel.view(t), feats.age[t], feats.medvol[t], feats.vol90[t])
        return [panel.base(panel.assets[j]) for j in ctx.universe(min_age=91, top=20) if panel.base(panel.assets[j]) not in MAJORS + CEX_MEMECOINS]

    def memes(t: int) -> list[str]:
        return [b for b in CEX_MEMECOINS if b in panel.assets and feats.age[t][panel.col(b)] >= MIN_AGE_DAYS]

    n_others = draw("others_usdt", COUNTS["others_usdt"], others)
    n_memes = draw("memecoin_usdt", COUNTS["memecoin_usdt"], memes)
    order = rng.permutation(len(samples))  # collecte dans un ordre aléatoire : une collecte interrompue reste représentative
    samples = [samples[k] for k in order]
    files = sorted({(s["symbol"], s["day"]) for s in samples})
    return {
        "kind": "cost-measurement-plan", "schema": 1, "plan_id": PLAN_ID, "registered_at": registered_at, "seed": SEED,
        "status": "RESEARCH", "can_trade": False,
        "purpose": "mesurer les coûts réels d'exécution fictive et comparer des méthodes d'exécution, avant toute évaluation de stratégie",
        "independence": "aucun résultat de stratégie n'est lu ; l'univers des « autres » ne dépend que de la liquidité connue la veille",
        "window": list(WINDOW), "split": SPLIT, "counts": COUNTS, "drawn": {"others_usdt": n_others, "memecoin_usdt": n_memes},
        "eur_listed": EUR_LISTED, "min_age_days": MIN_AGE_DAYS,
        "timing": {"open_offset_ms": OPEN_OFFSET_MS, "before_ms": BEFORE_MS, "after_ms": AFTER_MS,
                   "open_note": "00:01 UTC : la minute qui suit l'ouverture, où le laboratoire suppose l'exécution ; la fenêtre ne déborde pas sur la veille"},
        "sizes_eur": list(SIZES_EUR), "latencies_ms": list(LATENCIES_MS), "timeouts_ms": list(TIMEOUTS_MS), "sides": ["buy", "sell"],
        "policies": {"market": "preneur", "join": "à cours limité au meilleur prix", "improve": "un pas de prix mieux, si l'écart le permet",
                     "rules": ["conservative", "optimistic"], "fallbacks": ["cancel", "market"]},
        "source": {"historical": BULK, "checksum": "somme SHA-256 officielle publiée à côté de chaque fichier, vérifiée avant usage",
                   "probes": "https://data-api.binance.vision/api/v3 (depth + aggTrades récents) pour valider l'estimateur",
                   "eurusdt_daily": "archive d'ALPHA FACTORY v2 (clôture de la veille) pour convertir les tailles en devise de cotation"},
        "archive_manifest_sha256": panel.provenance["archive_manifest_sha256"],
        "probes": PROBES, "rules": RULES, "files": [{"symbol": s, "day": d} for s, d in files], "samples": samples,
        "implementation_sha256": implementation_sha256(root) if root is not None else None,
        "amendments": "aucun ; une correction de code après l'enregistrement est inscrite au journal avec sa raison et ses effets",
    }


def write(directory: Path, plan: dict) -> str:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "measurement-plan.json"
    if path.exists():
        raise FileExistsError("Un plan de mesure est déjà enregistré : il est immuable")
    payload = (json.dumps(plan, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "measurement-plan.json.sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def load(directory: Path) -> tuple[dict, str]:
    payload = (Path(directory) / "measurement-plan.json").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != (Path(directory) / "measurement-plan.json.sha256").read_text(encoding="utf-8").strip():
        raise ValueError("Le plan de mesure ne correspond plus à son empreinte")
    return json.loads(payload), digest


def code_drift(plan: dict, root: Path) -> list[str]:
    """Fichiers de calcul modifiés depuis l'enregistrement du plan (liste vide = code inchangé)."""
    if not plan.get("implementation_sha256"):
        return ["plan sans empreinte du code"]
    current = implementation_sha256(root)
    return [name for name, h in plan["implementation_sha256"].items() if current.get(name) != h]
