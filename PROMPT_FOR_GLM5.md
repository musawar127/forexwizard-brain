# ForexWizard AI Market Brain — GLM Coding Task

You are working inside an existing repository named `forexwizard-brain`.

## Your first action

Use **Plan/Start Plan** before editing. Inspect every existing file, run the existing checks you can run, and explain what is already implemented. Do not replace working code merely to demonstrate activity.

## Product goal

Build a continuously improving XAU/USD market-intelligence website that combines real market data, deterministic market analysis, historical outcome tracking, research with sources, persistent memory, and an AI question-answering interface.

The final system may produce **BUY / SELL / WAIT** assessments, but it must never claim guaranteed future accuracy. WAIT is mandatory when evidence is weak, contradictory, stale, or incomplete.

## Non-negotiable rules

- Never generate fake prices or candles.
- Never fabricate news, indicators, support/resistance, confidence values, statistics, citations, or trading outcomes.
- Never expose API keys in client-side code.
- Never scrape or bypass access controls, logins, CAPTCHAs, paywalls, or anti-bot protections.
- Do not use unofficial TradingView scraping as the critical market-data feed.
- Keep provider integrations behind a common `MarketDataProvider` interface.
- Every piece of external information needs provenance and timestamps.
- Store UTC internally.
- Do not implement real-money auto execution unless explicitly requested in a later phase.

## Existing Phase 1/2 foundation

The repository already contains:

- Next.js / TypeScript frontend.
- FastAPI backend.
- PostgreSQL service.
- Redis service.
- Gold API no-auth XAU live-price provider starter.
- Optional Twelve Data intraday-candle provider.
- Server-side quote/candle collection.
- Candle persistence.
- Duplicate prevention.
- Data freshness state.
- A dark dashboard.
- Brain analysis intentionally disabled.

First validate this foundation and repair it if necessary.

## Target repo structure

```text
forexwizard-brain/
  apps/
    web/
      app/
      components/
      lib/
    api/
      app/
        core/
        db/
        models/
        services/
        routers/
        engines/
        agents/
        workers/
        tests/
  packages/
    shared/
  docs/
  docker-compose.yml
  README.md
```

Keep modules small. Do not create one giant `main.py` or one giant React component.

# Development phases

## Phase 1 — Foundation

Already started. Validate:

- Next.js frontend starts.
- FastAPI starts.
- PostgreSQL works.
- Redis works or reports unavailable gracefully.
- environment variables work.
- CORS is correct.
- health endpoint exists.

Do not advance if the repo does not build.

## Phase 2 — Real XAU/USD market data

Validate and improve the provider layer. Use Gold API `/price/XAU` as the default no-auth current-price source. Twelve Data is optional for intraday candles only when configured and actually permitted by the user's plan.

Required normalized data:

- symbol
- latest price
- provider
- market timestamp when available
- received timestamp
- age
- OHLC candles
- 1min, 5min, 15min, 30min, 1h, 4h, 1day capability

Gold API asks clients to cache current prices for 30 seconds, so poll responsibly. For any keyed provider, respect its published rate limits. Do not cause each browser client to hit upstream providers directly.

If a provider does not permit a requested instrument or historical interval, report it visibly. Never substitute fabricated candles.

After Phase 2 passes, STOP and report results before implementing Phase 3.

## Phase 3 — Deterministic Market Engine

Only after Phase 2 is verified, implement Python-calculated features:

- swing highs/lows
- higher highs / higher lows
- lower highs / lower lows
- BOS
- CHOCH
- ATR
- RSI
- EMA relationships
- candle body/range statistics
- volatility percentile
- support/resistance zones
- breakout/retest detection
- market regime
- session classification

Regimes:

- TREND_UP
- TREND_DOWN
- RANGE
- BREAKOUT_UP
- BREAKOUT_DOWN
- HIGH_VOLATILITY
- LOW_VOLATILITY
- UNCERTAIN

Create automated tests with deterministic fixture candles.

## Phase 4 — Multi-timeframe Brain

Combine D1/H4/H1/M30/M15/M5/M1.

Output structured state, for example:

```json
{
  "D1": "bullish",
  "H4": "bullish",
  "H1": "bearish_pullback",
  "M15": "bearish",
  "M5": "retracement"
}
```

Do not let one timeframe dictate the entire answer.

## Phase 5 — BUY / SELL / WAIT decision engine

Implement an explainable scoring engine before using an LLM.

Possible measurable inputs:

- trend alignment
- multi-timeframe alignment
- structure quality
- zone strength
- momentum
- volatility
- distance to support/resistance
- data freshness
- historical setup statistics
- event/news risk once available

Final states:

- BUY
- SELL
- WAIT
- NO_DECISION_DATA_UNAVAILABLE

Every BUY/SELL must include:

- reasons_for
- reasons_against
- invalidation
- confidence components
- timestamp
- data freshness
- brain version

