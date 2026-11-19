"""Simulateur d'exécution FICTIVE du hub (aucun ordre, aucune transaction n'est jamais envoyé).

- ordre au marché : exécuté sur le carnet RELU au moment de l'exécution, avec un prix limite de protection ;
  si la profondeur manque, exécution partielle, le reste n'est pas exécuté ;
- ordre à cours limité : reste ouvert ; il n'est rempli que si des transactions publiques ont eu lieu AU-DELÀ de
  son prix (règle prudente de COST OPTIMIZER) ; un simple contact du prix ne remplit rien ; expiré : annulé ;
- échange sur la chaîne : nouveau devis à l'exécution ; s'il est sous le minimum accepté (tolérance), la
  transaction simulée échoue ET le gaz est payé ; le jeton de gaz doit être présent sur la chaîne ;
  la position n'est revendable qu'après la finalité simulée ; une réorganisation avant finalité est annulée
  par des écritures de compensation (rien n'est effacé) ;
- retrait / dépôt : quantité diminuée des frais, disponible après la finalité du réseau ;
- reprise après panne : une opération réservée sans trace d'exécution est ABANDONNÉE (réservation libérée,
  jamais retentée à l'aveugle) ; une opération dont la transaction simulée est enregistrée est réglée d'après
  cette trace, une seule fois.

Aucun gain n'est enregistré sans exécution démontrée : une exécution vient toujours d'un carnet, d'un devis ou
de transactions publiques datés, et porte le mode « prospectif » (observé maintenant) ou « reconstitué » (rejeu).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import assumptions as A
from .cex import Trade
from .ledger import Ledger, LedgerError
from .net import SourceUnavailable
from .router import walk
from .tokens import TokenId
from .view import MarketView

KIND = "paper"


class ExecutionRefused(ValueError):
    def __init__(self, message: str, code: str = "dmh_execution"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Outcome:
    state: str  # FILLED | PARTIAL | NOT_FILLED | FAILED | OPEN
    fills: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sim_tx: dict | None = None
    approvals: list[tuple[str, str, str]] = field(default_factory=list)
    final_ms: int = 0


class PaperExecutor:
    kind = KIND

    def __init__(self, view: MarketView, ledger: Ledger, *, limits: dict):
        self.view = view
        self.ledger = ledger
        self.limits = limits

    @property
    def mode(self) -> str:
        return "prospective" if self.view.mode == "prospective" else "reconstitue"

    # -- exécution d'une route complète --------------------------------------------------------------------------
    def run(self, op_id: str, route: dict, fresh: dict, *, which: str = "entry") -> Outcome:
        """Simule les étapes d'entrée (`which="entry"`) ou de sortie de `fresh` (route recalculée à l'exécution).

        Pour une vente d'une position détenue, la route n'a que des étapes d'« entrée » (les ventes elles-mêmes).
        """
        from .guard import check_runtime
        check_runtime(self)
        if which not in ("entry", "exit"):
            raise ExecutionRefused("étapes à simuler inconnues")
        legs = fresh[which] or []
        out = Outcome("FILLED")
        carried: dict[str, float] = {}  # quantités réellement obtenues par les étapes précédentes, par actif
        leg_no = 0
        for i, leg in enumerate(legs):
            try:
                res = self._leg(op_id, leg, carried, leg_no)
            except (ExecutionRefused, SourceUnavailable, LedgerError) as exc:
                out.notes.append(f"étape {i + 1} « {leg['step']} » non exécutée : {getattr(exc, 'message', str(exc))}")
                out.state = "PARTIAL" if out.fills else "NOT_FILLED"
                break
            out.fills += res["fills"]
            leg_no += len(res["fills"])
            out.approvals += res.get("approvals", [])
            out.final_ms = max(out.final_ms, res.get("final_ms", 0))
            if res.get("sim_tx"):
                out.sim_tx = res["sim_tx"]
            if res["status"] == "OPEN":
                out.state = "OPEN"
                out.notes.append("ordre à cours limité ouvert : rempli seulement si le prix est franchi")
                break
            if res["status"] == "FAILED":
                out.state = "FAILED" if not any(f["side"] != "fee" for f in out.fills) else "PARTIAL"
                out.notes.append(res.get("note", "transaction simulée échouée"))
                break
            if res["status"] == "PARTIAL":
                out.state = "PARTIAL"
                out.notes.append(res.get("note", "exécution partielle"))
            got = res.get("carried")
            if got is None or got["qty"] <= 0:
                if i < len(legs) - 1:
                    out.state = "PARTIAL" if out.fills else "NOT_FILLED"
                    out.notes.append("quantité nulle après l'étape : route interrompue")
                break
            carried[got["asset"]] = carried.get(got["asset"], 0.0) + got["qty"]
            if leg["asset_in"] in carried and leg["asset_in"] != got["asset"]:
                carried.pop(leg["asset_in"], None)  # consommé par cette étape
        return out

    def _leg(self, op_id: str, leg: dict, carried: dict[str, float], leg_no: int) -> dict:
        kind = leg["kind"]
        if kind == "cex":
            if leg.get("data", {}).get("order_type") == "limit":
                return self._limit_open(leg)
            return self._cex_market(leg, carried, leg_no, op_id)
        if kind == "transfer":
            return self._transfer(leg, carried, leg_no)
        if kind == "dex":
            return self._dex(op_id, leg, carried, leg_no)
        raise ExecutionRefused(f"type d'étape inconnu : {kind}")

    def _cex_market(self, leg: dict, carried: dict[str, float], leg_no: int, op_id: str | None = None) -> dict:
        d = leg["data"]
        venue, pair, side = d["venue"], d["pair"], d["side"]
        book = self.view.book(venue, pair)
        now = self.view.now_ms()
        if not book.fresh(now, int(self.limits["max_book_age_ms"])):
            raise ExecutionRefused(f"carnet {venue} {pair} périmé au moment de l'exécution")
        rules = self.view.rules(venue, pair)
        if not rules.trading:
            raise ExecutionRefused(f"{pair} suspendu sur {venue}")
        fee = float(d["fee"])
        move = self.limits["max_price_move_bps"] / 1e4
        base, quote = pair.split("/")
        have = carried.get(leg["asset_in"])
        if side == "buy":
            budget = have if have is not None else leg["qty_in"]
            limit_px = book.mid * (1 + move)
            q_est, _, _, _ = walk(book, "buy", quote=budget, price_limit=limit_px)
            qty = rules.floor_qty(q_est)
            got, spent, avg, _ = walk(book, "buy", qty=qty, price_limit=limit_px)
            got = rules.floor_qty(got)
            if got <= 0 or spent < rules.min_notional:
                raise ExecutionRefused(f"aucune quantité exécutable sous le prix de protection ({venue} {pair})")
            _, spent, avg, _ = walk(book, "buy", qty=got)
            received = got * (1 - fee)
            eur = spent * self._eur_per(quote, book)
            fill = {"leg": leg_no, "venue": venue, "side": "buy", "asset": leg["asset_out"], "location": leg["loc_out"], "qty": received,
                    "counter_asset": leg["asset_in"], "counter_location": leg["loc_in"], "counter_qty": spent, "eur": eur,
                    "costs": {"commission_eur": got * fee * avg * self._eur_per(quote, book), "mid": book.mid, "avg": avg},
                    "evidence": {"book_sha256": book.sha256, "received_ms": book.received_ms, "venue": venue, "pair": pair},
                    "mode": self.mode, "ts_ms": now, "step": rules.step}
            status = "FILLED" if spent >= budget * 0.98 or rules.floor_qty(budget / avg) <= got else "PARTIAL"
            return {"status": status, "fills": [fill], "carried": {"asset": leg["asset_out"], "qty": rules.floor_qty(received)},
                    "note": None if status == "FILLED" else f"exécution partielle : {spent:.4f} {quote} sur {budget:.4f}"}
        qty_avail = have if have is not None else leg["qty_in"]
        if leg["asset_in"] != "EUR":
            held = self.ledger.available_holding(leg["asset_in"], leg["loc_in"], exclude_op=op_id) + (have or 0.0)
            qty_avail = min(qty_avail, held)
        qty = rules.floor_qty(qty_avail)
        if qty <= 0:
            raise ExecutionRefused("quantité revendable nulle (pas de quantité)")
        limit_px = book.mid * (1 - move)
        got, spent, avg, complete = walk(book, "sell", qty=qty, price_limit=limit_px)
        got = rules.floor_qty(got)
        if got <= 0:
            raise ExecutionRefused(f"aucune contrepartie au-dessus du prix de protection ({venue} {pair})")
        _, spent, avg, _ = walk(book, "sell", qty=got)
        if spent < rules.min_notional:
            raise ExecutionRefused(f"montant inférieur au minimum de {venue}")
        proceeds = spent * (1 - fee)
        eur = proceeds * self._eur_per(quote, book)
        fill = {"leg": leg_no, "venue": venue, "side": "sell", "asset": leg["asset_in"], "location": leg["loc_in"], "qty": got,
                "counter_asset": leg["asset_out"], "counter_location": leg["loc_out"], "counter_qty": proceeds, "eur": eur,
                "costs": {"commission_eur": spent * fee * self._eur_per(quote, book), "mid": book.mid, "avg": avg},
                "evidence": {"book_sha256": book.sha256, "received_ms": book.received_ms, "venue": venue, "pair": pair},
                "mode": self.mode, "ts_ms": self.view.now_ms()}
        status = "FILLED" if got >= qty - rules.step * 0.5 else "PARTIAL"
        return {"status": status, "fills": [fill], "carried": {"asset": leg["asset_out"], "qty": proceeds},
                "note": None if status == "FILLED" else f"vente partielle : {got:g} sur {qty:g} (profondeur sous le prix de protection)"}

    def _eur_per(self, quote: str, book) -> float:
        if quote == "EUR":
            return 1.0
        if book.pair.startswith("EUR/"):
            return 1.0 / book.mid
        px, _ = self.view.eur_reference(quote)
        if px is None:
            raise ExecutionRefused(f"prix de référence {quote}/EUR indisponible")
        return px

    def _limit_open(self, leg: dict) -> dict:
        d = leg["data"]
        book = self.view.book(d["venue"], d["pair"])
        rules = self.view.rules(d["venue"], d["pair"])
        tick = rules.tick or book.bid * 1e-6
        price = book.bid + tick if book.bid + tick < book.ask else book.bid  # « améliorer » d'un pas sans croiser
        return {"status": "OPEN", "fills": [], "limit": {"price": price, "placed_ms": self.view.now_ms(),
                                                         "expires_ms": self.view.now_ms() + 300_000, "venue": d["venue"],
                                                         "pair": d["pair"], "budget": leg["qty_in"], "step": rules.step,
                                                         "fee_maker": self.view.fees(d["venue"])["maker"].value / 1e4,
                                                         "asset_out": leg["asset_out"], "loc_out": leg["loc_out"]}}

    def _transfer(self, leg: dict, carried: dict[str, float], leg_no: int) -> dict:
        d = leg["data"]
        qty = carried.get(leg["asset_in"], leg["qty_in"])
        network = leg["network"]
        if d["direction"] == "withdraw":
            px = leg["eur_in"] / leg["qty_in"] if leg["qty_in"] else 0.0
            fee_qty = d["fee_eur"] / px if px else qty
            out_qty = qty - fee_qty
            if out_qty <= 0:
                raise ExecutionRefused("les frais de retrait dépassent le montant : route absurde à cette taille")
        else:
            out_qty = qty
        final = self.view.now_ms() + A.LATENCY_MS[network][1]
        fill = {"leg": leg_no, "venue": leg["venue"], "side": "buy", "asset": leg["asset_out"], "location": leg["loc_out"],
                "qty": out_qty, "counter_asset": leg["asset_in"], "counter_location": leg["loc_in"], "counter_qty": qty,
                "eur": out_qty * (leg["eur_in"] / leg["qty_in"] if leg["qty_in"] else 0.0),
                "costs": {"frais_eur": d["fee_eur"]}, "evidence": {"type": "transfert simulé", "network": network},
                "mode": self.mode, "ts_ms": self.view.now_ms(), "final_ms": final}
        return {"status": "FILLED", "fills": [fill], "carried": {"asset": leg["asset_out"], "qty": out_qty}, "final_ms": final}

    def _dex(self, op_id: str, leg: dict, carried: dict[str, float], leg_no: int) -> dict:
        d = leg["data"]
        network = d["network"]
        t_in, t_out = TokenId.parse(d["token_in"]), TokenId.parse(d["token_out"])
        qty_in = carried.get(leg["asset_in"], leg["qty_in"])
        held = self.ledger.available_holding(t_in.key, f"chain:{network}", exclude_op=op_id) + carried.get(leg["asset_in"], 0.0)
        qty_in = min(qty_in, held)  # jamais plus que ce qui est détenu sur la chaîne
        amount = int(qty_in * 10 ** d["decimals_in"])
        if amount <= 0:
            raise ExecutionRefused("montant à échanger nul")
        native_key, native_loc = f"native:{network}", f"chain:{network}"
        gas_eur = float(d["gas_eur"])
        native_eur = float(d["native_eur"])
        gas_native = gas_eur / native_eur
        provided = carried.get(native_key, 0.0)
        if self.ledger.available_holding(native_key, native_loc, exclude_op=op_id) + provided < gas_native:
            raise ExecutionRefused(f"pas assez de jeton de gaz sur {network} pour payer la transaction simulée")
        if gas_eur > self.limits["max_network_fee_eur"]:
            raise ExecutionRefused(f"frais de réseau {gas_eur:.3f} € au-delà du budget")
        quotes = self.view.dex_quotes(network, t_in, t_out, amount)
        fresh = max(quotes, key=lambda q: q.amount_out)
        min_out = int(d["min_out"] * (amount / d["amount_in"])) if d.get("amount_in") else int(d["min_out"])
        now = self.view.now_ms()
        final = now + A.LATENCY_MS[network][1]
        fee_fill = {"leg": leg_no, "venue": f"{network} (gaz)", "side": "fee", "asset": native_key, "location": native_loc, "qty": 0.0,
                    "counter_asset": native_key, "counter_location": native_loc, "counter_qty": gas_native, "eur": gas_eur,
                    "costs": {"gaz_eur": gas_eur}, "evidence": {"quote_sha256": fresh.sha256, "type": "frais de réseau simulés"},
                    "mode": self.mode, "ts_ms": now}
        approvals = []
        if d.get("approval"):
            approvals.append((network, t_in.key, "router"))
        if fresh.amount_out < min_out:
            return {"status": "FAILED", "fills": [fee_fill], "final_ms": final, "approvals": approvals,
                    "note": f"échange simulé annulé sur la chaîne : montant {fresh.amount_out} < minimum accepté {min_out} (gaz payé)"}
        out_qty = fresh.amount_out / 10 ** d["decimals_out"]
        eur = leg["eur_in"] * (qty_in / leg["qty_in"]) if leg["qty_in"] else 0.0
        swap = {"leg": leg_no + 1, "venue": fresh.venue, "side": "buy", "asset": t_out.key, "location": f"chain:{network}", "qty": out_qty,
                "counter_asset": t_in.key, "counter_location": f"chain:{network}", "counter_qty": qty_in, "eur": eur,
                "costs": {"min_out": min_out, "amount_out": fresh.amount_out}, "mode": self.mode, "ts_ms": now, "final_ms": final,
                "evidence": {"quote_sha256": fresh.sha256, "quote_received_ms": fresh.received_ms, "route": list(fresh.route)}}
        return {"status": "FILLED", "fills": [fee_fill, swap], "carried": {"asset": t_out.key, "qty": out_qty}, "final_ms": final,
                "approvals": approvals, "sim_tx": {"network": network, "final_ms": final, "fee_eur": gas_eur}}

    # -- ordres à cours limité ouverts ---------------------------------------------------------------------------
    def evaluate_limit(self, op: dict, trades: list[Trade], now_ms: int) -> Outcome:
        """Règle prudente : seules les transactions AU-DELÀ du prix limite remplissent ; jamais au simple contact."""
        lim = (op.get("result") or {}).get("limit")
        if not lim:
            raise ExecutionRefused("ordre à cours limité sans paramètres")
        through = [t for t in trades if lim["placed_ms"] < t.ts_ms <= min(now_ms, lim["expires_ms"]) and t.price < lim["price"]]
        want = lim["budget"] / lim["price"]
        vol = sum(t.qty for t in through)
        step = lim["step"] or 1e-8
        got = math.floor(min(want, vol) / step + 1e-9) * step
        if got <= 0:
            if now_ms >= lim["expires_ms"]:
                return Outcome("EXPIRED", notes=["délai écoulé sans transaction au-delà du prix : aucun remplissage inventé"])
            return Outcome("OPEN", notes=["aucune transaction au-delà du prix limite pour l'instant"])
        spent = got * lim["price"]
        fill = {"leg": 0, "venue": lim["venue"], "side": "buy", "asset": lim["asset_out"], "location": lim["loc_out"],
                "qty": got * (1 - lim["fee_maker"]), "counter_asset": "EUR", "counter_location": "cash", "counter_qty": spent,
                "eur": spent, "costs": {"commission_eur": spent * lim["fee_maker"], "limit_price": lim["price"]},
                "evidence": {"trades_through": len(through), "volume_through": vol, "rule": "prudente (franchissement)"},
                "mode": self.mode, "ts_ms": now_ms}
        state = "FILLED" if got >= want - step else ("PARTIAL" if now_ms >= lim["expires_ms"] else "OPEN_PARTIAL")
        return Outcome("FILLED" if state == "FILLED" else "PARTIAL", fills=[fill],
                       notes=[] if state == "FILLED" else ["remplissage partiel (volume franchi limité)"])
