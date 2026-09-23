"use client";

import { useEffect, useMemo, useState } from "react";
import { BrainChat } from "@/components/BrainChat";
import { Candles } from "@/components/Candles";
import { DecisionCard } from "@/components/DecisionCard";
import { Timeframes } from "@/components/Timeframes";
import { TopBar } from "@/components/TopBar";
import { getCandles, getSnapshot } from "@/lib/api";
import type { Candle, Snapshot } from "@/lib/types";

function fmtTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function DashboardClient() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [interval, setIntervalName] = useState("1min");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const [snapshot, chart] = await Promise.all([getSnapshot(), getCandles(interval, 160)]);
        if (!mounted) return;
        setData(snapshot); setCandles(chart); setError(null);
      } catch (err) {
        if (mounted) setError(err instanceof Error ? err.message : "Backend unavailable");
      }
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [interval]);

  const quote = data?.quote;
  const brain = data?.brain;
  const priceChange = useMemo(() => {
    if (candles.length < 2) return null;
    const first = candles[0].open;
    const last = candles[candles.length - 1].close;
    return last - first;
  }, [candles]);

  return <>
    <TopBar data={data} />
    {error && <div className="global-alert"><strong>Backend connection:</strong> {error}. Start the FastAPI service on port 8000.</div>}

    <section className="hero-grid">
      <section className="market-card">
        <div className="market-head">
          <div><div className="asset-name"><span className="gold-orb" />XAU / USD</div><span className="muted">Gold Spot · USD per troy ounce</span></div>
          <div className="market-price"><strong>{quote ? quote.price.toFixed(2) : "—"}</strong><span className={priceChange != null && priceChange < 0 ? "negative" : "positive"}>{priceChange == null ? "Building history" : `${priceChange >= 0 ? "+" : ""}${priceChange.toFixed(2)} sampled`}</span></div>
        </div>
        <div className="market-kpis">
          <div><span>Provider</span><strong>{quote?.provider || "Connecting"}</strong></div>
          <div><span>Feed</span><strong>{quote?.status || "NO DATA"}</strong></div>
          <div><span>Age</span><strong>{quote?.age_seconds != null ? `${quote.age_seconds.toFixed(0)}s` : "—"}</strong></div>
          <div><span>Updated</span><strong>{fmtTime(quote?.received_timestamp)}</strong></div>
        </div>
      </section>
      <DecisionCard brain={brain} />
    </section>

    <section className="content-grid">
      <section className="panel chart-panel">
        <div className="panel-head">
          <div><div className="panel-kicker">LOCALLY ACCUMULATED PRICE MEMORY</div><h2>XAU sampled candles</h2></div>
          <div className="interval-tabs">{["1min","5min","15min","30min","1h","4h"].map((x)=><button className={x===interval?"active":""} key={x} onClick={()=>setIntervalName(x)}>{x.replace("min","m")}</button>)}</div>
        </div>
        <div className="chart-wrap"><Candles data={candles} /></div>
        <div className="chart-note">Candles labeled “Local sampled Gold API” are generated from this installation’s periodic spot observations. They are not tick-complete broker candles.</div>
      </section>

      <section className="panel evidence-panel">
        <div className="panel-head"><div><div className="panel-kicker">CURRENT THESIS</div><h2>Evidence</h2></div><span className="mini-chip">{brain?.data_quality || "WAITING"}</span></div>
        <div className="zones-row">
          <div className="zone support"><span>Support</span><strong>{brain?.support ? `${brain.support.low.toFixed(2)} – ${brain.support.high.toFixed(2)}` : "Learning"}</strong></div>
          <div className="zone resistance"><span>Resistance</span><strong>{brain?.resistance ? `${brain.resistance.low.toFixed(2)} – ${brain.resistance.high.toFixed(2)}` : "Learning"}</strong></div>
        </div>
        <div className="evidence-columns">
          <div><h3>For</h3>{brain?.reasons_for?.length ? brain.reasons_for.slice(0,5).map((x)=><p key={x} className="evidence good-e">✓ {x}</p>) : <p className="muted">No strong directional evidence yet.</p>}</div>
          <div><h3>Against</h3>{brain?.reasons_against?.length ? brain.reasons_against.slice(0,5).map((x)=><p key={x} className="evidence warn-e">• {x}</p>) : <p className="muted">No major contradiction recorded.</p>}</div>
        </div>
      </section>
    </section>

    <section className="panel timeframe-panel">
      <div className="panel-head"><div><div className="panel-kicker">MULTI-TIMEFRAME STATE</div><h2>Structure & momentum</h2></div><span className="mini-chip">Readiness {brain?.readiness.toFixed(0) ?? 0}%</span></div>
      <Timeframes items={brain?.timeframes || []} />
    </section>

    <BrainChat />

    <section className="status-strip">
      <span><i className={data?.source_status === "CONNECTED" ? "ok" : "warn"} />Market {data?.source_status || "STARTING"}</span>
      <span><i className={data?.research_status === "CONNECTED" ? "ok" : "warn"} />Research {data?.research_status || "STARTING"}</span>
      <span><i className={brain ? "ok" : "warn"} />Brain {brain ? "ONLINE" : "STARTING"}</span>
      <span className="muted">No broker login required</span>
    </section>
  </>;
}
