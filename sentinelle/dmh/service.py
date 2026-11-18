"""Service du hub : prévisualiser, confirmer, simuler, régler, réconcilier, observer. Rien ne tourne en continu.

Chaîne d'une opération fictive :
1. prévisualisation : routes chiffrées aller-retour, choix (ou ABSTENTION), verdict de sécurité, explication ;
2. confirmation explicite de l'utilisateur, avec un identifiant de demande unique (rejouer = même résultat) ;
3. NOUVEAU contrôle à l'exécution : données relues, route recalculée, dérive de prix ou de coût bornée,
   réglages inchangés depuis la prévisualisation, verdict de sécurité refait ;
4. réservation atomique des fonds, simulation, règlement atomique (ou abandon propre) ;
5. reprise après panne sans doublon ni nouvel essai aveugle.

Toutes les valeurs affichées sont FICTIVES : prix réels publics, exécution simulée.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime, timezone

from .. import safety
from . import LABEL, VERSION
from . import assumptions as A
from .guard import check_runtime, inspect_request
from .ledger import Ledger, StandaloneCash, MainAccountCash
from .net import PublicClient, SourceUnavailable
from .observe import consensus
from .router import Intent, Route, RouteError, build_routes, cex_leg, choose, dex_leg
from .security import (ABSOLUTE, DEFAULT_LIMITS, Portfolio, SecurityRefusal, Verdict, check_clock, check_execution_drift,
                       check_financial, check_route, config_hash, effective, validate_limits)
from .simulate import PaperExecutor
from .tokens import TokenId, TokenRegistry, load_registry
from .view import LiveView, MarketView

log = logging.getLogger("sentinelle.dmh")
ASSETS = ("BTC", "ETH", "SOL", "DOGE", "BONK", "WIF", "PEPE")
_REQ = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
REFERENCE_ENGINE = {"fee_bps": 10.0, "slippage_bps": 5.0, "fx_bps": 5.0,
                    "note": "réglages actuels du moteur papier existant (paper.fee_rate 0,1 %, slippage 5 pb, conversion 5 pb)"}


def reservation(route: dict) -> tuple[tuple[str, str, float], float]:
    """Ce que l'entrée d'une route consomme : (actif, lieu, quantité) à réserver, et les euros dépensés."""
    entry = route["entry"]
    eur = sum(l["qty_in"] for l in entry if l["asset_in"] == "EUR" and l["loc_in"] == "cash")
    if eur > 0:
        return ("EUR", "cash", eur), eur
    first = entry[0]
    qty = sum(l["qty_in"] for l in entry if l["asset_in"] == first["asset_in"] and l["loc_in"] == first["loc_in"])
    return (first["asset_in"], first["loc_in"], qty), 0.0


class HubError(ValueError):
    def __init__(self, message: str, code: str = "dmh"):
        super().__init__(message)
        self.message = message
        self.code = code


class HubService:
    name = "dmh"

    def __init__(self, db, account_id: int, *, view_factory=None, registry: TokenRegistry | None = None, cash=None,
                 clock=time.time, risk=None, client: PublicClient | None = None, llm_cfg: dict | None = None):
        self.db = db
        self.clock = clock
        self.ledger = Ledger(db, account_id, clock=clock)
        self.ledger.cash = cash or StandaloneCash(self.ledger)
        self.client = client or PublicClient(clock=clock)
        self.view_factory = view_factory or (lambda: LiveView(self.client, clock=clock))
        self.registry = registry if registry is not None else load_registry()
        self.risk = risk
        self.llm_cfg = llm_cfg or {}
        self._lock = threading.RLock()
        self.recovered = self.recover()

    # -- construction dans l'application -------------------------------------------------------------------------
    @classmethod
    def for_app(cls, app) -> "HubService":
        acc = app.main_account()
        if acc is None or acc["kind"] != "paper":
            raise HubError("portefeuille principal fictif introuvable : le hub reste désactivé")
        aid = acc["id"]
        holder: dict = {}

        def broker():
            b = app.runtime(aid).broker
            safety.require_paper_broker(b, "hub de marché")
            svc = holder.get("svc")
            if svc is not None:
                b.external_reserved = svc.ledger.reserved_by_asset
                b.external_value = svc.ledger.liquidation_value
            return b
        news = (app.settings.get("news") or {})
        client = PublicClient()
        from .tokenrisk import TokenRiskService
        registry = load_registry()
        svc = cls(app.db, aid, cash=MainAccountCash(broker), registry=registry, client=client,
                  risk=TokenRiskService(client, registry), llm_cfg={"url": news.get("ollama_url", "http://127.0.0.1:11434"),
                                                                   "model": news.get("model", "")})
        holder["svc"] = svc
        try:
            broker()  # branche tout de suite les réservations et la valeur du hub sur le broker du portefeuille
        except Exception as exc:  # pragma: no cover - dépend de l'état de l'application
            log.warning("Hub : broker du portefeuille indisponible (%s)", exc)
        return svc

    # -- réglages ------------------------------------------------------------------------------------------------
    def limits(self) -> dict:
        return effective(self.ledger.state("limits") or dict(DEFAULT_LIMITS))

    def set_limits(self, patch: dict, origin: str) -> dict:
        if not isinstance(patch, dict):
            raise SecurityRefusal("limites illisibles")
        new = validate_limits(patch, self.limits(), origin)
        with self.db.transaction():
            self.ledger.set_state("limits", new)
            self.ledger.journal(None, "limites", {"patch": patch, "origin": origin})
        return self.limits()

    def config_hash(self) -> str:
        return config_hash(self.limits(), A.digest(), self.registry.meta.get("sha256"))

    # -- état du portefeuille vu par la sécurité --------------------------------------------------------------------
    def logical(self, asset_key: str) -> str:
        if asset_key.startswith("native:"):
            return "SOL" if asset_key.endswith("solana") else "ETH"
        if ":" in asset_key:
            try:
                info = self.registry.get(TokenId.parse(asset_key))
            except ValueError:
                info = None
            return info.asset if info else asset_key
        return asset_key

    def portfolio(self) -> Portfolio:
        cash = self.ledger.cash
        positions: dict[str, float] = {}
        marks = {(m["asset"], m["location"]): m for m in self.db.query("SELECT * FROM dmh_marks WHERE account_id = ?", (self.ledger.account_id,))}
        for h in self.ledger.holdings():
            m = marks.get((h["asset"], h["location"]))
            val = m["value_eur"] if m else h["cost_eur"]
            a = self.logical(h["asset"])
            if a not in A.STABLECOINS:
                positions[a] = positions.get(a, 0.0) + val
        equity = None
        if isinstance(cash, MainAccountCash):
            b = cash._broker()
            equity = float(b.equity()[0])
            for asset, qty in b.balances().items():
                if asset not in (b.quote, "USDC", "USDT", "EUR") and qty > 0:
                    px = b.price_fn(f"{asset}/{b.trade_quote}")
                    rate = b.fx_rate(f"{asset}/{b.trade_quote}") if px else None
                    if px and rate:
                        positions[asset] = positions.get(asset, 0.0) + qty * px * rate
        if equity is None:
            equity = cash.total() + self.ledger.liquidation_value()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d") if self.clock is time.time else \
            datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
        ds = self.ledger.state("day_start") or {}
        if ds.get("day") != day:
            ds = {"day": day, "equity": equity}
            self.ledger.set_state("day_start", ds)
        peak = max(float(self.ledger.state("peak_equity") or equity), equity)
        self.ledger.set_state("peak_equity", peak)
        busy = tuple(sorted({self.logical(o["asset"]) for o in self.db.query(
            "SELECT asset FROM dmh_operations WHERE account_id = ? AND state IN ('RESERVED','OPEN','SUBMITTED')", (self.ledger.account_id,))}))
        recent = self.db.scalar("SELECT COUNT(*) FROM dmh_operations WHERE account_id = ? AND state != 'REJECTED' AND created_ms > ?",
                                (self.ledger.account_id, self.ledger.now_ms() - 60_000), 0)
        return Portfolio(cash.total(), cash.available(), positions, equity, float(ds["equity"]), peak,
                         ABSOLUTE["capital_eur"], busy, int(recent))

    # -- prévisualisation ---------------------------------------------------------------------------------------------
    def _intent(self, body: dict) -> tuple[str, Intent | None, dict]:
        side = body.get("side")
        if side not in ("buy", "sell"):
            raise HubError("sens attendu : buy (achat) ou sell (vente d'une position détenue)")
        edge = body.get("edge_bps")
        if edge is not None and (isinstance(edge, bool) or not isinstance(edge, (int, float)) or not -10_000 < edge < 10_000):
            raise HubError("avantage brut annoncé invalide")
        if side == "buy":
            asset = str(body.get("asset", "")).upper()
            if asset not in ASSETS:
                raise HubError(f"actif non pris en charge : {asset or '—'}")
            n = body.get("notional_eur")
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 < n <= ABSOLUTE["max_position_eur"]:
                raise HubError(f"montant en euros attendu, entre 0 et {ABSOLUTE['max_position_eur']:.0f} €")
            nets = tuple(x for x in body.get("networks", ("solana", "base", "ethereum")) if x in ("solana", "base", "ethereum"))
            return side, Intent(asset, float(n), None if edge is None else float(edge), nets, bool(body.get("include_limit", True)),
                                bool(body.get("prefer_limit", False))), {}
        h = self.ledger.holding(str(body.get("holding_asset", "")), str(body.get("holding_location", "")))
        if h is None or h["qty"] <= 0:
            raise HubError("position introuvable : seule une position détenue peut être vendue (pas de vente à découvert)")
        frac = float(body.get("fraction", 1.0))
        if not 0 < frac <= 1:
            raise HubError("fraction à vendre entre 0 et 1")
        return side, None, {"holding": h, "fraction": frac, "edge_bps": edge}

    def _exit_routes(self, view: MarketView, h: dict, fraction: float) -> list[Route]:
        qty = self.ledger.available_holding(h["asset"], h["location"]) * fraction
        loc, asset = h["location"], h["asset"]
        routes: list[Route] = []
        if loc.startswith("cex:"):
            venue = loc.split(":", 1)[1]
            for pair in (f"{asset}/EUR",):
                try:
                    leg = cex_leg(view, venue, pair, "sell", base_qty=qty, asset_in=asset, loc_in=loc, asset_out="EUR", loc_out="cash",
                                  eur_per_quote=1.0, cls=A.asset_class(asset), exit_leg=False)
                    routes.append(Route(f"exit:{venue}:{pair}", f"vente sur {venue} ({pair})", "exit", leg.eur_in, [leg], []))
                except (RouteError, SourceUnavailable, KeyError) as exc:
                    routes.append(Route(f"exit:{venue}:{pair}", f"vente sur {venue} ({pair})", "exit", max(h["cost_eur"], 1e-9), [], [],
                                        reasons=[f"route impossible : {exc}"]))
        elif loc.startswith("chain:"):
            network = loc.split(":", 1)[1]
            tok = TokenId.parse(asset)
            usdc = self.registry.by_asset("USDC", network)
            try:
                a_eur, _ = view.eur_reference(self.logical(asset))
                u_eur, _ = view.eur_reference("USDC")
                if a_eur is None or u_eur is None or usdc is None:
                    raise SourceUnavailable("référence", "prix de référence indisponible")
                s = dex_leg(view, self.registry, network, tok, usdc.id, qty, eur_in_per_unit=a_eur, eur_out_per_unit=u_eur, approved=False,
                            has_token_account=True)
                s.side = "sell"
                routes.append(Route(f"exit-chain:{network}", f"échange vers USDC sur {network}", "exit", s.eur_in, [s], []))
            except (RouteError, SourceUnavailable, KeyError, ValueError) as exc:
                routes.append(Route(f"exit-chain:{network}", f"échange vers USDC sur {network}", "exit", max(h["cost_eur"], 1e-9), [], [],
                                    reasons=[f"route impossible : {exc}"]))
        return routes

    def preview(self, body: dict, origin: str = "user") -> dict:
        inspect_request(body, "prévisualisation d'un ordre fictif", db=self.db)
        check_runtime()
        self.ledger.check_open()
        side, intent, extra = self._intent(body)
        view = self.view_factory()
        lim = self.limits()
        if side == "buy":
            routes = build_routes(view, self.registry, intent, ledger=self.ledger)
            choice = choose(routes, intent.edge_bps, max_cost_bps=lim["max_round_trip_cost_bps"], allow_limit=intent.prefer_limit,
                            max_immobilized_pct=lim["max_immobilized_pct"])
            edge = intent.edge_bps
        else:
            routes = self._exit_routes(view, extra["holding"], extra["fraction"])
            for r in routes:
                if not r.reasons and extra["holding"]["final_ms"] > view.now_ms():
                    r.reasons.append("position en attente de finalité sur la chaîne : vente impossible pour l'instant")
            ok = [r for r in routes if not r.reasons]
            ok.sort(key=lambda r: r.totals()["cost_eur"])
            from .router import Choice
            choice = Choice("ROUTE" if ok else "ABSTENTION", ok[0] if ok else None, ok + [r for r in routes if r.reasons],
                            [] if ok else ["aucune route de sortie disponible"], [])
            edge = None
        verdict = Verdict()
        chosen = choice.route.to_dict(edge) if choice.route else None
        if chosen and view.mode == "prospective" and hasattr(view, "refresh"):
            # la comparaison a pris du temps : la route retenue est relue juste avant le contrôle de sécurité
            view.refresh()
            try:
                chosen = self._rebuild(view, {"side": side, "body": body, "route": chosen, "edge_bps": edge})
            except (RouteError, SourceUnavailable, KeyError, ValueError) as exc:
                verdict.veto("relecture", f"route retenue impossible à relire : {exc}", "technique")
        risks = self._token_risks(chosen)
        if chosen:
            pf = self.portfolio()
            if side == "buy":
                _, cash_needed = reservation(chosen)
                check_financial(verdict, lim, pf, side="buy", asset=intent.asset, notional=chosen["notional_eur"],
                                route_totals=chosen.get("totals"), cash_needed=cash_needed)
            else:
                h = extra["holding"]
                check_financial(verdict, lim, pf, side="sell", asset=self.logical(h["asset"]), notional=chosen["notional_eur"],
                                route_totals=None, held_qty_value=chosen["notional_eur"])
            check_route(verdict, lim, chosen, view.now_ms(), self.registry, risks)
            check_clock(verdict, lim, self._clock_offsets(view))
        money = None
        if chosen and chosen.get("totals"):
            t = chosen["totals"]
            pf_now = self.portfolio()
            spend = reservation(chosen)[1] if side == "buy" else 0.0
            money = {"fees_paid_eur": t["fees_paid_eur"], "immobilized_eur": t["immobilized_eur"], "immobilized_pct": t["immobilized_pct"],
                     "risk_eur": t["risk_eur"], "unverified_fees_eur": t["unverified_fees_eur"],
                     "cash_available_now_eur": pf_now.cash_available, "cash_available_after_eur": pf_now.cash_available - spend,
                     "note": "frais réellement payés, capital immobilisé (reliquats, provision, dépôts) et risques sont séparés"}
        data = {"label": LABEL, "mode": view.mode, "side": side, "origin": origin, "body": body, "decision": choice.decision,
                "route": chosen, "alternatives": [r.to_dict(edge) for r in choice.ranked if r is not choice.route][:12],
                "choice_reasons": choice.reasons, "sensitivity": choice.sensitivity, "verdict": verdict.to_dict(),
                "fee_dependent": choice.fee_dependent, "scenario_winners": choice.scenario_winners, "money": money,
                "token_risks": risks, "config_hash": self.config_hash(), "assumptions": A.VERSION, "edge_bps": edge,
                "explanation": self._explain(choice, verdict, edge)}
        pid = self.ledger.save_preview(data, int(lim["preview_ttl_ms"]))
        return self.ledger.preview(pid)

    def _clock_offsets(self, view: MarketView) -> list[float]:
        if view.mode != "prospective":
            return []
        out = []
        for name in ("binance", "coinbase"):
            try:
                st = view.cx[name].server_time_ms()
                out.append(st[0] - (st[1] - st[2] / 2))
            except Exception:
                continue
        return out

    def _token_risks(self, route: dict | None) -> dict:
        if not route or self.risk is None:
            return {}
        keys = {l["data"].get(k) for l in route["entry"] + (route.get("exit") or []) if l["kind"] == "dex" for k in ("token_in", "token_out")}
        out = {}
        for k in sorted(x for x in keys if x):
            try:
                out[k] = self.risk.assess(TokenId.parse(k))
            except Exception as exc:  # une évaluation en échec n'est jamais un feu vert
                out[k] = {"status": "UNSAFE", "flags": [f"évaluation impossible ({exc.__class__.__name__})"]}
        return out

    @staticmethod
    def _explain(choice, verdict: Verdict, edge) -> str:
        if choice.route is None:
            return "ABSTENTION : " + " ; ".join(choice.reasons or ["aucune route"])
        t = choice.route.totals()
        parts = [f"Route retenue : {choice.route.label}. Coût aller-retour estimé {t['cost_eur']:.4f} € ({t['cost_bps']:.1f} pb, "
                 f"fourchette {t['cost_low_bps']:.1f}–{t['cost_high_bps']:.1f} pb), niveau de preuve le plus faible : {t['level']}."]
        if edge is not None:
            n = choice.route.net(edge)
            parts.append(f"Avec l'avantage brut ANNONCÉ de {edge:.0f} pb (hypothèse, non démontrée), résultat net attendu "
                         f"{n['net_eur']:+.4f} € ; au pire des coûts {n['net_pessimistic_eur']:+.4f} €.")
        if choice.decision == "ABSTENTION":
            parts.append("ABSTENTION : " + " ; ".join(choice.reasons))
        if not verdict.allowed:
            parts.append("REFUS de sécurité : " + " ; ".join(r.message for r in verdict.reasons if r.veto))
        parts += choice.sensitivity
        return " ".join(parts)

    # -- exécution ------------------------------------------------------------------------------------------------------
    def execute(self, body: dict, origin: str = "user") -> dict:
        inspect_request(body, "exécution d'un ordre fictif", db=self.db)
        if body.get("confirm") is not True:
            raise HubError("confirmation explicite requise (simulation, argent fictif)")
        rid = str(body.get("request_id", ""))
        if not _REQ.fullmatch(rid):
            raise HubError("identifiant de demande invalide (8 à 64 caractères : lettres, chiffres, - et _)")
        with self._lock:
            existing = self.ledger.operation(rid)
            if existing is not None:
                return existing | {"replayed": True}
            self.ledger.check_open()
            pv = self.ledger.preview(str(body.get("preview_id", "")))
            if pv is None:
                raise HubError("prévisualisation introuvable : en demander une nouvelle")
            side = pv["side"]
            asset = pv["body"].get("asset") or pv["body"].get("holding_asset", "?")
            refuse = lambda reasons: self.ledger.reject(rid, origin=origin, side=side, asset=str(asset), intent=pv["body"],  # noqa: E731
                                                        reasons=reasons, config_hash=pv["config_hash"], evidence_mode=pv["mode"])
            if self.ledger.now_ms() > pv["expires_ms"]:
                return refuse(["prévisualisation expirée : prix et coûts à revoir"])
            if pv["config_hash"] != self.config_hash():
                return refuse(["réglages modifiés pendant la décision : nouvelle prévisualisation requise"])
            if pv["decision"] != "ROUTE" or not pv["verdict"]["allowed"]:
                return refuse(["la prévisualisation concluait à l'abstention ou au refus"] + [r["message"] for r in pv["verdict"]["vetoes"]])
            view = self.view_factory()
            if hasattr(view, "refresh"):
                view.refresh()
            try:
                fresh = self._rebuild(view, pv)
            except (RouteError, SourceUnavailable, KeyError, ValueError) as exc:
                return refuse([f"nouveau contrôle impossible à l'exécution : {exc}"])
            lim = self.limits()
            v = Verdict()
            check_execution_drift(v, lim, pv["route"], fresh)
            check_route(v, lim, fresh, view.now_ms(), self.registry, self._token_risks(fresh))
            pf = self.portfolio()
            if side == "buy":
                res, cash_needed = reservation(fresh)
                check_financial(v, lim, pf, side="buy", asset=pv["body"]["asset"].upper(), notional=fresh["notional_eur"],
                                route_totals=fresh.get("totals"), cash_needed=cash_needed)
                if res[0] != "EUR" and res[2] > self.ledger.available_holding(res[0], res[1]) * (1 + 1e-9):
                    v.veto("capital", f"solde {res[0]} disponible insuffisant ({res[1]})", "financier")
            else:
                h = self.ledger.holding(pv["body"]["holding_asset"], pv["body"]["holding_location"])
                qty = fresh["entry"][0]["qty_in"] if fresh["entry"] else 0.0
                avail = self.ledger.available_holding(h["asset"], h["location"]) if h else 0.0
                if h is None or qty > avail * (1 + 1e-9):
                    v.veto("vente_a_decouvert", "quantité à vendre supérieure à la position disponible", "financier")
                res = (pv["body"]["holding_asset"], pv["body"]["holding_location"], max(qty, 1e-18))
            if not v.allowed:
                return refuse([r.message for r in v.reasons if r.veto])
            op = self.ledger.open_operation(rid, origin=origin, side=side, asset=str(asset), intent=pv["body"], route=fresh,
                                            verdict=v.to_dict(), config_hash=pv["config_hash"], evidence_mode=view.mode, reserve=res)
            if op.get("replayed"):
                return op
            return self._simulate_and_settle(rid, pv, fresh, view)

    def _rebuild(self, view: MarketView, pv: dict) -> dict:
        rid = pv["route"]["id"]
        if pv["side"] == "buy":
            b = pv["body"]
            intent = Intent(b["asset"].upper(), float(b["notional_eur"]), pv["edge_bps"], tuple(b.get("networks", ("solana", "base", "ethereum"))),
                            bool(b.get("include_limit", True)), bool(b.get("prefer_limit", False)))
            routes = [r for r in build_routes(view, self.registry, intent, ledger=self.ledger, only=rid) if r.id == rid]
        else:
            h = self.ledger.holding(pv["body"]["holding_asset"], pv["body"]["holding_location"])
            if h is None:
                raise RouteError("position disparue")
            routes = [r for r in self._exit_routes(view, h, float(pv["body"].get("fraction", 1.0))) if r.id == rid]
        if not routes or routes[0].reasons:
            raise RouteError(routes[0].reasons[0] if routes and routes[0].reasons else "route introuvable à l'exécution")
        return routes[0].to_dict(pv["edge_bps"])

    def _simulate_and_settle(self, rid: str, pv: dict, fresh: dict, view: MarketView, fail_after: str | None = None) -> dict:
        ex = PaperExecutor(view, self.ledger, limits=self.limits())
        try:
            out = ex.run(rid, pv["route"], fresh, which="entry")
        except Exception as exc:  # panne pendant la simulation : rien n'a été réglé
            log.warning("Hub : simulation interrompue (%s)", exc)
            return self.ledger.settle(rid, "NOT_FILLED", [], {"notes": [f"simulation interrompue : {exc.__class__.__name__}"]})
        result = {"notes": out.notes, "label": LABEL, "mode": ex.mode}
        if out.state == "OPEN":
            limit = None
            for leg in fresh["entry"]:
                if leg.get("data", {}).get("order_type") == "limit":
                    limit = PaperExecutor._limit_open(ex, leg)["limit"]
            return self.ledger.mark(rid, "OPEN", result | {"limit": limit})
        if out.sim_tx is not None:
            self.ledger.record_sim_tx(rid, out.sim_tx["network"], out.sim_tx["final_ms"], out.sim_tx["fee_eur"],
                                      {"state": out.state, "fills": out.fills, "result": result, "approvals": out.approvals})
        op = self.ledger.settle(rid, out.state, out.fills, result, approvals=out.approvals, fail_after=fail_after,
                                sim_tx={"tx_id": self.ledger.sim_tx(rid)["tx_id"], "status": "CONFIRMED"} if out.sim_tx else None)
        self._mark(view)
        return op

    # -- ordres ouverts, reprise, finalité ------------------------------------------------------------------------------
    def refresh_open(self) -> list[dict]:
        out = []
        view = self.view_factory()
        ex = PaperExecutor(view, self.ledger, limits=self.limits())
        for op in self.db.query("SELECT id FROM dmh_operations WHERE account_id = ? AND state = 'OPEN'", (self.ledger.account_id,)):
            o = self.ledger.operation(op["id"])
            lim = (o["result"] or {}).get("limit") or {}
            try:
                trades = view.trades(lim["venue"], lim["pair"], lim["placed_ms"])
            except (SourceUnavailable, KeyError) as exc:
                out.append({"id": o["id"], "state": "OPEN", "note": f"transactions indisponibles : {exc}"})
                continue
            res = ex.evaluate_limit(o, trades, view.now_ms())
            if res.state == "OPEN":
                out.append({"id": o["id"], "state": "OPEN", "note": res.notes[0]})
            elif res.state == "EXPIRED":
                out.append(self.ledger.mark(o["id"], "EXPIRED", (o["result"] or {}) | {"notes": res.notes}))
            else:
                out.append(self.ledger.settle(o["id"], res.state, res.fills, (o["result"] or {}) | {"notes": res.notes}))
        return out

    def recover(self) -> dict:
        """Reprise au démarrage : jamais de nouvel essai aveugle, jamais de doublon."""
        done = {"abandoned": 0, "settled_from_trace": 0, "expired": 0}
        for row in self.db.query("SELECT id, state FROM dmh_operations WHERE account_id = ? AND state IN ('RESERVED','SUBMITTED','OPEN')",
                                 (self.ledger.account_id,)):
            op = self.ledger.operation(row["id"])
            tx = self.ledger.sim_tx(row["id"])
            if row["state"] == "RESERVED" and tx is None:
                self.ledger.mark(row["id"], "ABANDONED", {"notes": ["interrompue avant toute exécution simulée : réservation libérée, "
                                                                    "non retentée (nouvelle demande nécessaire)"]})
                done["abandoned"] += 1
            elif tx is not None and op["state"] in ("RESERVED", "SUBMITTED"):
                d = tx["data"]
                self.ledger.settle(row["id"], d["state"], d["fills"], d["result"] | {"recovered": True}, approvals=[tuple(a) for a in d.get("approvals", [])],
                                   sim_tx={"tx_id": tx["tx_id"], "status": "CONFIRMED"})
                done["settled_from_trace"] += 1
            elif row["state"] == "OPEN":
                lim = (op["result"] or {}).get("limit") or {}
                if lim.get("expires_ms", 0) <= self.ledger.now_ms():
                    self.ledger.mark(row["id"], "EXPIRED", (op["result"] or {}) | {"notes": ["expiré pendant l'arrêt"]})
                    done["expired"] += 1
        if any(done.values()):
            with self.db.transaction():
                self.ledger.journal(None, "reprise_apres_arret", done)
        return done

    def finalize(self) -> int:
        n = 0
        now = self.ledger.now_ms()
        for tx in self.db.query("SELECT * FROM dmh_sim_tx WHERE status = 'CONFIRMED' AND final_ms <= ?", (now,)):
            self.db.execute("UPDATE dmh_sim_tx SET status = 'FINAL' WHERE tx_id = ?", (tx["tx_id"],))
            n += 1
        return n

    def simulate_reorg(self, op_id: str) -> dict:
        """Réorganisation simulée AVANT finalité : l'échange est annulé par compensation (le gaz reste payé)."""
        tx = self.ledger.sim_tx(op_id)
        if tx is None or tx["status"] not in ("CONFIRMED", "PENDING"):
            raise HubError("aucune transaction simulée non finale pour cette opération")
        if tx["final_ms"] <= self.ledger.now_ms():
            raise HubError("transaction déjà finale : plus de réorganisation possible")
        comp_id = f"{op_id}-reorg"
        swaps = [f for f in self.ledger.fills(op_id) if f["side"] == "buy" and f["location"].startswith("chain:")]
        fills = [{"leg": i, "venue": "réorganisation simulée", "side": "sell", "asset": f["asset"], "location": f["location"],
                  "qty": f["qty"], "counter_asset": f["counter_asset"], "counter_location": f["counter_location"],
                  "counter_qty": f["counter_qty"], "eur": f["eur"], "evidence": {"reorg_of": op_id}, "mode": f["mode"]}
                 for i, f in enumerate(swaps)]
        with self.db.transaction():
            op = self.ledger.operation(op_id)
            self.ledger.open_operation(comp_id, origin="system", side="sell", asset=op["asset"], intent={"reorg_of": op_id}, route={},
                                       verdict={}, config_hash=op["config_hash"], evidence_mode=op["evidence_mode"],
                                       reserve=(swaps[0]["asset"], swaps[0]["location"], swaps[0]["qty"]) if swaps else ("EUR", "cash", 1e-9))
            self.ledger.settle(comp_id, "FILLED", fills, {"notes": ["compensation d'une réorganisation simulée"]})
            self.db.execute("UPDATE dmh_sim_tx SET status = 'REORGED' WHERE tx_id = ?", (tx["tx_id"],))
            self.ledger.journal(op_id, "reorganisation", {"tx_id": tx["tx_id"], "compensation": comp_id})
        return self.ledger.operation(comp_id)

    # -- valorisation prudente ------------------------------------------------------------------------------------------
    def _mark(self, view: MarketView) -> None:
        for h in self.ledger.holdings():
            try:
                if h["location"].startswith("cex:"):
                    venue = h["location"].split(":", 1)[1]
                    pair = f"{h['asset']}/EUR"
                    b = view.book(venue, pair)
                    fee = view.fees(venue)["taker"].value / 1e4
                    val = h["qty"] * b.bid * (1 - fee)
                    method = f"meilleur achat {venue} moins commission"
                else:
                    px, _ = view.eur_reference(self.logical(h["asset"]))
                    if px is None:
                        continue
                    val = h["qty"] * px * 0.99
                    method = "référence des plateformes moins 1 % (sortie sur la chaîne)"
                self.ledger.set_mark(h["asset"], h["location"], val, method)
            except (SourceUnavailable, KeyError, ValueError):
                continue

    # -- arrêt d'urgence -----------------------------------------------------------------------------------------------
    def halt(self, reason: str, origin: str = "user") -> dict:
        return self.ledger.halt(reason or "arrêt immédiat des nouvelles opérations fictives", origin)

    def resume(self, origin: str = "user") -> dict:
        if origin != "user":
            raise SecurityRefusal("seul l'utilisateur peut relancer les opérations fictives")
        self.ledger.resume(origin)
        return self.status()

    # -- lectures pour le panel ------------------------------------------------------------------------------------------
    def status(self) -> dict:
        pf = None
        try:
            pf = self.portfolio()
        except Exception as exc:
            pf_err = str(exc)
        return {"label": LABEL, "version": VERSION, "paper_only": True, "can_sign": False, "can_broadcast": False,
                "halted": self.ledger.state("halted"), "desync": self.ledger.state("desync"), "limits": self.limits(),
                "absolute": dict(ABSOLUTE), "assumptions": {"version": A.VERSION, "sha256": A.digest()},
                "registry": {"tokens": len(self.registry), **{k: v for k, v in self.registry.meta.items() if k != "tokens"}},
                "portfolio": None if pf is None else {"cash_total": pf.cash_total, "cash_available": pf.cash_available,
                                                      "equity": pf.equity, "positions": pf.positions, "initial": pf.initial},
                "portfolio_error": None if pf is not None else pf_err, "recovered": self.recovered,
                "network": self.client.health(), "assets": list(ASSETS)}

    def market_board(self, assets=("BTC", "ETH", "SOL")) -> dict:
        view = self.view_factory()
        now = view.now_ms()
        rows = []
        for asset in assets:
            pair = f"{asset}/EUR"
            books = []
            for v in view.venues():
                if not view.supports(v, pair):
                    continue
                try:
                    b = view.book(v, pair)
                    books.append(b)
                    rows.append({"asset": asset, "venue": v, "pair": pair, "status": "DISPONIBLE" if b.fresh(now) else "PÉRIMÉ",
                                 "bid": b.bid, "ask": b.ask, "half_spread_bps": b.half_spread_bps, "depth_eur_10": b.depth_quote("buy"),
                                 "age_ms": b.age_ms(now), "sha256": b.sha256, "fee_taker_bps": view.fees(v)["taker"].value,
                                 "fee_level": view.fees(v)["taker"].level})
                except SourceUnavailable as exc:
                    rows.append({"asset": asset, "venue": v, "pair": pair, "status": "INDISPONIBLE", "reason": str(exc)[:160]})
            c = consensus(pair, books, now)
            for r in rows:
                if r["asset"] == asset and r["venue"] in c.outliers:
                    r["status"] = "ÉCARTÉ (incohérent)"
            rows.append({"asset": asset, "venue": "consensus", "pair": pair, "status": c.status, "mid": c.median_mid,
                         "deviations_bps": c.to_dict()["deviations_bps"]})
        nets = {}
        for network in ("base", "ethereum"):
            try:
                g = view.gas(network)
                nets[network] = {"status": "DISPONIBLE", "base_fee_gwei": g.base_fee_wei / 1e9, "priority_p50_gwei": g.priority_p50_wei / 1e9,
                                 "l1_fee_800b_eth": g.l1_fee_wei_800b / 1e18, "block": g.block}
            except SourceUnavailable as exc:
                nets[network] = {"status": "INDISPONIBLE", "reason": str(exc)[:160]}
        try:
            s = view.solana()
            nets["solana"] = {"status": "DISPONIBLE", "priority_p50": s.priority_micro_lamports_p50, "priority_p90": s.priority_micro_lamports_p90,
                              "rent_token_account_sol": s.rent_token_account_lamports / 1e9}
        except SourceUnavailable as exc:
            nets["solana"] = {"status": "INDISPONIBLE", "reason": str(exc)[:160]}
        return {"label": LABEL, "mode": view.mode, "ts_ms": now, "books": rows, "networks": nets, "health": self.client.health()}

    def positions(self) -> dict:
        """Positions avec, SÉPARÉMENT : part revendable, reliquat (valeur immobilisée), frais payés et capital disponible."""
        vals = {(v["asset"], v["location"]): v for v in self.ledger.valuation()}
        rows, dust_total = [], 0.0
        for h in self.ledger.holdings():
            v = vals.get((h["asset"], h["location"]), {})
            step = float(h.get("step") or 0.0)
            sellable = math.floor(h["qty"] / step + 1e-9) * step if step > 0 else h["qty"]
            dust_qty = max(0.0, h["qty"] - sellable)
            unit = (v.get("value_eur", h["cost_eur"]) / h["qty"]) if h["qty"] > 0 else 0.0
            dust_eur = dust_qty * unit
            dust_total += dust_eur
            rows.append(h | {"logical": self.logical(h["asset"]), "value_eur": v.get("value_eur"), "valuation_method": v.get("method"),
                             "valuation_stale": v.get("stale"), "mark_ts_ms": v.get("mark_ts_ms"),
                             "unrealized_eur": (v["value_eur"] - h["cost_eur"]) if v else None, "sellable_qty": sellable,
                             "dust_qty": dust_qty, "dust_eur": dust_eur,
                             "final": h["final_ms"] <= self.ledger.now_ms(), "available": self.ledger.available_holding(h["asset"], h["location"])})
        fees = 0.0
        for f in self.ledger.fills():
            c = f["costs"] or {}
            fees += sum(float(c.get(k, 0) or 0) for k in ("commission_eur", "gaz_eur", "frais_eur"))
        return {"label": LABEL, "positions": rows, "cash_total": self.ledger.cash.total(), "cash_available": self.ledger.cash.available(),
                "fees_paid_eur": fees, "dust_value_eur": dust_total,
                "note": "frais payés, valeur des reliquats (immobilisée, invendable seule) et capital disponible sont distincts"}

    def history(self, limit: int = 100) -> dict:
        ops = self.ledger.operations(limit)
        for o in ops:
            o["fills"] = self.ledger.fills(o["id"])
        return {"label": LABEL, "operations": ops}

    def observatory(self) -> dict:
        ops = self.ledger.operations(10_000)
        fills = self.ledger.fills()
        by_state: dict[str, int] = {}
        rejections: dict[str, int] = {}
        for o in ops:
            by_state[o["state"]] = by_state.get(o["state"], 0) + 1
            if o["state"] == "REJECTED":
                for r in (o["result"] or {}).get("reasons", []):
                    key = str(r)[:60]
                    rejections[key] = rejections.get(key, 0) + 1
        fees = sum(float((f["costs"] or {}).get("commission_eur", 0) or 0) + float((f["costs"] or {}).get("gaz_eur", 0) or 0)
                   + float((f["costs"] or {}).get("frais_eur", 0) or 0) for f in fills)
        est, val = [], []
        for o in ops:
            r = o.get("route") or {}
            t = r.get("totals")
            if not t or o["side"] != "buy":
                continue
            n = r["notional_eur"]
            pair_eur = r["kind"] == "cex_direct"
            ref_rt = 2 * (REFERENCE_ENGINE["fee_bps"] + REFERENCE_ENGINE["slippage_bps"] + (0 if pair_eur else REFERENCE_ENGINE["fx_bps"]))
            est.append({"op": o["id"], "reference_bps": ref_rt, "route_bps": t["cost_bps"], "saving_eur": (ref_rt - t["cost_bps"]) / 1e4 * n})
            if o["state"] in ("FILLED", "PARTIAL"):
                for f in self.ledger.fills(o["id"]):
                    c = f["costs"] or {}
                    if f["side"] == "buy" and c.get("mid") and c.get("avg") and f["counter_asset"] == "EUR":
                        actual = f["counter_qty"] - f["qty"] * c["mid"]  # coût réel simulé de l'entrée (écart + commission), en euros
                        ref = f["counter_qty"] * (REFERENCE_ENGINE["fee_bps"] + REFERENCE_ENGINE["slippage_bps"]) / 1e4
                        val.append({"op": o["id"], "actual_entry_eur": actual, "reference_entry_eur": ref, "saving_eur": ref - actual})
        pf = None
        try:
            pf = self.portfolio()
        except Exception:
            pass
        return {"label": LABEL, "operations": by_state, "rejections": rejections, "fees_paid_eur": fees,
                "net_performance_eur": None if pf is None else pf.equity - pf.initial,
                "savings_estimated": {"items": est[-50:], "total_eur": sum(x["saving_eur"] for x in est),
                                      "note": "face aux réglages du moteur existant ; une estimation n'est pas une économie réalisée"},
                "savings_validated": {"items": val[-50:], "total_eur": sum(x["saving_eur"] for x in val),
                                      "note": "seulement des entrées réellement exécutées en simulation ; la sortie n'est validée qu'à la vente"},
                "data_quality": self.client.health(), "reference_engine": REFERENCE_ENGINE,
                "errors": self.db.query("SELECT ts_ms, kind, data FROM dmh_journal WHERE kind IN ('desynchronisation','reprise_apres_arret',"
                                        "'reorganisation') ORDER BY seq DESC LIMIT 20")}

    def strategy(self, message: str, origin: str = "user") -> dict:
        from .llm import draft_strategy
        url = self.llm_cfg.get("url") or "http://127.0.0.1:11434"
        model = self.llm_cfg.get("model") or ""
        if not model:
            return {"available": False, "error": "aucun modèle local configuré (réglages : actualités → modèle Ollama)", "draft": None}
        return draft_strategy(message, url=url, model=model, assets=set(ASSETS), max_capital=self.limits()["max_position_eur"],
                              transport=self.llm_cfg.get("transport"))
