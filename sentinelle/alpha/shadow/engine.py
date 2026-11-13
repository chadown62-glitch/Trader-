"""Moteur shadow en ligne : observations ``as_of`` -> décisions figées -> simulation causale par bras.

Un pas (``step``) reçoit l'heure de l'appelant et des observations déjà collectées (bougies
closes, carnets). Il n'ouvre aucune connexion, ne lit aucune horloge et n'écrit que dans son
registre. Dans l'ordre :

1. **admission** : chaque bougie est validée (contrat), puis classée ``DECIDABLE`` (reçue et
   traitée à temps), ``HISTORY`` (authentique mais trop tardive : elle prolonge la série, elle ne
   donne lieu à aucune décision) ou ``PENDING`` (une bougie antérieure manque : mise en attente,
   jamais de trou comblé). Bougie future, incohérente, non close, révisée ou d'un autre marché :
   refus motivé ;
2. **règlement** d'une bougie dès que tous les actifs l'ont : pour chaque bras, entrées des
   décisions de la bougie précédente (premier prix disponible après la décision), sorties, valeur ;
3. **décisions** à la clôture : candidat R1 et bras de référence, écrites avant le prix suivant.
   Une décision ne dépend que des bougies publiques closes : chacun peut la recalculer.

Chaque bras a son propre portefeuille fictif de 30 € : aucune liquidité, aucun résultat n'est
partagé, et une occasion refusée ne reçoit jamais de résultat.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..bars import feature_row
from ..execution import CostModel, Outcome, PortfolioRules, simulate_trade
from ..teachers import TEACHERS, abstention_mask
from . import CAN_TRADE, CONTRACT
from .contract import MODES, ContractError, make, sha256, validate
from .ledger import ChainLedger, LedgerError, chain_lines, parse, verify_chain
from .online import Tape
from .r1 import R1Model, climate_of

DAY_MS = 86_400_000


class ShadowError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    max_reception_latency_ms: int = 300_000  # clôture -> réception de la bougie
    max_decision_latency_ms: int = 300_000  # clôture -> écriture de la décision
    max_fill_delay_ms: int = 300_000  # clôture -> carnet utilisable pour l'entrée
    warmup_bars: int = 320  # 200 (moyennes longues) + 120 (rang de volatilité)
    max_drawdown: float = 0.10  # critère d'arrêt du candidat (limite de risque du projet)


@dataclass(frozen=True)
class SessionConfig:
    protocol_id: str
    manifest_sha256: str
    mode: str
    symbols: tuple[str, ...]
    start_ms: int  # première clôture décidable
    end_ms: int  # dernière clôture de la fenêtre
    tf_ms: int = 14_400_000
    not_before_ms: int = 0
    cost: dict = field(default_factory=lambda: asdict(CostModel()))
    rules: dict = field(default_factory=lambda: asdict(PortfolioRules()))
    limits: dict = field(default_factory=lambda: asdict(Limits()))

    def check(self) -> None:
        if self.mode not in MODES:
            raise ShadowError("Mode inconnu")
        if self.start_ms % self.tf_ms or self.end_ms % self.tf_ms or self.end_ms <= self.start_ms:
            raise ShadowError("Fenêtre non alignée ou vide")
        if list(self.symbols) != sorted(set(self.symbols)) or not self.symbols:
            raise ShadowError("Actifs invalides")


def market_sha(bar: dict) -> str:
    """Empreinte du CONTENU de marché d'une bougie (recalculable par tous depuis l'historique public)."""
    return sha256({k: bar[k] for k in ("symbol", "timeframe_ms", "open_at_ms", "open", "high", "low", "close", "volume")})


def book_fill(asks: list, notional: float) -> tuple[float, float]:
    """Prix moyen payé pour ``notional`` euros en remontant les ventes ; (prix, montant réellement servi)."""
    spent = qty = 0.0
    for price, size in asks:
        take = min(size * price, notional - spent)
        if take <= 0:
            break
        spent += take
        qty += take / price
    return (spent / qty if qty else math.nan), spent


class Arm:
    """Portefeuille fictif indépendant d'un bras (mêmes règles que ``execution.run_portfolio``)."""

    def __init__(self, name: str, capital: float):
        self.name, self.initial, self.cash = name, capital, capital
        self.fees = self.traded = 0.0
        self.open: list[dict] = []
        self.pending: dict[int, list] = {}  # ouverture de la bougie d'entrée -> intentions
        self.closed = 0
        self.refused: dict[str, int] = {}
        self.last_value = capital
        self.peak, self.max_dd = capital, 0.0
        self.aborted_at: int | None = None
        self.hold: dict[str, float] | None = None  # achat-conservation

    def mark(self, value: float) -> None:
        self.last_value = value
        self.peak = max(self.peak, value)
        self.max_dd = max(self.max_dd, 1 - value / self.peak if self.peak > 0 else 0.0)

    def state(self) -> dict:
        return {"cash": self.cash, "fees": self.fees, "closed": self.closed, "value": self.last_value,
                "open": [[p["symbol"], p["index"], p["qty"]] for p in self.open], "aborted_at": self.aborted_at,
                "pending": sorted((k, len(v)) for k, v in self.pending.items()), "hold": self.hold}


