import type { Candle, Snapshot } from "./types";

// Phase 5.4: Use NEXT_PUBLIC_API_URL for production deployment.
// Falls back to http://localhost:8000 for local development.
export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function getSnapshot(): Promise<Snapshot> {
  const response = await fetch(`${API}/api/market/xauusd`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Backend returned ${response.status}`);
  return response.json();
}

export async function getCandles(interval = "1min", limit = 120): Promise<Candle[]> {
  const response = await fetch(`${API}/api/market/candles?interval=${interval}&limit=${limit}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Candle endpoint returned ${response.status}`);
  const payload = await response.json();
  return payload.candles || [];
}
