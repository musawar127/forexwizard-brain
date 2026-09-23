"use client";

import type { Candle } from "@/lib/types";

function fmtTimeLabel(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function Candles({ data }: { data: Candle[] }) {
  if (!data.length) {
    return (
      <div className="chart-empty">
        <div className="pulse-ring" />
        <strong>Building local candle memory</strong>
        <span>Keep the backend running. Sampled XAU prices are being stored and aggregated into OHLC candles.</span>
      </div>
    );
  }

  // Show the most recent 48 candles — enough for context, not so many that
  // individual candle bodies shrink to invisible slivers on dense ranges.
  const view = data.slice(-48);
  const min = Math.min(...view.map((c) => c.low));
  const max = Math.max(...view.map((c) => c.high));
  const range = Math.max(max - min, 0.001);
  const W = 1000, H = 330, padTop = 18, padBottom = 28, padX = 56;
  const plotH = H - padTop - padBottom;
  const y = (v: number) => padTop + ((max - v) / range) * plotH;
  const step = (W - padX * 2) / view.length;
  const bodyW = Math.max(4, step * 0.7);

  // 4 horizontal price gridlines + labels (max, 75%, 50%, 25%, min)
  const priceLevels = [max, max - range * 0.25, max - range * 0.5, max - range * 0.75, min];

  // 5 vertical time markers spaced across the X axis
  const timeMarkerIdx = [0, Math.floor(view.length * 0.25), Math.floor(view.length * 0.5), Math.floor(view.length * 0.75), view.length - 1];

  const last = view[view.length - 1];
  const lastY = y(last.close);
  const lastX = padX + step * (view.length - 1) + step / 2;

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="XAU sampled candlestick chart">
      {/* horizontal price gridlines + right-side price labels */}
      {priceLevels.map((p, i) => {
        const yp = y(p);
        return (
          <g key={`pg-${i}`}>
            <line x1={padX} x2={W - padX} y1={yp} y2={yp} className="grid-line" />
            <text x={W - padX + 4} y={yp + 3.5} textAnchor="start" className="chart-label">{p.toFixed(2)}</text>
          </g>
        );
      })}
      {/* vertical time markers + bottom labels */}
      {timeMarkerIdx.map((idx, i) => {
        const c = view[idx];
        if (!c) return null;
        const x = padX + step * idx + step / 2;
        return (
          <g key={`tg-${i}`}>
            <line x1={x} x2={x} y1={padTop} y2={H - padBottom} className="grid-line time-grid" />
            <text x={x} y={H - padBottom + 12} textAnchor="middle" className="chart-label time-label">{fmtTimeLabel(c.timestamp)}</text>
          </g>
        );
      })}
      {/* candles */}
      {view.map((c, i) => {
        const x = padX + step * i + step / 2;
        const openY = y(c.open), closeY = y(c.close), highY = y(c.high), lowY = y(c.low);
        const up = c.close >= c.open;
        const bodyH = Math.max(3, Math.abs(openY - closeY));
        return (
          <g key={`${c.timestamp}-${i}`} className={up ? "candle-up" : "candle-down"}>
            <line x1={x} x2={x} y1={highY} y2={lowY} className="candle-wick" />
            <rect x={x - bodyW / 2} y={Math.min(openY, closeY)} width={bodyW} height={bodyH} rx="0.5" className="candle-body" />
          </g>
        );
      })}
      {/* last price marker */}
      <g>
        <line x1={padX} x2={lastX} y1={lastY} y2={lastY} className="last-price-line" />
        <rect x={lastX - 1} y={lastY - 5} width={36} height={10} rx="1" className="last-price-tag" />
        <text x={lastX + 17} y={lastY + 3} textAnchor="middle" className="last-price-text">{last.close.toFixed(2)}</text>
      </g>
    </svg>
  );
}
