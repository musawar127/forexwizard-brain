"use client";

import type { Candle } from "@/lib/types";

export function Candles({ data }: { data: Candle[] }) {
  if (!data.length) {
    return <div className="chart-empty"><div className="pulse-ring" /><strong>Building local candle memory</strong><span>Keep the backend running. Sampled XAU prices are being stored and aggregated into OHLC candles.</span></div>;
  }

  const view = data.slice(-72);
  const min = Math.min(...view.map((c) => c.low));
  const max = Math.max(...view.map((c) => c.high));
  const range = Math.max(max - min, 0.001);
  const W = 1000, H = 330, pad = 24;
  const y = (v: number) => pad + ((max - v) / range) * (H - pad * 2);
  const step = (W - pad * 2) / view.length;
  const bodyW = Math.max(2.5, step * 0.5);

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="XAU sampled candlestick chart">
      {[0.2,0.4,0.6,0.8].map((p) => <line key={p} x1={pad} x2={W-pad} y1={pad+(H-pad*2)*p} y2={pad+(H-pad*2)*p} className="grid-line" />)}
      {view.map((c, i) => {
        const x = pad + step * i + step / 2;
        const openY = y(c.open), closeY = y(c.close), highY = y(c.high), lowY = y(c.low);
        const up = c.close >= c.open;
        return <g key={`${c.timestamp}-${i}`} className={up ? "candle-up" : "candle-down"}>
          <line x1={x} x2={x} y1={highY} y2={lowY} />
          <rect x={x-bodyW/2} y={Math.min(openY, closeY)} width={bodyW} height={Math.max(1.5, Math.abs(openY-closeY))} rx="1" />
        </g>;
      })}
      <text x={W-pad} y={18} textAnchor="end" className="chart-label">{max.toFixed(2)}</text>
      <text x={W-pad} y={H-5} textAnchor="end" className="chart-label">{min.toFixed(2)}</text>
    </svg>
  );
}
