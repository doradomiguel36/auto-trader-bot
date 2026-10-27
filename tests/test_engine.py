import asyncio
from datetime import datetime, timezone

import pytest

from backend.core import crossover, initial_state, stamp, validate_auto
from backend.engine import TradingEngine, restore_state
from backend.lock import ProcessLock


def test_crossover_uses_only_the_latest_completed_closes():
    candles = [{"close": value} for value in [10, 10, 9, 9, 15]]
    assert crossover(candles, 2, 3) == "BUY"
    assert crossover([{"close": value} for value in [10, 10, 12, 12, 8]], 2, 3) == "SELL"
    assert crossover(candles[:3], 2, 3) is None


def test_strategy_configuration_cannot_bypass_order_limit():
    risk = initial_state()["risk"]
    with pytest.raises(ValueError, match="Strategy order size"):
        validate_auto({"symbols": ["BTCUSDT"], "fastPeriod": 2, "slowPeriod": 3, "orderUsd": 251}, risk)


@pytest.mark.asyncio
async def test_strategy_anchors_then_places_one_paper_trade_per_candle(tmp_path):
    path = tmp_path / "state.json"
    service = TradingEngine(path=path, env={"OANDA_ENV": "practice"})
    try:
        service.state["auto"].update({"enabled": True, "symbols": ["BTCUSDT"], "fastPeriod": 2, "slowPeriod": 3, "orderUsd": 100})
        service.prices["BTCUSDT"] = {"price": 10, "time": stamp()}
        closes = [10, 10, 9, 9]

        async def candles(*_args, **_kwargs):
            return [{"time": index, "close": value} for index, value in enumerate(closes)]

        service.venues.coins_candles = candles
        await service.strategy_tick()
        assert service.state["paper"]["trades"] == []
        closes.append(15)
        await service.strategy_tick()
        assert len(service.state["paper"]["trades"]) == 1
        assert service.state["paper"]["trades"][0]["source"] == "strategy"
        await service.strategy_tick()
        assert len(service.state["paper"]["trades"]) == 1
        assert restore_state(path)["auto"]["lastCandle"]["BTCUSDT"] == "4"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_live_automation_requires_separate_confirmation(tmp_path):
    service = TradingEngine(path=tmp_path / "state.json", env={"COINS_API_KEY": "key", "COINS_API_SECRET": "secret"})
    try:
        service.state["mode"] = "live"
        with pytest.raises(ValueError, match="ENABLE AUTO LIVE TRADING"):
            await service.set_auto({"enabled": True})
        assert service.state["auto"]["enabled"] is False
    finally:
        await service.close()


def test_restart_disarms_live_mode_and_live_automation(tmp_path):
    path = tmp_path / "state.json"
    state = initial_state()
    state["mode"] = "live"
    state["auto"]["enabled"] = True
    path.write_text(__import__("json").dumps(state), encoding="utf-8")
    restored = restore_state(path)
    assert restored["mode"] == "paper"
    assert restored["auto"]["enabled"] is False


def test_restart_resumes_only_paper_automation(tmp_path):
    path = tmp_path / "state.json"
    state = initial_state()
    state["auto"]["enabled"] = True
    path.write_text(__import__("json").dumps(state), encoding="utf-8")
    assert restore_state(path)["auto"]["enabled"] is True


def test_only_one_process_can_hold_the_trading_state_lock(tmp_path):
    path = tmp_path / "engine.lock"
    with ProcessLock(path):
        with pytest.raises(RuntimeError, match="Another trading engine"):
            with ProcessLock(path):
                pass
