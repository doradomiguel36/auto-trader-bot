import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from backend.venues import Venues


@pytest.mark.asyncio
async def test_oanda_strategy_candles_use_account_endpoint_and_only_completed_bars():
    captured = []

    def reply(request):
        captured.append(request)
        return httpx.Response(200, json={"candles": [
            {"time": "2026-10-27T00:00:00Z", "complete": True, "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"}},
            {"time": "2026-10-27T01:00:00Z", "complete": False, "mid": {"o": "1.15", "h": "1.2", "l": "1.1", "c": "1.18"}},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        venue = Venues({"OANDA_TOKEN": "token", "OANDA_ACCOUNT_ID": "abc", "OANDA_ENV": "practice"}, client)
        candles = await venue.oanda_candles("EUR_USD", 23)
    assert len(candles) == 1
    assert candles[0]["close"] == 1.15
    assert captured[0].url.path == "/v3/accounts/abc/instruments/EUR_USD/candles"
    assert captured[0].url.params["granularity"] == "H1"


@pytest.mark.asyncio
async def test_coins_order_signs_exact_request_query():
    captured = []

    def reply(request):
        captured.append(request)
        return httpx.Response(200, json={"status": "FILLED", "executedQty": "0.01"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        venue = Venues({"COINS_API_KEY": "key", "COINS_API_SECRET": "secret"}, client)
        await venue.coins_order("BTCUSDT", "BUY", 0.01, 100)
    request = captured[0]
    query = str(request.url.query, "ascii")
    unsigned, signature = query.rsplit("&signature=", 1)
    assert hmac.new(b"secret", unsigned.encode(), hashlib.sha256).hexdigest() == signature
    assert request.headers["X-COINS-APIKEY"] == "key"
    assert parse_qs(unsigned)["quoteOrderQty"] == ["100.00"]
