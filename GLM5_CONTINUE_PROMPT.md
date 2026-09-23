# Instructions for GLM / ZCode

You are receiving an EXISTING ForexWizard AI Market Brain codebase.

Do NOT rebuild it from scratch.
Do NOT replace the backend architecture with mock data.
Do NOT add fake XAU prices or fake candles.
Do NOT invent BUY/SELL signals in the frontend.
Do NOT delete working persistence, analysis, research, memory, or performance code.

## First

Read these files:

- README.md
- apps/api/app/main.py
- apps/api/app/engine/analysis.py
- apps/api/app/services/market_state.py
- apps/web/app/page.tsx
- apps/web/components/DashboardClient.tsx
- apps/web/app/globals.css

Then inspect the entire project.

## Your task

I want you to act primarily as the website coding/finishing agent on the existing code.

1. Run backend tests.
2. Run `npm install` in `apps/web`.
3. Run `npm run typecheck`.
4. Run `npm run build`.
5. Fix genuine errors found by those checks.
6. Start backend + frontend locally.
7. Confirm the website loads and routes work.
8. Preserve the existing real-data behavior.
9. Improve UI/UX only where needed for a professional desktop/mobile trading terminal.
10. Do not start a new architecture unless an actual bug requires it.

## Existing behavior that must be preserved

- No MT5/broker login required.
- Current XAU spot provider has no frontend secret.
- Backend stores price observations in SQLite.
- Backend aggregates locally sampled OHLC candles.
- Locally sampled candles must remain clearly labeled.
- Brain uses deterministic measured inputs.
- Brain can say BUY, SELL, WAIT, or NO_DECISION.
- It must say WAIT / NO_DECISION when data quality is insufficient.
- Prediction memory and outcome evaluation must remain.
- GDELT research collector must remain optional and non-blocking.
- Frontend must never contain fake market values.

## Do not implement real-money execution

No auto trading, no broker order placement, no credential collection.

## When finished

Report:

- every file changed
- every command run
- test results
- typecheck result
- Next build result
- remaining limitations

Do not claim something passed unless you actually ran it.
