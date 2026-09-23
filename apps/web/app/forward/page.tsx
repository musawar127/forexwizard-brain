"use client";

import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";

function fmtTs(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch { return value; }
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

const HORIZON_LABELS: Record<number, string> = { 15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h", 480: "8h", 1440: "24h" };

type FwdStatus = {
  total_observations: number;
  pending: number;
  partially_evaluated: number;
  complete: number;
  invalid: number;
  by_decision: { decision: string; count: number }[];
  by_alignment: { alignment: string; count: number }[];
  by_horizon: { horizon_minutes: number; total: number; evaluated: number }[];
  forward_validation_started_at: string | null;
  probability_calibrated: boolean;
};

type FwdPerf = {
  horizon_minutes: number;
  total_evaluated: number;
  sample_quality: string;
  probability_calibrated: boolean;
  groups: {
    group: string;
    decision: string;
    alignment: string;
    sample_size: number;
    favorable_direction?: string;
    favorable_count?: number;
    favorable_rate?: number;
    wilson_lower?: number;
    wilson_upper?: number;
    median_mfe?: number | null;
    median_mae?: number | null;
    median_absolute_move?: number | null;
    note?: string;
  }[];
};

type FwdObs = {
  observation_id: string;
  created_at: string;
  live_price: number;
  technical_decision: string;
  technical_score: number;
  historical_alignment: string | null;
  historical_similarity_run_id: string | null;
  observation_status: string;
  capture_timeframe: string;
};

export default function ForwardPage() {
  const [status, setStatus] = useState<FwdStatus | null>(null);
  const [perf, setPerf] = useState<FwdPerf | null>(null);
  const [observations, setObservations] = useState<FwdObs[]>([]);
  const [selectedHorizon, setSelectedHorizon] = useState(60);
  const [error, setError] = useState<string | null>(null);
  const [captureResult, setCaptureResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [sRes, pRes, oRes] = await Promise.all([
        fetch(`${API}/api/forward/status`, { cache: "no-store" }),
        fetch(`${API}/api/forward/performance?horizon=${selectedHorizon}`, { cache: "no-store" }),
        fetch(`${API}/api/forward/observations?limit=20`, { cache: "no-store" }),
      ]);
      if (sRes.ok) setStatus(await sRes.json());
      if (pRes.ok) setPerf(await pRes.json());
      if (oRes.ok) { const d = await oRes.json(); setObservations(d.observations || []); }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backend unreachable");
    }
  }, [selectedHorizon]);

  useEffect(() => { load(); const t = window.setInterval(load, 30000); return () => window.clearInterval(t); }, [load]);

  async function capture() {
    setCaptureResult(null);
    try {
      const res = await fetch(`${API}/api/forward/capture`, { method: "POST" });
      const d = await res.json();
      if (d.skipped) setCaptureResult(`Skipped: ${d.error}`);
      else if (d.observation_id) setCaptureResult(`Captured: ${d.observation_id} (${d.technical_decision} @ $${d.live_price?.toFixed(2)})`);
      await load();
    } catch (err) { setCaptureResult(`Error: ${err instanceof Error ? err.message : "failed"}`); }
  }

  async function evaluate() {
    try {
      const res = await fetch(`${API}/api/forward/evaluate`, { method: "POST" });
      const d = await res.json();
      setCaptureResult(`Evaluated: ${d.evaluated || 0} outcomes, ${d.completed || 0} completed`);
      await load();
    } catch (err) { setCaptureResult(`Error: ${err instanceof Error ? err.message : "failed"}`); }
  }

  return (
    <>
      <div className="page-title">
        <div><span>FORWARD VALIDATION</span><h2>Live learning audit</h2></div>
        <p>Prospective out-of-sample evidence. Observations are immutable snapshots captured before future outcomes are known. probability_calibrated = FALSE.</p>
      </div>

      {error && <div className="global-alert"><strong>Backend:</strong> {error}. Start FastAPI on port 8000.</div>}

      <section className="hero-grid">
        <div className="panel big-number-panel">
          <span>Total observations</span>
          <strong>{status?.total_observations ?? "—"}</strong>
          <p>Immutable snapshots of the Brain's state. Each captures technical decision + historical similarity run before the future is known.</p>
        </div>
        <div className="panel big-number-panel">
          <span>Lifecycle</span>
          <strong style={{ fontSize: "16px" }}>
            {status?.pending ?? 0} pending · {status?.partially_evaluated ?? 0} partial · {status?.complete ?? 0} complete · {status?.invalid ?? 0} invalid
          </strong>
          <p>Forward validation started: {status?.forward_validation_started_at ? fmtTs(status.forward_validation_started_at) : "not started"}</p>
        </div>
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div><div className="panel-kicker">CAPTURE + EVALUATE</div><h2>Manual controls</h2></div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button onClick={capture} style={{ border: "1px solid var(--border)", background: "#171f29", color: "#cbd4df", borderRadius: "9px", padding: "9px 16px", cursor: "pointer", fontSize: "11px", fontWeight: 700 }}>Capture now</button>
            <button onClick={evaluate} style={{ border: "1px solid var(--border)", background: "#171f29", color: "#cbd4df", borderRadius: "9px", padding: "9px 16px", cursor: "pointer", fontSize: "11px", fontWeight: 700 }}>Evaluate pending</button>
          </div>
        </div>
        {captureResult && <p style={{ fontSize: "10px", color: "var(--green)", marginTop: "8px" }}>{captureResult}</p>}
        {(status?.by_decision ?? []).length > 0 && (
          <div style={{ marginTop: "12px" }}>
            <strong style={{ fontSize: "10px", color: "var(--muted)" }}>By decision:</strong>
            {" "}{(status?.by_decision ?? []).map(d => `${d.decision}: ${d.count}`).join(" · ")}
            <br />
            <strong style={{ fontSize: "10px", color: "var(--muted)" }}>By alignment:</strong>
            {" "}{(status?.by_alignment ?? []).map(a => `${a.alignment}: ${a.count}`).join(" · ")}
          </div>
        )}
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div><div className="panel-kicker">COMPARISON TABLE</div><h2>Technical vs historical evidence</h2></div>
          <select value={selectedHorizon} onChange={(e) => setSelectedHorizon(parseInt(e.target.value))} style={{ border: "1px solid var(--border)", background: "#0a0f15", color: "var(--text)", borderRadius: "9px", padding: "7px 9px", fontSize: "11px" }}>
            {Object.entries(HORIZON_LABELS).map(([m, lbl]) => <option key={m} value={m}>{lbl}</option>)}
          </select>
        </div>
        <p style={{ fontSize: "9px", color: "var(--muted)", marginTop: "4px" }}>
          Sample quality: {perf?.sample_quality || "—"} · probability_calibrated: {perf?.probability_calibrated ? "TRUE" : "FALSE"}
        </p>
        <div className="table-wrap" style={{ marginTop: "8px" }}>
          <table>
            <thead>
              <tr><th>Group</th><th>Sample</th><th>Favorable Dir</th><th>Rate</th><th>95% CI</th><th>Median MFE</th><th>Median MAE</th></tr>
            </thead>
            <tbody>
              {(perf?.groups ?? []).map(g => (
                <tr key={g.group}>
                  <td style={{ fontSize: "9px" }}>{g.group}</td>
                  <td>{g.sample_size}</td>
                  <td style={{ fontSize: "9px" }}>{g.favorable_direction || (g.decision === "WAIT" ? "N/A" : "—")}</td>
                  <td>{g.favorable_rate != null ? fmtPct(g.favorable_rate) : "—"}</td>
                  <td style={{ fontSize: "9px", color: "var(--muted)" }}>
                    {g.wilson_lower != null ? `[${fmtPct(g.wilson_lower)}, ${fmtPct(g.wilson_upper)}]` : "—"}
                  </td>
                  <td>{g.median_mfe != null ? `$${g.median_mfe.toFixed(2)}` : "—"}</td>
                  <td>{g.median_mae != null ? `$${g.median_mae.toFixed(2)}` : "—"}</td>
                </tr>
              ))}
              {(perf?.groups ?? []).length === 0 && (
                <tr><td colSpan={7} className="empty-state">No observations yet. Click "Capture now" to begin forward validation.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p style={{ fontSize: "9px", color: "var(--amber)", marginTop: "8px", fontStyle: "italic" }}>
          Forward validation begins from the deployment date. Do NOT interpret early results as prediction quality. Sample must reach 30+ for INSUFFICIENT, 100+ for EARLY.
        </p>
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div><div className="panel-kicker">OBSERVATION EXPLORER</div><h2>Recent observations</h2></div>
          <span className="mini-chip">{observations.length} shown</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Time</th><th>Price</th><th>Decision</th><th>Score</th><th>Alignment</th><th>Status</th><th>Capture TF</th></tr></thead>
            <tbody>
              {observations.map(o => (
                <tr key={o.observation_id}>
                  <td style={{ fontSize: "8px", color: "var(--gold)" }}>{o.observation_id}</td>
                  <td style={{ fontSize: "9px" }}>{fmtTs(o.created_at)}</td>
                  <td>${o.live_price.toFixed(2)}</td>
                  <td><span style={{ color: o.technical_decision === "BUY" ? "var(--green)" : o.technical_decision === "SELL" ? "var(--red)" : "var(--amber)", fontWeight: 700, fontSize: "9px" }}>{o.technical_decision}</span></td>
                  <td>{o.technical_score.toFixed(1)}</td>
                  <td style={{ fontSize: "9px" }}>{o.historical_alignment || "—"}</td>
                  <td style={{ fontSize: "9px" }}>{o.observation_status}</td>
                  <td style={{ fontSize: "9px" }}>{o.capture_timeframe}</td>
                </tr>
              ))}
              {observations.length === 0 && <tr><td colSpan={8} className="empty-state">No observations captured yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel info-panel" style={{ marginTop: "12px" }}>
        <div className="panel-kicker">IMPORTANT</div>
        <h2>Forward validation philosophy</h2>
        <p>Historical backtests tell us what worked on known history. Forward validation tells us whether the idea survives new market data. We will NOT let the Brain alter live decisions until prospective evidence shows that the historical layer adds useful information.</p>
        <p style={{ fontStyle: "italic", color: "var(--amber)" }}>probability_calibrated = FALSE. Phase 5 is observational only — no signal modification.</p>
      </section>
    </>
  );
}
