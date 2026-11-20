"""Routes HTTP du hub (préfixe /api/dmh), en refus par défaut.

- chaque demande qui modifie quelque chose est d'abord inspectée BRUTE (`guard.inspect_request`) : un mode autre que
  fictif, un champ lié au réel (signature, portefeuille, clé, levier…) ou un secret donne un 403, avant toute analyse ;
- l'origine d'une demande venue du panel est « user » ; l'API n'accepte pas qu'un client se déclare « système » ;
- les calculs (réseau compris, parfois plusieurs secondes) tournent dans un fil dédié, ni dans la boucle de l'API ni dans
  le fil du moteur ; la base reste sûre (verrou interne et transactions « BEGIN IMMEDIATE »).
"""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from . import memecoin_simulator
from .memecoin_worker import CycleBusy, MemecoinSessionWorker
from .net import SourceUnavailable
from .solana_scanner import scan_once
from .guard import inspect_request
from .ledger import Blocked, LedgerError
from .security import SecurityRefusal
from .service import HubError, HubService


def register_routes(api, app, service_factory) -> MemecoinSessionWorker:
    state: dict = {}

    def svc() -> HubService:
        if "svc" not in state:
            state["svc"] = service_factory()
        return state["svc"]

    try:  # construit tout de suite (aucun accès réseau) : reprise après arrêt et valeur du hub dans le portefeuille
        svc()
    except Exception as exc:
        state["error"] = f"hub de marché désactivé : {exc}"

    async def run(fn, *args):
        try:
            if "svc" not in state and state.get("error"):
                return JSONResponse({"detail": state["error"], "code": "dmh_disabled"}, status_code=503)
            return await asyncio.to_thread(fn, *args)
        except SourceUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        except CycleBusy as exc:
            return JSONResponse({"detail": str(exc)}, status_code=429)
        except SecurityRefusal as exc:
            return JSONResponse({"detail": exc.message, "code": "dmh_security", "reasons": exc.reasons}, status_code=403)
        except Blocked as exc:
            return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=409)
        except (HubError, LedgerError) as exc:
            return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)[:300], "code": "dmh_invalid"}, status_code=400)

    async def body(request: Request, operation: str) -> dict:
        try:
            data = await request.json()
        except ValueError:
            data = None
        inspect_request(data, operation, db=app.db)  # RealTradingForbidden -> 403 (gestionnaire de l'application)
        return data

    scan_lock = threading.Lock()
    next_scan = 0.0

    def scan(minimum, session=False, automatic=False, amount=None, currency=None):
        nonlocal next_scan
        if not scan_lock.acquire(blocking=False):
            raise CycleBusy("Scan déjà en cours")
        try:
            now = time.monotonic()
            if now < next_scan:
                raise CycleBusy("Intervalle minimal entre deux scans")
            next_scan = now + (15 if automatic else 60)
            hub = svc()
            if session:
                hub.ledger.check_open()
                memecoin_simulator.configure(app.db, hub.client, amount, currency)
                return memecoin_simulator.tick(hub, app.db, app.bus)
            return scan_once(hub.client, app.db, app.bus, min_liquidity_usd=minimum, registry=hub.registry)
        finally:
            scan_lock.release()

    def configure(amount, currency):
        hub = svc()
        hub.ledger.check_open()
        memecoin_simulator.configure(app.db, hub.client, amount, currency)

    worker = MemecoinSessionWorker(lambda: scan(25000, True, True), configure, app.bus)

    @api.post("/api/dmh/memecoin/worker/start")
    async def worker_start(request: Request):
        data = await body(request, "démarrer la session memecoin fictive")
        if data.keys() - {"mode", "interval_seconds", "initial_capital", "currency"}:
            return JSONResponse({"detail": "Paramètre de session inconnu"}, status_code=400)
        try:
            return await worker.start(data.get("interval_seconds", 15), data.get("initial_capital"), data.get("currency"))
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except Blocked as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except SourceUnavailable as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)

    @api.post("/api/dmh/memecoin/worker/stop")
    async def worker_stop(request: Request):
        data = await body(request, "mettre la session memecoin en pause")
        if set(data) != {"mode"}:
            return JSONResponse({"detail": "Paramètre de pause inconnu"}, status_code=400)
        return await worker.stop()

    @api.get("/api/dmh/memecoin/worker/status")
    async def worker_status():
        return worker.status()

    @api.post("/api/dmh/solana/scan")
    async def dmh_scan(request: Request):
        data = await body(request, "observation des pools Solana")
        if data.keys() - {"mode", "min_liquidity_usd"}:
            return JSONResponse({"detail": "Champ de scan inconnu"}, status_code=400)
        return await run(scan, data.get("min_liquidity_usd", 25000))

    @api.post("/api/dmh/memecoin/session/tick")
    async def memecoin_tick(request: Request):
        data = await body(request, "cycle memecoin simulé")
        if data.keys() - {"mode", "initial_capital", "currency"}:
            return JSONResponse({"detail": "Champ de session inconnu"}, status_code=400)
        return await run(scan, 25000, True, False, data.get("initial_capital"), data.get("currency"))

    @api.get("/api/dmh/memecoin/session")
    async def memecoin_session():
        return await run(lambda: memecoin_simulator.report(*memecoin_simulator.load(app.db)))

    @api.get("/api/dmh/status")
    async def dmh_status():
        return await run(lambda: svc().status())

    @api.get("/api/dmh/markets")
    async def dmh_markets(assets: str = "BTC,ETH,SOL"):
        wanted = tuple(a for a in assets.upper().split(",") if a)[:6]
        return await run(lambda: svc().market_board(wanted))

    @api.post("/api/dmh/preview")
    async def dmh_preview(request: Request):
        data = await body(request, "prévisualisation d'un ordre fictif")
        return await run(lambda: svc().preview(data, "user"))

    @api.post("/api/dmh/orders")
    async def dmh_execute(request: Request):
        data = await body(request, "exécution d'un ordre fictif")
        return await run(lambda: svc().execute(data, "user"))

    @api.get("/api/dmh/positions")
    async def dmh_positions():
        return await run(lambda: svc().positions())

    @api.get("/api/dmh/history")
    async def dmh_history(limit: int = 100):
        return await run(lambda: svc().history(max(1, min(limit, 500))))

    @api.get("/api/dmh/observatory")
    async def dmh_observatory():
        return await run(lambda: svc().observatory())

    @api.post("/api/dmh/open-orders/refresh")
    async def dmh_refresh(request: Request):
        await body(request, "mise à jour des ordres fictifs ouverts")
        return await run(lambda: svc().refresh_open())

    @api.post("/api/dmh/halt")
    async def dmh_halt(request: Request):
        data = await body(request, "arrêt des opérations fictives")
        await worker.stop()
        return await run(lambda: svc().halt(str(data.get("reason", ""))[:200], "user"))

    @api.post("/api/dmh/resume")
    async def dmh_resume(request: Request):
        await body(request, "reprise des opérations fictives")
        return await run(lambda: svc().resume("user"))

    @api.post("/api/dmh/reconcile")
    async def dmh_reconcile(request: Request):
        await body(request, "réconciliation du portefeuille fictif")
        return await run(lambda: svc().ledger.reconcile())

    @api.get("/api/dmh/limits")
    async def dmh_limits():
        return await run(lambda: {"limits": svc().limits(), "absolute": dict(__import__("sentinelle.dmh.security", fromlist=["ABSOLUTE"]).ABSOLUTE)})

    @api.put("/api/dmh/limits")
    async def dmh_set_limits(request: Request):
        data = await body(request, "modification des limites du hub")
        return await run(lambda: svc().set_limits(data.get("limits") or {}, "user"))

    @api.post("/api/dmh/strategy")
    async def dmh_strategy(request: Request):
        data = await body(request, "préparation de stratégie")
        return await run(lambda: svc().strategy(str(data.get("message", ""))[:2000], "user"))

    return worker