class ShadowEngine:
    def __init__(self, config: SessionConfig, model: R1Model, ledger: ChainLedger, reference=None):
        config.check()
        self.cfg, self.model, self.ledger, self.reference = config, model, ledger, reference
        self.cost, self.rules, self.limits = CostModel(**config.cost), PortfolioRules(**config.rules), Limits(**config.limits)
        self.tapes = {s: Tape(s, config.tf_ms, self.cost.default_spread_bps) for s in config.symbols}
        self.tape_sha = {s: "0" * 64 for s in config.symbols}
        self.bar_meta: dict[str, dict[int, dict]] = {s: {} for s in config.symbols}  # ouverture -> statut, empreinte
        self.waiting: dict[str, dict[int, dict]] = {s: {} for s in config.symbols}
        self.books: dict[str, list[dict]] = {s: [] for s in config.symbols}
        self.arm_names = ("r1", "take_all", f"teacher:{model.reference_teacher}", "project_mixte", "cash", "buy_and_hold")
        self.arms = {n: Arm(n, self.rules.capital) for n in self.arm_names}
        self.next_settle = config.start_ms - config.tf_ms  # ouverture de la prochaine bougie à régler
        self.session_sha: str | None = None
        self.now = 0
        self.counts = {"duplicates": 0, "books_ignored": 0}

    # ------------------------------------------------------------------ création / reprise

    @classmethod
    def start(cls, config: SessionConfig, model: R1Model, ledger: ChainLedger, now_ms: int, reference=None) -> "ShadowEngine":
        if ledger.lines or ledger.recovered:
            raise ShadowError("Ce registre contient déjà une session : reprenez-la, n'en créez pas une seconde")
        if config.mode == "prospective":
            if config.start_ms < config.not_before_ms:
                raise ShadowError("Fenêtre antérieure au préenregistrement : période déjà explorée")
            if now_ms > config.start_ms:
                raise ShadowError("Une session prospective ne peut pas commencer dans le passé (aucune décision antidatée)")
        eng = cls(config, model, ledger, reference)
        ledger.begin(now_ms)
        line = ledger.add("session", {"contract": CONTRACT, "protocol_id": config.protocol_id, "manifest_sha256": config.manifest_sha256,
                                      "model_sha256": model.sha256, "config": _jsonable(asdict(config)), "arms": list(eng.arm_names),
                                      "reference_model_sha256": getattr(reference, "sha256", None), "can_trade": CAN_TRADE,
                                      "shadow_only": True})
        eng.session_sha, eng.now = line["sha"], now_ms
        ledger.commit(eng.state_sha())
        return eng

    @classmethod
    def resume(cls, config: SessionConfig, model: R1Model, ledger: ChainLedger, now_ms: int, reference=None) -> "ShadowEngine":
        """Reprise après arrêt : rejoue les pas clos, exige qu'ils redonnent le registre, annule le pas inachevé."""
        if not ledger.lines:
            raise ShadowError("Registre vide : rien à reprendre")
        report = verify_chain(ledger.lines)
        valid = report["valid"]
        session = valid[0] if valid else None
        if session is None or session["kind"] != "session":
            raise ShadowError("Registre sans session close")
        body = session["body"]
        if body["manifest_sha256"] != config.manifest_sha256 or body["model_sha256"] != model.sha256 \
                or body["config"] != _jsonable(asdict(config)):
            raise ShadowError("La session enregistrée ne correspond pas au protocole ou au modèle fournis")
        eng = cls(config, model, ChainLedger(None), reference)
        eng.session_sha, eng.now = session["sha"], session["at"]
        steps: dict[int, list[dict]] = {}
        for ln in valid:
            steps.setdefault(ln["step"], []).append(ln)
        for step in sorted(steps):
            lines = steps[step]
            if lines[0]["kind"] in ("session", "recovery"):
                continue
            bars = [ln["body"]["bar"] for ln in lines if ln["kind"] == "admission" and not ln["body"]["from_pending"]]
            books = [ln["body"]["book"] for ln in lines if ln["kind"] == "book"]
            produced = eng._run(lines[0]["at"], bars, books)
            stored = [(ln["kind"], ln["body"]) for ln in lines if ln["kind"] not in ("rejection", "commit")]
            fresh = [(k, b) for k, b in produced if k != "rejection"]
            if not _same(stored, fresh):
                raise ShadowError(f"Le pas {step} du registre ne se recalcule pas à l'identique : registre altéré ou code différent")
            commit = lines[-1]
            if commit["kind"] != "commit" or commit["body"]["state_sha256"] != eng.state_sha():
                raise ShadowError(f"État incohérent après le pas {step}")
        eng.ledger = ledger
        ledger.recover(max(now_ms, ledger.clock))
        eng.now = max(eng.now, ledger.clock)
        return eng

    # ------------------------------------------------------------------ pas

    def step(self, now_ms: int, bars=(), books=()) -> dict:
        if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < self.now:
            raise ShadowError("L'heure fournie recule : pas refusé")
        self.ledger.begin(now_ms)
        try:
            produced = self._run(now_ms, list(bars), list(books))
        except Exception:
            self.ledger.abort()
            raise
        lines = [self.ledger.add(kind, body) for kind, body in produced]
        commit = self.ledger.commit(self.state_sha())
        return {"step": commit["step"], "lines": lines, "anchor": self.ledger.anchor(now_ms),
                "decisions": [ln for ln in lines if ln["kind"] == "decision"]}

    def _run(self, now: int, bars: list, books: list) -> list[tuple[str, dict]]:
        self.now = now
        out: list[tuple[str, dict]] = []
        appended: list[tuple[str, int, str]] = []  # (actif, ouverture, statut)
        for raw in bars:
            self._admit_bar(raw, now, out, appended)
        for raw in books:
            self._admit_book(raw, now, out)
        slots = sorted({o for _, o, _ in appended})
        for open_ms in slots:
            self._settle_through(open_ms, out)
            for sym, o, status in sorted(appended):
                if o == open_ms:
                    self._decide(sym, o, status, out)
        self._settle_through(None, out)
        for sym in self.cfg.symbols:  # carnets : on ne garde que ceux qui peuvent encore servir
            self.books[sym] = [b for b in self.books[sym] if b["received_at_ms"] > now - self.cfg.tf_ms]
        return out

    # ------------------------------------------------------------------ admission

    def _reject(self, out: list, code: str, raw, symbol=None) -> None:
        try:
            digest = sha256(raw)
        except (TypeError, ValueError):
            digest = sha256(repr(raw).encode("utf-8", "replace"))
        out.append(("rejection", {"code": code, "payload_sha256": digest, "symbol": symbol}))

    def _admit_bar(self, raw, now: int, out: list, appended: list) -> None:
        try:
            bar = validate(raw, "bar")
        except ContractError as exc:
            return self._reject(out, exc.code, raw)
        sym, slot = bar["symbol"], bar["open_at_ms"]
        if sym not in self.tapes or bar["timeframe_ms"] != self.cfg.tf_ms:
            return self._reject(out, "UNKNOWN_MARKET", raw, sym)
        if bar["received_at_ms"] > now:
            return self._reject(out, "RECEIVED_IN_FUTURE", raw, sym)
        if bar["close_at_ms"] > bar["received_at_ms"] or (bar["published_at_ms"] is not None and bar["published_at_ms"] < bar["close_at_ms"] - 1):
            return self._reject(out, "BAR_NOT_CLOSED", raw, sym)
        tape, digest = self.tapes[sym], market_sha(bar)
        known = self.bar_meta[sym].get(slot) or ({"sha": market_sha(self.waiting[sym][slot])} if slot in self.waiting[sym] else None)
        if known is not None:
            if known["sha"] == digest:
                self.counts["duplicates"] += 1
                return None
            return self._reject(out, "CONFLICTING_REVISION", raw, sym)
        if tape.n and slot < tape.last_open:
            return self._reject(out, "BEFORE_HISTORY_START", raw, sym)
        if tape.n and slot != tape.last_open + self.cfg.tf_ms:
            if len(self.waiting[sym]) >= 2000:
                return self._reject(out, "PENDING_OVERFLOW", raw, sym)
            self.waiting[sym][slot] = bar
            out.append(("admission", {"bar": bar, "status": "PENDING", "reason": "EARLIER_BAR_MISSING", "bar_sha256": digest,
                                      "from_pending": False, "index": None, "tape_sha256": self.tape_sha[sym]}))
            return None
        self._append(bar, now, out, appended, from_pending=False)
        while tape.last_open + self.cfg.tf_ms in self.waiting[sym]:
            self._append(self.waiting[sym].pop(tape.last_open + self.cfg.tf_ms), now, out, appended, from_pending=True)

    def _append(self, bar: dict, now: int, out: list, appended: list, *, from_pending: bool) -> None:
        sym, lim = bar["symbol"], self.limits
        late_rx = bar["received_at_ms"] - bar["close_at_ms"] > lim.max_reception_latency_ms
        late_dec = now - bar["close_at_ms"] > lim.max_decision_latency_ms
        status = "HISTORY" if (late_rx or late_dec) else "DECIDABLE"
        reason = "LATE_RECEPTION" if late_rx else "LATE_DECISION" if late_dec else "ON_TIME"
        index = self.tapes[sym].append(bar["open_at_ms"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])
        digest = market_sha(bar)
        self.tape_sha[sym] = sha256((self.tape_sha[sym] + digest).encode())
        self.bar_meta[sym][bar["open_at_ms"]] = {"sha": digest, "status": status, "reason": reason,
                                                 "published_at_ms": bar["published_at_ms"], "received_at_ms": bar["received_at_ms"]}
        out.append(("admission", {"bar": bar, "status": status, "reason": reason, "bar_sha256": digest, "from_pending": from_pending,
                                  "index": index, "tape_sha256": self.tape_sha[sym]}))
        appended.append((sym, bar["open_at_ms"], status))

    def _admit_book(self, raw, now: int, out: list) -> None:
        try:
            book = validate(raw, "book")
        except ContractError as exc:
            return self._reject(out, exc.code, raw)
        if book["symbol"] not in self.books:
            return self._reject(out, "UNKNOWN_MARKET", raw, book["symbol"])
        if book["received_at_ms"] > now:
            return self._reject(out, "RECEIVED_IN_FUTURE", raw, book["symbol"])
        digest = sha256(book)
        if all(sha256(b) != digest for b in self.books[book["symbol"]]):
            self.books[book["symbol"]].append(book)
            self.books[book["symbol"]].sort(key=lambda b: (b["received_at_ms"], sha256(b)))

    # ------------------------------------------------------------------ décisions

    def _candidates(self, sym: str, index: int) -> list[dict]:
        tape = self.tapes[sym]
        lo = max(0, index - 1)
        b2, i2 = tape.bars(lo), tape.indicators(lo)
        veto, reasons = abstention_mask(tape.bars(index), tape.indicators(index))
        full_b, full_i = tape.bars(), tape.indicators()
        found = []
        for t in TEACHERS:
            if not bool(t.setup(b2, i2)[-1]):
                continue
            plan = t.plan(full_b, full_i, index)
            if plan is None:
                continue
            climate = climate_of(full_i, index)
            found.append({"teacher": t.name, "vetoed": bool(veto[0]), "veto_reasons": list(reasons[0]), "climate": climate,
                          "score_r": self.model.score(t.name, climate), "plan": plan})
        return sorted(found, key=lambda c: c["teacher"])

    def _decide(self, sym: str, open_ms: int, status: str, out: list) -> None:
        cfg = self.cfg
        as_of = open_ms + cfg.tf_ms
        if not cfg.start_ms <= as_of <= cfg.end_ms:
            return  # hors fenêtre : historique d'échauffement, aucune décision
        tape, meta = self.tapes[sym], self.bar_meta[sym][open_ms]
        index = tape.index_of(open_ms)
        common = dict(protocol_id=cfg.protocol_id, symbol=sym, as_of_ms=as_of, bar_sha256=meta["sha"], recorded_at_ms=self.now)
        head = dict(manifest_sha256=cfg.manifest_sha256, model_sha256=self.model.sha256, arm="r1",
                    history={"bars": index + 1, "first_open_at_ms": tape.first_open, "tape_sha256": self.tape_sha[sym]},
                    observed={"published_at_ms": meta["published_at_ms"], "received_at_ms": meta["received_at_ms"]},
                    threshold_r=self.model.margin if math.isfinite(self.model.margin) else None)
        refusal = None
        if status != "DECIDABLE":
            refusal = meta["reason"]
        elif index != tape.n - 1:
            refusal = "LATE_DECISION"  # une bougie plus récente est déjà connue : décider serait rétrospectif
        elif index + 1 < self.limits.warmup_bars:
            refusal = "WARMUP_INCOMPLETE"
        if refusal:
            out.append(("decision", make("decision", **common, **head, action="REFUSED", reason=refusal, candidates=[], chosen=None,
                                         theoretical=None)))
            return
        cands = self._candidates(sym, index)
        r1 = self.arms["r1"]
        aborted = r1.aborted_at is not None
        feats = feature_row(tape.bars(), tape.indicators(), index) if (cands and self.reference is not None) else None
        taken = {name: [] for name in self.arm_names}
        records = []
        for c in cands:
            take = (not aborted) and self.model.takes(c["teacher"], c["climate"], c["vetoed"])
            if take:
                taken["r1"].append(c)
            if not c["vetoed"]:
                taken["take_all"].append(c)
                if c["teacher"] == self.model.reference_teacher:
                    taken[f"teacher:{self.model.reference_teacher}"].append(c)
                if self.reference is not None and self.reference.takes(feats, c["teacher"]):
                    taken["project_mixte"].append(c)
            p = c["plan"]
            records.append({"teacher": c["teacher"], "vetoed": c["vetoed"], "veto_reasons": c["veto_reasons"], "climate": c["climate"],
                            "score_r": c["score_r"], "taken": take,
                            "plan": {"stop_price": p.stop_price, "max_bars": p.max_bars, "exit_rule": p.exit_rule,
                                     "trail_atr": p.trail_atr, "target_price": p.target_price}})
        if taken["r1"]:
            action, reason = "ENTER", "SCORE_ABOVE_MARGIN"
        elif not cands:
            action, reason = "NO_SIGNAL", "NO_TEACHER_SIGNAL"
        elif aborted:
            action, reason = "ABSTAIN", "ABORT_DRAWDOWN_LIMIT"
        elif all(c["vetoed"] for c in cands):
            action, reason = "ABSTAIN", "VETO_ABSTENTION_TEACHER"
        else:
            action, reason = "ABSTAIN", "SCORE_NOT_ABOVE_MARGIN"
        theoretical = None
        if taken["r1"]:
            c = taken["r1"][0]
            close = float(tape.bars().close[index])
            unit = (close - c["plan"].stop_price) / close
            eq = r1.last_value
            theoretical = {"equity_eur": eq, "risk_eur": self.rules.risk_frac * eq,
                           "notional_eur": max(0.0, min(self.rules.risk_frac * eq / unit, self.rules.max_position_frac * eq)) if unit > 0 else 0.0}
        decision = make("decision", **common, **head, action=action, reason=reason, candidates=records,
                        chosen=taken["r1"][0]["teacher"] if taken["r1"] else None, theoretical=theoretical)
        out.append(("decision", decision))
        refs = {name: [c["teacher"] for c in taken[name]] for name in self.arm_names if name not in ("r1", "cash", "buy_and_hold")}
        unavailable = ["project_mixte"] if self.reference is None else []
        reference = make("reference", **common, arms=refs, unavailable=unavailable)
        out.append(("reference", reference))
        d_sha, r_sha = sha256(decision), sha256(reference)
        for name in self.arm_names:
            if taken[name]:
                self.arms[name].pending.setdefault(as_of, []).append(
                    {"symbol": sym, "index": index, "cands": taken[name], "ref": d_sha if name == "r1" else r_sha,
                     "as_of": as_of, "recorded_at": self.now})

    # ------------------------------------------------------------------ règlement

    def _settle_through(self, limit_open: int | None, out: list) -> None:
        cfg = self.cfg
        while self.next_settle + cfg.tf_ms <= cfg.end_ms and (limit_open is None or self.next_settle <= limit_open):
            if not all(self.next_settle in self.bar_meta[s] for s in cfg.symbols):
                return
            self._settle(self.next_settle, out)
            self.next_settle += cfg.tf_ms

    def _trade(self, sym: str, index: int, plan, k: int, fill: float | None, cache: dict) -> Outcome:
        key = (sym, index, plan.teacher, fill)
        if key not in cache:
            tape = self.tapes[sym]
            sig = tape.exits().get(plan.exit_rule) if plan.exit_rule else None
            cache[key] = simulate_trade(tape.bars(), tape.indicators(), index, plan, self.cost, sig, horizon_end=k + 1, entry_fill=fill)
        return cache[key]

    def _book_entry(self, intent: dict, plan, eq: float, out: list, used: set) -> tuple[float, str] | None:
        """Premier carnet reçu APRÈS l'écriture de la décision, dans le délai : prix réellement payable."""
        lim = self.limits
        for book in self.books[intent["symbol"]]:
            if not intent["recorded_at"] < book["received_at_ms"] <= intent["as_of"] + lim.max_fill_delay_ms:
                continue
            if book["exchange_at_ms"] is not None and book["exchange_at_ms"] < intent["as_of"]:
                continue
            best = book["asks"][0][0] * (1 + self.cost.slip)
            unit = (best - plan.stop_price) / best
            digest = sha256(book)
            if digest not in used:
                used.add(digest)
                out.append(("book", {"book": book, "book_sha256": digest}))
            if unit <= 0:
                return best, digest  # déjà sous le stop : la simulation abandonnera l'entrée
            want = max(self.rules.min_notional, min(self.rules.risk_frac * eq / unit, self.rules.max_position_frac * eq))
            price, _ = book_fill(book["asks"], want)
            return price * (1 + self.cost.slip), digest
        return None

    def _settle(self, open_ms: int, out: list) -> None:
        cfg, rules, cost = self.cfg, self.rules, self.cost
        close_ms = open_ms + cfg.tf_ms
        k = {s: self.tapes[s].index_of(open_ms) for s in cfg.symbols}
        prev_close = {s: float(self.tapes[s].bars().close[max(0, k[s] - 1)]) for s in cfg.symbols}
        close = {s: float(self.tapes[s].bars().close[k[s]]) for s in cfg.symbols}
        cache: dict = {}
        used_books: set = set()
        summary = {}
        for name in self.arm_names:
            arm = self.arms[name]
            if name == "buy_and_hold":
                if arm.hold is None:
                    part = arm.initial / len(cfg.symbols)
                    arm.hold = {s: part / (1 + cost.fee) / float(self.tapes[s].bars().open[k[s]]) for s in cfg.symbols}
                    arm.fees += arm.initial - arm.initial / (1 + cost.fee)
                    arm.cash = 0.0
                value = sum(q * close[s] * (1 - cost.fee) for s, q in arm.hold.items())
                arm.mark(value)
                summary[name] = {"equity_eur": value, "cash_eur": 0.0, "open": len(arm.hold), "max_drawdown": arm.max_dd}
                continue

            def value_at(prices: dict, arm: Arm = arm) -> float:
                return arm.cash + sum(p["qty"] * prices[p["symbol"]] * (1 - cost.fee) for p in arm.open)

            # 1) entrées : premier prix disponible après les décisions de la bougie précédente
            for intent in sorted(arm.pending.pop(open_ms, []), key=lambda x: x["symbol"]):
                sym = intent["symbol"]
                late = self.bar_meta[sym][open_ms]["status"] != "DECIDABLE"
                for c in intent["cands"]:
                    plan = c["plan"]
                    base = dict(protocol_id=cfg.protocol_id, arm=name, symbol=sym, teacher=plan.teacher,
                                decision_as_of_ms=intent["as_of"], decision_sha256=intent["ref"], available_at_ms=close_ms)
                    void = dict(entry=None, exit=None, gross_eur=None, costs_eur=None, net_eur=None, net_r=None, bars_held=None)

                    def deny(reason: str, arm: Arm = arm, base: dict = base, void: dict = void) -> None:
                        arm.refused[reason] = arm.refused.get(reason, 0) + 1
                        out.append(("outcome", make("outcome", **base, status="refused_by_portfolio", refusal=reason, **void)))

                    if any(p["symbol"] == sym for p in arm.open):
                        deny("position_déjà_ouverte")
                        continue
                    if len(arm.open) >= rules.max_positions:
                        deny("trop_de_positions")
                        continue
                    eq = value_at(prev_close)
                    booked = self._book_entry(intent, plan, eq, out, used_books)
                    fill = booked[0] if booked else None
                    trade = self._trade(sym, intent["index"], plan, k[sym], fill, cache)
                    if trade.status in ("invalid", "abandoned"):
                        out.append(("outcome", make("outcome", **base, status="abandoned",
                                                    refusal=trade.exit_reason or "bougie d'entrée invalide", **void)))
                        continue
                    unit = (trade.entry_price - trade.stop_price) / trade.entry_price
                    notional = min(rules.risk_frac * eq / unit, rules.max_position_frac * eq)
                    if notional < rules.min_notional:
                        if rules.min_notional * unit <= rules.min_order_max_risk * eq:
                            notional = rules.min_notional
                        else:
                            deny("sous_le_minimum_risque_trop_grand")
                            continue
                    partial = False
                    if booked:
                        _, served = book_fill(next(b for b in self.books[sym] if sha256(b) == booked[1])["asks"], notional)
                        partial = served < notional - 1e-9
                        notional = min(notional, served)  # exécution partielle : seulement ce que le carnet offrait
                        if notional < rules.min_notional:
                            deny("carnet_insuffisant")
                            continue
                    if notional * (1 + cost.fee) > arm.cash:
                        deny("liquidités_insuffisantes")
                        continue
                    qty, fee = notional / trade.entry_price, notional * cost.fee
                    arm.cash -= notional + fee
                    arm.fees += fee
                    arm.traded += notional
                    arm.open.append({"symbol": sym, "index": intent["index"], "plan": plan, "qty": qty, "cost": notional + fee,
                                     "notional": notional, "fill": fill, "base": base,
                                     "entry": {"at_ms": open_ms, "price": trade.entry_price, "evidence": "book" if booked else "bar_model",
                                               "book_sha256": booked[1] if booked else None, "notional_eur": notional, "qty": qty,
                                               "fee_eur": fee, "partial": partial, "bar_received_late": late}})
            # 2) sorties pendant cette bougie (stop touché dès la bougie d'entrée compris)
            for p in list(arm.open):
                sym = p["symbol"]
                trade = self._trade(sym, p["index"], p["plan"], k[sym], p["fill"], cache)
                if trade.status != "filled":
                    continue
                proceeds = p["qty"] * trade.exit_price
                fee = proceeds * cost.fee
                arm.cash += proceeds - fee
                arm.fees += fee
                arm.traded += proceeds
                arm.closed += 1
                arm.open.remove(p)
                risk_eur = p["qty"] * (trade.entry_price - trade.stop_price)
                out.append(("outcome", make("outcome", **{**p["base"], "available_at_ms": close_ms}, status="filled", refusal=None,
                                            entry=p["entry"],
                                            exit={"at_ms": int(self.tapes[sym].bars().ts[trade.exit_index]), "price": trade.exit_price,
                                                  "reason": trade.exit_reason, "evidence": "bar_model", "fee_eur": fee},
                                            gross_eur=trade.gross_r * risk_eur, costs_eur=trade.cost_r * risk_eur,
                                            net_eur=proceeds - fee - p["cost"], net_r=trade.net_r, bars_held=trade.bars_held)))
            value = value_at(close)
            arm.mark(value)
            if name == "r1" and arm.aborted_at is None and arm.max_dd >= self.limits.max_drawdown:
                arm.aborted_at = close_ms  # critère d'arrêt : plus aucune nouvelle entrée du candidat
            summary[name] = {"equity_eur": value, "cash_eur": arm.cash, "open": len(arm.open), "max_drawdown": arm.max_dd}
        final = close_ms >= cfg.end_ms
        if final:  # fin de fenêtre : positions encore ouvertes valorisées à la dernière clôture (résultat latent)
            for name in self.arm_names:
                for p in self.arms[name].open:
                    mark = p["qty"] * close[p["symbol"]] * (1 - cost.fee) - p["cost"]
                    out.append(("outcome", make("outcome", **{**p["base"], "available_at_ms": close_ms}, status="latent", refusal=None,
                                                entry=p["entry"], exit=None, gross_eur=None, costs_eur=None, net_eur=mark, net_r=None,
                                                bars_held=k[p["symbol"]] - p["index"])))
        out.append(("settlement", {"close_at_ms": close_ms, "arms": summary, "final": final,
                                   "r1_aborted_at_ms": self.arms["r1"].aborted_at,
                                   "bars": {s: self.bar_meta[s][open_ms]["sha"] for s in cfg.symbols}}))

    # ------------------------------------------------------------------ état

    def state_sha(self) -> str:
        return sha256({"session": self.session_sha, "next_settle": self.next_settle,
                       "tapes": {s: [t.n, self.tape_sha[s]] for s, t in self.tapes.items()},
                       "waiting": {s: sorted(w) for s, w in self.waiting.items()},
                       "arms": {n: a.state() for n, a in self.arms.items()}})

    def anchor(self, now_ms: int) -> dict:
        return self.ledger.anchor(now_ms)


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return v


def _same(a, b, rtol: float = 1e-9) -> bool:
    """Égalité structurelle ; les nombres à virgule sont comparés à 1e-9 près (autre machine, autre arrondi)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], rtol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, rtol) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None or isinstance(a, str) or isinstance(b, str):
        return a == b and type(a) is type(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b or abs(a - b) <= rtol * max(1.0, abs(a), abs(b))
    return a == b


def load_lines(path) -> list[dict]:
    """Lecture seule d'un registre (pour l'analyse) : lève si la chaîne est rompue."""
    items, fragment = parse(Path(path).read_bytes())
    if fragment:
        raise LedgerError("Registre terminé par une ligne incomplète : reprise nécessaire")
    verify_chain(items)
    return chain_lines(items)
