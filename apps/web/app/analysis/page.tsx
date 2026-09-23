"use client";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { Timeframes } from "@/components/Timeframes";
import { getSnapshot } from "@/lib/api";
import type { Snapshot } from "@/lib/types";

export default function AnalysisPage() {
  const [data,setData]=useState<Snapshot|null>(null);
  useEffect(()=>{let on=true; const run=()=>getSnapshot().then(x=>on&&setData(x)).catch(()=>{}); run(); const t=setInterval(run,5000); return()=>{on=false;clearInterval(t)}},[]);
  const b=data?.brain;
  return <><TopBar data={data}/><div className="page-title"><div><span>ANALYSIS</span><h2>Explainable market engine</h2></div><p>Deterministic calculations first. The text layer only explains stored facts.</p></div>
    <section className="analysis-grid">
      <div className="panel big-number-panel"><span>Score</span><strong>{b?.score ?? "—"}</strong><p>Positive favors bullish evidence; negative favors bearish evidence. Thresholds are intentionally conservative.</p></div>
      <div className="panel big-number-panel"><span>Confidence</span><strong>{b ? `${b.confidence.toFixed(0)}%` : "—"}</strong><p>Derived from rule agreement and readiness, not invented by a language model.</p></div>
      <div className="panel big-number-panel"><span>Readiness</span><strong>{b ? `${b.readiness.toFixed(0)}%` : "—"}</strong><p>Increases as this installation collects enough local candle history.</p></div>
    </section>
    <section className="panel timeframe-panel"><div className="panel-head"><div><div className="panel-kicker">TIMEFRAMES</div><h2>Observed state</h2></div></div><Timeframes items={b?.timeframes||[]}/></section>
    <section className="two-col">
      <div className="panel"><div className="panel-kicker">EVIDENCE FOR</div><h2>Supporting observations</h2><div className="list-stack">{b?.reasons_for?.length?b.reasons_for.map(x=><div className="list-item" key={x}>✓ {x}</div>):<div className="muted">Nothing strong enough yet.</div>}</div></div>
      <div className="panel"><div className="panel-kicker">COUNTER-EVIDENCE</div><h2>Reasons to stay cautious</h2><div className="list-stack">{b?.reasons_against?.length?b.reasons_against.map(x=><div className="list-item" key={x}>• {x}</div>):<div className="muted">Nothing recorded.</div>}</div></div>
    </section>
  </>;
}
