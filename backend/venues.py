"""Coins.ph Spot and OANDA v20 HTTP adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from .core import stamp

COINS = "https://api.pro.coins.ph"


class Venues:
    def __init__(self, env: dict, client: httpx.AsyncClient):
        self.env = env
        self.client = client

    @property
    def oanda_base(self) -> str:
        return "https://api-fxtrade.oanda.com" if self.env.get("OANDA_ENV") == "live" else "https://api-fxpractice.oanda.com"

    @property
    def oanda_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.env.get('OANDA_TOKEN', '')}", "Content-Type": "application/json"}

    async def request(self, method: str, url: str, **options):
        try:
            response = await self.client.request(method, url, timeout=10, **options)
            data = response.json()
            response.raise_for_status()
            return data
        except httpx.TimeoutException as error:
            raise ValueError("Venue request timed out; order status may be unknown") from error
        except httpx.HTTPStatusError as error:
            detail = data if isinstance(data, dict) else {}
            raise ValueError(detail.get("msg") or detail.get("errorMessage") or detail.get("message") or f"Venue returned {error.response.status_code}") from error
        except (httpx.RequestError, json.JSONDecodeError) as error:
            raise ValueError(f"Venue request failed: {error}") from error

    async def coins_quotes(self, symbols: list[str]) -> dict:
        data = await self.request("GET", f"{COINS}/openapi/quote/v1/ticker/24hr", params={"symbols": json.dumps(symbols, separators=(',', ':'))})
        quoted_at = stamp()
        return {row["symbol"]: {
            "symbol": row["symbol"], "price": float(row["lastPrice"]),
            "changePercent": (float(row["lastPrice"]) / float(row["openPrice"]) - 1) * 100,
            "volume": float(row["quoteVolume"]), "high": float(row["highPrice"]),
            "low": float(row["lowPrice"]), "bid": float(row["bidPrice"]),
            "ask": float(row["askPrice"]), "time": quoted_at, "venue": "Coins.ph",
        } for row in (data if isinstance(data, list) else [data]) if row.get("symbol") in symbols}

    async def coins_candles(self, symbol: str, limit: int = 80, completed: bool = False) -> list[dict]:
        data = await self.request("GET", f"{COINS}/openapi/quote/v1/klines", params={"symbol": symbol, "interval": "1h", "limit": limit})
        current_ms = int(time.time() * 1000)
        return [{"time": item[0], "open": float(item[1]), "high": float(item[2]),
                 "low": float(item[3]), "close": float(item[4])}
                for item in data if not completed or int(item[6]) < current_ms]

    async def coins_signed(self, method: str, path: str, params: dict | None = None):
        key = self.env.get("COINS_API_KEY")
        secret = self.env.get("COINS_API_SECRET")
        if not key or not secret:
            raise ValueError("Coins.ph API credentials are missing")
        query = urlencode({**(params or {}), "recvWindow": "5000", "timestamp": str(int(time.time() * 1000))})
        signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return await self.request(method, f"{COINS}{path}?{query}&signature={signature}", headers={"X-COINS-APIKEY": key})

    async def coins_account(self) -> dict:
        data = await self.coins_signed("GET", "/openapi/v1/account")
        return {
            "canTrade": bool(data.get("canTrade")),
            "balances": {row["asset"]: float(row["free"]) + float(row["locked"]) for row in data["balances"]},
            "available": {row["asset"]: float(row["free"]) for row in data["balances"]},
        }

    async def coins_order(self, symbol: str, side: str, quantity: float, quote_amount: float) -> dict:
        params = {"symbol": symbol, "side": side, "type": "MARKET", "newOrderRespType": "FULL",
                  "newClientOrderId": f"tp_{uuid4().hex[:24]}"}
        if side == "BUY":
            params["quoteOrderQty"] = str(Decimal(str(quote_amount)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        else:
            info = await self.request("GET", f"{COINS}/openapi/v1/exchangeInfo", params={"symbol": symbol})
            filters = info.get("symbols", [{}])[0].get("filters", [])
            lot = next((item for item in filters if item.get("filterType") == "LOT_SIZE"), None)
            if not lot:
                raise ValueError("Coins.ph lot size is unavailable")
            step = Decimal(str(lot["stepSize"]))
            rounded = (Decimal(str(quantity)) // step) * step
            if rounded < Decimal(str(lot["minQty"])):
                raise ValueError("Order is below Coins.ph minimum quantity")
            params["quantity"] = format(rounded.normalize(), "f")
        return await self.coins_signed("POST", "/openapi/v1/order", params)

    async def oanda_quotes(self, symbols: list[str]) -> dict:
        if not self.env.get("OANDA_TOKEN") or not self.env.get("OANDA_ACCOUNT_ID"):
            return {}
        account = self.env["OANDA_ACCOUNT_ID"]
        data = await self.request("GET", f"{self.oanda_base}/v3/accounts/{account}/pricing",
                                  params={"instruments": ",".join(symbols)}, headers=self.oanda_headers)
        quoted_at = stamp()
        return {row["instrument"]: {
            "symbol": row["instrument"],
            "price": (float(row["bids"][0]["price"]) + float(row["asks"][0]["price"])) / 2,
            "bid": float(row["bids"][0]["price"]), "ask": float(row["asks"][0]["price"]),
            "tradeable": row.get("status") == "tradeable", "changePercent": None,
            "volume": None, "high": None, "low": None, "time": quoted_at,
            "venue": f"OANDA {self.env.get('OANDA_ENV', 'practice')}",
        } for row in data.get("prices", []) if row.get("bids") and row.get("asks")}

    async def oanda_candles(self, symbol: str, limit: int) -> list[dict]:
        account = self.env["OANDA_ACCOUNT_ID"]
        data = await self.request("GET", f"{self.oanda_base}/v3/accounts/{account}/instruments/{symbol}/candles",
                                  params={"count": limit, "granularity": "H1", "price": "M"}, headers=self.oanda_headers)
        return [{"time": row["time"], "open": float(row["mid"]["o"]),
                 "high": float(row["mid"]["h"]), "low": float(row["mid"]["l"]),
                 "close": float(row["mid"]["c"])}
                for row in data.get("candles", []) if row.get("complete") and row.get("mid")]

    async def oanda_summary(self) -> dict:
        account = self.env["OANDA_ACCOUNT_ID"]
        data = await self.request("GET", f"{self.oanda_base}/v3/accounts/{account}/summary", headers=self.oanda_headers)
        return data["account"]

    async def oanda_positions(self) -> list[dict]:
        account = self.env["OANDA_ACCOUNT_ID"]
        data = await self.request("GET", f"{self.oanda_base}/v3/accounts/{account}/openPositions", headers=self.oanda_headers)
        return data.get("positions", [])

    async def oanda_order(self, symbol: str, side: str, quantity: float, reduce_only: bool) -> dict:
        units = int(quantity) * (1 if side == "BUY" else -1)
        if not units:
            raise ValueError("OANDA order is below one unit")
        account = self.env["OANDA_ACCOUNT_ID"]
        data = await self.request("POST", f"{self.oanda_base}/v3/accounts/{account}/orders", headers=self.oanda_headers,
                                  json={"order": {"type": "MARKET", "instrument": symbol, "units": str(units),
                                                  "timeInForce": "FOK", "positionFill": "REDUCE_ONLY" if reduce_only else "DEFAULT"}})
        if data.get("orderRejectTransaction"):
            raise ValueError(data["orderRejectTransaction"].get("rejectReason") or "OANDA rejected the order")
        return data
