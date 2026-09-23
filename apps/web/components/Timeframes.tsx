import type { TimeframeState } from "@/lib/types";

function pretty(tf: string) {
  return ({ "1min": "M1", "5min": "M5", "15min": "M15", "30min": "M30", "1h": "H1", "4h": "H4", "1day": "D1" } as Record<string,string>)[tf] || tf;
}

export function Timeframes({ items = [] }: { items?: TimeframeState[] }) {
  return (
    <div className="tf-grid">
      {items.map((item) => {
        const tone = item.trend === "BULLISH" ? "up" : item.trend === "BEARISH" ? "down" : "flat";
        return (
          <div className="tf-card" key={item.timeframe}>
            <div className="tf-top"><strong>{pretty(item.timeframe)}</strong><span className={`trend-pill ${tone}`}>{item.trend.replaceAll("_", " ")}</span></div>
            <div className="tf-stats"><span>RSI <b>{item.rsi ?? "—"}</b></span><span>Candles <b>{item.candles}</b></span></div>
            <div className="tiny-meter"><span style={{ width: `${Math.min(100, item.candles / 14 * 100)}%` }} /></div>
          </div>
        );
      })}
    </div>
  );
}
