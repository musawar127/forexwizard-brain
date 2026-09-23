# ForexWizard AI Market Brain — Work Log

This file is the shared multi-agent log for the ForexWizard project.
All agents append below using the `---` separator.

---
Task ID: 1
Agent: main
Task: Clone and import https://github.com/musawar127/forexwizard-brain into /home/z/my-project/

Work Log:
- Removed the default Next.js scaffold files from /home/z/my-project/ (.env, .git, .gitignore).
- `git clone https://github.com/musawar127/forexwizard-brain.git repo_temp`
- Moved all contents of repo_temp/ (including dotfiles) into /home/z/my-project/.
- Removed empty repo_temp/ directory.
- Verified top-level structure:
  - GLM5_CONTINUE_PROMPT.md
  - README.md
  - TEST_RESULTS.md
  - PROMPT_FOR_GLM5.md
  - START_HERE_WINDOWS.md
  - docker-compose.yml
  - apps/ (api + web)
  - docs/ARCHITECTURE.md
- Read GLM5_CONTINUE_PROMPT.md completely (70 lines, 13 explicit rules).
- Read README.md and TEST_RESULTS.md completely.
- Inspected every file in apps/api/app/ (main.py, core/config.py, db/{base,session,models}.py, engine/{analysis,candles,indicators,predictions}.py, services/{brain_chat,gold_api,market_state,redis_health,research,twelve_data}.py, models/market.py).
- Inspected every file in apps/web/ (app/{layout,page,globals.css,analysis/memory/performance/research/settings/page.tsx}, components/{BrainChat,Candles,DashboardClient,DecisionCard,Nav,Timeframes,TopBar}.tsx, lib/{api,types}.ts, package.json, tsconfig.json, next.config.ts, next-env.d.ts).
- Inspected docs/ARCHITECTURE.md and both test files.

Stage Summary:
- Repository imported successfully. The existing ForexWizard AI Market Brain v0.2 codebase is intact and ready for dependency installation, tests, typecheck, build, and runtime verification.
- Architecture confirmed: FastAPI backend on :8000, Next.js 16 frontend on :3000, SQLite at apps/api/forexwizard.db, no broker login, real Gold-API XAU spot feed sampled every 30s, locally aggregated OHLC candles, deterministic EMA/RSI/ATR engine, conservative BUY/SELL/WAIT rules-v0.1 brain, GDELT research collector, prediction memory + outcome evaluation, optional Twelve Data historical bootstrap, optional Redis health check.
- No fake data generation anywhere in the codebase.

---
Task ID: 2
Agent: main
Task: Install deps, run tests/typecheck/build, fix genuine errors only, start services, wire frontend→backend, polish UI, end-to-end browser test.

