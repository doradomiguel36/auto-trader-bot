"""Persistent trading service; every order uses the same serialized risk path."""

from __future__ import annotations

import asyncio
import json
import math
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .core import (CRYPTO, FOREX, SYMBOLS, base_asset, check_risk, crossover, day,
                   finite, initial_state, is_forex, paper_equity, paper_margin,
                   paper_order, quantity_for_usd, stamp, validate_auto)
from .venues import Venues

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "state.json"


def load_env() -> dict:
    env = dict(os.environ)
    file = ROOT / ".env"
    if file.exists():
        for raw in file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                if not env.get(key.strip()):
                    env[key.strip()] = value.strip().strip("\"'")
    return env


def restore_state(path: Path) -> dict:
    defaults = initial_state()
    if not path.exists():
        return defaults
    saved = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("risk", "paper", "auto"):
        saved[key] = {**defaults[key], **saved.get(key, {})}
    restored = {**defaults, **saved}
    was_live = restored["mode"] == "live"
    restored["mode"] = "paper"
    restored["copyEnabled"] = False
    if was_live:
        restored["auto"]["enabled"] = False
    return restored


class TradingEngine:
    def __init__(self, path: Path = DATA_FILE, env: dict | None = None, client: httpx.AsyncClient | None = None):
        self.path = path
        self.env = env if env is not None else load_env()
        self.state = restore_state(path)
        self.prices: dict = {}
        self.market_error: str | None = None
        self.last_market_check: str | None = None
        self.live_account: dict | None = None
        self.live_account_time = 0.0
        self.lock = asyncio.Lock()
        self.listeners: set[asyncio.Queue] = set()
        self.tasks: list[asyncio.Task] = []
        self.client = client or httpx.AsyncClient()
        self.owns_client = client is None
        self.venues = Venues(self.env, self.client)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.owns_client:
            await self.client.aclose()

    def start(self):
        self.tasks = [asyncio.create_task(self._loop(self.update_markets, 10)),
                      asyncio.create_task(self._loop(self.strategy_tick, 60))]

    async def _loop(self, action, seconds: int):
        while True:
            try:
                await action()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                print(f"Background engine: {error}")
            await asyncio.sleep(seconds)

    async def save(self):
        snapshot = json.dumps(self.state, indent=2, allow_nan=False)

        def write():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(snapshot, encoding="utf-8")
            temporary.replace(self.path)

        await asyncio.to_thread(write)

    def publish(self):
        for queue in tuple(self.listeners):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(stamp())

    async def update_markets(self):
        results = await asyncio.gather(self.venues.coins_quotes([*CRYPTO, "USDTPHP"]),
                                       self.venues.oanda_quotes(list(FOREX)), return_exceptions=True)
        errors = []
        async with self.lock:
            for result in results:
                if isinstance(result, Exception):
                    errors.append(str(result))
                else:
                    self.prices.update(result)
            self.last_market_check = stamp()
            self.market_error = "; ".join(errors) or None
            if not any(isinstance(result, dict) and result for result in results):
                self.market_error = self.market_error or "No market feed configured"
            self.publish()

    async def get_live_account(self, force: bool = False) -> dict:
        if not force and self.live_account and asyncio.get_running_loop().time() - self.live_account_time < 15:
            return self.live_account
        account = {"coins": None, "oanda": None, "errors": []}
        if self.env.get("COINS_API_KEY") and self.env.get("COINS_API_SECRET"):
            try:
                info = await self.venues.coins_account()
                balances, available = info["balances"], info["available"]
                assets = [base_asset(symbol) for symbol in CRYPTO]
                holdings = {asset: balances[asset] for asset in assets if balances.get(asset, 0) > 0}
                free_holdings = {asset: available[asset] for asset in assets if available.get(asset, 0) > 0}
                rate = self.prices.get("USDTPHP", {}).get("price")
                if balances.get("PHP", 0) and not self.fresh("USDTPHP"):
                    raise ValueError("USD/PHP conversion quote is unavailable")
                equity = balances.get("USDT", 0) + balances.get("PHP", 0) / (rate or 1)
                for asset, quantity in holdings.items():
                    symbol = f"{asset}USDT"
                    if not self.fresh(symbol):
                        raise ValueError(f"A held {asset} asset has no fresh market quote")
                    equity += quantity * self.prices[symbol]["price"]
                account["coins"] = {"usdt": available.get("USDT", 0), "php": available.get("PHP", 0),
                                    "holdings": holdings, "freeHoldings": free_holdings, "equityUsd": equity,
                                    "canTrade": info["canTrade"]}
            except Exception as error:
                account["errors"].append(f"Coins.ph: {error}")
        if self.env.get("OANDA_TOKEN") and self.env.get("OANDA_ACCOUNT_ID"):
            try:
                summary = await self.venues.oanda_summary()
                account["oanda"] = {"currency": summary["currency"], "nav": float(summary["NAV"]),
                                    "balance": float(summary["balance"]),
                                    "openPositionCount": int(summary.get("openPositionCount", 0)),
                                    "environment": "live" if self.env.get("OANDA_ENV") == "live" else "practice"}
            except Exception as error:
                account["errors"].append(f"OANDA: {error}")
        self.live_account = account
        self.live_account_time = asyncio.get_running_loop().time()
        return account

    def fresh(self, symbol: str) -> bool:
        quote = self.prices.get(symbol)
        if not quote:
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(quote["time"].replace("Z", "+00:00"))).total_seconds()
            return 0 <= age < 60
        except (ValueError, TypeError):
            return False

    def daily_stats(self) -> dict:
        trades = [trade for trade in (self.state["paper"]["trades"] if self.state["mode"] == "paper" else self.state["liveTrades"])
                  if trade["time"].startswith(day()) and trade.get("status") in ("filled", "partially_filled")]
        paper_baseline = self.state.get("paperDailyBaseline")
        paper_change = paper_equity(self.state["paper"], self.prices) - paper_baseline["equityUsd"] if self.state["mode"] == "paper" and paper_baseline and paper_baseline["date"] == day() else None
        live_baseline = self.state.get("liveDailyBaseline")
        live_change = live_loss = None
        if self.state["mode"] == "live" and live_baseline and live_baseline["date"] == day() and self.live_account:
            coins, oanda = self.live_account.get("coins"), self.live_account.get("oanda")
            if (live_baseline.get("coinsEquityUsd") is None or coins) and (live_baseline.get("oandaNavUsd") is None or oanda):
                coin_delta = coins["equityUsd"] - live_baseline["coinsEquityUsd"] if coins and live_baseline.get("coinsEquityUsd") is not None else 0
                oanda_delta = oanda["nav"] - live_baseline["oandaNavUsd"] if oanda and live_baseline.get("oandaNavUsd") is not None else 0
                live_change = coin_delta + oanda_delta
                live_loss = max(0, -coin_delta) + max(0, -oanda_delta)
        return {"tradedNotionalUsd": sum(trade["notionalUsd"] for trade in trades),
                "realizedPnlUsd": sum(trade.get("realizedPnl", 0) for trade in trades),
                "paperChangeUsd": paper_change, "liveChangeUsd": live_change,
                "liveLossUsd": live_loss, "count": len(trades)}

    async def snapshot(self) -> dict:
        if self.state["mode"] == "live":
            await self.get_live_account()
        profiles = [{**{key: item.get(key) for key in ("id", "name", "sourceUrl", "enabled", "importedAt", "lastFetchedAt", "lastError")},
                     "signalCount": len(item.get("signals", [])), "signals": list(reversed(item.get("signals", [])[-8:]))}
                    for item in self.state["profiles"]]
        return {"mode": self.state["mode"], "copyEnabled": self.state["copyEnabled"], "risk": deepcopy(self.state["risk"]),
                "paper": deepcopy(self.state["paper"]), "paperEquity": paper_equity(self.state["paper"], self.prices),
                "paperAvailable": self.state["paper"]["cash"] - paper_margin(self.state["paper"]),
                "live": deepcopy(self.live_account) if self.state["mode"] == "live" else None,
                "liveTrades": deepcopy(self.state["liveTrades"][:100]), "profiles": profiles,
                "daily": self.daily_stats(), "prices": deepcopy(self.prices), "marketError": self.market_error,
                "lastMarketCheck": self.last_market_check, "auto": deepcopy(self.state["auto"]),
                "connectors": {"coins": bool(self.env.get("COINS_API_KEY") and self.env.get("COINS_API_SECRET")),
                               "oanda": bool(self.env.get("OANDA_TOKEN") and self.env.get("OANDA_ACCOUNT_ID")),
                               "oandaEnvironment": "live" if self.env.get("OANDA_ENV") == "live" else "practice"}}

    async def set_mode(self, mode: str, confirm: str):
        if mode not in ("paper", "live"):
            raise ValueError("Invalid trading mode")
        if mode == "live":
            if confirm != "ENABLE LIVE TRADING":
                raise ValueError("Type ENABLE LIVE TRADING to arm live orders")
            if not (self.env.get("COINS_API_KEY") and self.env.get("COINS_API_SECRET")) and not (self.env.get("OANDA_TOKEN") and self.env.get("OANDA_ACCOUNT_ID") and self.env.get("OANDA_ENV") == "live"):
                raise ValueError("No live venue is configured in .env")
            account = await self.get_live_account(True)
            if not account["coins"] and not (account["oanda"] and account["oanda"]["environment"] == "live"):
                raise ValueError("; ".join(account["errors"]) or "Could not verify a live account")
            if account["oanda"] and account["oanda"]["environment"] == "live" and account["oanda"]["currency"] != "USD" and not account["coins"]:
                raise ValueError("Live OANDA risk limits currently require a USD account")
            if not self.state.get("liveDailyBaseline") or self.state["liveDailyBaseline"]["date"] != day():
                self.state["liveDailyBaseline"] = {"date": day(), "coinsEquityUsd": account["coins"]["equityUsd"] if account["coins"] else None,
                                                   "oandaNavUsd": account["oanda"]["nav"] if account["oanda"] and account["oanda"]["environment"] == "live" else None}
        self.state["mode"] = mode
        self.state["copyEnabled"] = False
        self.state["auto"]["enabled"] = False
        await self.save()
        self.publish()

    async def execute_order(self, value: dict, source: str = "manual", profile_id: str | None = None) -> dict:
        symbol = str(value.get("symbol", "")).upper()
        side = str(value.get("side", "")).upper()
        if symbol not in SYMBOLS or side not in ("BUY", "SELL"):
            raise ValueError("Choose a supported symbol and side")
        notional = finite(value.get("notionalUsd"), "Order size", 1, 100_000_000)
        if not self.fresh(symbol):
            raise ValueError("Market quote is unavailable or stale")
        price = self.prices[symbol]["price"]
        quantity = quantity_for_usd(symbol, notional, price)
        if not quantity:
            raise ValueError("Order size is too small for this market")
        if self.state["mode"] == "paper":
            existing = self.state["paper"]["positions"].get(symbol, {}).get("quantity", 0)
            reduce_only = ((existing != 0 and math.copysign(1, existing) != (1 if side == "BUY" else -1) and quantity <= abs(existing))
                           if is_forex(symbol) else side == "SELL" and quantity <= existing)
            check_risk(self.state, symbol, notional, self.prices, reduce_only)
            if not self.state.get("paperDailyBaseline") or self.state["paperDailyBaseline"]["date"] != day():
                self.state["paperDailyBaseline"] = {"date": day(), "equityUsd": paper_equity(self.state["paper"], self.prices)}
                await self.save()
            trade = paper_order(self.state["paper"], symbol, side, quantity, price, source, profile_id)
            await self.save(); self.publish()
            return trade

        if is_forex(symbol):
            if not (self.env.get("OANDA_TOKEN") and self.env.get("OANDA_ACCOUNT_ID") and self.env.get("OANDA_ENV") == "live"):
                raise ValueError("Live OANDA trading is not configured")
        elif not (self.env.get("COINS_API_KEY") and self.env.get("COINS_API_SECRET")):
            raise ValueError("Live Coins.ph trading is not configured")
        account = await self.get_live_account(True)
        venue_account = account["oanda" if is_forex(symbol) else "coins"]
        if not venue_account:
            raise ValueError("; ".join(account["errors"]) or "Live account is unavailable")
        forex_positions = []
        if is_forex(symbol):
            if venue_account["currency"] != "USD":
                raise ValueError("Live OANDA risk limits currently require a USD account")
            forex_positions = await self.venues.oanda_positions()
            position = next((item for item in forex_positions if item["instrument"] == symbol), None)
            reducible = float(position.get("long", {}).get("units", 0)) if position and side == "SELL" else abs(float(position.get("short", {}).get("units", 0))) if position else 0
            reduce_only = reducible > 0 and quantity <= reducible
        else:
            if not venue_account["canTrade"]:
                raise ValueError("Coins.ph account cannot trade")
            asset = base_asset(symbol)
            if side == "SELL" and venue_account["freeHoldings"].get(asset, 0) + 1e-10 < quantity:
                raise ValueError("Insufficient Coins.ph holdings")
            if side == "BUY" and venue_account["usdt"] < notional:
                raise ValueError("Insufficient Coins.ph USDT")
            reduce_only = side == "SELL"
        check_risk(self.state, symbol, notional, self.prices, reduce_only)
        baseline = self.state.get("liveDailyBaseline")
        if not baseline or baseline["date"] != day():
            baseline = {"date": day(), "coinsEquityUsd": account["coins"]["equityUsd"] if account["coins"] else None,
                        "oandaNavUsd": account["oanda"]["nav"] if account["oanda"] and account["oanda"]["environment"] == "live" else None}
            self.state["liveDailyBaseline"] = baseline
            await self.save()
        if not reduce_only and baseline.get("coinsEquityUsd") is not None and not account["coins"]:
            raise ValueError("Coins.ph account is unavailable; daily loss cannot be checked")
        if not reduce_only and baseline.get("oandaNavUsd") is not None and not account["oanda"]:
            raise ValueError("OANDA account is unavailable; daily loss cannot be checked")
        drawdown = sum(max(0, baseline[key] - account[venue][value_key]) for key, venue, value_key in
                       (("coinsEquityUsd", "coins", "equityUsd"), ("oandaNavUsd", "oanda", "nav"))
                       if baseline.get(key) is not None and account[venue])
        if not reduce_only and drawdown >= self.state["risk"]["maxDailyLossUsd"]:
            raise ValueError("Daily live account loss limit reached")
        open_count = len(account["coins"]["holdings"]) if account["coins"] else 0
        open_count += account["oanda"]["openPositionCount"] if account["oanda"] and account["oanda"]["environment"] == "live" else 0
        if not reduce_only and open_count >= self.state["risk"]["maxOpenPositions"]:
            if is_forex(symbol) and not any(item["instrument"] == symbol for item in forex_positions):
                raise ValueError("Maximum live positions reached")
            if not is_forex(symbol) and side == "BUY" and not account["coins"]["holdings"].get(base_asset(symbol)):
                raise ValueError("Maximum live positions reached")
        # Uncertain venue responses are never retried automatically.
        result = await (self.venues.oanda_order(symbol, side, quantity, reduce_only) if is_forex(symbol)
                        else self.venues.coins_order(symbol, side, quantity, notional))
        fill = result.get("orderFillTransaction") if is_forex(symbol) else result
        status = ("filled" if fill else "canceled" if result.get("orderCancelTransaction") else "pending") if is_forex(symbol) else str(result.get("status", "UNKNOWN")).lower()
        executed = abs(float(fill.get("units", quantity))) if is_forex(symbol) and fill else float(result.get("executedQty", quantity if status == "filled" else 0)) if not is_forex(symbol) else quantity
        if not is_forex(symbol) and executed > 0 and status != "filled":
            status = "partially_filled"
        actual_notional = notional if is_forex(symbol) else float(result.get("cummulativeQuoteQty", executed * price))
        trade = {"id": str(uuid4()), "venueOrderId": str(fill.get("orderID", "") if is_forex(symbol) and fill else result.get("clientOrderId", "")),
                 "time": stamp(), "symbol": symbol, "side": side, "quantity": executed,
                 "price": float(fill.get("price", price)) if is_forex(symbol) and fill else actual_notional / executed if executed else price,
                 "notionalUsd": actual_notional, "realizedPnl": float(fill.get("pl", 0)) if is_forex(symbol) and fill else 0,
                 "source": source, "profileId": profile_id, "mode": "live", "status": status}
        self.state["liveTrades"].insert(0, trade)
        del self.state["liveTrades"][500:]
        self.live_account_time = 0
        await self.save(); self.publish()
        return trade

    async def set_auto(self, value: dict):
        requested = bool(value.get("enabled"))
        if requested:
            config = validate_auto(self.state["auto"], self.state["risk"])
            if any(symbol not in self.state["risk"]["allowedSymbols"] for symbol in config["symbols"]):
                raise ValueError("Strategy markets must be allowed by risk settings")
            if self.state["mode"] == "live":
                if value.get("confirm") != "ENABLE AUTO LIVE TRADING":
                    raise ValueError("Type ENABLE AUTO LIVE TRADING to arm unattended live orders")
                for symbol in config["symbols"]:
                    if is_forex(symbol) and not (self.env.get("OANDA_ENV") == "live" and self.env.get("OANDA_TOKEN") and self.env.get("OANDA_ACCOUNT_ID")):
                        raise ValueError("A selected forex market has no live OANDA connector")
                    if not is_forex(symbol) and not (self.env.get("COINS_API_KEY") and self.env.get("COINS_API_SECRET")):
                        raise ValueError("A selected crypto market has no live Coins.ph connector")
        self.state["auto"]["enabled"] = requested
        if requested:
            self.state["auto"]["lastCandle"] = {}
            self.state["auto"]["lastError"] = None
        await self.save(); self.publish()

    async def strategy_tick(self):
        if not self.state["auto"]["enabled"]:
            return
        config = deepcopy(self.state["auto"])
        for symbol in config["symbols"]:
            try:
                candles = (await self.venues.oanda_candles(symbol, config["slowPeriod"] + 2) if is_forex(symbol)
                           else await self.venues.coins_candles(symbol, config["slowPeriod"] + 2, completed=True))
                if len(candles) < config["slowPeriod"] + 1:
                    raise ValueError("Not enough completed candles for moving averages")
                candle_id = str(candles[-1]["time"])
                async with self.lock:
                    auto = self.state["auto"]
                    if not auto["enabled"] or symbol not in auto["symbols"]:
                        continue
                    auto["lastRunAt"] = stamp()
                    if auto["lastCandle"].get(symbol) == candle_id:
                        continue
                    if symbol not in auto["lastCandle"]:
                        auto["lastCandle"][symbol] = candle_id
                        self._decision(symbol, "ready", "Anchored to the latest completed candle")
                        await self.save(); self.publish()
                        continue
                    # Commit the candle before a possible venue request so timeouts cannot duplicate an order.
                    auto["lastCandle"][symbol] = candle_id
                    signal = crossover(candles, auto["fastPeriod"], auto["slowPeriod"])
                    if not signal:
                        self._decision(symbol, "hold", "No moving-average crossover")
                        await self.save(); self.publish()
                        continue
                    if not self.fresh(symbol):
                        raise ValueError("Market quote is unavailable or stale")
                    notional = auto["orderUsd"]
                    if symbol in CRYPTO and signal == "SELL":
                        if self.state["mode"] == "paper":
                            held = self.state["paper"]["positions"].get(symbol, {}).get("quantity", 0)
                        else:
                            account = await self.get_live_account(True)
                            held = (account["coins"] or {}).get("freeHoldings", {}).get(base_asset(symbol), 0)
                        notional = min(notional, held * self.prices[symbol]["price"])
                        if notional < 1:
                            self._decision(symbol, "skip", "No spot holdings to sell")
                            await self.save(); self.publish()
                            continue
                    await self.save()
                    try:
                        trade = await self.execute_order({"symbol": symbol, "side": signal, "notionalUsd": notional}, source="strategy")
                        auto["lastTradeAt"] = trade["time"]
                        auto["lastError"] = None
                        self._decision(symbol, "trade", f"{signal} {trade['status']} · ${notional:.2f}")
                    except Exception as error:
                        auto["lastError"] = f"{symbol}: {error}"
                        self._decision(symbol, "blocked", str(error))
                    await self.save(); self.publish()
            except Exception as error:
                async with self.lock:
                    if self.state["auto"]["enabled"]:
                        self.state["auto"]["lastRunAt"] = stamp()
                        self.state["auto"]["lastError"] = f"{symbol}: {error}"
                        self._decision(symbol, "error", str(error))
                        await self.save(); self.publish()

    def _decision(self, symbol: str, outcome: str, detail: str):
        decisions = self.state["auto"]["recentDecisions"]
        decisions.insert(0, {"time": stamp(), "symbol": symbol, "outcome": outcome, "detail": detail})
        del decisions[30:]
