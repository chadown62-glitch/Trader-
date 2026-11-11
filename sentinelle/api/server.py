"""Serveur web du panel : API sécurisée + WebSocket temps réel + fichiers du panel."""

from __future__ import annotations

import asyncio
import csv
import io
import ipaddress
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..config import SettingsError
from ..execution.base import OrderError
from ..safety import RealTradingForbidden, forbid
from ..services.core import EMERGENCY_CONFIRM, App, UserError
from ..services.secrets import LEGACY_IGNORED
from ..storage.db import loads
from .auth import COOKIE, CSRF_HEADER, Auth

log = logging.getLogger("sentinelle.api")

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
PUBLIC = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup"}


# ---------------------------------------------------------------------------
# Corps de requêtes
# ---------------------------------------------------------------------------
class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=10)


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class DisableTotpBody(BaseModel):
    password: str
    code: str


class ChangePasswordBody(BaseModel):
    current: str
    new: str


class AccountBody(BaseModel):
    name: str = Field(default="Compte papier", max_length=60)
    initial_balance: float = Field(gt=0, le=10_000_000)


class ResetBody(BaseModel):
    initial_balance: float | None = Field(default=None, gt=0, le=10_000_000)


class FXBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base: Literal['EUR', 'USD', 'USDC', 'USDT']
    target: Literal['EUR', 'USD', 'USDC', 'USDT']
    amount: Decimal = Field(gt=0, le=10_000_000, max_digits=16, decimal_places=8, allow_inf_nan=False)
    via: Literal['EUR', 'USD', 'USDC', 'USDT'] | None = None
    request_id: str = Field(min_length=16, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


class OrderBody(BaseModel):
    account_id: int
    symbol: str = Field(max_length=30)
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop"] = "market"
    amount: float | None = Field(default=None, gt=0)
    quote_amount: float | None = Field(default=None, gt=0)  # montant dans la monnaie de la paire (USDC)
    cash_amount: float | None = Field(default=None, gt=0)  # montant dans la monnaie du compte (euros), frais compris
    price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=200)
    confirm: bool = False


class ConfirmBody(BaseModel):
    confirm: bool = False


class ModifyBody(BaseModel):
    stop: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    clear_target: bool = False


class SizeBody(BaseModel):
    account_id: int
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    risk_pct: float | None = Field(default=None, gt=0, le=10)


class AgentStartBody(BaseModel):
    confirm: str | None = None


class AgentStopBody(BaseModel):
    close_positions: bool = False


class TrainBody(BaseModel):
    days: int | None = Field(default=None, ge=180, le=3000)


class BacktestBody(BaseModel):
    days: int = Field(default=90, ge=30, le=3000)
    use_model: bool = True
    capital: float | None = Field(default=None, ge=10, le=10_000_000)  # monnaie du portefeuille ; défaut : son capital de départ


class EmergencyBody(BaseModel):
    action: Literal["pause", "flatten"]
    scope: Literal["ai", "all"] = "ai"
    confirm: str | None = None


class SecretsBody(BaseModel):
    values: dict[str, str | None]


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _host_name(request: Request) -> str:
    h = request.headers.get("host", "")
    if h.startswith("["):
        return h.split("]")[0] + "]"
    return h.split(":")[0]