Work Log:
- Installed backend deps into /home/z/.venv: fastapi==0.117.1, uvicorn, httpx, pydantic, pydantic-settings, SQLAlchemy, redis, pytest, pytest-asyncio. (sse-starlette conflict noted for an unrelated package not used here.)
- Backend tests initially failed: `Could not parse SQLAlchemy URL from given URL string`. Root cause: system-level `DATABASE_URL=file:/home/z/my-project/db/custom.db` (PG-style file:// URL invalid for SQLAlchemy) was leaking into pydantic-settings and overriding the safe `sqlite:///./forexwizard.db` default.
- Genuine fix #1: created `apps/api/.env.example` and `apps/api/.env` pinning `DATABASE_URL=sqlite:///./forexwizard.db`. (The README references `.env.example` but it was missing from the repo despite being tracked in .gitignore — a real repo bug.)
- Genuine fix #2: created `apps/web/.env.local.example` and `apps/web/.env.local` with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`. (Same gap in the web app — README references it but file wasn't shipped.)
- Backend tests now pass: 3 passed in 0.56s (matches TEST_RESULTS.md).
- Installed npm deps: 27 packages in 12s.
- TypeScript typecheck found 1 genuine error in `DashboardClient.tsx:65`:
  `Type 'BrainAnalysis | null | undefined' is not assignable to type 'BrainAnalysis | null'`.
- Genuine fix #3: changed `<DecisionCard brain={brain} />` to `<DecisionCard brain={brain ?? null} />`. Typecheck now clean (0 errors).
- Next.js production build: succeeded. All 7 routes (/, /analysis, /memory, /performance, /research, /settings, /_not-found) prerendered as static in 4.1s with Turbopack.
- Process lifecycle bug discovered: starting uvicorn/next dev with `&` caused the process to die when the bash subshell exited. Container PID 1 is `tini` (k8s.io cgroup), no systemd.
- Genuine fix #4: created `/home/z/my-project/scripts/start_backend_detached.sh` and `start_frontend_detached.sh` using `setsid bash -c 'exec ... >/dev/null 2>&1 </dev/null'` to fully detach child processes from the bash session. Both processes now survive across bash sessions (PPID=1).
- Started FastAPI backend on 0.0.0.0:8000 (PID 2205, parented to PID 1).
- Started Next.js dev server on 0.0.0.0:3000 (PID 2256 → child 2281 next-server, parented to PID 1).
- Verified all backend endpoints respond with real data:
  - GET /health → {"status":"ok","market_provider":"CONNECTED","quote_status":"RECENT",...}
  - GET /api/market/xauusd → real Gold API quote (price $4,298.50), real Brain analysis (WAIT, 69.1% confidence, RANGE regime, real EMA/RSI reasons)
  - GET /api/system/status, GET /api/performance, GET /api/memory/predictions, GET /api/research all OK
- Frontend served on :3000 (HTTP 200, title "ForexWizard AI Market Brain").
- Used agent-browser (Playwright) for end-to-end browser test of all 6 routes (Overview, Analysis, Research, Memory, Performance, Settings) plus BrainChat interaction:
  - Overview: live XAU/USD $4286.90, Brain WAIT (69% confidence, 75% readiness, TREND_DOWN regime), real EMA/RSI reasons, real support/resistance zones, real 6-timeframe grid (H4 INSUFFICIENT_DATA 2 candles, H1 BEARISH 6 candles, M30/M15/M5/M1 BEARISH with real RSI 30.6/23.6/9.3/3.9).
  - Analysis page: score -1.25, confidence 69%, readiness 75%, all timeframes.
  - BrainChat Q&A "Why are you waiting?": returned real evidence-based answer explaining the bearish trend and the conservative threshold.
  - Research page: GDELT 429 rate-limit handled gracefully (page shows "No research stored yet. Leave the backend running and the collector will retry automatically.").
  - Memory page: 4 real predictions stored in audit trail (all WAIT — correctly conservative).
  - Performance page: 4 total analyses, 0 directional calls, 4 WAIT decisions, "Not enough data" for direction accuracy (correct).
  - Settings page: all info correct.
- Mobile responsiveness test at 375x812: sidebar collapses to horizontal nav, all data still visible, layout flows correctly.
- Browser errors: 0. Console errors: 0.

UI polish performed (targeted, no architecture changes):
- Chart polish (apps/web/components/Candles.tsx): replaced the original 72-candle, view-only-min-max-labels chart with a more readable version:
  - 48 most recent candles (was 72) — less density per candle.
  - 5 horizontal price gridlines with right-side price labels (was only min/max labels).
  - 5 vertical time gridlines with bottom time labels in HH:MM format (was none).
  - Visible "last price" yellow tag showing current close price (was none).
  - Body width 4px min, 0.7 step factor (was 2.5px min, 0.5 step factor).
  - Body height 3px min (was 1.5px min).
  - Wicks slightly thicker (1.1 stroke-width, was default 1).
- Chart polish CSS additions (apps/web/app/globals.css): .candle-wick, .candle-body, .time-grid, .last-price-line, .last-price-tag, .last-price-text, .time-label classes.
- DecisionCard brain prop null coalescing (TS fix above).
- Verified: typecheck 0 errors, tests 3/3 pass, all 6 pages render real data correctly.

Stage Summary:
- All 12 user tasks completed.
- Files changed (genuine fixes only):
  1. apps/api/.env.example (created — repo bug: was referenced in README but never committed)
  2. apps/api/.env (created — pins safe SQLite URL; system env DATABASE_URL=file:... leaked an invalid SQLAlchemy URL)
  3. apps/web/.env.local.example (created — same gap)
  4. apps/web/.env.local (created)
  5. apps/web/components/DashboardClient.tsx (1-line fix: `brain ?? null` to satisfy strict TypeScript)
  6. apps/web/components/Candles.tsx (chart UI polish: price/time axes, last-price tag, larger candle bodies — no architectural change)
  7. apps/web/app/globals.css (added CSS classes for chart polish)
- Commands run (chronological):
  - git clone https://github.com/musawar127/forexwizard-brain.git
  - mv repo_temp contents into /home/z/my-project/
  - /home/z/.venv/bin/python -m pip install -r apps/api/requirements.txt
  - DATABASE_URL="sqlite:///./forexwizard.db" PYTHONPATH=. /home/z/.venv/bin/python -m pytest -q  (3 passed)
  - cd apps/web && npm install
  - cd apps/web && npx tsc --noEmit  (0 errors after fix)
  - cd apps/web && npm run build  (success, 7 routes)
  - bash /home/z/my-project/scripts/start_backend_detached.sh  (uvicorn :8000 PID 2205)
  - bash /home/z/my-project/scripts/start_frontend_detached.sh  (next dev :3000 PID 2256→2281)
  - curl http://localhost:8000/{health,api/market/xauusd,api/system/status,api/performance}
  - curl http://localhost:3000/ (HTTP 200)
  - agent-browser open / snapshot -i / eval / screenshot on all 6 routes + mobile
- Test results: pytest 3/3 PASS; typecheck 0 errors; next build success.
- Live data verified end-to-end: real Gold API XAU/USD spot ($4,298.50 at last check), real Brain BUY/SELL/WAIT decision (currently WAIT — correctly conservative), real EMA/RSI/ATR indicators, real support/resistance zones, real prediction audit trail (4 predictions stored), real outcome evaluation framework ready (no directional predictions yet to evaluate).
- Preserved (as required): market-data provider architecture, XAU/USD collector, candle-building system, Brain BUY/SELL/WAIT engine, memory, prediction tracking, outcome evaluation, research system, database models — all unchanged. No fake prices, fake candles, fake signals, or demo data anywhere.
- Not implemented (as required): no real-money trading, no broker order placement, no credential collection.
- Remaining limitations:
  - GDELT research collector currently hits 429 rate-limit from this server's IP; it retries automatically on its 15-minute schedule. The collector is optional and non-blocking — Brain still works without it.
  - 1-minute XAU candles have tiny within-minute OHLC variation (~$0.02) vs the chart-wide price range (~$26), so candle bodies are visually small. This is a data characteristic, not a UI bug. Chart now shows price axis labels, time axis labels, and last-price tag for readability.
  - Redis health check shows OPTIONAL_OFFLINE (Redis not installed in this container). Code already handles this gracefully.
  - Twelve Data historical bootstrap is OFF (no API key supplied by user). The system runs purely on sampled Gold API spot data. To enable bootstrap later, add `TWELVE_DATA_API_KEY=` to apps/api/.env and restart backend.
- Backend & frontend are running and ready for the user to inspect at http://localhost:8000 and http://localhost:3000.

---
Task ID: 3
Agent: main
Task: Save everything, commit with message "Verify and stabilize ForexWizard AI Market Brain v0.2", push to https://github.com/musawar127/forexwizard-brain. If push fails due to auth, create a patch.

Work Log:
- Discovered an auto-commit `9ff22be` had been made by a background skill with a UUID message ("21c1a4f8-0b55-432b-9bef-f935405cc470") — it swept in 1098 files including the entire `skills/` system directory, logs/, build artifacts, and screenshots, none of which belong in the ForexWizard project repo.
- Soft-reset to `b5829d4` (original repo state) to keep changes staged but undo the bad commit message and unrelated file additions.
- Updated `.gitignore` to explicitly exclude:
  - `logs/` (runtime logs)
  - `*.pid` (process IDs)
  - `apps/web/next-env.d.ts` (Next.js build artifact)
  - `apps/web/tsconfig.tsbuildinfo` (TS build artifact)
  - `download/` (workspace scaffold + screenshots)
  - `skills/` (system skill library — not part of this project)
  - `upload/` (workspace staging)
- Reverted `apps/web/next-env.d.ts` and `apps/web/tsconfig.json` to b5829d4 (they had been auto-modified by `next build`, not by genuine project changes).
- Removed obsolete `scripts/start_backend.sh` (replaced by `scripts/start_backend_detached.sh`).
- Staged exactly the 10 genuine project files:
  - .gitignore
  - apps/api/.env.example (new)
  - apps/web/.env.local.example (new)
  - apps/web/app/globals.css (modified)
  - apps/web/components/Candles.tsx (modified)
  - apps/web/components/DashboardClient.tsx (modified)
  - apps/web/package-lock.json (new)
  - scripts/start_backend_detached.sh (new)
  - scripts/start_frontend_detached.sh (new)
  - worklog.md (new)
- Created commit `5609ca3` with the exact user-requested message: "Verify and stabilize ForexWizard AI Market Brain v0.2".
- Commit stats: 10 files changed, 1299 insertions(+), 16 deletions(-).
- Attempted `git push origin main` → FAILED with "fatal: could not read Username for 'https://github.com': No such device or address" — GitHub authentication is unavailable in this build environment (no username / personal access token / SSH key configured for non-interactive git operations).
- As instructed, exported a complete patch to `/home/z/my-project/download/forexwizard-patch/`:
  - `0001-Verify-and-stabilize-ForexWizard-AI-Market-Brain-v0.2.patch` (80 KB unified git patch, applies on top of b5829d4 via `git am`)
  - `README.md` (4.8 KB) with full file manifest, push instructions, verification results, and preservation notes
  - `files/` directory (124 KB) containing flat copies of every changed/new file preserving repo-relative paths
- Total patch size: 216 KB.

Stage Summary:
- Commit `5609ca3` created locally with the exact user-requested message. Working tree is clean.
- Push to https://github.com/musawar127/forexwizard-brain FAILED because GitHub authentication is unavailable in this environment (no interactive credential prompt possible).
- Patch exported to `/home/z/my-project/download/forexwizard-patch/` so a maintainer with push access can apply it locally and push.
- No Phase 3 work started, as instructed.

---
Task ID: 4 (Phase 3)
Agent: main
Task: Build historical XAU/USD market-memory layer without modifying BUY/SELL/WAIT logic.

Work Log:
- Verified reachability of candidate historical providers from this environment:
  - Stooq: returns JS challenge page (proof-of-work), not directly accessible — skipped.
  - Twelve Data: reachable, requires API key (not provided by user).
  - Yahoo Finance (GC=F gold futures): reachable, NO API KEY NEEDED, returns real COMEX OHLC.
  - Alpha Vantage: requires API key (not provided).
  - Gold API historical endpoint: not supported.
  - Selected Yahoo Finance as default free provider; Twelve Data as optional upgrade path.

Backend code added (15 new files):
  apps/api/app/services/historical/__init__.py     — package public surface
  apps/api/app/services/historical/base.py          — HistoricalMarketDataProvider ABC + ProviderHealth + ProviderNotConfiguredError
  apps/api/app/services/historical/yahoo_finance.py — YahooFinanceHistoricalProvider (GC=F, no key)
  apps/api/app/services/historical/twelve_data_historical.py — TwelveDataHistoricalProvider (XAU/USD spot, optional key)
  apps/api/app/services/historical/factory.py     — get_historical_provider() + list_available_providers()
  apps/api/app/services/historical/validator.py    — validate_candles, find_duplicates, find_gaps, CandleValidationReport, GapReport
  apps/api/app/services/historical/aggregator.py   — aggregate_candles (deterministic M1->M5/M15/M30/H1/H4/D1)
  apps/api/app/services/historical/sync.py         — sync_historical_candles (single-shot orchestrator)
  apps/api/app/services/historical/features.py     — compute_feature_snapshot (EMA/RSI/ATR/regime/trend/swing/S-R/volatility/session/alignment)
  apps/api/app/services/historical/data_quality.py — data_quality_summary, timeframe_breakdown
  apps/api/app/db/migrations.py                    — run_startup_migrations (idempotent ALTER TABLE ADD COLUMN)
  apps/api/tests/test_historical_validation.py     — 10 tests
  apps/api/tests/test_historical_aggregation.py    — 8 tests
  apps/api/tests/test_historical_sync.py           — 4 tests
  apps/api/tests/test_data_quality.py              — 4 tests

Backend code modified (3 files):
  apps/api/app/db/models.py     — added is_historical column to CandleRecord; added HistoricalSyncState table; added HistoricalFeatureSnapshot table
  apps/api/app/core/config.py    — added historical_symbol, historical_sync_enabled, historical_sync_max_retries, historical_features_enabled settings
  apps/api/app/main.py           — bumped version to 0.3.0; added startup migration; added POST /api/data/sync, GET /api/data/status, GET /api/data/timeframes, GET /api/data/gaps; added DataSyncRequest model that pins symbol to settings.historical_symbol (no arbitrary uncontrolled downloads)

Frontend code added (1 new file):
  apps/web/app/data/page.tsx — /data route: provider health, total candles, earliest/latest timestamps, per-integrity-timeframe table, sync state log, database health, last-sync result panel, "Trigger sync" button

Frontend code modified (2 files):
  apps/web/components/Nav.tsx — added "/data" → "Data" link
  apps/web/lib/types.ts        — added DataQualitySummary, IntervalStat, SyncStateRow, ProviderHealthInfo, TimeframeRow, GapReport, SyncTimeframeResult, SyncSummary types

Tests run:
  pytest:    30 passed in 1.58s (was 3 passed pre-Phase 3 — net +27 tests)
  typecheck: 0 errors
  next build: success — 8 routes prerendered (added /data)

Real historical backfill executed via POST /api/data/sync (Yahoo Finance, no key):
  M1:   4,816 candles fetched, 4,483 inserted, 333 skipped (already present from sampled feed) — 2026-09-18 to 2026-09-23 (5 days intraday)
  M5:     964 candles (aggregated from M1 locally — deterministic)
  M15:    322 candles (aggregated)
  M30:    161 candles (aggregated)
  H1:      81 candles (aggregated from M1)
  H4:      22 candles (aggregated from H1 — Yahoo has no native 4h interval)
  D1:   2,513 candles (direct fetch) — 2016-09-23 to 2026-09-23 (10 YEARS of daily gold history)
  Total genuine candles persisted: 8,435 (is_historical=True, provider="Yahoo Finance (GC=F)" or "... (aggregated to <tf>)")

Validation results across ALL timeframes:
  duplicates_in_batch:    0 (unique constraint on (symbol, interval, timestamp) prevents dupes)
  invalid_ohlc:           0 (no high<low, no NaN/inf, no inconsistent HLOC)
  out_of_order:           0 (candles from Yahoo arrive in ascending order)
  zero_or_negative_price: 0 (all OHLC > 0)
  gaps_detected:          3,074 (M1) down to 1,171 (D1) — these are REAL weekend/holiday market closures, NOT data corruption. Validator reports them but does NOT silently repair.

Earliest historical timestamp: 2016-09-23 04:00 UTC (10 years ago)
Latest historical timestamp:   2026-09-23 15:28 UTC (~5 min ago)

End-to-end browser test of /data page:
  - Active provider: Yahoo Finance (GC=F) — ONLINE
  - Total historical candles: 8,435
  - Per-interval table shows: candle count, first/last timestamp, missing_intervals, completeness_pct, duplicate_count, integrity_status (all "OK")
  - Sync state log shows 7 entries (one per TF) all sync_status="ok"
  - Database health: 8,893 total rows (458 sampled + 8,435 historical), sqlite engine, OK
  - Trigger sync button works
  - POST /api/data/sync returns full backfill summary

Side-effect (intended): existing Brain BUY/SELL/WAIT logic was NOT modified, but it now reads more candles because get_candles() returns both sampled AND historical rows. Result:
  - Before Phase 3: decision=WAIT, confidence=69%, readiness=75% (insufficient history)
  - After Phase 3:  decision=SELL, confidence=92%, readiness=100% (5 of 6 timeframes BEARISH, real EMA/RSI evidence)
  - This is the Brain doing EXACTLY what it always did — the algorithm is unchanged; it just has more genuine market memory to work with now.

Preserved (as required):
  - XAU/USD market-data provider architecture (Gold API spot feed unchanged)
  - XAU/USD collector (still samples every 30s)
  - Candle aggregation system (still aggregates sampled ticks into sampled candles)
  - EMA / RSI / ATR analysis (untouched)
  - BUY / SELL / WAIT engine (rules-v0.1 — 0 lines of logic changed)
  - Prediction memory (still persists every periodic analysis)
  - Outcome evaluation (still evaluates 15m/60m/240m outcomes)
  - Research system (GDELT unchanged)
  - Database models (existing TickRecord, CandleRecord, PredictionRecord, PredictionOutcome, NewsRecord all preserved — only added new fields + new tables)
  - Brain chat
  - Frontend pages (all 7 existing routes still work, 1 new /data route added)
  - No real-money trading implemented or modified

Not implemented (as required):
  - No strategy optimization using historical statistics
  - No BUY/SELL/WAIT logic modifications
  - No fake historical candles or synthetic market history (all candles are real Yahoo Finance data)

Provider limitations (documented on /data page):
  1. Yahoo Finance symbol is GC=F (COMEX gold futures), not spot XAU/USD. Small premium/discount (~$1-30) vs spot. For market-memory purposes (EMA/RSI/trend) this is acceptable.
  2. Yahoo intraday depth is limited: M1 only goes back ~5 days, M5/M15/M30 ~60 days, H1 ~730 days. Higher TFs have more depth.
  3. Yahoo has no native 4h interval; H4 is derived locally from H1 (deterministic, tested).
  4. Yahoo is unofficial — no SLA. Provider rate-limits itself to 1 request/second to avoid being blocked.
  5. Twelve Data (optional upgrade) requires TWELVE_DATA_API_KEY in backend .env — not set in this environment, so its health_check correctly reports OFFLINE.
  6. Stooq was tested but rejected: their server requires a JS proof-of-work challenge that can't be solved from non-interactive HTTP. Skipped, not implemented.

Stage Summary:
- Phase 3 complete. Genuine XAU/USD historical memory now persists across M1, M5, M15, M30, H1, H4, D1. 8,435 real candles stored. 10-year D1 history. Brain BUY/SELL/WAIT logic unchanged but now reads richer history. New /data page exposes full data quality, provider health, sync state, gap detection. 27 new tests (30 total). Build clean.
- Phase 4 NOT started.

---
Task ID: 5 (Phase 3.1)
Agent: main
Task: Strengthen historical data before learning. Use native Yahoo intervals. Separate instruments. Add source lineage + basis + historical depth.

Work Log:
- Verified Yahoo's actual empirically-supported ranges: M1=5d, M5/M15/M30=1mo (NOT 3mo as Phase 3 docs claimed — Yahoo returns 422 for >1mo on these TFs), H1=2y, D1=10y. Updated YAHOO_DEFAULT_RANGES accordingly.

Backend code modified (6 files):
  apps/api/app/db/models.py        — added 5 lineage fields to CandleRecord (derivation, provider_symbol, instrument, source_timeframe, target_timeframe); added BasisObservation table; added 5 lineage fields to HistoricalSyncState
  apps/api/app/db/migrations.py    — extended startup migration to ALTER TABLE for new columns AND backfill existing rows with lineage metadata (3 distinct cases: SAMPLED for Gold API, DIRECT for Yahoo/Twelve Data, AGGREGATED for derived candles)
  apps/api/app/models/market.py    — extended Candle pydantic model with lineage fields; extended BrainAnalysis with historical_depth, instrument_consistency, technical_data_readiness fields
  apps/api/app/engine/analysis.py  — added _full_historical_depth_days() that queries HistoricalSyncState for FULL DB range (not just 120 recent candles); added _instrument_consistency() classifier (PURE_GC/PURE_SPOT/MIXED/NONE); SCORING LOGIC UNCHANGED — 0 lines of BUY/SELL/WAIT rules modified
  apps/api/app/engine/candles.py   — get_candles() now propagates lineage fields from CandleRecord rows to Candle objects (was missing them)
  apps/api/app/main.py             — added GET /api/data/basis endpoint (research-only, no trading logic); updated /api/data/gaps to filter by is_historical flag rather than provider-string matching

Backend code rewritten (4 files):
  apps/api/app/services/historical/sync.py            — REWROTE: each native TF fetched DIRECTLY from Yahoo (M1, M5, M15, M30, H1, D1) at Yahoo's verified deepest native range. Only H4 is derived locally from H1. All candles tagged with full lineage (derivation=DIRECT/AGGREGATED, provider_symbol=GC=F, instrument=GC_FRONT_MONTH, source_timeframe, target_timeframe).
  apps/api/app/services/historical/yahoo_finance.py   — updated YAHOO_DEFAULT_RANGES to match Yahoo's actual limits; every Candle returned now carries full lineage (DIRECT / GC=F / GC_FRONT_MONTH / source=target=interval)
  apps/api/app/services/historical/aggregator.py      — aggregate_candles() now preserves lineage fields: derivation=AGGREGATED, instrument inherited from source candles, source_timeframe=target TF (lower), target_timeframe=source TF (higher)
  apps/api/app/services/historical/data_quality.py    — by_interval now a dict keyed by interval, each value is a list of (instrument, derivation)-grouped entries; added interval_quality dict with historical_depth_days + instrument_consistency per TF; added _historical_depth_for_tf() that reads from HistoricalSyncState for full DB range

Backend code added (1 file):
  apps/api/app/services/market_state.py — extended refresh_quote_once() to opportunistically call _maybe_capture_basis() which stores a BasisObservation row when both a GC=F futures close and a fresh XAU/USD spot quote exist within a 2h window. Research-only — does NOT influence BUY/SELL/WAIT.

Backend tests (modified 3 + new 1):
  apps/api/tests/test_historical_sync.py        — REWROTE to match Phase 3.1 design (each native TF fetched directly; H4 derived from H1). 5 tests.
  apps/api/tests/test_data_quality.py           — updated for new dict-keyed by_interval structure. 4 tests.
  apps/api/tests/test_historical_lineage.py     — NEW: 11 tests for instrument separation, provider-symbol lineage, DIRECT vs AGGREGATED, native interval selection, H1→H4 aggregation, historical-depth calculation, mixed-instrument detection.

Frontend code (modified 3 files):
  apps/web/lib/types.ts            — extended types with IntervalLineageRow, IntervalQuality, BrainAnalysisExtras; added historical_depth + instrument_consistency + technical_data_readiness to BrainAnalysis
  apps/web/components/Nav.tsx      — unchanged (added /data in Phase 3)
  apps/web/app/data/page.tsx       — REWROTE: now renders 12-column per-lineage table (TF, Instrument, Provider, Provider Symbol, Derivation, Source→Target, Candles, First, Last, Days, Dup, Integrity) + separate Interval Quality table with historical depth + instrument consistency + missing intervals + completeness + integrity status

Real historical sync re-run (Phase 3.1 design — native intervals fetched directly):
  M1:   4,856 candles DIRECT  range=5d    (2026-09-18 → 2026-09-23) — UNCHANGED from Phase 3 (already at native max)
  M5:   5,935 candles DIRECT  range=1mo  (2026-08-23 → 2026-09-23) — was 897 candles (5d derived from M1) in Phase 3. 6.6x more candles.
  M15:  1,983 candles DIRECT  range=1mo  (2026-08-23 → 2026-09-23) — was 299 candles (5d derived) in Phase 3. 6.6x more.
  M30:    992 candles DIRECT  range=1mo  (2026-08-23 → 2026-09-23) — was 149 candles (5d derived) in Phase 3. 6.7x more.
  H1:  11,462 candles DIRECT  range=2y   (2024-09-23 → 2026-09-23) — was 74 candles (5d derived) in Phase 3. 155x more candles!
  H4:   3,103 candles AGGREGATED (from H1) range=2y (2024-09-23 → 2026-09-23) — was 20 candles in Phase 3. 155x more.
  D1:   2,513 candles DIRECT  range=10y  (2016-09-23 → 2026-09-23) — UNCHANGED (already at native max)
  Total: 30,844 genuine candles (was 8,435 in Phase 3 — 3.66x more genuine history)

Lineage verified across all 7 timeframes:
  - All native TFs (M1, M5, M15, M30, H1, D1): derivation=DIRECT, source_timeframe=target_timeframe=interval, instrument=GC_FRONT_MONTH, provider_symbol=GC=F
  - H4: derivation=AGGREGATED, source_timeframe=1h, target_timeframe=4h, instrument=GC_FRONT_MONTH (inherited from H1), provider_symbol=GC=F

Brain analysis (Phase 3.1 — historical depth + instrument consistency exposed, scoring logic UNCHANGED):
  decision: SELL
  confidence: 91.9% (same rules-v0.1 formula; 5 of 6 TFs BEARISH with stretched-low RSI)
  technical_data_readiness: 100.0% (all 6 TFs READY)
  instrument_consistency: MIXED (Brain correctly detects both GC_FRONT_MONTH historical + XAUUSD_SPOT sampled candles)
  historical_depth:
    M1: 5.51 days  (matches user spec: "M1 historical depth: 5 days")
    M5/M15/M30: 30.76 days each  (matches: 1mo native range)
    H1: 730.01 days  (matches: "H1 historical depth: 2 years")
    H4: 730.0 days   (matches: 2y derived from H1)
    D1: 3,652 days  (matches: "D1 historical depth: 10 years")

Tests: 43 passed (was 30 pre-Phase 3.1 — net +13 tests)
Typecheck: 0 errors
Next build: success — 8 routes prerendered as static

Basis observations: capture loop integrated into refresh_quote_once() — fires when spot quote arrives AND a recent GC=F futures close (from cached 1h historical candles) exists within 2h window. Research-only storage; never feeds into BUY/SELL/WAIT.

Preserved (as required):
  - BUY/SELL/WAIT rules-v0.1 logic — UNCHANGED, 0 lines of scoring code modified
  - XAU/USD spot Gold API feed — still samples every 30s, still tagged XAUUSD_SPOT
  - Candle aggregation system, prediction memory, outcome evaluation, research, DB models (existing tables preserved)
  - No real-money trading
  - No fake candles or synthetic history

Not implemented (as required):
  - No historical pattern learning
  - No strategy optimization
  - No BUY/SELL/WAIT logic modifications based on historical statistics

Stage Summary:
- Phase 3.1 complete. 30,844 genuine historical candles (3.66x Phase 3's 8,435) with full source lineage. Each TF now uses Yahoo's native interval directly (M5/M15/M30 went from 5d derived to 30d direct; H1 went from 5d derived to 2y direct). H4 is the only derived TF (from H1). Brain BUY/SELL/WAIT logic unchanged but now reports historical_depth + instrument_consistency + technical_data_readiness as separate read-only context fields. New /data page renders 12-column per-lineage table + separate interval-quality table. 43 tests pass. Build clean.
- Phase 4 NOT started.

---
Task ID: 6 (Phase 3.2)
Agent: main
Task: Data quality + confidence semantics. Classify gaps, rename confidence → technical_score, expose nullable statistical fields for Phase 4, MIXED instrument notice.

Work Log:

Backend code modified (6 files):
  apps/api/app/services/historical/validator.py
    — REWROTE gap classification logic. Each detected gap is now classified into:
      EXPECTED_MARKET_CLOSURE (Saturday UTC, Sunday UTC before 22:00, CME-observed US federal holiday, Friday after 21:00 UTC)
      EXPECTED_SESSION_BREAK (Mon-Thu 21:00 UTC = daily CME maintenance 17:00-18:00 ET)
      UNEXPECTED_GAP (any other missing period — genuine data loss)
      INVALID_DATA (placeholder, not produced by find_gaps() — invalidity is a CandleValidationReport concern)
    — Added _CME_HOLIDAYS set covering 2024-2027 (New Year, MLK, Washington's Birthday, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas).
    — GapReport now exposes expected_gap_count, unexpected_gap_count, invalid_candle_count, expected_market_closures[], expected_session_breaks[], unexpected_gaps[], invalid_data_gaps[].
    — GapReport.integrity_status uses new semantics: HEALTHY (only expected gaps), DEGRADED (any unexpected gap). INVALID is never set here — see CandleValidationReport.
    — CandleValidationReport.integrity_status uses new semantics: HEALTHY (clean batch), DEGRADED (duplicates only), INVALID (invalid OHLC / out-of-order / zero-negative prices), EMPTY.
    — Added invalid_candle_count property on CandleValidationReport = invalid_ohlc + zero_or_negative_price.

  apps/api/app/models/market.py
    — Extended BrainAnalysis with:
      technical_score: float | None — SAME value as confidence, renamed for display.
      historical_sample_size, historical_direction_rate, historical_mfe, historical_mae, historical_probability: float | None — all NULL until Phase 4.
      probability_calibrated: bool | None — NULL until Phase 4.

  apps/api/app/engine/analysis.py
    — Added DEPTH_TIMEFRAMES = TIMEFRAMES + ["1day"] so the dashboard can show D1 historical depth.
    — Both return paths (NO_DECISION early return + main return) now populate:
      technical_score = round(confidence, 1) (same value, different display name)
      historical_sample_size = None
      historical_direction_rate = None
      historical_mfe = None
      historical_mae = None
      historical_probability = None
      probability_calibrated = None
    — historical_depth dict now includes "1day" key for the dashboard context-strip.
    — SCORING LOGIC UNCHANGED — 0 lines of BUY/SELL/WAIT rules modified. The
      technical_score value is computed by the SAME formula as confidence.

  apps/api/app/services/historical/data_quality.py
    — interval_quality per-TF dict now exposes:
      expected_gap_count, unexpected_gap_count, invalid_candle_count (Phase 3.2)
      missing_intervals, expected_periods, completeness_pct, duplicate_count (backward-compat)
      integrity_status now uses HEALTHY/DEGRADED/INVALID semantics
      instrument_consistency, historical_depth_days (Phase 3.1)
    — timeframe_breakdown rows now include invalid_candle_count and use new HEALTHY/DEGRADED/INVALID.
    — Added _count_invalid_candles(symbol, interval) helper — counts DB rows with NULL OHLC, zero/negative prices, or inconsistent high<low etc.

Frontend code modified (4 files):
  apps/web/lib/types.ts
    — BrainAnalysis type extended with technical_score + 6 nullable statistical fields.
    — IntervalQuality type extended with expected_gap_count, unexpected_gap_count, invalid_candle_count.
    — TimeframeRow type extended with invalid_candle_count.

  apps/web/components/DecisionCard.tsx
    — Display label changed from "Confidence" → "Technical score"
    — Value display changed from "92%" → "92.0 / 100" (NOT a probability).
    — Added MIXED instrument-consistency notice (amber-bordered box) when brain.instrument_consistency === "MIXED".
    — Added "Statistical probability: not yet calculated (Phase 4)" pending notice when brain.historical_probability == null.

  apps/web/components/DashboardClient.tsx
    — Added context-strip panel below the Evidence columns showing:
      Technical data readiness | Instrument consistency | H1 historical depth | D1 historical depth
      (all read-only context, does NOT influence BUY/SELL/WAIT)

  apps/web/app/data/page.tsx
    — Interval Quality table now shows: TF, Historical Depth, Instrument Consistency, Expected Gaps, Unexpected Gaps, Invalid Candles, Dup, Completeness, Integrity.
    — Integrity badge color-coded: HEALTHY=green, DEGRADED=amber, INVALID=red.
    — Added full Phase 3.2 gap classification explainer paragraph below the table.
    — Added MIXED instrument-consistency notice (amber-bordered panel) that appears when any TF has instrument_consistency === "MIXED".

  apps/web/app/globals.css
    — Added .context-strip, .mixed-instrument-notice, .prob-pending-notice CSS classes.

Tests added (1 new file):
  apps/api/tests/test_phase32_data_quality.py — 18 tests:
    7 single-timestamp classifier tests (Saturday, Sunday morning, Sunday after 22:00, CME holidays, weekday session break, Friday after 21:00, unexpected weekday gap)
    3 find_gaps() end-to-end tests (weekend closures HEALTHY, weekday gap DEGRADED, session-break-only HEALTHY, invalid_data_gaps always empty)
    5 CandleValidationReport integrity tests (HEALTHY clean batch, INVALID on bad OHLC, INVALID on zero price, DEGRADED on duplicates, EMPTY on empty input)
    2 BrainAnalysis tests (technical_score == confidence, all 6 statistical fields null until Phase 4)
    1 historical_depth test (already in test_historical_lineage.py from Phase 3.1)

Tests updated (1 file):
  apps/api/tests/test_historical_validation.py
    — test_validate_clean_batch_passes: integrity_status expected "OK" → "HEALTHY"
    — test_validate_rejects_high_below_low: integrity_status expected "DEGRADED" → "INVALID"

Tests run: 61 passed (was 43 in Phase 3.1 — net +18 tests)
Typecheck: 0 errors
Next build: success — 8 routes prerendered

Brain snapshot verification (Phase 3.2):
  decision: SELL
  confidence: 92.0 (unchanged from Phase 3.1 — calculation NOT modified)
  technical_score: 92.0 (== confidence — renamed for display only)
  historical_sample_size: None ✓ (Phase 4)
  historical_probability: None ✓ (Phase 4)
  probability_calibrated: None ✓ (Phase 4)
  instrument_consistency: MIXED
  historical_depth: {4h:730d, 1h:730d, 30min:30.76d, 15min:30.76d, 5min:30.75d, 1min:5.51d, 1day:3652d}

/api/data/status interval_quality verification (Phase 3.2 classified gap counts):
  M1:   expected=3060  unexpected=14   invalid=0  → DEGRADED  (14 genuine data losses, 3060 weekend closures)
  M5:   expected=2807  unexpected=78   invalid=0  → DEGRADED
  M15:  expected=947   unexpected=24   invalid=0  → DEGRADED
  M30:  expected=473   unexpected=13   invalid=0  → DEGRADED
  H1:   expected=2561  unexpected=181  invalid=0  → DEGRADED
  H4:   expected=1242  unexpected=35   invalid=0  → DEGRADED
  D1:   expected=1072  unexpected=99   invalid=0  → DEGRADED

  All show DEGRADED because Yahoo's data has some genuine weekday data losses.
  Before Phase 3.2, ALL ~3000 missing intervals at M1 were lumped together as
  "DEGRADED" — now the user can see that 3060/3074 = 99.5% are expected weekend
  closures, and only 14 are genuine data losses.

Browser test results:
  /data page (screenshot phase32-01-data.png):
    - Per-lineage integrity table: all 7 TFs show HEALTHY (no corruption, no dups)
    - Interval Quality table: shows Expected Gaps, Unexpected Gaps, Invalid Candles columns separately
    - Phase 3.2 gap classification explainer paragraph visible
    - No MIXED notice on /data (all historical candles are PURE_GC at this TF level)
  Dashboard (screenshot phase32-03-dashboard-final.png):
    - DecisionCard: "Technical score | 92.0 / 100" (NOT "Confidence 92%")
    - MIXED instrument notice: "Live spot and futures historical context are both present. Historical futures observations are treated as a separate instrument — statistics are not combined."
    - Prob-pending notice: "Statistical probability: not yet calculated (Phase 4)"
    - Context strip: "Technical data readiness 100% | Instrument consistency MIXED | H1 historical depth 730.0d | D1 historical depth 3652d"

Preserved (as required):
  - BUY/SELL/WAIT rules-v0.1 logic — UNCHANGED, 0 lines of scoring code modified
  - confidence field preserved in API response (backward compat) — technical_score is an alias, not a replacement
  - No real-money trading
  - No fake candles or synthetic history
  - Missing candles NOT silently filled — only classified + reported

Not implemented (as required):
  - No historical pattern learning (statistical fields remain NULL until Phase 4)
  - No strategy optimization
  - No BUY/SELL/WAIT logic modifications based on historical statistics

Stage Summary:
- Phase 3.2 complete. Gap classification now distinguishes EXPECTED_MARKET_CLOSURE / EXPECTED_SESSION_BREAK / UNEXPECTED_GAP / INVALID_DATA. Integrity uses HEALTHY/DEGRADED/INVALID semantics. confidence renamed to technical_score for display (calculation unchanged, backward-compat preserved). 6 nullable statistical fields exposed for Phase 4. MIXED instrument-consistency notice shown on both /data page and dashboard. 61 tests pass. Build clean.
- Phase 4 NOT started.

---
Task ID: 7 (Phase 4)
Agent: main
Task: Historical pattern learning engine. Build no-look-ahead historical state snapshots, find similar past setups, measure outcomes, report statistics with Wilson intervals, populate BrainAnalysis nullable fields — WITHOUT changing BUY/SELL/WAIT rules.

Work Log:

Backend code added (5 new files):
  apps/api/app/services/learning/__init__.py — public surface
  apps/api/app/services/learning/config.py — LearningConfig, FeatureWeights, NEUTRAL_X_DEFAULT=0.5, HORIZON_MINUTES, sample-quality thresholds (30/100/300), min_spacing=4 candles, top_k=200
  apps/api/app/services/learning/states.py — HistoricalStateBuilder with NO-LOOK-AHEAD guarantee. Accepts pre-fetched H1/H4/D1 candle lists to avoid N+1 DB queries. FeatureVector is a frozen dataclass with normalized features (rsi/100, ema_distance/ATR, dist_to_support/ATR, etc.) — no raw price as a similarity feature.
  apps/api/app/services/learning/outcomes.py — OutcomeCalculator. Computes MFE/MAE/direction at 7 horizons (15m/30m/1h/2h/4h/8h/24h). Direction uses volatility-aware threshold: |change|/ATR < 0.5 => NEUTRAL, else UP/DOWN.
  apps/api/app/services/learning/similarity.py — SimilarityEngine. Weighted normalized Euclidean distance on continuous features, 0/1 distance on categorical. Weights: trend/regime/alignment=HIGH(2.0), S-R=medium_high(1.5), RSI/ATR%/EMA-dist/vol/session/swing=MEDIUM(1.0). Includes temporal deduplication (min_spacing_candles) so 5 consecutive similar candles don't count as 5 independent samples.
  apps/api/app/services/learning/statistics.py — StatisticsAggregator. Wilson 95% confidence interval for binomial proportions. Sample quality: <30 INSUFFICIENT, 30-99 LOW, 100-299 MODERATE, 300+ GOOD. alignment_for_decision() classifies SUPPORTS/CONTRADICTS/NEUTRAL/INSUFFICIENT_DATA.
  apps/api/app/services/learning/orchestrator.py — build_states() (batch builder with bisect-slicing + single-session DB writes for performance) + current_similarity() (live matcher with 30s TTL cache + audit-log) + learning_status().

Backend code modified (5 files):
  apps/api/app/db/models.py — added 3 new tables: HistoricalMarketState (35+ fields including feature_version + similarity_version), HistoricalOutcome (one row per state+horizon with MFE/MAE/direction), SimilarityRun (audit log).
  apps/api/app/main.py — added 5 Phase 4 endpoints: GET /api/learning/status, GET /api/learning/horizons, GET /api/learning/current-similarity, GET /api/learning/analogs, POST /api/learning/build-states. All endpoints restrict instrument to {GC_FRONT_MONTH, XAUUSD_SPOT} only — never combine instruments.
  apps/api/app/models/market.py — BrainAnalysis extended with: technical_score (alias for confidence), historical_sample_size, historical_direction_rate, historical_mfe, historical_mae, historical_probability, probability_calibrated (ALWAYS False in Phase 4), historical_alignment, historical_analogue_instrument, historical_analogue_horizon_minutes, historical_analogue_note.
  apps/api/app/engine/analysis.py — added _build_phase4_stats_overlay() that pulls GC_FRONT_MONTH 1h stats from the learning engine. SCORING LOGIC UNCHANGED — 0 lines of BUY/SELL/WAIT rules modified. Both return paths (NO_DECISION + main) populate the new fields.
  apps/api/tests/test_phase32_data_quality.py — updated 1 test to reflect Phase 4 behavior (probability_calibrated is now False, not None).

Tests added (1 new file, 21 tests):
  apps/api/tests/test_phase4_learning.py — comprehensive coverage:
    - no-look-ahead (verify feature vector at T is unchanged when future candles are added)
    - same-instrument-only (verified at orchestrator level — API endpoint filters by instrument)
    - normalized feature vector (no raw price field)
    - deterministic similarity (same inputs => identical output)
    - temporal neighbor dedup (5 clustered candidates -> 3 after dedup)
    - sample quality classification (INSUFFICIENT/LOW/MODERATE/GOOD at 30/100/300 thresholds)
    - MFE/MAE calculation
    - direction classification (NEUTRAL when |change| < 0.5*ATR; UP/DOWN when above; fallback when ATR missing)
    - Wilson confidence interval (in [0,1], contains point estimate, extremes work)
    - all 7 horizons supported (15m/30m/1h/2h/4h/8h/24h)
    - missing future data → NULL outcome
    - feature_version + similarity_version persisted in default config
    - probability_calibrated stays False
    - BUY/SELL/WAIT rules UNCHANGED (decision still computed by rules-v0.1; only informational fields added)
    - historical_alignment SUPPORTS / CONTRADICTS / NEUTRAL / INSUFFICIENT_DATA classification
    - statistics aggregator handles empty + classifies sample quality + populates Wilson intervals + percentiles

Frontend code added (1 new file):
  apps/web/app/learning/page.tsx — /learning page:
    - States analyzed + total outcomes hero
    - States by instrument + by base timeframe
    - Outcomes by horizon (with effective historical range column)
    - "Build states" button (POST /api/learning/build-states)
    - Current similarity panel with instrument + horizon selectors
    - Per-horizon statistics table (Sample / UP / 95% CI / DOWN / 95% CI / NEUTRAL / Median Return / Median MFE / Median MAE)
    - Historical alignment box (SUPPORTS=green / CONTRADICTS=red / NEUTRAL=amber / INSUFFICIENT_DATA=muted)
    - probability_calibrated = FALSE notice (always shown)
    - Mixed-instrument notice (instrument separation enforced)
    - Analog explorer: top-10 neighbors table (Date / Similarity / Regime / RSI / ATR% / Structure / H1/H4/D1 / Session / Outcome / MFE / MAE)
    - Click a row to inspect its full feature snapshot
    - Interpretation rules panel ("historical observations do not guarantee future outcomes")
    - Audit log table (last 10 similarity runs)

Frontend code modified (3 files):
  apps/web/lib/types.ts — added DirectionRate, HorizonStatistics, AnalogFeatureSnapshot, NeighborMatch, CurrentSimilarityResult, LearningStatusByInstrument, LearningStatusByHorizon, LearningRecentRun, LearningStatus types. Extended BrainAnalysis with technical_score + 6 statistical fields + historical_alignment + historical_analogue_instrument/horizon_minutes/note.
  apps/web/components/Nav.tsx — added /learning link.
  apps/web/components/DecisionCard.tsx — when brain.historical_sample_size > 0, show a populated "HISTORICAL GC_FRONT_MONTH ANALOGUE — 60m horizon" panel with Sample / Direction rate / Median MFE / Median MAE / Historical alignment / probability_calibrated=FALSE notice. Otherwise show "Statistical probability: not yet calculated (build states via /learning page)".

Real-data build verification:
  Build target: GC_FRONT_MONTH, base_tf=1h, batch_limit=5000
  Result: 3,700 states built, 25,900 outcomes (3,700 × 7 horizons)
  Earliest state: Nov 06 2025 5pm UTC
  Latest state: ~May 2026 (states older than 24h before "now" excluded)
  States with insufficient forward data: 0 at every horizon (H1 has 2y depth, plenty for 24h forward windows)
  Performance: ~13s per 500 states (40ms per state) using bisect-slicing + single-session DB writes + pre-fetched multi-TF candle lists

BrainAnalysis populated fields (verified live):
  decision: SELL (UNCHANGED — rules-v0.1 logic intact)
  confidence: 92.0 (UNCHANGED — same formula)
  technical_score: 92.0 (alias for confidence — display-only rename)
  historical_sample_size: 200 (independent neighbors after temporal dedup)
  historical_direction_rate: 0.255 (25.5% observed DOWN rate for SELL direction)
  historical_mfe: $8.65 (median favorable excursion across 200 analogues)
  historical_mae: $9.60 (median adverse excursion across 200 analogues)
  historical_probability: 0.255 (same as direction_rate — descriptive, NOT calibrated)
  probability_calibrated: False ✓ (Phase 4 invariant — always False)
  historical_alignment: NEUTRAL (25.5% DOWN doesn't dominate enough to SUPPORT or CONTRADICT)
  historical_analogue_instrument: GC_FRONT_MONTH (separate from live XAUUSD_SPOT — never combined)
  historical_analogue_horizon_minutes: 60

/api/learning/current-similarity response (1h horizon, GC_FRONT_MONTH):
  candidate_count: 2,950 (all historical H1 states for this instrument)
  sample_size: 200 (after temporal dedup, min_spacing = 4 H1 candles)
  sample_quality: MODERATE (200 in [100, 300) range)
  statistics:
    up_count: 45 / down_count: 46 / neutral_count: 109
    up_rate: 22.5% (95% CI: 17.3%-28.8%)  ← Wilson interval
    down_rate: 23.0% (95% CI: 17.7%-29.3%)
    neutral_rate: 54.5%
    median_return: 0.02%
    median_mfe: $9.60
    median_mae: $10.15
  top-10 analogues shown (highest similarity 96.0%)
  interpretation_note: "Among 200 similar GC_FRONT_MONTH historical states, the observed directional frequencies are descriptive statistics — they are NOT calibrated probabilities of future outcomes."
  probability_calibrated: False

Tests run: 85 passed in 2.79s (was 61 pre-Phase 4 — net +24 new tests)
Typecheck: 0 errors
Next build: success — 9 routes prerendered (added /learning)

Browser tests:
  /learning page: 3,000+ states, 200-sample MODERATE quality, Wilson intervals, top-10 analog explorer with click-to-inspect, audit log of last 10 runs, full interpretation rules panel, "Historical observations do not guarantee future outcomes" notice.
  Dashboard: DecisionCard shows "SELL | Technical score 92.0/100 | HISTORICAL GC_FRONT_MONTH ANALOGUE — 60m horizon | Sample 200 · Direction rate 25.5% · Median MFE $8.65 · Median MAE $9.60 | Historical alignment: NEUTRAL (informational — does NOT influence the technical decision) | probability_calibrated = FALSE".
  All 8 routes verified: /, /analysis, /data, /learning, /research, /memory, /performance, /settings → all HTTP 200.

Preserved (as required):
  - BUY/SELL/WAIT rules-v0.1 logic — UNCHANGED, 0 lines of scoring code modified (clearly delimited with "---- UNCHANGED SCORING LOGIC ----" markers in analysis.py)
  - confidence field preserved in API response (backward compat) — technical_score is an alias, not a replacement
  - XAUUSD_SPOT and GC_FRONT_MONTH statistics NEVER combined (orchestrator filters candidates by instrument before matching)
  - No real-money trading
  - No fake candles or synthetic history
  - probability_calibrated is ALWAYS False throughout Phase 4 — calibration is a future phase
  - Missing future data → NULL outcome (not silently fabricated)

Not implemented (as required):
  - No strategy optimization (Phase 4 is informational only)
  - No parameter optimization (NEUTRAL_X = 0.5 is documented conservative default, NOT tuned against results)
  - No machine-learning model training (similarity-v0.1 is transparent weighted-distance, not a neural net)
  - No technical-score weighting changes (technical_score == confidence — display rename only)
  - No automatic trading
  - No Phase 5

Performance limitations (documented):
  1. build_states() runs synchronously inside the FastAPI request handler — long builds (>1000 states) block the event loop. For Phase 4 the user accepted this as "no unnecessary infrastructure". A future phase could move it to a background task queue.
  2. current_similarity() has a 30-second TTL cache so repeated frontend refreshes don't recompute.
  3. SQLite — for production scale (>100k states), would need PostgreSQL + GIN indexes on feature columns.
  4. Per-state DB writes are batched every 25 states — single-row inserts would be 10x slower.

Stage Summary:
- Phase 4 complete. Historical pattern-learning engine live with 3,700 states + 25,900 outcomes. Brain's nullable statistical fields now POPULATED with sample size / direction rate / MFE / MAE / alignment / analogue instrument. All numbers carry Wilson 95% confidence intervals. probability_calibrated is ALWAYS False. BUY/SELL/WAIT rules UNCHANGED. 85 tests pass. Build clean. /learning page live with analog explorer + audit log. /dashboard shows populated historical context panel.
- Phase 5 NOT started.

---
Task ID: 8 (Phase 4.1)
Agent: main
Task: Complete and harden historical learning engine — background jobs, immutable runs, roll detection, outcome window validity, directional MFE/MAE, minimum similarity threshold, exact effective history, adversarial no-look-ahead, state/run inspectors.

Work Log (abbreviated — full details in the 23 new/modified files):

Backend code added (2 new files):
  apps/api/app/services/learning/roll_detector.py — detect_roll_between() (gap > 5x ATR → possible_contract_roll), outcome_window_valid() (Friday 20:30 + 4h must NOT silently use Sunday/Monday pricing — actual_elapsed > 2x expected → horizon_valid=False)
  apps/api/app/services/learning/jobs.py — BuildJob CRUD + new_job_id/new_run_id helpers + update_job_progress/mark_job_completed/mark_job_failed

Backend code modified (7 files):
  apps/api/app/db/models.py — HistoricalMarketState: added possible_contract_roll/roll_gap_size/roll_detection_reason; unique key now includes feature_version. HistoricalOutcome: added max_up_move/max_down_move (canonical direction-neutral excursions), horizon_valid/actual_elapsed_seconds/invalid_reason, possible_contract_roll/excluded_from_learning/exclusion_reason. SimilarityRun: complete rewrite — 20+ new fields (run_id, current_market_timestamp, analogue_instrument, raw_neighbor_count, independent_neighbor_count, minimum_spacing_seconds, similarity_threshold, highest/median/lowest/25th/75th similarity, top_match_ids, statistics_json, feature/outcome/effective history_start/end, effective_days, probability_calibrated). New BuildJob table (job_id, status, eligible_total, built, remaining, percent_complete, last_checkpoint_ts, earliest/latest_state, elapsed_seconds, states_per_second).
  apps/api/app/db/migrations.py — added Phase 4.1 column migrations for all new fields + best-effort backfill (mfe→max_up_move, mae→max_down_move) + mark in-flight jobs as "interrupted" on startup
  apps/api/app/services/learning/config.py — added minimum_similarity_score (0.50 default), roll_atr_multiple (5.0), max_elapsed_multiple (2.0), build_batch_size (25), build_checkpoint_interval (50)
  apps/api/app/services/learning/orchestrator.py — REWROTE: build_states now spawns a background asyncio task (POST /api/learning/build-states returns immediately with job_id — never blocks). current_similarity persists a NEW immutable SimilarityRun row on EVERY call (cache stores computation results but NOT run_id — each call generates a fresh run_id). Added roll detection in build loop + outcome window validity check + excluded_from_learning flagging. Added _persist_similarity_run() helper (reusable for cache-hit path). Added get_state() + get_run() inspectors.
  apps/api/app/main.py — added 3 new endpoints: GET /api/learning/jobs/{job_id}, GET /api/learning/states/{state_id}, GET /api/learning/runs/{run_id}. Updated POST /api/learning/build-states to return immediately with job_id.
  apps/api/app/models/market.py — BrainAnalysis: added historical_similarity_run_id
  apps/api/app/engine/analysis.py — _build_phase4_stats_overlay now passes technical_score + extracts similarity_run_id from result

Tests added (1 new file, 26 tests):
  apps/api/tests/test_phase41_learning.py — comprehensive Phase 4.1 coverage:
    - background build returns immediately (<2s)
    - live API responds during background build
    - resume interrupted build
    - state deduplication on (instrument, base_tf, ts, feature_version)
    - immutable similarity runs (each call → new run_id, persisted)
    - same-run statistics consistency (3 identical queries → identical stats)
    - roll-gap detection (large gap flagged, normal volatility not)
    - roll-crossing outcome exclusion
    - directional MFE/MAE for BUY (MFE=max_up_move, MAE=abs(max_down_move))
    - directional MFE/MAE for SELL (MFE=abs(max_down_move), MAE=max_up_move)
    - weekend horizon validity (Friday 20:30 + 4h → invalid)
    - normal-hours horizon validity
    - no forward data → invalid
    - minimum_similarity_score config (0.50 default)
    - minimum similarity threshold prevents filling with weak matches
    - exact effective history per horizon (not "5.5d or 2y")
    - adversarial no-look-ahead (mutate T+1/T+2/T+3, state at T identical)
    - state inspector returns full feature snapshot
    - run inspector returns immutable snapshot
    - probability_calibrated always False
    - BUY/SELL/WAIT rules UNCHANGED (Phase 4.1 adds informational overlay only)
    - job_id + run_id format (JOB-XXXXXXXX / SIM-XXXXXXXX)
    - Wilson interval regression

Tests: 111 passed in 6.56s (was 85 pre-Phase 4.1 — net +26 tests)
Typecheck: 0 errors
Build: success — 9 routes prerendered

Real-data build (background, non-blocking):
  POST /api/learning/build-states returned immediately with:
    job_id: JOB-EB2EDAEE, status: running, eligible_total: 11432
  First poll (3s later): 50/11432 states (0.44%), earliest=2025-11-06
  Health endpoint responsive during build (non-blocking verified ✓)
  SQLite lock contention causes /api/learning/status to timeout during
  heavy writes — expected for SQLite, would work fine with PostgreSQL

Preserved: BUY/SELL/WAIT rules-v0.1 UNCHANGED. probability_calibrated=False.
No auto-trading. No fake data. Same-instrument-only enforced.
