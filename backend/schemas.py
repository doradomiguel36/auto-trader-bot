"""OpenAPI request and response contracts for the local trading API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "EUR_USD", "GBP_USD", "USD_JPY"]
CryptoSymbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
Side = Literal["BUY", "SELL"]
Mode = Literal["paper", "live"]


class RiskSettings(BaseModel):
    maxOrderUsd: float = Field(gt=0, description="Largest opening order in USD.", examples=[250])
    maxDailyLossUsd: float = Field(gt=0, description="Maximum UTC-day account drawdown in USD.", examples=[500])
    maxDailyNotionalUsd: float = Field(gt=0, description="Maximum opening order notional per UTC day.", examples=[1500])
    maxOpenPositions: int = Field(ge=1, description="Maximum number of open positions.", examples=[4])
    copyMultiplier: float = Field(gt=0, description="Multiplier for manually copied paper signals.", examples=[1])
    allowedSymbols: list[Symbol] = Field(min_length=1, description="Markets permitted for exposure-increasing orders.")


class RiskResponse(BaseModel):
    risk: RiskSettings


class ModeRequest(BaseModel):
    mode: Mode = Field(description="The desired trading mode.")
    confirm: str = Field(default="", description="For live mode, type ENABLE LIVE TRADING exactly.")


class ModeResponse(BaseModel):
    mode: Mode


class OrderRequest(BaseModel):
    symbol: Symbol = Field(description="Market to trade.", examples=["BTCUSDT"])
    side: Side = Field(description="Order direction.", examples=["BUY"])
    notionalUsd: float = Field(gt=0, description="Approximate USD size of the market order.", examples=[100])


class Trade(BaseModel):
    id: str
    time: str
    symbol: Symbol
    side: Side
    quantity: float
    price: float
    notionalUsd: float
    realizedPnl: float
    source: str
    profileId: str | None = None
    mode: Mode
    status: str
    venueOrderId: str | None = None


class AutoConfigRequest(BaseModel):
    symbols: list[Symbol] = Field(min_length=1, description="Markets to scan for hourly crossovers.", examples=[["BTCUSDT"]])
    fastPeriod: int = Field(ge=2, le=100, description="Fast simple moving-average length in completed hourly candles.", examples=[9])
    slowPeriod: int = Field(ge=3, le=200, description="Slow simple moving-average length; must exceed fastPeriod.", examples=[21])
    orderUsd: float = Field(gt=0, description="USD order size per crossover; must not exceed the risk order cap.", examples=[100])


class AutoToggleRequest(BaseModel):
    enabled: bool = Field(description="Arm or pause unattended trading.")
    confirm: str = Field(default="", description="In live mode, type ENABLE AUTO LIVE TRADING exactly.")


class AutoDecision(BaseModel):
    time: str
    symbol: Symbol
    outcome: str
    detail: str


class AutoStatus(AutoConfigRequest):
    enabled: bool
    lastCandle: dict[str, str]
    lastRunAt: str | None = None
    lastTradeAt: str | None = None
    lastError: str | None = None
    recentDecisions: list[AutoDecision]


class AutoResponse(BaseModel):
    auto: AutoStatus


class Signal(BaseModel):
    id: str
    symbol: Symbol
    side: Side
    notionalUsd: float
    timestamp: str


class SignalInput(BaseModel):
    id: str
    symbol: Symbol
    side: Side
    notionalUsd: float = Field(gt=0)
    timestamp: str | None = Field(default=None, description="Defaults to the current UTC time when omitted.")


class ProfileImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60, examples=["Macro Momentum"])
    sourceUrl: str | None = Field(default=None, description="Saved for a future feed integration; automatic fetching is currently disabled.")
    signals: list[SignalInput] = Field(default_factory=list, description="Optional historical signals for manual paper copying.")


class ProfileImportResponse(BaseModel):
    id: str
    name: str


class ProfileToggleRequest(BaseModel):
    id: str
    enabled: bool


class ProfileToggleResponse(BaseModel):
    enabled: bool


class ProfileOrderRequest(BaseModel):
    profileId: str
    signalId: str


class CopyToggleRequest(BaseModel):
    enabled: bool = Field(description="Only false is currently accepted; automatic copy trading is deferred.")


class CopyToggleResponse(BaseModel):
    copyEnabled: bool


class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float


class CandleResponse(BaseModel):
    candles: list[Candle]


class Quote(BaseModel):
    symbol: str
    price: float
    time: str
    venue: str
    bid: float | None = None
    ask: float | None = None
    changePercent: float | None = None
    volume: float | None = None
    high: float | None = None
    low: float | None = None
    tradeable: bool | None = None


class Position(BaseModel):
    quantity: float
    avgPrice: float


class PaperAccount(BaseModel):
    cash: float
    positions: dict[str, Position]
    trades: list[Trade]


class CoinsAccount(BaseModel):
    usdt: float
    php: float
    holdings: dict[str, float]
    freeHoldings: dict[str, float]
    equityUsd: float
    canTrade: bool


class OandaAccount(BaseModel):
    currency: str
    nav: float
    balance: float
    openPositionCount: int
    environment: Literal["practice", "live"]


class LiveAccounts(BaseModel):
    coins: CoinsAccount | None = None
    oanda: OandaAccount | None = None
    errors: list[str]


class DailyStats(BaseModel):
    tradedNotionalUsd: float
    realizedPnlUsd: float
    paperChangeUsd: float | None = None
    liveChangeUsd: float | None = None
    liveLossUsd: float | None = None
    count: int


class ProfileView(BaseModel):
    id: str
    name: str
    sourceUrl: str | None = None
    enabled: bool
    importedAt: str
    lastFetchedAt: str | None = None
    lastError: str | None = None
    signalCount: int
    signals: list[Signal]


class Connectors(BaseModel):
    coins: bool
    oanda: bool
    oandaEnvironment: Literal["practice", "live"]


class DashboardState(BaseModel):
    mode: Mode
    copyEnabled: bool
    risk: RiskSettings
    paper: PaperAccount
    paperEquity: float
    paperAvailable: float
    live: LiveAccounts | None = None
    liveTrades: list[Trade]
    profiles: list[ProfileView]
    daily: DailyStats
    prices: dict[str, Quote]
    marketError: str | None = None
    lastMarketCheck: str | None = None
    auto: AutoStatus
    connectors: Connectors


class ErrorResponse(BaseModel):
    error: str