Never allow an LLM to invent the confidence score.

## Phase 6 — Prediction memory and outcome evaluation

Persist every analysis snapshot.

Tables should include:

- predictions
- prediction_features
- prediction_outcomes
- brain_versions

Evaluate outcomes at configurable windows such as:

- 5m
- 15m
- 30m
- 1h
- 2h
- 4h
- 8h
- 24h

Measure:

- future price
- maximum favorable excursion
- maximum adverse excursion
- target reached
- invalidation reached
- directional result

Do not reduce learning to simple win/loss.

## Phase 7 — Similar setup engine

Search historical feature snapshots for similar market states.

Return sample size and distributions. Never present historical frequency as a guarantee.

## Phase 8 — Internet research system

Build separate agents/services:

- Scout
- Reader
- Researcher
- Fact Checker
- Memory Manager
- Critic

Research topics can include:

- gold
- XAU/USD
- Federal Reserve
- inflation
- DXY
- real yields
- central bank gold demand
- macro releases

Use public/authorized web pages, APIs, RSS, and official releases only.

Store:

- URL
- domain
- title
- author
- publication date
- discovered date
- last checked
- content hash
- extracted claim
- source type
- status

Knowledge statuses:

- ACTIVE
- SUPERSEDED
- DISPUTED
- OUTDATED
- UNVERIFIED

Do not silently overwrite contradictory knowledge.

## Phase 9 — Economic calendar/news risk

Add source-backed event data for high-impact events such as:

- FOMC
- CPI
- PPI
- NFP
- PCE
- GDP
- unemployment
- interest-rate decisions

Market decisions should be allowed to downgrade to WAIT around configured event risk windows.

## Phase 10 — AI Answer Agent

The AI receives structured facts from the deterministic engines plus retrieved research/memory.

It can answer:

- What is gold doing right now?
- Why?
- Should the engine currently classify the setup BUY, SELL, or WAIT?
- Why is it waiting?
- What invalidates the current thesis?
- What changed since the previous analysis?
- Show similar historical setups.
- Why did the last setup fail?
- What did the Brain learn today?

For numerical market claims, the AI may use only supplied structured values.

If a value is missing, say `Data unavailable`.

## Phase 11 — Backtesting and strategy candidates

Do not let one loss rewrite the live strategy.

Use:

Observe → Store → Evaluate → Candidate → Backtest → Validation → Out-of-sample test → Review → Version promotion.

Keep training/validation/test data separated and prevent look-ahead bias.

## Phase 12 — Production dashboard

Pages:

- `/` live brain
- `/market`
- `/memory`
- `/learning`
- `/performance`
- `/research`
- `/replay`
- `/settings`

Homepage should include:

- XAU/USD
- current provider price
- freshness
- market status
- multi-timeframe state
- regime
- zones
- BUY/SELL/WAIT
- confidence breakdown
- reasons for
- reasons against
- invalidation
- news risk
- similar setups
- Brain version
- Ask Brain interface

## Security

Secrets belong in server-only `.env` files or production secret managers.

Never expose:

- TWELVE_DATA_API_KEY
- database credentials
- Redis credentials
- AI provider keys
- webhook secrets

`.env` must stay gitignored.

## Data provider abstraction

Create a provider interface so we can later use multiple sources:

```python
class MarketDataProvider:
    async def get_latest_price(self, symbol): ...
    async def get_candles(self, symbol, interval, outputsize): ...
    async def health_check(self): ...
```

Potential future providers must not require rewriting the analysis engine. The immediate current-price source should work without user login or API key.

## UI direction

Premium dark institutional interface. No casino styling. No fake profit screenshots. No giant flashing trade buttons.

Use clear states:

- RECENT
- STALE
- DISCONNECTED
- DATA ERROR

For analysis:

- BUY
- SELL
- WAIT
- NO DECISION

## Workflow rule

For every phase:

1. Inspect.
2. Plan.
3. Implement.
4. Run tests.
5. Run backend/frontend builds.
6. Fix errors.
7. Verify actual behavior.
8. Summarize files changed.
9. STOP at the requested phase boundary.

Do not claim success for anything you could not actually test.

# Current instruction

Work only on **Phase 1 and Phase 2** right now.

Inspect the included code, fix any problems, install dependencies if permitted, run the backend/frontend, verify PostgreSQL and Redis, verify the Twelve Data integration with the user's API key when it is provided, and ensure real XAU/USD data reaches the dashboard without exposing the key.

When Phase 1 and 2 are complete, STOP and report:

1. exact files changed
2. commands used
3. tests/build results
4. anything not testable
5. confirm that the default current-price source requires no login/key
6. how to verify the displayed XAU/USD price against the source
7. provider/rate-limit and intraday-history limitations discovered
8. what you propose for Phase 3, without implementing it
