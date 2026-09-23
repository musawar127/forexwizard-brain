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