def create_api(app: App, *, remote_access: bool = False) -> FastAPI:
    auth = Auth(app.db)
    allowed_hosts = {"127.0.0.1", "localhost", "[::1]", "testserver"} | {
        h.strip() for h in os.environ.get("SENTINELLE_ALLOWED_HOSTS", "").split(",") if h.strip()
    }

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        app.bus.bind_loop(asyncio.get_running_loop())
        yield

    api = FastAPI(title="Sentinelle", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @api.middleware("http")
    async def security(request: Request, call_next):
        host = _host_name(request)
        if not remote_access and host not in allowed_hosts:
            return JSONResponse({"detail": "Hôte non autorisé"}, status_code=400)
        path = request.url.path
        if path.startswith("/api/"):
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "Requête refusée (protection CSRF)"}, status_code=403)
            if path not in PUBLIC and not auth.valid(request.cookies.get(COOKIE)):
                return JSONResponse({"detail": "Connexion requise"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @api.exception_handler(RealTradingForbidden)
    async def _paper_only(_: Request, exc: RealTradingForbidden):
        # refus de sécurité explicite (à ne pas confondre avec une donnée manquante ou une saisie invalide)
        return JSONResponse({"detail": exc.message, "code": exc.code, "operation": exc.operation}, status_code=403)

    @api.exception_handler(UserError)
    async def _user_error(_: Request, exc: UserError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @api.exception_handler(OrderError)
    async def _order_error(_: Request, exc: OrderError):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=400)

    @api.exception_handler(SettingsError)
    async def _settings_error(_: Request, exc: SettingsError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @api.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def _login_response(payload: dict) -> JSONResponse:
        token = auth.new_session()
        resp = JSONResponse(payload)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=7 * 86400, path="/")
        return resp

    # -- authentification ----------------------------------------------------
    @api.get("/api/health")
    async def health():
        return {"ok": True, "version": __version__, "setup_required": not auth.configured}

    @api.get("/api/auth/status")
    async def auth_status(request: Request):
        return {"setup_required": not auth.configured, "authenticated": auth.valid(request.cookies.get(COOKIE)),
                "setup_allowed": _is_loopback(request.client.host if request.client else None) and _host_name(request) in allowed_hosts}

    @api.post("/api/auth/setup")
    async def auth_setup(body: PasswordBody, request: Request):
        if auth.configured:
            raise HTTPException(409, "Le mot de passe est déjà défini")
        client = request.client.host if request.client else None
        if not (_is_loopback(client) or client == "testclient") or _host_name(request) not in allowed_hosts:
            raise HTTPException(403, "Le premier mot de passe doit être créé depuis l'ordinateur où tourne Sentinelle")
        auth.set_password(body.password)
        app.db.log("info", "sécurité", "Mot de passe du panel créé")
        return _login_response({"ok": True})

    @api.post("/api/auth/login")
    async def auth_login(body: PasswordBody, request: Request):
        ip = request.client.host if request.client else "?"
        wait = auth.locked(ip)
        if wait > 0:
            raise HTTPException(429, f"Trop d'essais. Réessayez dans {int(wait // 60) + 1} minute(s).")
        if not auth.configured:
            raise HTTPException(400, "Aucun mot de passe défini")
        ok = await asyncio.to_thread(auth.check, body.password, ip)
        if not ok:
            app.db.log("warning", "sécurité", f"Échec de connexion au panel depuis {ip}")
            raise HTTPException(401, "Mot de passe incorrect")
        if auth.totp_enabled:
            if not body.code:
                return JSONResponse({"detail": "Code de double authentification requis", "need_code": True}, status_code=401)
            if not auth.totp_check(body.code):
                auth._fails.setdefault(ip, []).append(time.time())
                app.db.log("warning", "sécurité", f"Code de double authentification invalide depuis {ip}")
                return JSONResponse({"detail": "Code de double authentification invalide", "need_code": True}, status_code=401)
        return _login_response({"ok": True})

    @api.get("/api/auth/2fa")
    async def totp_status():
        return {"enabled": auth.totp_enabled}

    @api.post("/api/auth/2fa/setup")
    async def totp_setup():
        if auth.totp_enabled:
            raise HTTPException(409, "La double authentification est déjà active")
        return auth.totp_setup()

    @api.post("/api/auth/2fa/enable")
    async def totp_enable(body: CodeBody):
        if not auth.totp_enable(body.code):
            raise HTTPException(400, "Code invalide : vérifiez l'heure de votre téléphone et réessayez")
        app.db.log("info", "sécurité", "Double authentification activée")
        return {"enabled": True}

    @api.post("/api/auth/2fa/disable")
    async def totp_disable(body: DisableTotpBody, request: Request):
        ip = request.client.host if request.client else "?"
        if not await asyncio.to_thread(auth.check, body.password, ip) or not auth.totp_check(body.code):
            raise HTTPException(401, "Mot de passe ou code incorrect")
        auth.totp_disable()
        app.db.log("warning", "sécurité", "Double authentification désactivée")
        return {"enabled": False}

    @api.post("/api/auth/logout")
    async def auth_logout(request: Request):
        auth.revoke(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @api.post("/api/auth/password")
    async def auth_password(body: ChangePasswordBody, request: Request):
        ip = request.client.host if request.client else "?"
        if not await asyncio.to_thread(auth.check, body.current, ip):
            raise HTTPException(401, "Mot de passe actuel incorrect")
        auth.set_password(body.new)
        app.db.log("info", "sécurité", "Mot de passe du panel modifié")
        return _login_response({"ok": True})

    # -- vue d'ensemble, marché ----------------------------------------------------
    @api.get("/api/overview")
    async def overview():
        return await app.acall(app.overview)

    @api.get("/api/markets")
    async def markets():
        wl = app.settings["general"]["watchlist"]
        quote = app.settings["general"]["quote"]
        known = sorted(s for s, m in app.hub.markets.items() if m.quote == quote)
        return {
            "watchlist": wl,
            "quote": quote,
            "available": known[:2000],
            "prices": dict(app.hub.prices),
            "info": {s: app.hub.markets[s].to_dict() for s in wl if s in app.hub.markets},
            "status": app.hub.snapshot()["status"],
        }

    @api.get("/api/candles")
    async def candles(symbol: str, tf: str = "1h", limit: int = 300):
        if tf not in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
            raise HTTPException(400, "Unité de temps invalide")
        if symbol not in app.hub.markets:
            raise HTTPException(404, "Paire inconnue")
        limit = max(10, min(limit, 1000))
        try:
            rows = await asyncio.to_thread(app.hub.ui_candles, symbol, tf, limit)
        except Exception as exc:
            raise HTTPException(503, f"Données indisponibles : {exc}") from None
        return {"symbol": symbol, "tf": tf, "candles": rows}

    @api.get("/api/coach")
    async def coach(symbol: str):
        return await app.acall(app.coach, symbol)

    # -- comptes --------------------------------------------------------------------
    @api.get("/api/accounts")
    async def accounts():
        return app.db.query("SELECT id, name, kind, role, quote, initial_balance, created_at FROM accounts WHERE archived = 0 ORDER BY id")

    @api.post("/api/accounts")
    async def create_account(body: AccountBody):
        return await app.acall(app.create_paper_account, body.name, body.initial_balance)

    @api.post("/api/accounts/{account_id}/reset")
    async def reset_account(account_id: int, body: ResetBody):
        await app.acall(app.reset_paper_account, account_id, body.initial_balance)
        return {"ok": True}

    @api.post("/api/accounts/{account_id}/resume")
    async def resume_account(account_id: int):
        await app.acall(app.resume_account, account_id)
        return {"ok": True}

    @api.get("/api/accounts/{account_id}")
    async def account(account_id: int):
        return await app.acall(app.account_summary, account_id)

    @api.get("/api/accounts/{account_id}/equity")
    async def equity(account_id: int, days: float = 30):
        since = time.time() - max(0.04, min(days, 3650)) * 86400
        rows = app.db.query("SELECT ts, equity FROM equity WHERE account_id = ? AND ts >= ? ORDER BY ts", (account_id, since))
        step = max(1, len(rows) // 600)
        return [[r["ts"], r["equity"]] for r in rows[::step]]

    @api.get("/api/accounts/{account_id}/ledger")
    async def ledger(account_id: int, limit: int = 200):
        """Grand livre : chaque variation de solde, avec le solde obtenu et le contrôle comptable."""
        return await app.acall(app.ledger, account_id, limit)

    @api.get("/api/portfolio")
    async def portfolio_status():
        return await app.acall(app.portfolio_status)

    @api.post("/api/portfolio/activate")
    async def portfolio_activate(body: ConfirmBody):
        """Passage explicite au portefeuille principal (anciens comptes conservés en lecture seule)."""
        return await app.acall(app.activate_main_portfolio, body.confirm)

    @api.get("/api/accounts/{account_id}/fills")
    async def fills(account_id: int, limit: int = 100):
        return app.db.query("SELECT * FROM fills WHERE account_id = ? ORDER BY ts DESC LIMIT ?", (account_id, min(limit, 1000)))

    @api.post("/api/accounts/{account_id}/fx")
    async def convert_fx(account_id: int, body: FXBody):
        return await app.acall(app.convert_paper_currency, account_id, body.base, body.target, body.amount, body.via, body.request_id)

    @api.get("/api/accounts/{account_id}/fx")
    async def fx_history(account_id: int):
        return await app.acall(app.paper_fx_history, account_id)

    # -- ordres et positions ----------------------------------------------------------
    @api.post("/api/orders/preview")
    async def order_preview(body: OrderBody):
        return await app.acall(app.preview_order, body.model_dump())

    @api.post("/api/orders")
    async def order(body: OrderBody):
        return await app.acall(app.place_manual_order, body.model_dump())

    @api.delete("/api/orders/{order_id}")
    async def cancel(order_id: int, account_id: int):
        return await app.acall(app.cancel_order, account_id, order_id)

    @api.post("/api/positions/{position_id}/close")
    async def close_position(position_id: int):
        return await app.acall(app.close_position, position_id, "Clôture manuelle")

    @api.patch("/api/positions/{position_id}")
    async def modify_position(position_id: int, body: ModifyBody):
        return await app.acall(app.modify_position, position_id, body.stop, body.target, body.clear_target)

    @api.post("/api/risk/size")
    async def size(body: SizeBody):
        return await app.acall(app.size_helper, body.account_id, body.entry, body.stop, body.risk_pct)

    # -- IA ----------------------------------------------------------------------------------
    @api.get("/api/agents")
    async def agents():
        return await app.acall(app.agents_status)

    @api.post("/api/agents/{name}/start")
    async def agent_start(name: str, body: AgentStartBody):
        return await app.acall(app.start_agent, name, body.confirm)

    @api.post("/api/agents/{name}/stop")
    async def agent_stop(name: str, body: AgentStopBody):
        return await app.acall(app.stop_agent, name, body.close_positions)

    @api.get("/api/ai/readiness")
    async def readiness():
        return await app.acall(app.readiness)

    @api.get("/api/ai/models")
    async def models():
        return app.models.list()

    @api.get("/api/ai/models/{version}/report")
    async def model_report(version: str):
        rep = app.models.report(version)
        if rep is None:
            raise HTTPException(404, "Rapport introuvable")
        return rep

    @api.post("/api/ai/models/{version}/promote")
    async def model_promote(version: str):
        await app.acall(app.models.promote, version)
        return {"ok": True}

    @api.post("/api/ai/models/{version}/retire")
    async def model_retire(version: str):
        await app.acall(app.models.retire, version)
        return {"ok": True}

    @api.post("/api/ai/train")
    async def train(body: TrainBody):
        return app.start_training(body.days)

    @api.post("/api/ai/backtest")
    async def backtest(body: BacktestBody):
        return app.start_backtest(body.days, body.use_model, body.capital)

    @api.get("/api/ai/last-training")
    async def last_training():
        return loads(app.db.get_meta("last_train_report"), None)

    # -- capacités d'apprentissage et fiches par crypto -----------------------------------
    @api.get("/api/ai/bench")
    async def bench_report():
        return {"report": app.bench_report(), "running": bool(app.jobs.running("bench"))}

    @api.post("/api/ai/bench")
    async def bench_start():
        return app.start_bench()

    @api.get("/api/ai/learning")
    async def learning_monitor():
        return await asyncio.to_thread(app.learning_monitor)

    @api.get("/api/knowledge")
    async def knowledge_list():
        return await asyncio.to_thread(app.knowledge_list)

    @api.get("/api/knowledge/detail")
    async def knowledge_detail(symbol: str):
        prof = await asyncio.to_thread(app.knowledge_get, symbol)
        if prof is None:
            raise HTTPException(404, "Fiche introuvable")
        return prof

    @api.post("/api/knowledge/refresh")
    async def knowledge_refresh():
        return app.start_knowledge()

    @api.get("/api/jobs")
    async def jobs():
        return app.jobs.list()

    @api.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        j = app.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Tâche introuvable")
        j.pop("trace", None)
        return j

    @api.get("/api/decisions")
    async def decisions(account_id: int | None = None, limit: int = 100):
        if account_id is None:
            return app.db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (min(limit, 1000),))
        return app.db.query("SELECT * FROM decisions WHERE account_id = ? ORDER BY id DESC LIMIT ?", (account_id, min(limit, 1000)))

    @api.get("/api/candidates")
    async def candidates(limit: int = 200):
        rows = app.db.query(
            "SELECT id, symbol, timeframe, ts, strategy, entry, stop, target, p_win, expected_r, model_version, decision, "
            "decision_reason, outcome, outcome_r, exit_ts FROM candidates WHERE source = 'live' ORDER BY ts DESC LIMIT ?",
            (min(limit, 2000),),
        )
        resolved = [r for r in rows if r["outcome_r"] is not None]
        taken = [r["outcome_r"] for r in resolved if r["decision"] == "pris"]
        refused = [r["outcome_r"] for r in resolved if r["decision"] != "pris"]
        from ..ai.metrics import trade_metrics

        return {"rows": rows, "taken": trade_metrics(taken), "refused": trade_metrics(refused)}

    # -- historique et statistiques ----------------------------------------------------------
    def _trades(account_id: int | None, origin: str | None, limit: int):
        sql = "SELECT * FROM positions WHERE status = 'closed'"
        params: list[Any] = []
        if account_id is not None:
            sql += " AND account_id = ?"
            params.append(account_id)
        if origin in ("ai", "manual"):
            sql += " AND origin = ?"
            params.append(origin)
        sql += " ORDER BY exit_ts DESC LIMIT ?"
        params.append(min(limit, 10_000))
        rows = app.db.query(sql, params)
        for r in rows:
            r["data"] = loads(r["data"], {})
            r["mode"] = r["data"].get("mode")
            r.pop("data", None)
        return rows

    @api.get("/api/trades")
    async def trades(account_id: int | None = None, origin: str | None = None, limit: int = 200):
        return _trades(account_id, origin, limit)

    @api.get("/api/trades.csv")
    async def trades_csv(account_id: int | None = None):
        rows = _trades(account_id, None, 10_000)
        buf = io.StringIO()
        cols = ["id", "account_id", "symbol", "origin", "agent", "strategy", "entry_ts", "entry_price", "exit_ts", "exit_price",
                "initial_qty", "realized_pnl", "fees", "pnl_r", "reason_close"]
        w = csv.writer(buf, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[c])) if c.endswith("_ts") and r[c] else r[c] for c in cols])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=sentinelle-trades.csv"})

    @api.get("/api/fiscal.csv")
    async def fiscal_csv(account_id: int, year: int | None = None):
        """Toutes les exécutions (achats et ventes) d'un compte : base des déclarations."""
        sql = "SELECT f.*, o.origin, o.purpose FROM fills f LEFT JOIN orders o ON o.id = f.order_id WHERE f.account_id = ?"
        params: list[Any] = [account_id]
        if year:
            start = time.mktime((year, 1, 1, 0, 0, 0, 0, 0, -1))
            end = time.mktime((year + 1, 1, 1, 0, 0, 0, 0, 0, -1))
            sql += " AND f.ts >= ? AND f.ts < ?"
            params += [start, end]
        rows = app.db.query(sql + " ORDER BY f.ts", params)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["date", "opération", "paire", "quantité", "prix", "montant", "frais", "devise des frais", "origine"])
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), "achat" if r["side"] == "buy" else "vente",
                        r["symbol"], f"{r['amount']:.8f}".replace(".", ","), f"{r['price']:.8f}".replace(".", ","),
                        f"{r['amount'] * r['price']:.2f}".replace(".", ","), f"{r['fee']:.8f}".replace(".", ","),
                        r["fee_asset"] or "", "IA" if r["origin"] == "ai" else "manuel"])
        name = f"sentinelle-operations-{account_id}{'-' + str(year) if year else ''}.csv"
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @api.get("/api/fiscal/summary")
    async def fiscal_summary(account_id: int):
        rows = app.db.query("SELECT exit_ts, realized_pnl, fees FROM positions WHERE account_id = ? AND status = 'closed' AND exit_ts IS NOT NULL",
                            (account_id,))
        years: dict[int, dict] = {}
        for r in rows:
            y = time.localtime(r["exit_ts"]).tm_year
            d = years.setdefault(y, {"year": y, "trades": 0, "pnl": 0.0, "fees": 0.0})
            d["trades"] += 1
            d["pnl"] += r["realized_pnl"]
            d["fees"] += r["fees"]
        return sorted(years.values(), key=lambda d: -d["year"])

    @api.get("/api/stats")
    async def stats(account_id: int):
        from ..ai.metrics import trade_metrics

        rows = _trades(account_id, None, 10_000)
        out = {"all": trade_metrics([r["pnl_r"] for r in rows if r["pnl_r"] is not None])}
        out["pnl"] = float(sum(r["realized_pnl"] for r in rows))
        out["fees"] = float(sum(r["fees"] for r in rows))
        for key in ("ai", "manual"):
            sub = [r for r in rows if r["origin"] == key]
            out[key] = {**trade_metrics([r["pnl_r"] for r in sub if r["pnl_r"] is not None]), "pnl": float(sum(r["realized_pnl"] for r in sub))}
        by = {}
        for r in rows:
            if r["strategy"]:
                by.setdefault(r["strategy"], []).append(r["pnl_r"])
        out["by_strategy"] = {k: trade_metrics([x for x in v if x is not None]) for k, v in by.items()}
        return out

    # -- réglages et secrets ------------------------------------------------------------------
    @api.get("/api/settings")
    async def get_settings():
        return app.settings

    @api.put("/api/settings")
    async def put_settings(patch: dict):
        return await app.acall(app.update_settings, patch)

    @api.get("/api/secrets")
    async def secrets_status():
        return app.secrets.status()

    @api.put("/api/secrets")
    async def put_secrets(body: SecretsBody):
        # Simulation uniquement : on ne peut plus enregistrer de clé d'exchange (on peut seulement effacer les anciennes).
        if any(k in LEGACY_IGNORED and v for k, v in body.values.items()):
            forbid("enregistrement d'une clé API d'exchange", db=app.db)
        app.secrets.set(body.values)
        app.db.log("info", "sécurité", "Secrets modifiés : " + ", ".join(sorted(body.values)))
        return app.secrets.status()

    @api.get("/api/security")
    async def security_status():
        return await app.acall(app.paper_only_status)

    @api.post("/api/live/connect")
    async def live_connect():
        # ancien point d'accès : toujours refusé (403), aucune connexion n'est tentée
        return await app.acall(app.reconnect_live)

    # -- urgence et journal ----------------------------------------------------------------------
    @api.post("/api/emergency")
    async def emergency(body: EmergencyBody):
        return {**(await app.acall(app.emergency, body.action, body.confirm, body.scope)), "confirm_phrase": EMERGENCY_CONFIRM}

    @api.get("/api/logs")
    async def logs(level: str | None = None, limit: int = 200):
        if level:
            return app.db.query("SELECT * FROM logs WHERE level = ? ORDER BY id DESC LIMIT ?", (level, min(limit, 2000)))
        return app.db.query("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (min(limit, 2000),))

    # -- extensions (actualités, Telegram) -------------------------------------------------------
    for ext in app.extensions:
        if hasattr(ext, "register_routes"):
            ext.register_routes(api)

    # -- temps réel --------------------------------------------------------------------------------
    @api.websocket("/api/ws")
    async def ws(websocket: WebSocket):
        origin = websocket.headers.get("origin", "")
        host = websocket.headers.get("host", "")
        if origin and origin.split("://", 1)[-1] != host:
            await websocket.close(code=4403)
            return
        if not auth.valid(websocket.cookies.get(COOKIE)):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        q = app.bus.subscribe()
        try:
            await websocket.send_text(json.dumps({"topic": "hello", "data": {"version": __version__}}))
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    event = {"topic": "ping", "ts": time.time(), "data": {}}
                await websocket.send_text(json.dumps(event, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            app.bus.unsubscribe(q)

    # -- fichiers du panel ---------------------------------------------------------------------------
    if WEB_DIR.exists():
        api.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return api
