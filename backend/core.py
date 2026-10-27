"""Pure trading calculations and validation shared by API and automation."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import uuid4

CRYPTO = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
FOREX = ("EUR_USD", "GBP_USD", "USD_JPY")
SYMBOLS = (*CRYPTO, *FOREX)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(now: datetime | None = None) -> str:
    return (now or utc_now()).isoformat().replace("+00:00", "Z")


def day(now: datetime | None = None) -> str:
    return (now or utc_now()).date().isoformat()


def initial_state() -> dict:
    return {
        "mode": "paper",
        "copyEnabled": False,
        "risk": {
            "maxOrderUsd": 250,
            "maxDailyLossUsd": 500,
            "maxDailyNotionalUsd": 1500,
            "maxOpenPositions": 4,
            "copyMultiplier": 1,
            "allowedSymbols": list(SYMBOLS),
        },
        "paper": {"cash": 10000, "positions": {}, "trades": []},
        "paperDailyBaseline": None,
        "liveTrades": [],
        "liveDailyBaseline": None,
        "profiles": [],
        "processedSignals": [],
        "auto": {
            "enabled": False,
            "symbols": ["BTCUSDT"],
            "fastPeriod": 9,
            "slowPeriod": 21,
            "orderUsd": 100,
            "lastCandle": {},
            "lastRunAt": None,
            "lastTradeAt": None,
            "lastError": None,
            "recentDecisions": [],
        },
    }


def finite(value, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def validate_risk(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Risk settings are required")
    symbols = value.get("allowedSymbols")
    if not isinstance(symbols, list) or not symbols or any(symbol not in SYMBOLS for symbol in symbols):
        raise ValueError("Select at least one supported symbol")
    positions = finite(value.get("maxOpenPositions"), "Maximum open positions", 1, 100)
    if not positions.is_integer():
        raise ValueError("Maximum open positions must be a whole number")
    return {
        "maxOrderUsd": finite(value.get("maxOrderUsd"), "Maximum order", 1, 1_000_000),
        "maxDailyLossUsd": finite(value.get("maxDailyLossUsd"), "Daily loss limit", 1, 1_000_000),
        "maxDailyNotionalUsd": finite(value.get("maxDailyNotionalUsd"), "Daily notional limit", 1, 10_000_000),
        "maxOpenPositions": int(positions),
        "copyMultiplier": finite(value.get("copyMultiplier"), "Copy multiplier", 0.01, 10),
        "allowedSymbols": list(dict.fromkeys(symbols)),
    }


def validate_auto(value: dict, risk: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Strategy settings are required")
    symbols = value.get("symbols")
    if not isinstance(symbols, list) or not symbols or any(symbol not in SYMBOLS for symbol in symbols):
        raise ValueError("Select at least one supported strategy market")
    fast = finite(value.get("fastPeriod"), "Fast period", 2, 100)
    slow = finite(value.get("slowPeriod"), "Slow period", 3, 200)
    if not fast.is_integer() or not slow.is_integer() or fast >= slow:
        raise ValueError("Moving-average periods must be whole numbers with fast below slow")
    order_usd = finite(value.get("orderUsd"), "Strategy order size", 1, risk["maxOrderUsd"])
    return {"symbols": list(dict.fromkeys(symbols)), "fastPeriod": int(fast), "slowPeriod": int(slow), "orderUsd": order_usd}


def validate_signal(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Invalid signal")
    signal_id = str(value.get("id", "")).strip()
    symbol = str(value.get("symbol", "")).upper()
    side = str(value.get("side", "")).upper()
    if not signal_id or len(signal_id) > 120:
        raise ValueError("Signal id is required (up to 120 characters)")
    if symbol not in SYMBOLS or side not in ("BUY", "SELL"):
        raise ValueError("Signal has an unsupported symbol or side")
    return {"id": signal_id, "symbol": symbol, "side": side,
            "notionalUsd": finite(value.get("notionalUsd"), "Signal size", 1, 1_000_000),
            "timestamp": str(value.get("timestamp") or stamp())}


def validate_profile(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Profile must be a JSON object")
    name = str(value.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("Profile name must be 1–60 characters")
    # Automatic feed ingestion remains deferred; preserve the URL for a future integration.
    source_url = str(value.get("sourceUrl") or "").strip() or None
    if source_url and not source_url.startswith("https://"):
        raise ValueError("Signal feed must be an HTTPS URL")
    signals = value.get("signals") or []
    if not isinstance(signals, list) or len(signals) > 100:
        raise ValueError("Profile signals must be an array of at most 100 items")
    return {"name": name, "sourceUrl": source_url, "signals": [validate_signal(item) for item in signals]}


def is_forex(symbol: str) -> bool:
    return symbol in FOREX


def base_asset(symbol: str) -> str:
    return symbol.removesuffix("USDT")


def usd_notional(symbol: str, quantity: float, price: float) -> float:
    return abs(quantity) * (1 if symbol == "USD_JPY" else price)


def quantity_for_usd(symbol: str, usd: float, price: float) -> float:
    return math.floor(usd) if symbol == "USD_JPY" else math.floor(usd / price) if is_forex(symbol) else usd / price


def paper_equity(paper: dict, prices: dict) -> float:
    equity = float(paper["cash"])
    for symbol, position in paper["positions"].items():
        price = (prices.get(symbol) or {}).get("price") or position["avgPrice"]
        if is_forex(symbol):
            pnl = (price - position["avgPrice"]) * position["quantity"]
            equity += pnl / price if symbol == "USD_JPY" else pnl
        else:
            equity += position["quantity"] * price
    return equity


def paper_margin(paper: dict) -> float:
    return sum(usd_notional(symbol, pos["quantity"], pos["avgPrice"]) * 0.1
               for symbol, pos in paper["positions"].items() if is_forex(symbol))


def paper_order(paper: dict, symbol: str, side: str, quantity: float, price: float,
                source: str = "manual", profile_id: str | None = None) -> dict:
    quantity = finite(quantity, "Quantity", 1e-8, 100_000_000)
    price = finite(price, "Price", 1e-8, 100_000_000)
    signed = quantity if side == "BUY" else -quantity
    old = paper["positions"].get(symbol, {"quantity": 0, "avgPrice": 0})
    forex = is_forex(symbol)
    if not forex and signed < 0 and old["quantity"] + signed < -1e-8:
        raise ValueError("Paper spot sells cannot exceed holdings")
    notional = usd_notional(symbol, quantity, price)
    if not forex and signed > 0 and paper["cash"] < notional:
        raise ValueError("Insufficient paper cash")
    if forex:
        added = max(0, abs(old["quantity"] + signed) - abs(old["quantity"]))
        if paper["cash"] - paper_margin(paper) < usd_notional(symbol, added, price) * 0.1:
            raise ValueError("Insufficient paper margin")
    realized = 0.0
    if forex and old["quantity"] and math.copysign(1, old["quantity"]) != math.copysign(1, signed):
        closed = min(abs(old["quantity"]), quantity)
        pnl = (price - old["avgPrice"]) * closed * math.copysign(1, old["quantity"])
        realized = pnl / price if symbol == "USD_JPY" else pnl
        paper["cash"] += realized
    elif not forex and signed < 0:
        realized = quantity * (price - old["avgPrice"])
    if not forex:
        paper["cash"] -= signed * price
    next_quantity = old["quantity"] + signed
    if abs(next_quantity) < 1e-8:
        paper["positions"].pop(symbol, None)
    else:
        average = old["avgPrice"]
        if not old["quantity"] or math.copysign(1, old["quantity"]) != math.copysign(1, next_quantity):
            average = price
        elif math.copysign(1, old["quantity"]) == math.copysign(1, signed):
            average = (abs(old["quantity"]) * old["avgPrice"] + quantity * price) / abs(next_quantity)
        paper["positions"][symbol] = {"quantity": next_quantity, "avgPrice": average}
    trade = {"id": str(uuid4()), "time": stamp(), "symbol": symbol, "side": side,
             "quantity": quantity, "price": price, "notionalUsd": notional, "realizedPnl": realized,
             "source": source, "profileId": profile_id, "mode": "paper", "status": "filled"}
    paper["trades"].insert(0, trade)
    del paper["trades"][500:]
    return trade


def check_risk(state: dict, symbol: str, notional: float, prices: dict, reduce_only: bool = False) -> None:
    risk = state["risk"]
    if not math.isfinite(notional) or notional <= 0:
        raise ValueError("Order size must be positive")
    if not reduce_only and symbol not in risk["allowedSymbols"]:
        raise ValueError(f"{symbol} is disabled in risk settings")
    if not reduce_only and notional > risk["maxOrderUsd"]:
        raise ValueError(f"Order exceeds the ${risk['maxOrderUsd']} maximum")
    quote = prices.get(symbol)
    if not quote or not quote.get("price") or (utc_now() - datetime.fromisoformat(quote["time"].replace("Z", "+00:00"))).total_seconds() > 60:
        raise ValueError("Market quote is unavailable or stale")
    if is_forex(symbol) and quote.get("tradeable") is False:
        raise ValueError("Forex market is not tradeable right now")
    trades = [item for item in (state["paper"]["trades"] if state["mode"] == "paper" else state["liveTrades"])
              if item["time"].startswith(day()) and item.get("status") in ("filled", "partially_filled")]
    if not reduce_only and sum(item["notionalUsd"] for item in trades) + notional > risk["maxDailyNotionalUsd"]:
        raise ValueError("Daily traded notional limit reached")
    if state["mode"] == "paper":
        baseline = state.get("paperDailyBaseline")
        loss = (baseline["equityUsd"] - paper_equity(state["paper"], prices)) if baseline and baseline["date"] == day() else -sum(item.get("realizedPnl", 0) for item in trades)
        if not reduce_only and loss >= risk["maxDailyLossUsd"]:
            raise ValueError("Daily paper loss limit reached")
        if not reduce_only and symbol not in state["paper"]["positions"] and len(state["paper"]["positions"]) >= risk["maxOpenPositions"]:
            raise ValueError("Maximum open positions reached")


def crossover(candles: list[dict], fast: int, slow: int) -> str | None:
    """Return a side only when the latest completed close crosses the slow SMA."""
    if len(candles) < slow + 1:
        return None
    closes = [float(candle["close"]) for candle in candles]
    previous_fast = sum(closes[-fast-1:-1]) / fast
    previous_slow = sum(closes[-slow-1:-1]) / slow
    current_fast = sum(closes[-fast:]) / fast
    current_slow = sum(closes[-slow:]) / slow
    if previous_fast <= previous_slow and current_fast > current_slow:
        return "BUY"
    if previous_fast >= previous_slow and current_fast < current_slow:
        return "SELL"
    return None
