from fastapi.testclient import TestClient

from backend.api import app
from backend.core import stamp
from backend.engine import TradingEngine


def test_fastapi_dashboard_contract_and_auto_controls(tmp_path):
    service = TradingEngine(path=tmp_path / "state.json", env={"OANDA_ENV": "practice"})
    app.state.engine = service
    try:
        client = TestClient(app)
        response = client.get("/api/state")
        assert response.status_code == 200
        assert response.json()["mode"] == "paper"
        assert response.json()["auto"]["enabled"] is False

        config = {"symbols": ["BTCUSDT", "ETHUSDT"], "fastPeriod": 9, "slowPeriod": 21, "orderUsd": 75}
        assert client.post("/api/auto/config", json=config).status_code == 200
        assert client.post("/api/auto", json={"enabled": True}).json()["auto"]["enabled"] is True
        assert client.post("/api/auto/config", json=config).status_code == 400
        assert client.post("/api/mode", json={"mode": "paper"}).status_code == 200
        assert client.get("/api/state").json()["auto"]["enabled"] is False
        service.prices["BTCUSDT"] = {"symbol": "BTCUSDT", "price": 100, "time": stamp(), "venue": "Coins.ph"}
        order = client.post("/api/order", json={"symbol": "BTCUSDT", "side": "BUY", "notionalUsd": 100})
        assert order.status_code == 200
        assert order.json()["mode"] == "paper"
        assert client.get("/api/state").json()["paper"]["positions"]["BTCUSDT"]["quantity"] == 1
    finally:
        import asyncio
        asyncio.run(service.close())


def test_api_rejects_cross_origin_mutations(tmp_path):
    service = TradingEngine(path=tmp_path / "state.json", env={"OANDA_ENV": "practice"})
    app.state.engine = service
    try:
        client = TestClient(app)
        response = client.post("/api/auto", json={"enabled": True}, headers={"Origin": "https://example.com"})
        assert response.status_code == 403
    finally:
        import asyncio
        asyncio.run(service.close())


def test_swagger_describes_trading_contracts():
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "TradePilot Trading API"
    assert "/api/order" in schema["paths"]
    assert "/api/auto" in schema["paths"]
    assert "/" not in schema["paths"]
    order = schema["paths"]["/api/order"]["post"]
    assert order["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/OrderRequest")
    assert order["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/Trade")
    assert "arm_live" in schema["paths"]["/api/auto"]["post"]["requestBody"]["content"]["application/json"]["examples"]
