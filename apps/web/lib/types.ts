export type Quote = {
  symbol: string;
  price: number;
  provider: string;
  market_timestamp: string | null;
  received_timestamp: string;
  age_seconds: number | null;
  status: "RECENT" | "STALE" | "NO_DATA";
};

export type Zone = {
  kind: "SUPPORT" | "RESISTANCE";
  low: number;
  high: number;
  touches: number;
  strength: number;
};

export type TimeframeState = {
  timeframe: string;
  candles: number;
  status: string;
  trend: string;
  rsi: number | null;
  atr: number | null;
  ema_fast: number | null;
  ema_slow: number | null;
};

export type BrainAnalysis = {
  symbol: string;
  timestamp: string;
  decision: "BUY" | "SELL" | "WAIT" | "NO_DECISION";
  confidence: number;
  readiness: number;
  regime: string;
  risk: string;
  score: number;
  price: number | null;
  reasons_for: string[];
  reasons_against: string[];
  invalidation: number | null;
  support: Zone | null;
  resistance: Zone | null;
  timeframes: TimeframeState[];
  data_quality: string;
  message: string;
  brain_version: string;
  // Phase 3.1: distinguish technical readiness from historical depth +
  // instrument consistency. These do NOT influence the BUY/SELL/WAIT
  // decision — only clarify what the confidence number is backed by.
  historical_depth?: Record<string, number>;
  instrument_consistency?: string;
  technical_data_readiness?: number;
};

export type Snapshot = {
  quote: Quote | null;
  source_status: string;
  last_error: string | null;
  research_status: string;
  research_error: string | null;
  last_research_at: string | null;
  brain: BrainAnalysis | null;
};

export type Candle = {
  symbol: string;
  interval: string;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  sample_count: number;
  provider: string;
};

export type ResearchItem = {
  id: number;
  title: string;
  url: string;
  domain: string;
  seen_at: string | null;
  language: string | null;
  source_country: string | null;
  topic: string;
  discovered_at: string;
};

// ---------------------------------------------------------------------------
// Phase 3 + 3.1: Historical market-memory types
// ---------------------------------------------------------------------------

export type ProviderHealthInfo = {
  provider_name: string;
  reachable: boolean;
  requires_api_key: boolean;
  has_api_key: boolean;
  last_error: string | null;
  extra: Record<string, unknown> | null;
};

/** Phase 3.1: per-(interval, instrument, derivation) row — one entry per
 * lineage group. The /data page renders one row per group so DIRECT and
 * AGGREGATED candles at the same TF appear as separate rows. */
export type IntervalLineageRow = {
  interval: string;
  provider: string;
  derivation: string;        // "DIRECT" | "AGGREGATED" | "SAMPLED"
  instrument: string;        // "GC_FRONT_MONTH" | "XAUUSD_SPOT"
  provider_symbol: string;  // "GC=F" | "XAU"
  source_timeframe: string;
  target_timeframe: string;
  candle_count: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  days_covered: number | null;
};

export type IntervalQuality = {
  interval: string;
  missing_intervals: number;
  expected_periods: number;
  completeness_pct: number;
  duplicate_count: number;
  integrity_status: string;
  instrument_consistency: string;  // PURE_GC | PURE_SPOT | MIXED | NONE
  historical_depth_days: number;
};

export type SyncStateRow = {
  provider: string;
  interval: string;
  earliest_timestamp: string | null;
  latest_timestamp: string | null;
  last_sync_at: string | null;
  total_candles: number;
  sync_status: string;
  last_error: string | null;
  instrument: string;
  provider_symbol: string;
  derivation: string;
  source_timeframe: string;
  target_timeframe: string;
};

export type DataQualitySummary = {
  symbol: string;
  active_provider: string | null;
  providers: ProviderHealthInfo[];
  historical: {
    earliest_timestamp: string | null;
    latest_timestamp: string | null;
    total_candles: number;
  };
  by_interval: Record<string, IntervalLineageRow[]>;  // Phase 3.1: dict keyed by interval
  interval_quality: Record<string, IntervalQuality>;  // Phase 3.1: per-interval quality
  sync_states: SyncStateRow[];
  database_health: {
    total_candle_rows: number;
    historical_candle_rows: number;
    storage_engine: string;
    ok: boolean;
  };
  generated_at: string;
};

export type TimeframeRow = {
  interval: string;
  provider: string;
  derivation: string;
  instrument: string;
  provider_symbol: string;
  source_timeframe: string;
  target_timeframe: string;
  candle_count: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  days_covered: number | null;
  duplicate_count: number;
  integrity_status: string;
};

export type GapReport = {
  interval: string;
  interval_seconds: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  actual_count: number;
  expected_periods: number;
  missing_periods: number;
  gaps: string[];
  completeness_pct: number;
};

export type SyncTimeframeResult = {
  candles_fetched: number;
  duplicates_in_batch: number;
  invalid_ohlc: number;
  out_of_order: number;
  zero_or_negative_price: number;
  gaps_detected: number;
  inserted: number;
  skipped_already_present: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  sync_status: string;
  last_error: string | null;
  instrument: string | null;
  provider_symbol: string | null;
  derivation: string;
  source_timeframe: string | null;
  target_timeframe: string;
};

export type SyncSummary = {
  provider: string;
  symbol: string;
  started_at: string;
  completed_at?: string;
  timeframes: Record<string, SyncTimeframeResult>;
  notes: string[];
};

/** Phase 3.1: BrainAnalysis now carries historical_depth per TF +
 * instrument_consistency + technical_data_readiness (separate from
 * the legacy "readiness" field which is kept for backward compat). */
export type BrainAnalysisExtras = {
  historical_depth: Record<string, number>;
  instrument_consistency: string;  // PURE_GC | PURE_SPOT | MIXED | NONE
  technical_data_readiness: number;
};
