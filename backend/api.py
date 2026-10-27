"""FastAPI control plane for the Vue dashboard and unattended engine."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .core import CRYPTO, stamp, validate_auto, validate_profile, validate_risk
from .engine import ROOT, TradingEngine
from .lock import ProcessLock
from .schemas import (AutoConfigRequest, AutoResponse, AutoToggleRequest, CandleResponse,
                      CopyToggleRequest, CopyToggleResponse, CryptoSymbol, DashboardState,
                      ErrorResponse, ModeRequest, ModeResponse, OrderRequest,
                      ProfileImportRequest, ProfileImportResponse, ProfileOrderRequest,
                      ProfileToggleRequest, ProfileToggleResponse, RiskResponse,
                      RiskSettings, Trade)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with ProcessLock(ROOT / "data" / "engine.lock"):
        engine = TradingEngine()
        app.state.engine = engine
        engine.start()
        try:
            yield
        finally:
            await engine.close()


app = FastAPI(
    title="TradePilot Trading API",
    version="0.1.0",
    description=(
        "Local control API for paper and explicitly armed live trading. "
        "**POST /api/order can place a real market order when mode is live.** "
        "**POST /api/auto can arm unattended real orders when mode is live.** "
        "The service binds to loopback; venue credentials remain server-side. "
        "Swagger UI is at `/docs`, ReDoc at `/redoc`, and the OpenAPI document at `/openapi.json`."
    ),
    openapi_tags=[
        {"name": "Dashboard", "description": "Current account, market, and engine state."},
        {"name": "Trading", "description": "Risk settings, mode switching, and manual market orders."},
        {"name": "Automation", "description": "Configure and arm the hourly moving-average engine."},
        {"name": "Profiles", "description": "Paper-only saved signal profiles; automatic feed execution is deferred."},
    ],
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

BAD_REQUEST = {400: {"model": ErrorResponse, "description": "The request violates a trading or risk rule."}}


@app.middleware("http")
async def protect_local_posts(request: Request, call_next):
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"error": "Cross-origin request denied"}, status_code=403)
        if "application/json" not in request.headers.get("content-type", ""):
            return JSONResponse({"error": "JSON request required"}, status_code=415)
        if int(request.headers.get("content-length", "0") or 0) > 200_000:
            return JSONResponse({"error": "Request body is too large"}, status_code=413)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, error: ValueError):
    return JSONResponse({"error": str(error)}, status_code=400)


def engine(request: Request) -> TradingEngine:
    return request.app.state.engine


@app.get("/api/state", response_model=DashboardState, tags=["Dashboard"], summary="Get dashboard state",
         description="Returns market quotes, paper or live account summary, risk settings, automation status, and recent trades.")
async def get_state(request: Request):
    service = engine(request)
    async with service.lock:
        return await service.snapshot()


@app.get("/api/candles", response_model=CandleResponse, tags=["Dashboard"], summary="Get Coins.ph hourly candles",
         description="Returns the most recent hourly candles for a supported Coins.ph USDT pair.", responses=BAD_REQUEST)
async def get_candles(request: Request, symbol: CryptoSymbol):
    if symbol not in CRYPTO:
        raise ValueError("Candles are available for crypto symbols")
    return {"candles": await engine(request).venues.coins_candles(symbol)}


@app.get("/api/events", tags=["Dashboard"], summary="Stream dashboard refresh events",
         description="Server-sent events. Each `data` message tells clients to refresh `/api/state`; heartbeat comments keep the connection open.",
         responses={200: {"description": "Server-sent event stream", "content": {"text/event-stream": {}}}})
async def events(request: Request):
    service = engine(request)
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    service.listeners.add(queue)

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    value = await asyncio.wait_for(queue.get(), 30)
                    yield f"data: {json.dumps({'type': 'refresh', 'at': value})}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            service.listeners.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/risk", response_model=RiskResponse, tags=["Trading"], summary="Update risk limits",
          description="Updates limits used by both manual and automatic orders. An active strategy cannot be made invalid by this change.", responses=BAD_REQUEST)
async def set_risk(request: Request, value: RiskSettings):
    service = engine(request)
    async with service.lock:
        risk = validate_risk(value.model_dump())
        if service.state["auto"]["enabled"]:
            validate_auto(service.state["auto"], risk)
            if any(symbol not in risk["allowedSymbols"] for symbol in service.state["auto"]["symbols"]):
                raise ValueError("Pause automation before disabling a strategy market")
        service.state["risk"] = risk
        await service.save(); service.publish()
        return {"risk": risk}


@app.post("/api/mode", response_model=ModeResponse, tags=["Trading"], summary="Switch paper or live mode",
          description="Switching mode pauses automation. Live mode requires verified venue credentials and an exact confirmation phrase.", responses=BAD_REQUEST)
async def set_mode(request: Request, value: ModeRequest = Body(..., openapi_examples={
    "paper": {"summary": "Return to paper trading", "value": {"mode": "paper"}},
    "live": {"summary": "Arm real orders", "value": {"mode": "live", "confirm": "ENABLE LIVE TRADING"}},
})):
    service = engine(request)
    async with service.lock:
        await service.set_mode(value.mode, value.confirm)
        return {"mode": service.state["mode"]}


@app.post("/api/order", response_model=Trade, tags=["Trading"], summary="Place a market order",
          description="Places a paper order by default. In live mode this sends a real market order to Coins.ph or OANDA after risk checks.", responses=BAD_REQUEST)
async def order(request: Request, value: OrderRequest):
    service = engine(request)
    async with service.lock:
        return await service.execute_order(value.model_dump())


@app.post("/api/auto/config", response_model=AutoResponse, tags=["Automation"], summary="Configure moving averages",
          description="Save markets, completed hourly candle periods, and order size while automation is paused.", responses=BAD_REQUEST)
async def auto_config(request: Request, value: AutoConfigRequest):
    service = engine(request)
    async with service.lock:
        if service.state["auto"]["enabled"]:
            raise ValueError("Pause automation before changing its strategy")
        config = validate_auto(value.model_dump(), service.state["risk"])
        service.state["auto"].update(config)
        service.state["auto"]["lastCandle"] = {}
        service.state["auto"]["lastError"] = None
        await service.save(); service.publish()
        return {"auto": service.state["auto"]}


@app.post("/api/auto", response_model=AutoResponse, tags=["Automation"], summary="Arm or pause unattended trading",
          description="Paper automation can be armed without a phrase. In live mode, arming requires `ENABLE AUTO LIVE TRADING`; while armed, the server can send real orders without further prompts.", responses=BAD_REQUEST)
async def auto_toggle(request: Request, value: AutoToggleRequest = Body(..., openapi_examples={
    "arm_paper": {"summary": "Arm paper automation", "value": {"enabled": True}},
    "arm_live": {"summary": "Arm unattended live orders", "value": {"enabled": True, "confirm": "ENABLE AUTO LIVE TRADING"}},
    "pause": {"summary": "Pause automation", "value": {"enabled": False}},
})):
    service = engine(request)
    async with service.lock:
        await service.set_auto(value.model_dump())
        return {"auto": service.state["auto"]}


@app.post("/api/profiles", response_model=ProfileImportResponse, tags=["Profiles"], summary="Import a trader profile",
          description="Stores a profile and optional signals for manual paper copying. Feed URLs are saved but are not fetched.", responses=BAD_REQUEST)
async def import_profile(request: Request, value: ProfileImportRequest):
    service = engine(request)
    async with service.lock:
        profile = {**validate_profile(value.model_dump()), "id": str(uuid4()), "enabled": True,
                   "importedAt": stamp(), "lastFetchedAt": None, "lastError": None}
        service.state["profiles"].append(profile)
        await service.save(); service.publish()
        return {"id": profile["id"], "name": profile["name"]}


@app.post("/api/profiles/toggle", response_model=ProfileToggleResponse, tags=["Profiles"], summary="Enable or pause a saved profile", responses=BAD_REQUEST)
async def toggle_profile(request: Request, value: ProfileToggleRequest):
    service = engine(request)
    async with service.lock:
        profile = next((item for item in service.state["profiles"] if item["id"] == value.id), None)
        if not profile:
            raise ValueError("Profile not found")
        profile["enabled"] = value.enabled
        await service.save(); service.publish()
        return {"enabled": profile["enabled"]}


@app.post("/api/profiles/order", response_model=Trade, tags=["Profiles"], summary="Copy one saved signal in paper mode",
          description="Executes one saved profile signal using the paper account and current risk limits. Each signal can be copied once.", responses=BAD_REQUEST)
async def copy_profile_order(request: Request, value: ProfileOrderRequest):
    service = engine(request)
    async with service.lock:
        if service.state["mode"] == "live":
            raise ValueError("Live copy trading is deferred until a signal source is chosen")
        profile = next((item for item in service.state["profiles"] if item["id"] == value.profileId and item["enabled"]), None)
        if not profile:
            raise ValueError("Active profile not found")
        signal = next((item for item in profile["signals"] if item["id"] == value.signalId), None)
        if not signal:
            raise ValueError("Signal not found")
        key = f"{profile['id']}:{signal['id']}"
        if key in service.state["processedSignals"]:
            raise ValueError("This signal was already copied")
        trade = await service.execute_order({**signal, "notionalUsd": signal["notionalUsd"] * service.state["risk"]["copyMultiplier"]}, "copy", profile["id"])
        service.state["processedSignals"].append(key)
        service.state["processedSignals"] = service.state["processedSignals"][-1000:]
        await service.save()
        return trade


@app.post("/api/copy", response_model=CopyToggleResponse, tags=["Profiles"], summary="Keep automatic copying paused",
          description="Automatic feed-based copy trading is deferred. This endpoint currently accepts only `enabled: false`.", responses=BAD_REQUEST)
async def copy_toggle(request: Request, value: CopyToggleRequest):
    service = engine(request)
    async with service.lock:
        if value.enabled:
            raise ValueError("Automatic copy trading is deferred until a signal source is chosen")
        service.state["copyEnabled"] = False
        await service.save(); service.publish()
        return {"copyEnabled": False}


@app.get("/", include_in_schema=False)
async def index():
    file = ROOT / "dist" / "index.html"
    if not file.exists():
        raise HTTPException(503, "Frontend not built. Run npm.cmd run build.")
    return FileResponse(file, headers={"Cache-Control": "no-store"})


@app.get("/assets/{name}", include_in_schema=False)
async def asset(name: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise HTTPException(404, "Not found")
    file = ROOT / "dist" / "assets" / name
    if not file.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable"})
