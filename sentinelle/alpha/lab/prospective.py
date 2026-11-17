"""ALPHA FACTORY v2 — observation PROSPECTIVE préenregistrée du seul candidat retenu (mission 55).

Le candidat ``mom_ts_28`` (momentum 4 semaines BTC/ETH, révision hebdomadaire) est FIGÉ : sa définition, son code,
les coûts (contrat #50), le modèle de reliquats et les règles de marché sont ceux de l'évaluation unique
``alpha-factory-v2-e1``. Seules comptent les journées POSTÉRIEURES à l'enregistrement de ce protocole : la fenêtre
2020-01-01 → 2026-11-12, déjà consultée, n'est jamais réutilisée comme donnée vierge.

Plan d'analyse (fixé ici, avant toute observation) :
- critère principal : écart quotidien moyen entre le candidat et la détention de BTC+ETH à la même exposition moyenne
  (référence sans frais), test unilatéral, erreur standard par bootstrap circulaire par blocs de 7 jours ;
- un regard par an (365 jours observés), 9 au plus ; bornes d'O'Brien-Fleming (fonction de dépense de Lan-DeMets,
  risque total 5 %) calculées une fois pour toutes et écrites dans le protocole : une avance précoce et nette peut
  conclure plus tôt sans gonfler le risque d'erreur ;
- confirmation = borne franchie ET résultat net positif ET résultat net positif dans le scénario de tension ;
- arrêt pour risque si la baisse maximale depuis le début dépasse 50 % ; arrêt pour futilité RECOMMANDÉ (non
  contraignant) si l'écart est négatif à partir du 2e regard ;
- entre deux regards : chiffres descriptifs seulement, sans valeur de décision.

Même confirmée, la règle n'est jamais promue automatiquement : le trading réel reste interdit.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np

from . import CAN_TRADE, CAPITAL, LEVERAGE, LONG_ONLY
from . import data as labdata
from .costs import CostHypotheses
from .experiment import benchmark_returns
from .simulate import Features, is_decision_day, simulate
from .strategies import BY_NAME, Context
from ..shadow.protocol import file_sha, journal_append, read_journal

PROTOCOL_ID = "alpha-factory-v2-p1"
DIRECTORY = "research/alpha-factory-v2/prospective-p1"
CANDIDATE = "mom_ts_28"
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
MAJORS = ("BTC", "ETH")
CONFIRMATORY_END = "2026-11-12"  # dernier jour de l'évaluation unique (consultée)
FIRST_DECISION = "2026-11-22"  # première clôture du dimanche après l'enregistrement
START = "2026-11-23"  # premier jour évalué (exécution à l'ouverture)
LOOK_DAYS, MAX_LOOKS, ALPHA = 365, 9, 0.05
HARM_DRAWDOWN = 0.50
BLOCK, REPS, SEED = 7, 5000, 2701
BOUNDARY_PATHS, BOUNDARY_SEED = 1_000_000, 55
DAY_MS = 86_400_000
IMPLEMENTATION = ("sentinelle/alpha/lab/prospective.py",) + tuple(
    f"sentinelle/alpha/lab/{n}.py" for n in ("__init__", "data", "costs", "strategies", "simulate", "stats", "experiment"))
STATUSES = {
    "PAS_COMMENCÉ": "aucune journée évaluée : la première décision est prise à la clôture du dimanche 22/11/2026",
    "EN_COURS": "observation en cours ; chiffres descriptifs, sans valeur de décision avant le prochain regard",
    "CONFIRMÉ_PROSPECTIVEMENT": "borne franchie, net positif et positif en tension : avantage confirmé sur données nouvelles (aucune promotion)",
    "NON_CONFIRMÉ": "9 regards sans franchir la borne : avantage non confirmé",
    "ARRÊT_RISQUE": "baisse maximale au-delà de 50 % depuis le début : observation arrêtée, avantage non confirmé",
}


class ProspectiveError(ValueError):
    pass


# -- plan statistique ---------------------------------------------------------------------------------------------------
def spending(t: float, alpha: float = ALPHA) -> float:
    """Risque d'erreur dépensé à la fraction d'information ``t`` (O'Brien-Fleming selon Lan-DeMets, unilatéral)."""
    z = NormalDist().inv_cdf(1 - alpha / 2)
    return 2 * (1 - NormalDist().cdf(z / math.sqrt(t)))


def boundaries(looks: int = MAX_LOOKS, alpha: float = ALPHA, *, paths: int = BOUNDARY_PATHS, seed: int = BOUNDARY_SEED) -> list[float]:
    """Bornes en score z pour des regards équidistants : la 1re est exacte, les suivantes par simulation (graine fixe)."""
    t = np.arange(1, looks + 1) / looks
    out = [NormalDist().inv_cdf(1 - spending(t[0], alpha))]
    rng = np.random.default_rng(seed)
    w = np.cumsum(rng.standard_normal((paths, looks)) * math.sqrt(1 / looks), axis=1)
    z = w / np.sqrt(t)
    alive = z[:, 0] < out[0]
    for k in range(1, looks):
        extra = spending(t[k], alpha) - spending(t[k - 1], alpha)
        cut = int(round(extra * paths))
        zk = np.sort(z[alive, k])[::-1]
        c = float(zk[cut - 1]) if 0 < cut <= len(zk) else math.inf
        out.append(c)
        alive &= z[:, k] < c
    return out


def operating_characteristics(bounds: list[float], mu: float, sigma: float, *, trials: int = 100_000, seed: int = 7) -> list[float]:
    """Probabilité cumulée de conclure à chaque regard si l'écart quotidien vaut ``mu`` (approximation gaussienne)."""
    rng = np.random.default_rng(seed)
    yearly = rng.normal(mu, sigma / math.sqrt(LOOK_DAYS), size=(trials, len(bounds)))
    k = np.arange(1, len(bounds) + 1)
    z = np.cumsum(yearly, axis=1) / k / (sigma / np.sqrt(k * LOOK_DAYS))
    hit = np.zeros(trials, bool)
    out = []
    for i, c in enumerate(bounds):
        hit |= z[:, i] >= c
        out.append(float(hit.mean()))
    return out


def block_z(x: np.ndarray, *, block: int = BLOCK, reps: int = REPS, seed: int = SEED) -> float:
    """Score z de la moyenne : erreur standard par bootstrap circulaire par blocs (dépendance d'un jour à l'autre)."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 2 * block:
        return float("nan")
    rng = np.random.default_rng(seed)
    nb = (n + block - 1) // block
    means = np.empty(reps)
    for lo in range(0, reps, 500):
        m = min(500, reps - lo)
        starts = rng.integers(n, size=(m, nb))
        idx = ((starts[:, :, None] + np.arange(block)) % n).reshape(m, -1)[:, :n]
        means[lo:lo + m] = x[idx].mean(axis=1)
    se = float(means.std(ddof=1))
    return float(x.mean() / se) if se > 0 else float("nan")


# -- protocole ----------------------------------------------------------------------------------------------------------
def build_protocol(*, root: Path, plan: dict, plan_sha256: str, results_sha256: str, hyp: CostHypotheses, rules: dict,
                   history: dict, registered_at: str, registered_at_ms: int) -> dict:
    spec = BY_NAME[CANDIDATE]
    if spec.describe() != plan["candidate_definitions"][CANDIDATE]:
        raise ProspectiveError("La définition du candidat a changé depuis l'évaluation unique")
    if hyp.sha256 != plan["costs"]["sha256"]:
        raise ProspectiveError("Les coûts ne sont pas ceux de l'évaluation unique")
    bounds = boundaries()
    mu_all, mu_p2, sigma_all, sigma_p2 = (history[k] for k in ("mu_all", "mu_p2", "sigma_all", "sigma_p2"))
    return {
        "kind": "alpha-lab-prospective-protocol", "schema": 1, "protocol_id": PROTOCOL_ID, "status": "PREREGISTERED_NOT_STARTED",
        "can_trade": CAN_TRADE, "promotion_allowed": False, "no_permanent_service": True, "long_only": LONG_ONLY, "leverage": LEVERAGE,
        "capital": CAPITAL, "registered_at": registered_at, "registered_at_ms": registered_at_ms,
        "question": "L'avantage de mom_ts_28 mesuré sur 2020-2026 se retrouve-t-il sur des journées que personne n'a encore vues ?",
        "candidate": {"name": CANDIDATE, "definition": spec.describe(), "frozen_since_plan": plan["plan_id"]},
        "source": {"plan_id": plan["plan_id"], "plan_sha256": plan_sha256, "results_sha256": results_sha256,
                   "verdict": "AVANTAGE_DÉMONTRÉ sur l'historique (seul candidat retenu sur 15)"},
        "data": {"symbols": dict(SYMBOLS), "endpoint": "https://data-api.binance.vision/api/v3", "interval": "1d",
                 "confirmatory_end": CONFIRMATORY_END, "history_check": "les bougies jusqu'au 12/11/2026 doivent reproduire à l'octet près "
                 "les fichiers de l'archive (empreintes du manifeste versionné) : sinon l'observation est refusée",
                 "archive_manifest_sha256": plan["data"]["archive_manifest_sha256"]},
        "window": {"first_decision": FIRST_DECISION, "start": START, "look_days": LOOK_DAYS, "max_looks": MAX_LOOKS,
                   "max_days": LOOK_DAYS * MAX_LOOKS, "never_reused": "2020-01-01 → 2026-11-12 (évaluation unique, déjà consultée)"},
        "execution": {"rules": {b: rules[b] for b in MAJORS}, "catalogue_sha256": plan["execution"]["rules"]["exchange_info_sha256"],
                      "dust_model": plan["execution"]["dust_model"], "orders": "au marché, à l'ouverture qui suit la décision"},
        "costs": {"file": plan["costs"]["file"], "sha256": hyp.sha256, "primary": hyp.primary, "stress": hyp.stress,
                  "scope": "ces hypothèses ne doivent jamais être extrapolées à une plateforme dont les commissions sont différentes"},
        "analysis": {
            "primary": "écart quotidien moyen candidat − détention de BTC+ETH à la même exposition moyenne (sans frais), test unilatéral",
            "z": {"method": "moyenne / erreur standard par bootstrap circulaire par blocs", "block_days": BLOCK, "reps": REPS, "seed": SEED},
            "alpha": ALPHA, "spending": "O'Brien-Fleming (Lan-DeMets), unilatéral",
            "boundaries_z": bounds, "boundaries_method": {"paths": BOUNDARY_PATHS, "seed": BOUNDARY_SEED, "first": "exacte"},
            "confirmed_if_all": ["score z au regard ≥ borne du regard", "résultat net > 0 (scénario principal)", "résultat net > 0 (scénario de tension)"],
            "stop_for_harm": f"baisse maximale depuis le début > {HARM_DRAWDOWN:.0%}",
            "futility": "recommandée (non contraignante) si l'écart moyen est négatif à partir du 2e regard",
            "between_looks": "descriptif seulement, sans valeur de décision",
        },
        "power": {
            "basis": "écart quotidien mesuré sur l'historique (déjà consulté : sert seulement à dimensionner la durée)",
            "mu_all": mu_all, "sigma_all": sigma_all, "mu_p2": mu_p2, "sigma_p2": sigma_p2,
            "fixed_horizon_days_80pct": {"effet_toute_la_periode": ((1.645 + 0.8416) * sigma_all / mu_all) ** 2,
                                         "effet_periode_2": ((1.645 + 0.8416) * sigma_p2 / mu_p2) ** 2},
            "probability_confirmed_by_look": {"effet_toute_la_periode": operating_characteristics(bounds, mu_all, sigma_all),
                                              "effet_periode_2": operating_characteristics(bounds, mu_p2, sigma_p2),
                                              "aucun_effet": operating_characteristics(bounds, 0.0, sigma_all)},
        },
        "statuses": STATUSES,
        "known_limits": [
            "BTC et ETH : pas de données vierges dans le passé, d'où cette observation sur l'avenir seulement",
            "commission 10 pb : hypothèse non vérifiée ; résultat valable pour cette grille de frais seulement",
            "montants en USDT : la variation du change EUR/USDT sur les positions n'est pas modélisée",
            "règles de marché figées au 13/11/2026 ; une modification future par la plateforme n'est pas suivie",
            "observation lancée à la main (commande à exécution finie), aucun service permanent",
        ],
        "implementation_sha256": {name: file_sha(Path(root) / name) for name in IMPLEMENTATION},
    }


def write_protocol(directory: Path, protocol: dict) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "protocol.json"
    if path.exists():
        raise ProspectiveError("Un protocole est déjà enregistré : il est immuable")
    payload = (json.dumps(protocol, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "protocol.json.sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def load_protocol(directory: Path, *, root: Path | None = None, hyp: CostHypotheses | None = None) -> tuple[dict, str, list[str]]:
    payload = (directory / "protocol.json").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(payload).hexdigest()
    problems = []
    if digest != (directory / "protocol.json.sha256").read_text(encoding="utf-8").strip():
        problems.append("le protocole ne correspond plus à son empreinte")
    proto = json.loads(payload)
    if hyp is not None and hyp.sha256 != proto["costs"]["sha256"]:
        problems.append("les hypothèses de coûts ont changé")
    if root is not None:
        problems += [f"code modifié depuis l'enregistrement : {n}" for n, h in proto["implementation_sha256"].items()
                     if file_sha(Path(root) / n) != h]
    if BY_NAME[CANDIDATE].describe() != proto["candidate"]["definition"]:
        problems.append("la définition du candidat a changé")
    return proto, digest, problems


def journal(directory: Path) -> list[dict]:
    return read_journal(directory / "journal.jsonl")


def record(directory: Path, kind: str, body: dict, at_ms: int) -> dict:
    return journal_append(directory / "journal.jsonl", kind, body, at_ms)


# -- données d'observation ----------------------------------------------------------------------------------------------
def _rows(payload: bytes) -> list[list[str]]:
    return [ln.split(",") for ln in gzip.decompress(payload).decode("utf-8").splitlines()[1:]]


def check_history(root: Path, manifest: dict, encode) -> list[str]:
    """Les bougies jusqu'au 12/11/2026 reproduisent-elles à l'octet près les fichiers de l'archive de l'évaluation unique ?"""
    end_ms = int(manifest["end_ms"])
    problems = []
    for base, symbol in SYMBOLS.items():
        rows = [r for r in _rows((root / "klines" / f"{symbol}.csv.gz").read_bytes()) if int(r[0]) + DAY_MS <= end_ms]
        if hashlib.sha256(encode(rows)).hexdigest() != manifest["symbols"][symbol]["sha256"]:
            problems.append(f"{symbol} : l'historique ne reproduit pas l'archive (données révisées ou altérées)")
    return problems


def load_observation(root: Path) -> tuple[labdata.Panel, str]:
    """Lit un instantané d'observation (bougies BTC/ETH) et vérifie ses empreintes ; renvoie le panneau et l'empreinte globale."""
    snap = json.loads((root / "snapshot.json").read_bytes())
    series = {}
    for base, symbol in SYMBOLS.items():
        raw = (root / "klines" / f"{symbol}.csv.gz").read_bytes()
        if hashlib.sha256(raw).hexdigest() != snap["files"][symbol]:
            raise ProspectiveError(f"Fichier d'observation altéré : {symbol}")
        a = np.array([[float(x) for x in r[:7]] for r in _rows(raw)])
        series[base] = {"days": (a[:, 0] // DAY_MS).astype(np.int64), "open": a[:, 1], "high": a[:, 2], "low": a[:, 3],
                        "close": a[:, 4], "qvol": a[:, 6]}
    digest = hashlib.sha256(json.dumps(snap["files"], sort_keys=True).encode()).hexdigest()
    return labdata.from_series(series, {"snapshot_sha256": digest, "fetched_at_ms": snap.get("fetched_at_ms")}), digest


def last_complete_day(panel: labdata.Panel) -> int:
    ok = np.flatnonzero(np.isfinite(panel.close[:, [panel.col(b) for b in MAJORS]]).all(axis=1))
    return int(ok[-1])


def weekly_decisions(panel: labdata.Panel, features: Features | None = None) -> list[list]:
    """Décisions hebdomadaires du candidat à partir du 22/11/2026, calculées avec les seules journées ``<=`` décision."""
    spec = BY_NAME[CANDIDATE]
    feats = features or Features(panel)
    out = []
    try:
        first = panel.index_of(FIRST_DECISION)
    except KeyError:
        return out
    for t in range(first, last_complete_day(panel) + 1):
        if t == first or is_decision_day(spec, panel, t):
            ctx = Context(panel.view(t), feats.age[t], feats.medvol[t], feats.vol90[t], {}, frozenset(), MAJORS)
            w = spec.fn(ctx, spec.params)
            out.append([panel.date(t), {k: round(float(v), 12) for k, v in sorted(w.items())}])
    return out


# -- évaluation ---------------------------------------------------------------------------------------------------------
def evaluate(panel: labdata.Panel, proto: dict, hyp: CostHypotheses) -> dict:
    """État de l'observation : regards passés, décision éventuelle, chiffres descriptifs. Aucune journée antérieure au début."""
    spec = BY_NAME[CANDIDATE]
    try:
        start = panel.index_of(proto["window"]["start"])
    except KeyError:
        return {"status": "PAS_COMMENCÉ", "meaning": STATUSES["PAS_COMMENCÉ"], "days": 0}
    end = min(last_complete_day(panel), start + proto["window"]["max_days"] - 1)
    if end < start:
        return {"status": "PAS_COMMENCÉ", "meaning": STATUSES["PAS_COMMENCÉ"], "days": 0}
    feats = Features(panel)
    rules = proto["execution"]["rules"]
    primary, stress = hyp.scenario(proto["costs"]["primary"]), hyp.scenario(proto["costs"]["stress"])
    run = simulate(panel, spec, primary, start=start, end=end, capital=CAPITAL, features=feats, rules=rules, majors=MAJORS)
    tense = simulate(panel, spec, stress, start=start, end=end, capital=CAPITAL, features=feats, rules=rules, majors=MAJORS)
    bench = benchmark_returns(panel, feats, start, end, majors=MAJORS)["majors"]
    exposure = float(run.exposure.mean())
    rel = run.returns - exposure * bench
    peak = np.maximum.accumulate(np.concatenate([[CAPITAL], run.equity]))
    drawdown = float(np.max(1 - np.concatenate([[CAPITAL], run.equity]) / peak))
    bounds = proto["analysis"]["boundaries_z"]
    n = end - start + 1
    looks, status = [], "EN_COURS"
    for k in range(1, min(n // LOOK_DAYS, MAX_LOOKS) + 1):
        m = k * LOOK_DAYS
        x = rel[:m]
        z = block_z(x)
        net = float(run.equity[m - 1] - CAPITAL)
        net_tense = float(tense.equity[m - 1] - CAPITAL)
        crossed = bool(np.isfinite(z) and z >= bounds[k - 1] and net > 0 and net_tense > 0)
        looks.append({"look": k, "days": m, "z": z, "boundary": bounds[k - 1], "mean_relative_daily": float(x.mean()), "net": net,
                      "net_stress": net_tense, "crossed": crossed, "futility_recommended": bool(k >= 2 and x.mean() < 0)})
        if crossed:
            status = "CONFIRMÉ_PROSPECTIVEMENT"
            break
    if status == "EN_COURS" and drawdown > HARM_DRAWDOWN:
        status = "ARRÊT_RISQUE"
    elif status == "EN_COURS" and len(looks) == MAX_LOOKS:
        status = "NON_CONFIRMÉ"
    next_look = None
    if status == "EN_COURS":
        k = len(looks) + 1
        next_look = str(np.datetime64(int(panel.days[start]) + k * LOOK_DAYS - 1, "D"))
    return {"status": status, "meaning": STATUSES[status], "first_day": panel.date(start), "last_day": panel.date(end), "days": n,
            "looks": looks, "next_look_on": next_look,
            "descriptive": {"net_pnl": run.net_pnl, "net_pnl_stress": tense.net_pnl, "mean_relative_daily": float(rel.mean()),
                            "exposure": exposure, "max_drawdown": drawdown, "trades": run.trades, "fees": run.fees,
                            "dust_lost": run.dust_lost, "hold_majors_return": float(np.prod(1 + bench) - 1),
                            "note": "entre deux regards : descriptif seulement, sans valeur de décision"}}
