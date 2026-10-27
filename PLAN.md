# TradePilot project plan

This file records what is implemented in the workspace and what remains before relying on unattended live trading. The first venue scope is Coins.ph Spot USDT markets and OANDA forex. Automatic copy-feed trading is deferred until a source is chosen.

## Built

- [x] Vue 3 and Vite dashboard with live quote display, hourly crypto chart, market list, order ticket, activity, venue status, risk controls, automation controls, and a profile import screen.
- [x] FastAPI service on `127.0.0.1:8787` that serves the built dashboard and exposes the trading API. Swagger UI is at `/docs`, ReDoc at `/redoc`, and the OpenAPI schema at `/openapi.json`.
- [x] Coins.ph Spot adapter for BTC, ETH, SOL, XRP, and DOGE USDT pairs; OANDA adapter for EUR/USD, GBP/USD, and USD/JPY. Credentials are read by the backend from `.env`.
- [x] Paper account and explicit paper/live mode switch. Live mode requires venue readiness and the `ENABLE LIVE TRADING` phrase. Changing mode pauses automation, and startup disarms live mode.
- [x] Shared risk checks for manual and strategy orders: allowed markets, maximum order size, daily traded notional, daily loss, and open positions. Paper crypto sells are limited by simulated holdings.
- [x] Unattended moving-average crossover engine with configurable markets, fast and slow periods, and order size. It checks completed hourly candles from a background loop, records a candle before acting to avoid automatic retry, and shows recent decisions and errors.
- [x] Separate `ENABLE AUTO LIVE TRADING` phrase to arm unattended orders in live mode. Paper automation can resume after restart; live automation starts disarmed.
- [x] Local state persistence in `data/state.json`, a single-process lock, loopback binding, and cross-origin mutation protection.
- [x] JSON trader-profile import and one-at-a-time copying of saved signals in paper mode. A saved feed URL is not fetched; automatic and live copy trading remain disabled.
- [x] Python tests for API, engine, and venue behavior; frontend test and production build commands.

## Code map

| Area | Files |
| --- | --- |
| Dashboard | `src/App.vue`, `src/style.css`, `src/main.js` |
| API and Swagger contracts | `backend/api.py`, `backend/schemas.py` |
| Trading state and automation | `backend/engine.py`, `backend/core.py`, `backend/lock.py` |
| Venue access | `backend/venues.py` |
| Example profile | `examples/trader-profile.json` |
| Setup and operation | `README.md`, `.env.example` |

## Run and verify

Follow `README.md` to create the Python environment, install Node packages, build the frontend, and start `python -m backend`. Keep that process and its host computer running for unattended trading; closing the browser does not stop it. The dashboard is at <http://127.0.0.1:8787>.

Run `.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp`, `npm.cmd test`, and `npm.cmd run build` after changes. Inspect `/docs` and `/api/state` with the server running. Start in paper mode and review actual venue balances and quotes before enabling live mode.

## Next steps

1. **Practice and venue validation:** exercise the OANDA practice account and Coins.ph API credentials with the intended markets. Confirm symbol filters, balances, fills, and failure behavior against real venue responses. No live order has been verified by the automated tests.
2. **Strategy evaluation:** backtest the hourly crossover using historical data, then run a sustained paper trial. Add fee, spread, slippage, and financing assumptions before judging results.
3. **Live reliability:** reconcile app orders and positions with venue records after timeouts and restarts, add monitoring and alerts, and run the backend under a supervised service on an always-on host.
4. **Risk refinement:** evaluate stop or exit policies, portfolio-wide exposure across venues, and the effect of deposits, withdrawals, or external trades on daily loss limits.
5. **Copy trading decision:** choose a trader signal source and its authentication, permissions, update rules, and audit trail before adding automatic feed ingestion or live copying.

The current strategy is a mechanical example. It has no profit guarantee and does not include backtesting, transaction costs, attached stops, or full venue reconciliation. See `README.md` for exact operational and valuation limitations.
