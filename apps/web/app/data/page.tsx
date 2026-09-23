"use client";

import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";
import type { DataQualitySummary, SyncSummary, TimeframeRow } from "@/lib/types";

function fmtTs(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString([], {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

function prettyTf(tf: string): string {
  return (
    ({ "1min": "M1", "5min": "M5", "15min": "M15", "30min": "M30", "1h": "H1", "4h": "H4", "1day": "D1" } as Record<string, string>)[tf] || tf
  );
}

export default function DataPage() {
  const [summary, setSummary] = useState<DataQualitySummary | null>(null);
  const [timeframes, setTimeframes] = useState<TimeframeRow[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [statusRes, tfRes] = await Promise.all([
        fetch(`${API}/api/data/status`, { cache: "no-store" }),
        fetch(`${API}/api/data/timeframes`, { cache: "no-store" }),
      ]);
      if (!statusRes.ok) throw new Error(`status endpoint returned ${statusRes.status}`);
      if (!tfRes.ok) throw new Error(`timeframes endpoint returned ${tfRes.status}`);
      setSummary(await statusRes.json());
      const tfJson = await tfRes.json();
      setTimeframes(tfJson.intervals || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backend unreachable");
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 15000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function runSync() {
    if (syncing) return;
    setSyncing(true);
    setError(null);
    setSyncResult(null);
    try {
      const res = await fetch(`${API}/api/data/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`sync endpoint returned ${res.status}`);
      const payload: SyncSummary = await res.json();
      setSyncResult(payload);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  const historical = summary?.historical;
  const providers = summary?.providers ?? [];
  const syncStates = summary?.sync_states ?? [];
  const byInterval = summary?.by_interval ?? [];
  const dbHealth = summary?.database_health;

  // Build a unified per-interval view (status + timeframes merge).
  const intervalRows = (timeframes.length ? timeframes : byInterval).map((row) => {
    const detailed = byInterval.find((x) => x.interval === row.interval);
    return {
      interval: row.interval,
      candle_count: row.candle_count ?? detailed?.candle_count ?? 0,
      first_timestamp: row.first_timestamp ?? detailed?.first_timestamp ?? null,
      last_timestamp: row.last_timestamp ?? detailed?.last_timestamp ?? null,
      missing_intervals: detailed?.missing_intervals ?? 0,
      expected_periods: detailed?.expected_periods ?? 0,
      completeness_pct: detailed?.completeness_pct ?? 0,
      duplicate_count: row.duplicate_count ?? detailed?.duplicate_count ?? 0,
      integrity_status: row.integrity_status ?? detailed?.integrity_status ?? "OK",
    };
  });

  return (
    <>
      <div className="page-title">
        <div>
          <span>DATA QUALITY</span>
          <h2>Historical market memory</h2>
        </div>
        <p>Genuine backfilled XAU/USD OHLC storage, validation, and sync state. No synthetic candles.</p>
      </div>

      {error && (
        <div className="global-alert">
          <strong>Backend connection:</strong> {error}. Start the FastAPI service on port 8000.
        </div>
      )}

      <section className="hero-grid">
        <div className="panel big-number-panel">
          <span>Active provider</span>
          <strong style={{ fontSize: "20px", letterSpacing: "-0.02em" }}>
            {summary?.active_provider || "—"}
          </strong>
          <p>The current historical-data provider. Yahoo Finance (GC=F gold futures) is the default; Twelve Data takes priority when a key is set in backend .env.</p>
        </div>
        <div className="panel big-number-panel">
          <span>Total historical candles</span>
          <strong>{historical?.total_candles ?? "—"}</strong>
          <p>Across all timeframes M1, M5, M15, M30, H1, H4, D1 — only candles marked is_historical=true.</p>
        </div>
      </section>

      <section className="two-col" style={{ marginTop: "12px" }}>
        <div className="panel">
          <div className="panel-kicker">EARLIEST HISTORY</div>
          <h2>First candle</h2>
          <strong style={{ fontSize: "18px", display: "block", marginTop: "8px" }}>
            {fmtTs(historical?.earliest_timestamp)}
          </strong>
          <p className="muted" style={{ fontSize: "10px", marginTop: "6px" }}>
            Oldest historical timestamp the active provider returned.
          </p>
        </div>
        <div className="panel">
          <div className="panel-kicker">LATEST HISTORY</div>
          <h2>Last candle</h2>
          <strong style={{ fontSize: "18px", display: "block", marginTop: "8px" }}>
            {fmtTs(historical?.latest_timestamp)}
          </strong>
          <p className="muted" style={{ fontSize: "10px", marginTop: "6px" }}>
            Most recent historical timestamp. May lag the live sampled feed.
          </p>
        </div>
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div>
            <div className="panel-kicker">PROVIDER HEALTH</div>
            <h2>Available historical sources</h2>
          </div>
          <button
            onClick={runSync}
            disabled={syncing}
            style={{
              border: "1px solid var(--border)",
              background: syncing ? "#0b1016" : "#171f29",
              color: syncing ? "#5a6473" : "#cbd4df",
              borderRadius: "9px",
              padding: "9px 16px",
              cursor: syncing ? "wait" : "pointer",
              fontSize: "11px",
              fontWeight: 700,
            }}
          >
            {syncing ? "Syncing…" : "Trigger sync"}
          </button>
        </div>
        <div className="research-list">
          {providers.length === 0 && (
            <div className="empty-state">No providers registered.</div>
          )}
          {providers.map((p) => (
            <div className="research-item" key={p.provider_name}>
              <div>
                <strong>{p.provider_name}</strong>
                <span>
                  reachable: {p.reachable ? "yes" : "no"} · requires_api_key: {p.requires_api_key ? "yes" : "no"} · has_api_key: {p.has_api_key ? "yes" : "no"}
                  {p.last_error ? ` · error: ${p.last_error}` : ""}
                </span>
              </div>
              <b style={{ color: p.reachable ? "var(--green)" : "var(--amber)" }}>
                {p.reachable ? "ONLINE" : "OFFLINE"}
              </b>
            </div>
          ))}
        </div>
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div>
            <div className="panel-kicker">BY TIMEFRAME</div>
            <h2>Candle integrity per interval</h2>
          </div>
          <span className="mini-chip">
            {intervalRows.reduce((acc, r) => acc + r.candle_count, 0)} total candles
          </span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>TF</th>
                <th>Candles</th>
                <th>First</th>
                <th>Last</th>
                <th>Missing</th>
                <th>Completeness</th>
                <th>Dup</th>
                <th>Integrity</th>
              </tr>
            </thead>
            <tbody>
              {intervalRows.map((row) => {
                const tone =
                  row.integrity_status === "OK" ? "ok" : row.integrity_status === "DEGRADED" ? "warn" : "flat";
                return (
                  <tr key={row.interval}>
                    <td><strong>{prettyTf(row.interval)}</strong></td>
                    <td>{row.candle_count}</td>
                    <td>{fmtTs(row.first_timestamp)}</td>
                    <td>{fmtTs(row.last_timestamp)}</td>
                    <td>{row.missing_intervals}</td>
                    <td>{row.completeness_pct ? `${row.completeness_pct}%` : "—"}</td>
                    <td>{row.duplicate_count}</td>
                    <td>
                      <span
                        style={{
                          color: tone === "ok" ? "var(--green)" : tone === "warn" ? "var(--amber)" : "var(--muted)",
                          background: tone === "ok" ? "rgba(46,211,154,.08)" : tone === "warn" ? "rgba(242,184,75,.08)" : "#1a222d",
                          padding: "4px 7px",
                          borderRadius: "999px",
                          fontSize: "8px",
                          fontWeight: 800,
                        }}
                      >
                        {row.integrity_status}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {intervalRows.length === 0 && (
                <tr>
                  <td colSpan={8} className="empty-state">
                    No timeframes synced yet. Click “Trigger sync” to backfill genuine XAU/USD history.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="two-col" style={{ marginTop: "12px" }}>
        <div className="panel">
          <div className="panel-kicker">SYNC STATE</div>
          <h2>Per-interval sync log</h2>
          <div className="list-stack">
            {syncStates.length === 0 && (
              <div className="empty-state">No sync runs recorded yet.</div>
            )}
            {syncStates.map((s, i) => (
              <div className="list-item" key={`${s.provider}-${s.interval}-${i}`}>
                <strong>{prettyTf(s.interval)} · {s.provider}</strong>
                <div style={{ fontSize: "9px", color: "var(--muted)", marginTop: "4px" }}>
                  status: <b>{s.sync_status}</b> · candles: <b>{s.total_candles}</b>
                  <br />
                  last sync: {fmtTs(s.last_sync_at)}
                  <br />
                  range: {fmtTs(s.earliest_timestamp)} → {fmtTs(s.latest_timestamp)}
                  {s.last_error ? <><br /><span style={{ color: "var(--red)" }}>error: {s.last_error}</span></> : null}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="panel-kicker">DATABASE HEALTH</div>
          <h2>Storage</h2>
          <div className="settings-list">
            <div>
              <span>Engine</span>
              <strong>{dbHealth?.storage_engine || "—"}</strong>
            </div>
            <div>
              <span>Total candle rows</span>
              <strong>{dbHealth?.total_candle_rows ?? "—"}</strong>
            </div>
            <div>
              <span>Historical rows</span>
              <strong>{dbHealth?.historical_candle_rows ?? "—"}</strong>
            </div>
            <div>
              <span>Status</span>
              <strong style={{ color: dbHealth?.ok ? "var(--green)" : "var(--red)" }}>
                {dbHealth?.ok ? "OK" : "ERROR"}
              </strong>
            </div>
          </div>
        </div>
      </section>

      {syncResult && (
        <section className="panel info-panel" style={{ marginTop: "12px" }}>
          <div className="panel-kicker">LAST SYNC RESULT</div>
          <h2>Backfill summary</h2>
          <p style={{ fontSize: "10px", color: "var(--muted)" }}>
            Provider: <strong style={{ color: "var(--text)" }}>{syncResult.provider}</strong>
            {" · "}
            started at <strong style={{ color: "var(--text)" }}>{fmtTs(syncResult.started_at)}</strong>
            {syncResult.completed_at ? <> · completed at <strong style={{ color: "var(--text)" }}>{fmtTs(syncResult.completed_at)}</strong></> : null}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>TF</th>
                  <th>Fetched</th>
                  <th>Inserted</th>
                  <th>Skipped</th>
                  <th>Dup in batch</th>
                  <th>Invalid OHLC</th>
                  <th>Out of order</th>
                  <th>Gaps</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(syncResult.timeframes).map(([tf, r]) => (
                  <tr key={tf}>
                    <td><strong>{prettyTf(tf)}</strong></td>
                    <td>{r.candles_fetched}</td>
                    <td>{r.inserted}</td>
                    <td>{r.skipped_already_present}</td>
                    <td>{r.duplicates_in_batch}</td>
                    <td>{r.invalid_ohlc}</td>
                    <td>{r.out_of_order}</td>
                    <td>{r.gaps_detected}</td>
                    <td>
                      <span style={{ color: r.sync_status === "ok" ? "var(--green)" : "var(--amber)" }}>
                        {r.sync_status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {syncResult.notes.length > 0 && (
            <div style={{ marginTop: "10px" }}>
              {syncResult.notes.map((n, i) => (
                <p key={i} style={{ fontSize: "10px", color: "var(--muted)", margin: "4px 0" }}>
                  · {n}
                </p>
              ))}
            </div>
          )}
        </section>
      )}
    </>
  );
}
