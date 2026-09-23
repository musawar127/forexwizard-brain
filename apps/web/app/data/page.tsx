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

function fmtDays(days: number | null | undefined): string {
  if (days == null) return "—";
  if (days < 1) return `${(days * 24).toFixed(1)}h`;
  if (days < 30) return `${days.toFixed(1)}d`;
  if (days < 365) return `${(days / 30.44).toFixed(1)}mo`;
  return `${(days / 365.25).toFixed(1)}y`;
}

const INSTRUMENT_LABEL: Record<string, string> = {
  GC_FRONT_MONTH: "GC=F futures",
  XAUUSD_SPOT: "XAU spot",
};

const CONSISTENCY_COLOR: Record<string, string> = {
  PURE_GC: "var(--green)",
  PURE_SPOT: "var(--blue)",
  MIXED: "var(--amber)",
  NONE: "var(--muted)",
};

const DERIVATION_COLOR: Record<string, string> = {
  DIRECT: "var(--green)",
  AGGREGATED: "var(--amber)",
  SAMPLED: "var(--muted)",
};

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
  const byInterval = summary?.by_interval ?? {};
  const intervalQuality = summary?.interval_quality ?? {};
  const dbHealth = summary?.database_health;

  // Phase 3.1: timeframe_breakdown returns one row per (interval, instrument,
  // derivation) group — DIRECT and AGGREGATED candles at the same TF appear
  // as separate rows. Sort by interval length, then derivation.
  const sortedTimeframes = [...timeframes].sort((a, b) => {
    const order = ["1min", "5min", "15min", "30min", "1h", "4h", "1day"];
    const ia = order.indexOf(a.interval);
    const ib = order.indexOf(b.interval);
    if (ia !== ib) return ia - ib;
    return (a.derivation || "").localeCompare(b.derivation || "");
  });

  return (
    <>
      <div className="page-title">
        <div>
          <span>DATA QUALITY</span>
          <h2>Historical market memory</h2>
        </div>
        <p>Genuine backfilled XAU/USD OHLC with full source lineage. No synthetic candles.</p>
      </div>

      {error && (
        <div className="global-alert">
          <strong>Backend connection:</strong> {error}. Start the FastAPI service on port 8000.
        </div>
      )}

      <section className="hero-grid">
        <div className="panel big-number-panel">
          <span>Active provider</span>
          <strong style={{ fontSize: "18px", letterSpacing: "-0.02em" }}>
            {summary?.active_provider || "—"}
          </strong>
          <p>Yahoo Finance (GC=F gold futures) is the default; Twelve Data takes priority when a key is set in backend .env.</p>
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
            <div className="panel-kicker">BY TIMEFRAME + INSTRUMENT + DERIVATION</div>
            <h2>Per-lineage candle integrity</h2>
          </div>
          <span className="mini-chip">
            {sortedTimeframes.reduce((acc, r) => acc + r.candle_count, 0)} candles · {sortedTimeframes.length} lineage groups
          </span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>TF</th>
                <th>Instrument</th>
                <th>Provider</th>
                <th>Provider Symbol</th>
                <th>Derivation</th>
                <th>Source → Target</th>
                <th>Candles</th>
                <th>First</th>
                <th>Last</th>
                <th>Days</th>
                <th>Dup</th>
                <th>Integrity</th>
              </tr>
            </thead>
            <tbody>
              {sortedTimeframes.map((row, i) => (
                <tr key={`${row.interval}-${row.derivation}-${row.instrument}-${i}`}>
                  <td><strong>{prettyTf(row.interval)}</strong></td>
                  <td>
                    <span style={{ color: "var(--blue)", fontSize: "10px", fontWeight: 700 }}>
                      {INSTRUMENT_LABEL[row.instrument] || row.instrument}
                    </span>
                  </td>
                  <td style={{ fontSize: "9px", color: "var(--muted)" }}>{row.provider}</td>
                  <td><code style={{ fontSize: "9px", color: "var(--gold)" }}>{row.provider_symbol}</code></td>
                  <td>
                    <span style={{ color: DERIVATION_COLOR[row.derivation] || "var(--muted)", fontWeight: 700, fontSize: "9px" }}>
                      {row.derivation}
                    </span>
                  </td>
                  <td style={{ fontSize: "9px" }}>
                    <code>{prettyTf(row.source_timeframe)}</code> → <code>{prettyTf(row.target_timeframe)}</code>
                  </td>
                  <td>{row.candle_count}</td>
                  <td>{fmtTs(row.first_timestamp)}</td>
                  <td>{fmtTs(row.last_timestamp)}</td>
                  <td><strong>{fmtDays(row.days_covered)}</strong></td>
                  <td>{row.duplicate_count}</td>
                  <td>
                    <span style={{
                      color: row.integrity_status === "OK" ? "var(--green)" : "var(--amber)",
                      background: row.integrity_status === "OK" ? "rgba(46,211,154,.08)" : "rgba(242,184,75,.08)",
                      padding: "4px 7px",
                      borderRadius: "999px",
                      fontSize: "8px",
                      fontWeight: 800,
                    }}>
                      {row.integrity_status}
                    </span>
                  </td>
                </tr>
              ))}
              {sortedTimeframes.length === 0 && (
                <tr>
                  <td colSpan={12} className="empty-state">
                    No timeframes synced yet. Click “Trigger sync” to backfill genuine XAU/USD history.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div>
            <div className="panel-kicker">INTERVAL QUALITY</div>
            <h2>Historical depth + instrument consistency per TF</h2>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>TF</th>
                <th>Historical Depth</th>
                <th>Instrument Consistency</th>
                <th>Missing</th>
                <th>Expected</th>
                <th>Completeness</th>
                <th>Dup</th>
                <th>Integrity</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(intervalQuality).map(([tf, q]) => (
                <tr key={tf}>
                  <td><strong>{prettyTf(tf)}</strong></td>
                  <td><strong style={{ color: "var(--gold)" }}>{fmtDays(q.historical_depth_days)}</strong></td>
                  <td>
                    <span style={{
                      color: CONSISTENCY_COLOR[q.instrument_consistency] || "var(--muted)",
                      fontWeight: 700,
                      fontSize: "10px",
                    }}>
                      {q.instrument_consistency}
                    </span>
                  </td>
                  <td>{q.missing_intervals}</td>
                  <td>{q.expected_periods}</td>
                  <td>{q.completeness_pct ? `${q.completeness_pct}%` : "—"}</td>
                  <td>{q.duplicate_count}</td>
                  <td>
                    <span style={{
                      color: q.integrity_status === "OK" ? "var(--green)" : "var(--amber)",
                      background: q.integrity_status === "OK" ? "rgba(46,211,154,.08)" : "rgba(242,184,75,.08)",
                      padding: "4px 7px",
                      borderRadius: "999px",
                      fontSize: "8px",
                      fontWeight: 800,
                    }}>
                      {q.integrity_status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted" style={{ fontSize: "9px", marginTop: "8px", lineHeight: 1.5 }}>
          Missing intervals are real market closures (weekends/holidays) — detected but NOT silently repaired.
          Historical depth is the full DB range from HistoricalSyncState, not just the 120 most recent candles the Brain reads.
          Instrument consistency MIXED means both GC_FRONT_MONTH (Yahoo futures) and XAUUSD_SPOT (Gold API spot) candles exist at this TF — never merged.
        </p>
      </section>

      <section className="two-col" style={{ marginTop: "12px" }}>
        <div className="panel">
          <div className="panel-kicker">SYNC STATE</div>
          <h2>Per-lineage sync log</h2>
          <div className="list-stack">
            {syncStates.length === 0 && (
              <div className="empty-state">No sync runs recorded yet.</div>
            )}
            {syncStates.map((s, i) => (
              <div className="list-item" key={`${s.provider}-${s.interval}-${i}`}>
                <strong>{prettyTf(s.interval)} · {s.provider}</strong>
                <div style={{ fontSize: "9px", color: "var(--muted)", marginTop: "4px" }}>
                  status: <b>{s.sync_status}</b> · candles: <b>{s.total_candles}</b> · derivation: <b style={{ color: DERIVATION_COLOR[s.derivation] || "var(--muted)" }}>{s.derivation}</b>
                  <br />
                  instrument: <code style={{ color: "var(--blue)" }}>{s.instrument}</code> · provider_symbol: <code style={{ color: "var(--gold)" }}>{s.provider_symbol}</code>
                  <br />
                  source_timeframe: <code>{prettyTf(s.source_timeframe)}</code> → target_timeframe: <code>{prettyTf(s.target_timeframe)}</code>
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
                  <th>Instrument</th>
                  <th>Derivation</th>
                  <th>Source → Target</th>
                  <th>Fetched</th>
                  <th>Inserted</th>
                  <th>Skipped</th>
                  <th>Dup in batch</th>
                  <th>Invalid OHLC</th>
                  <th>OOO</th>
                  <th>Gaps</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(syncResult.timeframes).map(([tf, r]) => (
                  <tr key={tf}>
                    <td><strong>{prettyTf(tf)}</strong></td>
                    <td>
                      <span style={{ color: "var(--blue)", fontSize: "9px" }}>
                        {INSTRUMENT_LABEL[r.instrument || ""] || r.instrument || "—"}
                      </span>
                    </td>
                    <td>
                      <span style={{ color: DERIVATION_COLOR[r.derivation] || "var(--muted)", fontWeight: 700, fontSize: "9px" }}>
                        {r.derivation}
                      </span>
                    </td>
                    <td style={{ fontSize: "9px" }}>
                      <code>{prettyTf(r.source_timeframe || "")}</code> → <code>{prettyTf(r.target_timeframe)}</code>
                    </td>
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
