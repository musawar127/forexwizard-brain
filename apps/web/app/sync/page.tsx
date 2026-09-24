"use client";

import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";

function fmtTs(v: string | null | undefined): string {
  if (!v) return "—";
  try { return new Date(v).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  catch { return v; }
}

function fmtDur(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${Math.floor(s%60)}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

const MODE_COLOR: Record<string, string> = {
  STARTING: "var(--amber)", CATCHING_UP: "var(--blue)", LIVE: "var(--green)",
  DEGRADED: "var(--amber)", OFFLINE: "var(--red)", LIVE_SYNCING: "var(--green)",
};

type SyncStatus = {
  system_mode: { mode: string; offline_duration_seconds: number; started_at: string; progress_percent: number; };
  sync_states: { component: string; status: string; last_successful_sync: string | null; last_error: string | null; }[];
  latest_job: {
    job_id: string; status: string; started_at: string; completed_at: string | null;
    market_sync_status: string; research_sync_status: string; forward_outcome_status: string; historical_state_status: string;
    recovered_market_candles: number; research_items_added: number; forward_outcomes_evaluated: number;
    missed_forward_captures: number; historical_states_added: number; progress_percent: number; last_error: string | null;
  } | null;
  last_live: { market_timestamp?: string | null; analysis_timestamp?: string | null; research_timestamp?: string | null; forward_capture_timestamp?: string | null; };
};

export default function SyncPage() {
  const [data, setData] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/catchup/status`, { cache: "no-store" });
      if (!res.ok) throw new Error(`status ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : "Backend unreachable"); }
  }, []);

  useEffect(() => { load(); const t = window.setInterval(load, 5000); return () => window.clearInterval(t); }, [load]);

  const mode = data?.system_mode;
  const job = data?.latest_job;
  const states = data?.sync_states ?? [];
  const live = data?.last_live ?? {};

  return (
    <>
      <div className="page-title">
        <div><span>SYSTEM SYNC</span><h2>Wake and catch-up recovery</h2></div>
        <p>Distinguishes between RECOVERABLE information and PREDICTIONS THAT NEVER ACTUALLY EXISTED. Historical data may be recovered; missed forward captures are logged as MISSED forever.</p>
      </div>

      {error && <div className="global-alert"><strong>Backend:</strong> {error}</div>}

      <section className="hero-grid">
        <div className="panel big-number-panel">
          <span>System mode</span>
          <strong style={{ color: MODE_COLOR[mode?.mode ?? "OFFLINE"] || "var(--muted)" }}>{mode?.mode ?? "—"}</strong>
          <p>Offline duration: {fmtDur(mode?.offline_duration_seconds)}</p>
        </div>
        <div className="panel big-number-panel">
          <span>Catch-up progress</span>
          <strong>{mode?.progress_percent?.toFixed(1) ?? "—"}%</strong>
          <p>Started: {fmtTs(mode?.started_at)}</p>
        </div>
      </section>

      {job && (
        <section className="panel" style={{ marginTop: "12px" }}>
          <div className="panel-head">
            <div><div className="panel-kicker">CATCH-UP JOB</div><h2>{job.job_id}</h2></div>
            <span className="mini-chip">{job.status}</span>
          </div>
          <div className="table-wrap" style={{ marginTop: "8px" }}>
            <table>
              <thead><tr><th>Component</th><th>Status</th><th>Recovered</th></tr></thead>
              <tbody>
                <tr><td>Market history</td><td>{job.market_sync_status}</td><td>{job.recovered_market_candles} candles</td></tr>
                <tr><td>Forward outcomes</td><td>{job.forward_outcome_status}</td><td>{job.forward_outcomes_evaluated} evaluated</td></tr>
                <tr><td>Missed captures</td><td>—</td><td>{job.missed_forward_captures} missed</td></tr>
                <tr><td>Research</td><td>{job.research_sync_status}</td><td>{job.research_items_added} items</td></tr>
                <tr><td>Historical states</td><td>{job.historical_state_status}</td><td>{job.historical_states_added} added</td></tr>
              </tbody>
            </table>
          </div>
          {job.last_error && <p style={{ fontSize: "10px", color: "var(--red)", marginTop: "8px" }}>Error: {job.last_error}</p>}
        </section>
      )}

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head"><div><div className="panel-kicker">COMPONENT SYNC STATES</div><h2>Per-component status</h2></div></div>
        <div className="table-wrap" style={{ marginTop: "8px" }}>
          <table>
            <thead><tr><th>Component</th><th>Status</th><th>Last successful sync</th><th>Error</th></tr></thead>
            <tbody>
              {states.map(s => (
                <tr key={s.component}>
                  <td style={{ fontSize: "9px" }}>{s.component}</td>
                  <td><span style={{ color: s.status === "COMPLETE" ? "var(--green)" : s.status === "DEGRADED" || s.status === "FAILED" ? "var(--red)" : "var(--amber)", fontWeight: 700, fontSize: "9px" }}>{s.status}</span></td>
                  <td style={{ fontSize: "9px" }}>{fmtTs(s.last_successful_sync)}</td>
                  <td style={{ fontSize: "8px", color: "var(--muted)" }}>{s.last_error || "—"}</td>
                </tr>
              ))}
              {states.length === 0 && <tr><td colSpan={4} className="empty-state">No sync states recorded yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head"><div><div className="panel-kicker">LAST LIVE STATE</div><h2>Live timestamps</h2></div></div>
        <div className="settings-list">
          <div><span>Market data</span><strong>{fmtTs(live.market_timestamp)}</strong></div>
          <div><span>Brain analysis</span><strong>{fmtTs(live.analysis_timestamp)}</strong></div>
          <div><span>Research</span><strong>{fmtTs(live.research_timestamp)}</strong></div>
          <div><span>Forward capture</span><strong>{fmtTs(live.forward_capture_timestamp)}</strong></div>
        </div>
      </section>

      <section className="panel info-panel" style={{ marginTop: "12px" }}>
        <div className="panel-kicker">DATA HONESTY</div>
        <h2>Recoverable vs missed</h2>
        <p><strong style={{ color: "var(--green)" }}>RECOVERED</strong> — historical candles, news, research fetched from providers after a gap. Labeled clearly as RECOVERED, never as LIVE CAPTURE.</p>
        <p><strong style={{ color: "var(--red)" }}>MISSED</strong> — forward observations that were never captured because the server was offline. These remain MISSED forever. Never fabricated retroactively.</p>
        <p><strong style={{ color: "var(--amber)" }}>UNAVAILABLE</strong> — spot history cannot be recovered from the free Gold API (no historical endpoint). Marked SPOT_HISTORY_NOT_RECOVERABLE.</p>
      </section>
    </>
  );
}
