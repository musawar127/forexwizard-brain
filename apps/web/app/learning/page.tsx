"use client";

import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";
import type {
  CurrentSimilarityResult,
  HorizonStatistics,
  LearningStatus,
  NeighborMatch,
} from "@/lib/types";

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

function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

function fmtUsd(value: number | null | undefined): string {
  if (value == null) return "—";
  return `$${value.toFixed(2)}`;
}

const HORIZON_LABELS: Record<number, string> = {
  15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h", 480: "8h", 1440: "24h",
};

const ALIGNMENT_COLOR: Record<string, string> = {
  SUPPORTS: "var(--green)",
  CONTRADICTS: "var(--red)",
  NEUTRAL: "var(--amber)",
  INSUFFICIENT_DATA: "var(--muted)",
};

const QUALITY_COLOR: Record<string, string> = {
  GOOD: "var(--green)",
  MODERATE: "var(--blue)",
  LOW: "var(--amber)",
  INSUFFICIENT: "var(--red)",
};

export default function LearningPage() {
  const [status, setStatus] = useState<LearningStatus | null>(null);
  const [selectedHorizon, setSelectedHorizon] = useState<number>(60);
  const [selectedInstrument, setSelectedInstrument] = useState<string>("GC_FRONT_MONTH");
  const [similarity, setSimilarity] = useState<CurrentSimilarityResult | null>(null);
  const [similarityLoading, setSimilarityLoading] = useState(false);
  const [buildLoading, setBuildLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [buildMessage, setBuildMessage] = useState<string | null>(null);
  const [selectedAnalogId, setSelectedAnalogId] = useState<number | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/learning/status`, { cache: "no-store" });
      if (!res.ok) throw new Error(`status endpoint returned ${res.status}`);
      setStatus(await res.json());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backend unreachable");
    }
  }, []);

  const loadSimilarity = useCallback(async () => {
    setSimilarityLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API}/api/learning/current-similarity?instrument=${selectedInstrument}&horizon=${selectedHorizon}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`similarity endpoint returned ${res.status}`);
      setSimilarity(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Similarity fetch failed");
    } finally {
      setSimilarityLoading(false);
    }
  }, [selectedHorizon, selectedInstrument]);

  useEffect(() => {
    loadStatus();
    const t = window.setInterval(loadStatus, 30000);
    return () => window.clearInterval(t);
  }, [loadStatus]);

  useEffect(() => {
    loadSimilarity();
  }, [loadSimilarity]);

  async function runBuildStates() {
    if (buildLoading) return;
    setBuildLoading(true);
    setBuildMessage(null);
    setError(null);
    try {
      const res = await fetch(`${API}/api/learning/build-states`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instrument: selectedInstrument, batch_limit: 5000, clear_existing: false }),
      });
      if (!res.ok) throw new Error(`build-states endpoint returned ${res.status}`);
      const payload = await res.json();
      setBuildMessage(
        `Built ${payload.states_built} states (${payload.outcomes_built} outcomes) for ${payload.instrument} — earliest ${fmtTs(payload.earliest_state)}, latest ${fmtTs(payload.latest_state)}`,
      );
      await loadStatus();
      await loadSimilarity();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Build failed");
    } finally {
      setBuildLoading(false);
    }
  }

  const stats = similarity?.statistics;
  const neighbors = similarity?.neighbors || [];
  const selectedAnalog = neighbors.find((n) => n.state_id === selectedAnalogId);

  return (
    <>
      <div className="page-title">
        <div>
          <span>HISTORICAL LEARNING</span>
          <h2>Pattern similarity engine</h2>
        </div>
        <p>When the market looked genuinely similar in the past, what happened afterward? Historical observations do not guarantee future outcomes.</p>
      </div>

      {error && (
        <div className="global-alert">
          <strong>Backend:</strong> {error}. Start the FastAPI service on port 8000.
        </div>
      )}

      {/* ===== Status overview ===== */}
      <section className="hero-grid">
        <div className="panel big-number-panel">
          <span>States analyzed</span>
          <strong>{status?.total_states ?? "—"}</strong>
          <p>Historical market-state snapshots built from H1 candles with full no-look-ahead feature vectors.</p>
        </div>
        <div className="panel big-number-panel">
          <span>Total outcomes</span>
          <strong>{status?.total_outcomes ?? "—"}</strong>
          <p>Forward-looking outcome windows across 7 horizons (15m, 30m, 1h, 2h, 4h, 8h, 24h). Computed strictly from candles AFTER the state.</p>
        </div>
      </section>

      <section className="two-col" style={{ marginTop: "12px" }}>
        <div className="panel">
          <div className="panel-kicker">STATES BY INSTRUMENT</div>
          <h2>Instrument separation</h2>
          <div className="settings-list">
            {(status?.by_instrument ?? []).length === 0 && (
              <div className="muted" style={{ fontSize: "10px" }}>No states built yet — click “Build states” below.</div>
            )}
            {(status?.by_instrument ?? []).map((row) => (
              <div key={row.instrument}>
                <span>{row.instrument}</span>
                <strong>{row.count} states · {fmtTs(row.earliest)} → {fmtTs(row.latest)}</strong>
              </div>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="panel-kicker">STATES BY BASE TIMEFRAME</div>
          <h2>Base timeframe</h2>
          <div className="settings-list">
            {(status?.by_base_timeframe ?? []).length === 0 && (
              <div className="muted" style={{ fontSize: "10px" }}>No states built yet.</div>
            )}
            {(status?.by_base_timeframe ?? []).map((row) => (
              <div key={row.base_timeframe}>
                <span>{row.base_timeframe}</span>
                <strong>{row.count} states</strong>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div>
            <div className="panel-kicker">OUTCOMES BY HORIZON</div>
            <h2>Effective historical range per horizon</h2>
          </div>
          <button
            onClick={runBuildStates}
            disabled={buildLoading}
            style={{
              border: "1px solid var(--border)",
              background: buildLoading ? "#0b1016" : "#171f29",
              color: buildLoading ? "#5a6473" : "#cbd4df",
              borderRadius: "9px",
              padding: "9px 16px",
              cursor: buildLoading ? "wait" : "pointer",
              fontSize: "11px",
              fontWeight: 700,
            }}
          >
            {buildLoading ? "Building…" : "Build states"}
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Horizon</th>
                <th>Total Outcomes</th>
                <th>Valid (non-NULL)</th>
                <th>Effective Historical Range</th>
              </tr>
            </thead>
            <tbody>
              {(status?.by_horizon ?? []).map((row) => (
                <tr key={row.horizon_minutes}>
                  <td><strong>{HORIZON_LABELS[row.horizon_minutes] || `${row.horizon_minutes}m`}</strong></td>
                  <td>{row.total_outcomes}</td>
                  <td>{row.valid_outcomes}</td>
                  <td>
                    <span className="muted" style={{ fontSize: "9px" }}>
                      {row.horizon_minutes <= 30 ? "5.5d (M1) or 2y (H1-derived)" : row.horizon_minutes <= 480 ? "2y (H1)" : "2y (H1) or 10y (D1)"}
                    </span>
                  </td>
                </tr>
              ))}
              {(status?.by_horizon ?? []).length === 0 && (
                <tr>
                  <td colSpan={4} className="empty-state">No outcomes yet — click “Build states” to populate.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {buildMessage && (
          <p style={{ fontSize: "10px", color: "var(--green)", marginTop: "8px" }}>✓ {buildMessage}</p>
        )}
      </section>

      {/* ===== Current similarity ===== */}
      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div>
            <div className="panel-kicker">CURRENT SIMILARITY</div>
            <h2>When the market looked similar before, what happened?</h2>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <select
              value={selectedInstrument}
              onChange={(e) => setSelectedInstrument(e.target.value)}
              style={{
                border: "1px solid var(--border)", background: "#0a0f15", color: "var(--text)",
                borderRadius: "9px", padding: "7px 9px", fontSize: "11px", cursor: "pointer",
              }}
            >
              <option value="GC_FRONT_MONTH">GC_FRONT_MONTH (Yahoo futures)</option>
              <option value="XAUUSD_SPOT">XAUUSD_SPOT (Gold API spot)</option>
            </select>
            <select
              value={selectedHorizon}
              onChange={(e) => setSelectedHorizon(parseInt(e.target.value))}
              style={{
                border: "1px solid var(--border)", background: "#0a0f15", color: "var(--text)",
                borderRadius: "9px", padding: "7px 9px", fontSize: "11px", cursor: "pointer",
              }}
            >
              {Object.entries(HORIZON_LABELS).map(([m, lbl]) => (
                <option key={m} value={m}>{lbl}</option>
              ))}
            </select>
          </div>
        </div>

        {similarity?.error && (
          <div className="global-alert">
            <strong>Similarity error:</strong> {similarity.error}
          </div>
        )}

        {similarityLoading && (
          <div className="empty-state">Computing similarity…</div>
        )}

        {!similarityLoading && similarity && !similarity?.error && stats && (
          <>
            <div className="context-strip" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
              <div>
                <small>Instrument</small>
                <strong>{similarity.instrument}</strong>
              </div>
              <div>
                <small>Candidate states</small>
                <strong>{similarity.candidate_count}</strong>
              </div>
              <div>
                <small>Independent sample</small>
                <strong>{similarity.sample_size}</strong>
              </div>
              <div>
                <small>Sample quality</small>
                <strong style={{ color: QUALITY_COLOR[stats.sample_quality] || "var(--muted)" }}>
                  {stats.sample_quality}
                </strong>
              </div>
            </div>

            {/* Horizon statistics table */}
            <div className="table-wrap" style={{ marginTop: "12px" }}>
              <table>
                <thead>
                  <tr>
                    <th>Horizon</th>
                    <th>Sample</th>
                    <th>UP</th>
                    <th>95% CI</th>
                    <th>DOWN</th>
                    <th>95% CI</th>
                    <th>NEUTRAL</th>
                    <th>Median Return</th>
                    <th>Median MFE</th>
                    <th>Median MAE</th>
                  </tr>
                </thead>
                <tbody>
                  <tr key={stats.horizon_minutes}>
                    <td><strong>{HORIZON_LABELS[stats.horizon_minutes] || `${stats.horizon_minutes}m`}</strong></td>
                    <td>{stats.sample_size}</td>
                    <td>
                      <span style={{ color: "var(--green)", fontWeight: 700 }}>
                        {fmtPct(stats.up_rate.rate)} ({stats.up_count})
                      </span>
                    </td>
                    <td style={{ fontSize: "9px", color: "var(--muted)" }}>
                      {fmtPct(stats.up_rate.wilson_lower)}–{fmtPct(stats.up_rate.wilson_upper)}
                    </td>
                    <td>
                      <span style={{ color: "var(--red)", fontWeight: 700 }}>
                        {fmtPct(stats.down_rate.rate)} ({stats.down_count})
                      </span>
                    </td>
                    <td style={{ fontSize: "9px", color: "var(--muted)" }}>
                      {fmtPct(stats.down_rate.wilson_lower)}–{fmtPct(stats.down_rate.wilson_upper)}
                    </td>
                    <td>
                      <span style={{ color: "var(--amber)", fontWeight: 700 }}>
                        {fmtPct(stats.neutral_rate.rate)} ({stats.neutral_count})
                      </span>
                    </td>
                    <td>{stats.median_return != null ? `${stats.median_return.toFixed(2)}%` : "—"}</td>
                    <td style={{ color: "var(--green)" }}>{fmtUsd(stats.median_mfe)}</td>
                    <td style={{ color: "var(--red)" }}>{fmtUsd(stats.median_mae)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Historical alignment */}
            {similarity.historical_alignment && (
              <div style={{
                marginTop: "12px",
                border: "1px solid var(--border)",
                borderRadius: "9px",
                padding: "11px 13px",
                background: "rgba(10,14,20,.7)",
              }}>
                <div className="panel-kicker" style={{ marginBottom: "6px" }}>HISTORICAL ALIGNMENT (informational only — does NOT influence the BUY/SELL/WAIT decision)</div>
                <div style={{
                  color: ALIGNMENT_COLOR[similarity.historical_alignment] || "var(--muted)",
                  fontSize: "16px",
                  fontWeight: 850,
                  letterSpacing: "-0.02em",
                  marginBottom: "6px",
                }}>
                  {similarity.historical_alignment}
                </div>
                <p style={{ fontSize: "10px", color: "var(--muted)", lineHeight: 1.55 }}>
                  {similarity.interpretation_note || "Historical observations are descriptive statistics, not calibrated probabilities."}
                </p>
                {similarity.probability_calibrated === false && (
                  <p style={{ fontSize: "10px", color: "var(--amber)", marginTop: "6px" }}>
                    ⚠ probability_calibrated = FALSE — these rates are observed historical frequencies, not calibrated prediction probabilities.
                  </p>
                )}
              </div>
            )}

            {/* Mixed-instrument notice */}
            <div style={{
              marginTop: "10px",
              border: "1px solid rgba(242,184,75,.32)",
              background: "rgba(242,184,75,.06)",
              borderRadius: "9px",
              padding: "9px 11px",
              fontSize: "10px",
              color: "#c8b888",
              lineHeight: 1.55,
            }}>
              <strong style={{ color: "var(--amber)", display: "block", marginBottom: "4px" }}>
                Instrument context
              </strong>
              Historical analogue instrument: <strong>{similarity.instrument}</strong>. Live Brain instrument may be <strong>XAUUSD_SPOT</strong> — these are SEPARATE instruments. Statistics are NOT combined. Say: "Among {similarity.sample_size} similar {similarity.instrument} historical states, {fmtPct(stats.down_rate.rate)} produced a downward outcome." — NOT "{fmtPct(stats.down_rate.rate)} probability Gold will fall."
            </div>
          </>
        )}
      </section>

      {/* ===== Analogue explorer ===== */}
      <section className="panel" style={{ marginTop: "12px" }}>
        <div className="panel-head">
          <div>
            <div className="panel-kicker">ANALOGUE EXPLORER</div>
            <h2>Closest historical setups (top 10)</h2>
          </div>
          <span className="mini-chip">
            {neighbors.length} shown · click a row to inspect its feature snapshot
          </span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Similarity</th>
                <th>Regime</th>
                <th>RSI</th>
                <th>ATR%</th>
                <th>Structure</th>
                <th>H1 / H4 / D1</th>
                <th>Session</th>
                <th>Outcome</th>
                <th>MFE</th>
                <th>MAE</th>
              </tr>
            </thead>
            <tbody>
              {neighbors.map((n) => {
                const isSelected = n.state_id === selectedAnalogId;
                return (
                  <tr
                    key={n.state_id}
                    onClick={() => setSelectedAnalogId(isSelected ? null : n.state_id)}
                    style={{ cursor: "pointer", background: isSelected ? "rgba(215,181,109,.06)" : "transparent" }}
                  >
                    <td style={{ fontSize: "9px" }}>{fmtTs(n.timestamp)}</td>
                    <td>
                      <strong style={{ color: "var(--gold)" }}>{(n.similarity_score * 100).toFixed(1)}%</strong>
                    </td>
                    <td style={{ fontSize: "9px" }}>{n.feature_snapshot.market_regime || "—"}</td>
                    <td style={{ fontSize: "9px" }}>
                      {n.feature_snapshot.rsi != null ? (n.feature_snapshot.rsi * 100).toFixed(0) : "—"}
                    </td>
                    <td style={{ fontSize: "9px" }}>{n.feature_snapshot.atr_pct?.toFixed(3) ?? "—"}</td>
                    <td style={{ fontSize: "9px" }}>{n.feature_snapshot.swing_structure || "—"}</td>
                    <td style={{ fontSize: "8px", color: "var(--muted)" }}>
                      {n.feature_snapshot.h1_direction?.slice(0, 3) || "—"}/
                      {n.feature_snapshot.h4_direction?.slice(0, 3) || "—"}/
                      {n.feature_snapshot.d1_direction?.slice(0, 3) || "—"}
                    </td>
                    <td style={{ fontSize: "9px" }}>{n.feature_snapshot.session || "—"}</td>
                    <td>
                      <span style={{
                        color: n.outcome_direction === "UP" ? "var(--green)" : n.outcome_direction === "DOWN" ? "var(--red)" : "var(--amber)",
                        fontWeight: 700,
                        fontSize: "9px",
                      }}>
                        {n.outcome_direction || "NULL"}
                      </span>
                      {n.outcome_percentage_change != null && (
                        <span style={{ fontSize: "8px", color: "var(--muted)", marginLeft: "4px" }}>
                          ({n.outcome_percentage_change > 0 ? "+" : ""}{n.outcome_percentage_change.toFixed(2)}%)
                        </span>
                      )}
                    </td>
                    <td style={{ color: "var(--green)", fontSize: "9px" }}>{fmtUsd(n.outcome_mfe)}</td>
                    <td style={{ color: "var(--red)", fontSize: "9px" }}>{fmtUsd(n.outcome_mae)}</td>
                  </tr>
                );
              })}
              {neighbors.length === 0 && (
                <tr>
                  <td colSpan={11} className="empty-state">No analogs available — build states first.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {selectedAnalog && (
          <div style={{
            marginTop: "12px",
            border: "1px solid var(--border)",
            borderRadius: "9px",
            padding: "11px 13px",
            background: "rgba(10,14,20,.7)",
          }}>
            <div className="panel-kicker" style={{ marginBottom: "6px" }}>
              FEATURE SNAPSHOT — State #{selectedAnalog.state_id} @ {fmtTs(selectedAnalog.timestamp)}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px", fontSize: "10px" }}>
              <div><small style={{ color: "var(--muted)" }}>Trend</small><br /><strong>{selectedAnalog.feature_snapshot.trend || "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Regime</small><br /><strong>{selectedAnalog.feature_snapshot.market_regime || "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>RSI</small><br /><strong>{selectedAnalog.feature_snapshot.rsi != null ? (selectedAnalog.feature_snapshot.rsi * 100).toFixed(1) : "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>ATR %</small><br /><strong>{selectedAnalog.feature_snapshot.atr_pct?.toFixed(3) ?? "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Swing structure</small><br /><strong>{selectedAnalog.feature_snapshot.swing_structure || "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>H1 direction</small><br /><strong>{selectedAnalog.feature_snapshot.h1_direction || "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>H4 direction</small><br /><strong>{selectedAnalog.feature_snapshot.h4_direction || "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>D1 direction</small><br /><strong>{selectedAnalog.feature_snapshot.d1_direction || "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Session</small><br /><strong>{selectedAnalog.feature_snapshot.session || "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Dist to support (ATR)</small><br /><strong>{selectedAnalog.feature_snapshot.distance_to_support_atr ?? "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Dist to resistance (ATR)</small><br /><strong>{selectedAnalog.feature_snapshot.distance_to_resistance_atr ?? "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Volatility percentile</small><br /><strong>{selectedAnalog.feature_snapshot.volatility_percentile?.toFixed(1) ?? "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Timeframe alignment</small><br /><strong>{selectedAnalog.feature_snapshot.timeframe_alignment_score?.toFixed(2) ?? "—"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Similarity score</small><br /><strong style={{ color: "var(--gold)" }}>{(selectedAnalog.similarity_score * 100).toFixed(1)}%</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Outcome (future)</small><br /><strong style={{ color: selectedAnalog.outcome_direction === "UP" ? "var(--green)" : selectedAnalog.outcome_direction === "DOWN" ? "var(--red)" : "var(--amber)" }}>{selectedAnalog.outcome_direction || "NULL"}</strong></div>
              <div><small style={{ color: "var(--muted)" }}>Future price</small><br /><strong>{fmtUsd(selectedAnalog.outcome_future_price)}</strong></div>
            </div>
          </div>
        )}
      </section>

      <section className="panel info-panel" style={{ marginTop: "12px" }}>
        <div className="panel-kicker">INTERPRETATION RULES</div>
        <h2>How to read these numbers</h2>
        <p>
          The numbers above are <strong>descriptive statistics</strong> of how similar past market states behaved afterward.
          They are NOT calibrated probabilities of future outcomes.
          A "DOWN 64%" rate means "among {stats?.sample_size ?? "N"} selected historical {similarity?.instrument ?? "GC_FRONT_MONTH"} analogues, 64% produced a downward outcome over the next hour."
          It does <strong>NOT</strong> mean "64% probability Gold will fall."
        </p>
        <p>
          Historical analogue instrument may differ from the live Brain instrument — these statistics are never combined across instruments.
          probability_calibrated is <strong>FALSE</strong> throughout Phase 4 — calibration comes in a later phase.
          The BUY/SELL/WAIT decision is determined entirely by rules-v0.1 technical logic — historical_alignment is informational only.
        </p>
        <p style={{ fontStyle: "italic", color: "var(--amber)" }}>Historical observations do not guarantee future outcomes.</p>
      </section>

      {/* Recent audit runs */}
      {status && status.recent_runs && status.recent_runs.length > 0 && (
        <section className="panel" style={{ marginTop: "12px" }}>
          <div className="panel-head">
            <div>
              <div className="panel-kicker">AUDIT LOG</div>
              <h2>Recent similarity runs (last 10)</h2>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Instrument</th>
                  <th>Horizon</th>
                  <th>Candidates</th>
                  <th>Sample</th>
                  <th>Tech Decision</th>
                  <th>Alignment</th>
                </tr>
              </thead>
              <tbody>
                {status.recent_runs.map((r) => (
                  <tr key={r.id}>
                    <td style={{ fontSize: "9px" }}>{fmtTs(r.timestamp)}</td>
                    <td style={{ fontSize: "9px" }}>{r.instrument}</td>
                    <td style={{ fontSize: "9px" }}>{HORIZON_LABELS[r.horizon_minutes] || `${r.horizon_minutes}m`}</td>
                    <td>{r.candidate_count}</td>
                    <td>{r.sample_size}</td>
                    <td style={{ fontSize: "9px" }}>{r.technical_decision || "—"}</td>
                    <td>
                      <span style={{
                        color: ALIGNMENT_COLOR[r.historical_alignment || ""] || "var(--muted)",
                        fontWeight: 700,
                        fontSize: "9px",
                      }}>
                        {r.historical_alignment || "—"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
