# ForexWizard AI Market Brain v0.2

A working foundation for a no-broker-login XAU/USD market intelligence website.

## What is already implemented

- Next.js 16 / React 19 dark trading terminal UI
- FastAPI backend
- No-auth current XAU price provider adapter (Gold API)
- Persistent SQLite database by default (zero setup)
- Periodic XAU spot sampling
- Local OHLC aggregation: M1, M5, M15, M30, H1, H4, D1
- Clear labeling of locally sampled candles (never presented as tick-complete broker candles)
- Deterministic EMA / RSI / ATR / trend analysis
- Conservative BUY / SELL / WAIT engine
- `WAIT` while market history is insufficient
- Support/resistance zone estimation from stored observations
- Confidence/readiness/data-quality states
- Prediction audit trail
- 15m / 60m / 240m outcome evaluator
- Performance endpoint/page
- GDELT no-key research metadata collector
- Research page
- Brain Q&A endpoint/page for current market-state questions
- Memory page
- WebSocket market snapshot endpoint
- Optional Redis health check (not required)
- Optional Twelve Data historical bootstrap (not required)
- Unit/integration tests for indicators and sampled-candle analysis

## Important limitation

The default Gold API current-price source supplies a current spot price. The app samples that price periodically and builds its own local candles over time. Therefore a fresh installation does **not** instantly possess days of M1/H1/H4 intraday history.

Until enough history exists the correct Brain output is `WAIT`, not a fabricated signal.

If you later add a historical provider, the provider interface can seed the database immediately.

## Data sources

- Current XAU spot: https://api.gold-api.com/price/XAU
- Research discovery: GDELT DOC 2.0 API
- Optional historical bootstrap: Twelve Data when a key is supplied

No MT5 login, Exness/Vantage login, broker password, or TradingView login is required.

## Windows quick start

### 1. Backend

Open PowerShell in `apps/api`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open:

- API: http://localhost:8000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### 2. Frontend

Open another PowerShell in `apps/web`:

```powershell
npm install
copy .env.local.example .env.local
npm run dev
```

Open http://localhost:3000

## First-run behavior

1. Backend requests current XAU spot price.
2. It stores observations in `apps/api/forexwizard.db`.
3. Those observations are aggregated into sampled candles.
4. The Brain measures readiness by timeframe.
5. It returns `WAIT` while evidence/history is insufficient.
6. Analysis snapshots are persisted.
7. Later outcomes are measured automatically.
8. Research metadata is periodically collected in the background.

## API endpoints

- `GET /health`
- `GET /api/market/xauusd`
- `POST /api/market/refresh`
- `GET /api/market/candles?interval=1min&limit=120`
- `GET /api/market/readiness`
- `GET /api/brain`
- `POST /api/brain/ask`
- `GET /api/research`
- `GET /api/memory/predictions`
- `GET /api/performance`
- `GET /api/system/status`
- `WS /ws/market`

## Tests

From `apps/api`:

```powershell
$env:PYTHONPATH="."
pytest -q
```

Expected current suite: 3 tests passing.

## What was verified in the ChatGPT build environment

- Python source compilation: PASS
- Backend import/startup: PASS
- `/health`: PASS
- `/api/performance`: PASS
- Rules/indicator tests: 3 PASS
- TypeScript/TSX syntax transpilation: PASS (17 files)
- Live external XAU fetch: could not be executed inside the build container because outbound DNS/network is disabled there
- `npm install` / `next build`: could not be completed in the build container because it cannot reach npm

GLM/ZCode should run `npm install`, `npm run typecheck`, and `npm run build` on your internet-connected Windows machine and fix only genuine frontend/build issues it finds.

## Safety/design principle

The system must never claim 100% market accuracy. BUY/SELL/WAIT is experimental market analysis. Future price is uncertain. The architecture is designed to prefer `WAIT` over manufacturing certainty.
