# TradePilot

TradePilot is a local Vue 3 trading dashboard with a FastAPI backend. It shows Coins.ph Spot USDT quotes and OANDA forex quotes when configured, supports paper and explicitly armed live orders, and runs a moving-average crossover strategy while the server stays online. Trader profile import remains a paper-only draft; automatic copy feeds are deferred.

## Setup (Windows PowerShell)

1. Install Python 3.11 or newer and Node.js 20.19+ (20.x) or 22.12+. Node.js includes npm. Check that both are available:

   ```powershell
   python --version
   node --version
   npm.cmd --version
   ```

2. Open PowerShell in the repository root (the folder containing `requirements.txt` and `package.json`). Create a virtual environment and install the Python and frontend dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   npm.cmd ci
   ```

3. Build the dashboard, then start the backend:

   ```powershell
   npm.cmd run build
   .venv\Scripts\python.exe -m backend
   ```

4. Open <http://127.0.0.1:8787> in your browser. The dashboard starts in paper mode. Check that it loads and review **Risk controls** before placing a paper order. Stop the server with `Ctrl+C` in its PowerShell window.

Venue credentials are optional for paper trading. To connect an account, copy the example file once, then edit `.env` with credentials for only the venues you use:

```powershell
Copy-Item .env.example .env
```

Skip that command if `.env` already exists so you do not replace your credentials. Coins.ph uses `COINS_API_KEY` and `COINS_API_SECRET`; OANDA uses `OANDA_ACCOUNT_ID`, `OANDA_TOKEN`, and `OANDA_ENV` (`practice` or `live`). Restart the backend after changing `.env`. Keep `.env` private; it is ignored by Git. Live orders require a configured live venue and explicit activation in the dashboard.

The FastAPI service serves the built frontend and binds to loopback only. State is saved in `data/state.json`, which is ignored by Git. Keep the computer awake and the FastAPI process running for unattended trading; closing the browser does not stop the engine. Run only one process. A local process lock prevents a second instance from using the same state directory. Set `PORT` in `.env` if port 8787 is unavailable, then open the matching `127.0.0.1` URL.

For frontend development, run the FastAPI service and `npm.cmd run dev` in separate terminals. Vite proxies `/api` to port 8787, so leave `PORT=8787` for this workflow. Rebuild with `npm.cmd run build` before using the frontend served by FastAPI.

## API documentation

Open Swagger UI at <http://127.0.0.1:8787/docs>, ReDoc at <http://127.0.0.1:8787/redoc>, or the OpenAPI JSON at <http://127.0.0.1:8787/openapi.json>. The dashboard also links to Swagger UI. The schemas describe request fields, responses, and example mode and automation changes. Swagger's **Try it out** sends requests to the running server: `/api/order` can place a real order in live mode, and `/api/auto` can arm unattended live orders.

See [`PLAN.md`](PLAN.md) for the implemented scope, current limits, and next steps.

## Automation

Open **Automation** to choose markets, fast and slow moving-average periods, and USD order size. The defaults are a 9-hour fast average, a 21-hour slow average, BTC/USDT, and $100 per order. Save the settings and arm the engine. It polls once per minute, uses only completed hourly candles, and anchors to the latest candle when first armed; it will not act on an older crossover. A crossing above triggers a buy, and a crossing below triggers a sell. Crypto sells are capped by current holdings. Each candle is recorded before attempting an order, so a timeout cannot cause that candle to be retried automatically. The dashboard shows recent decisions and errors.

Automation starts in **paper mode**. Paper automation resumes after a server restart if it was enabled; live trading and any previously live automation are disarmed. To run unattended live orders, configure the venue, switch to live mode with `ENABLE LIVE TRADING`, then arm automation with `ENABLE AUTO LIVE TRADING`. While armed, the server can place real orders without asking again. Changing trading mode pauses automation. Pause it from the Automation page at any time.

The strategy is a mechanical example, not a claim of profitability. It does not backtest, model fees or slippage, attach stops, or guarantee that a crossover will lead to a profitable trade. Check its paper behavior and venue account before arming live orders.

## Venues and risk

Copy `.env.example` to `.env` and add credentials for the venues you use. Coins.ph Spot supports the five displayed USDT pairs. OANDA supports EUR/USD, GBP/USD, and USD/JPY. Set `OANDA_ENV=live` for real forex orders; `practice` provides practice quotes but cannot place live orders. API credentials stay on the server and are never sent to the browser. Use a trading key without withdrawal permissions. USDT is treated as a close proxy for USD. Coins.ph USDTPHP is used only to value existing PHP cash; PHP pairs cannot be traded.

Risk settings limit allowed markets, order size, UTC-day traded notional, account drawdown, and open positions. Both manual and strategy orders use the same risk checks. Orders verified to reduce an existing position can pass opening limits. Paper spot orders cannot sell more than simulated holdings. Paper forex uses simplified net positions and a 10% opening margin check; it omits spreads, fees, financing, and liquidation. Live market orders may fill away from the displayed quote. A timed-out venue order is not retried because its status may be unknown.

Live daily loss checks use the first observed USD account value for the UTC day. Coins.ph valuation includes PHP, USDT, and the five supported assets; other assets are outside this calculation. OANDA live trading requires a USD account. Deposits, withdrawals, external trades, and unsupported assets can affect the check. Daily notional and app trade history include only orders placed through TradePilot.

## Trader profiles

Import a JSON file such as [`examples/trader-profile.json`](examples/trader-profile.json). Saved signals can be copied individually in paper mode. Automatic feed ingestion and live copy trading are disabled until a specific source is chosen.

## Tests

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp
npm.cmd test
npm.cmd run build
```

The venue adapters follow the official [Coins.ph REST API](https://docs.coins.ph/rest-api/) and [OANDA v20 API](https://developer.oanda.com/rest-live-v20/development-guide/). The background loop uses the [FastAPI lifespan pattern](https://fastapi.tiangolo.com/advanced/events/).
