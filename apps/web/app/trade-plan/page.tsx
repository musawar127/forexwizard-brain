"use client";

// Phase 5.6: Trade Plan Engine — advisory XAU/USD trade plan page.
//
// This page is DOWNSTREAM of the Brain's BUY/SELL/WAIT decision.
// - WAIT -> plan_status=NO_TRADE (no invented entries/SL/TP)
// - BUY  -> entry zone + SL + TP1-4 + R:R from genuine structure
// - SELL -> same (mirrored)
//
// All plans are immutable once created. Lifecycle transitions and
// forward-validation outcomes are stored separately from the plan row.
// This page shows: current plan, lifecycle history, position-sizing
// calculator (optional, requires broker spec), and performance.

import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";

function fmtPrice(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

function fmtRR(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

function fmtTs(v: string | null | undefined): string {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleString([], {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return v;
  }
}

type Plan = {
  plan_id: string;
  created_at: string | null;
  market_timestamp: string | null;
  instrument: string;
  brain_decision: string;
  technical_score: number | null;
  entry_low: number | null;
  entry_high: number | null;
  entry_type: string | null;
  entry_reference: number | null;
  preferred_entry: number | null; // Phase 5.7
  entry_reason?: string | null; // Phase 5.7
  stop_loss: number | null;
  invalidation_level: number | null;
  invalidation_reason: string | null;
  structural_invalidation: number | null; // Phase 5.7
  sl_distance: number | null;
  tp1: number | null;
  tp2: number | null;
  tp3: number | null;
  tp4: number | null;
  max_objective: number | null; // Phase 5.7
  max_objective_reason: string | null; // Phase 5.7
  tp1_reason: string | null;
  tp2_reason: string | null;
  tp3_reason: string | null;
  tp4_reason: string | null;
  risk_distance: number | null;
  reward_tp1: number | null;
  reward_tp2: number | null;
  reward_tp3: number | null;
  reward_tp4: number | null;
  rr_tp1: number | null;
  rr_tp2: number | null;
  rr_tp3: number | null;
  rr_tp4: number | null;
  management_instructions: string | null;
  plan_status: string;
  plan_version: string;
  plan_engine_version: string | null; // Phase 5.7: 'trade-plan-v0.1' or 'ict-plan-v0.1'
  historical_similarity_run_id: string | null;
  historical_context: string | null;
  lifecycle_state: string;
  final_status: string | null;
  // Phase 5.7 ICT extensions
  setup_thesis: string | null;
  for_evidence: Array<{ kind: string; timeframe: string | null; description: string; bullish_or_bearish: string; confidence: number }> | null;
  against_evidence: Array<{ kind: string; timeframe: string | null; description: string; bullish_or_bearish: string; confidence: number }> | null;
  session_context: {
    active_sessions: string[];
    htf_trend: string;
    m15_trend: string;
    liquidity_swept: boolean;
    sweep_direction: string | null;
    displacement_confirmed: boolean;
    fvg_active: boolean;
    ob_active: boolean;
    location: string;
    sessions?: Array<{ name: string; high: number; low: number; is_dst: boolean }>;
  } | null;
  setup_pattern_id: string | null;
  // Phase 5.7.1: dedup + quality hardening
  setup_fingerprint: string | null;
  short_reason: string | null;
  reused_existing_plan: boolean;
  reused_reason: string | null;
};

type LifecycleEvent = {
  event_at: string | null;
  from_state: string | null;
  to_state: string;
  reason: string;
  market_price: number | null;
};

type Outcome = {
  last_evaluated_at: string | null;
  entry_touched: boolean;
  entry_touched_at: string | null;
  entry_touch_price: number | null;
  sl_before_target: boolean | null;
  sl_hit_at: string | null;
  sl_hit_price: number | null;
  tp1_reached: boolean;
  tp2_reached: boolean;
  tp3_reached: boolean;
  tp4_reached: boolean;
  tp1_reached_at: string | null;
  tp2_reached_at: string | null;
  tp3_reached_at: string | null;
  tp4_reached_at: string | null;
  max_favorable_excursion: number | null;
  max_adverse_excursion: number | null;
  time_to_entry_seconds: number | null;
  time_to_tp1_seconds: number | null;
  time_to_tp2_seconds: number | null;
  time_to_tp3_seconds: number | null;
  time_to_tp4_seconds: number | null;
  final_status: string | null;
};

type DetailResponse = {
  plan: Plan | null;
  lifecycle_events?: LifecycleEvent[];
  outcome?: Outcome | null;
  reason?: string;
};

type CurrentResponse = { plan: Plan | null; reason?: string };
type HistoryResponse = { plans: Plan[]; count: number };
type PerformanceResponse = {
  total_plans: number;
  by_decision: Record<string, number>;
  by_plan_status: Record<string, number>;
  by_lifecycle_state: Record<string, number>;
  by_engine_version: Record<string, number>;
  version_display: string; // "ict-plan-v0.1" | "trade-plan-v0.1" | "MIXED"
  wait_count: number;
  actionable_buy_count: number;
  actionable_sell_count: number;
  no_trade_count: number;
  actionable_total: number;
  outcome_summary: {
    total_outcomes_tracked: number;
    entry_touched_count: number;
    sl_before_target_count: number;
    tp1_reached: number;
    tp2_reached: number;
    tp3_reached: number;
    tp4_reached: number;
  };
  plan_version: string;
};

type PositionSizeRequest = {
  account_equity: number;
  risk_percent: number;
  sl_distance: number;
  instrument: string;
};

type PositionSizeResponse = {
  status: string;
  risk_amount: number | null;
  sl_distance: number | null;
  lot_size: number | null;
  contract_size: number | null;
  tick_size: number | null;
  tick_value: number | null;
  instrument: string | null;
  reason: string | null;
};

const STATUS_COLORS: Record<string, string> = {
  ACTIONABLE: "var(--green)",
  WAIT_FOR_ENTRY: "var(--yellow)",
  NO_TRADE: "var(--muted)",
  NO_VALID_ENTRY: "var(--red)",
  NO_VALID_SL: "var(--red)",
  NO_VALID_TP: "var(--red)",
  INSUFFICIENT_DATA: "var(--red)",
  STALE: "var(--red)",
};

const DECISION_COLORS: Record<string, string> = {
  BUY: "var(--green)",
  SELL: "var(--red)",
  WAIT: "var(--muted)",
  NO_DECISION: "var(--muted)",
};

export default function TradePlanPage() {
  const [current, setCurrent] = useState<CurrentResponse | null>(null);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [perf, setPerf] = useState<PerformanceResponse | null>(null);
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Position sizing form state
  const [accountEquity, setAccountEquity] = useState("");
  const [riskPercent, setRiskPercent] = useState("");
  const [slDistance, setSlDistance] = useState("");
  const [posResult, setPosResult] = useState<PositionSizeResponse | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, h, p] = await Promise.all([
        fetch(`${API}/api/trade-plan/current`, { cache: "no-store" }).then((r) => r.json()),
        fetch(`${API}/api/trade-plan/history?limit=20`, { cache: "no-store" }).then((r) => r.json()),
        fetch(`${API}/api/trade-plan/performance`, { cache: "no-store" }).then((r) => r.json()),
      ]);
      setCurrent(c);
      setHistory(h);
      setPerf(p);
      // If there's a current plan, load its detail too
      if (c.plan && c.plan.plan_id) {
        const d = await fetch(`${API}/api/trade-plan/${c.plan.plan_id}`, { cache: "no-store" }).then((r) => r.json());
        setDetail(d);
      } else {
        setDetail(null);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load trade plan");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000); // refresh every 15s
    return () => clearInterval(id);
  }, [refresh]);

  const onGenerate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await fetch(`${API}/api/trade-plan/generate`, { method: "POST", cache: "no-store" });
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to generate plan");
    } finally {
      setLoading(false);
    }
  }, [refresh]);

  const onCalculatePositionSize = useCallback(async () => {
    setError(null);
    try {
      const equity = parseFloat(accountEquity);
      const risk = parseFloat(riskPercent);
      const sl = parseFloat(slDistance);
      if (isNaN(equity) || isNaN(risk) || isNaN(sl)) {
        setError("Please enter valid numbers for account equity, risk %, and SL distance.");
        return;
      }
      const response = await fetch(`${API}/api/trade-plan/calculate-position-size`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_equity: equity,
          risk_percent: risk,
          sl_distance: sl,
          instrument: "XAU/USD",
        } satisfies PositionSizeRequest),
      });
      const result: PositionSizeResponse = await response.json();
      setPosResult(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to calculate position size");
    }
  }, [accountEquity, riskPercent, slDistance]);

  const plan = current?.plan ?? null;
  const isTradeable = plan && plan.plan_status === "ACTIONABLE" || plan?.plan_status === "WAIT_FOR_ENTRY";
  const isBuy = plan?.brain_decision === "BUY";
  const isSell = plan?.brain_decision === "SELL";

  return (
    <div className="page-content">
      <div className="page-title">
        <div>
          <span>PHASE 5.6</span>
          <h2>XAU/USD Trade Plan Engine</h2>
        </div>
        <p>
          Advisory trade plans downstream of the Brain&apos;s BUY/SELL/WAIT decision. Plans are immutable —
          lifecycle transitions and forward validation outcomes are tracked separately.
        </p>
      </div>

      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-head">
          <div>
            <div className="panel-kicker">CURRENT LIVE PLAN</div>
            <h2>Most recent trade plan</h2>
          </div>
          <button
            onClick={onGenerate}
            disabled={loading}
            style={{
              border: "1px solid var(--border)",
              background: "#171f29",
              color: "#cbd4df",
              borderRadius: 9,
              padding: "9px 16px",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 700,
            }}
          >
            Generate fresh plan
          </button>
        </div>

        {error && (
          <div className="empty-state" style={{ color: "var(--red)" }}>
            {error}
          </div>
        )}

        {!plan && (
          <div className="empty-state">
            {loading ? "Loading…" : "No plan generated yet. Click \"Generate fresh plan\"."}
          </div>
        )}

        {plan && (
          <div className="trade-plan-card" style={{ marginTop: 12 }}>
            <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
              <strong style={{ fontSize: 22, color: DECISION_COLORS[plan.brain_decision] || "var(--muted)" }}>
                {plan.brain_decision}
              </strong>
              <span className="mini-chip" style={{ color: STATUS_COLORS[plan.plan_status] || "var(--muted)" }}>
                {plan.plan_status}
              </span>
              <span className="mini-chip">{plan.lifecycle_state}</span>
              <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)" }}>
                Plan ID: {plan.plan_id} · v{plan.plan_version}
                {plan.plan_engine_version && (
                  <span style={{ color: "var(--accent)", marginLeft: 6 }}>
                    [{plan.plan_engine_version}]
                  </span>
                )}
              </span>
            </div>

            {/* Phase 5.7.1: dedup indicator */}
            {plan.reused_existing_plan && (
              <div className="panel" style={{ marginBottom: 12, background: "#0d131b", borderLeft: "3px solid var(--yellow)" }}>
                <div className="panel-kicker" style={{ color: "var(--yellow)" }}>REUSED EXISTING PLAN</div>
                <p style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                  {plan.reused_reason || "Market state unchanged — existing plan returned, no duplicate inserted."}
                </p>
              </div>
            )}

            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 12 }}>
              Created {fmtTs(plan.created_at)} · Market timestamp {fmtTs(plan.market_timestamp)} ·
              Technical score {plan.technical_score ?? "—"}
            </div>

            {!isTradeable && (
              <div className="empty-state" style={{ marginBottom: 12 }}>
                {plan.brain_decision === "WAIT" && "Brain is in WAIT — no trade plan emitted (NO_TRADE)."}
                {plan.brain_decision === "NO_DECISION" && "Brain has no decision — no trade plan emitted."}
                {plan.plan_status === "STALE" && "Market data was stale at plan generation time."}
                {plan.plan_status === "INSUFFICIENT_DATA" && "Insufficient data to derive a valid plan."}
                {plan.plan_status === "NO_VALID_ENTRY" && "No actionable entry zone could be derived from current structure."}
                {plan.plan_status === "NO_VALID_SL" && "No technical stop-loss level could be derived."}
                {plan.plan_status === "NO_VALID_TP" && "Could not derive 4 valid targets."}
              </div>
            )}

            {/* Phase 5.7: ICT/SMC thesis — always shown for ICT plans (even WAIT) */}
            {plan.plan_engine_version === "ict-plan-v0.1" && plan.setup_thesis && (
              <div className="panel" style={{ marginTop: 12, background: "#0d131b", borderLeft: "3px solid var(--accent)" }}>
                <div className="panel-kicker">PHASE 5.7 — ICT/SMC THESIS</div>
                <p style={{ fontSize: 11, marginTop: 4, lineHeight: 1.5 }}>{plan.setup_thesis}</p>

                {/* FOR evidence */}
                {plan.for_evidence && plan.for_evidence.length > 0 && (
                  <div style={{ marginTop: 12 }}>
                    <div style={{ fontSize: 10, color: "var(--green)", fontWeight: 700 }}>FOR</div>
                    <ul style={{ fontSize: 10, marginTop: 4, paddingLeft: 16 }}>
                      {plan.for_evidence.map((e, i) => (
                        <li key={i}>
                          <strong>{e.kind}</strong>
                          {e.timeframe && <span style={{ color: "var(--muted)" }}> [{e.timeframe}]</span>}: {e.description}
                          <span style={{ color: "var(--muted)", marginLeft: 4 }}>(conf {e.confidence.toFixed(0)})</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* AGAINST evidence */}
                {plan.against_evidence && plan.against_evidence.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ fontSize: 10, color: "var(--red)", fontWeight: 700 }}>AGAINST</div>
                    <ul style={{ fontSize: 10, marginTop: 4, paddingLeft: 16 }}>
                      {plan.against_evidence.map((e, i) => (
                        <li key={i}>
                          <strong>{e.kind}</strong>
                          {e.timeframe && <span style={{ color: "var(--muted)" }}> [{e.timeframe}]</span>}: {e.description}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Session context */}
                {plan.session_context && (
                  <div style={{ marginTop: 12, fontSize: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                    <strong>SESSION CONTEXT:</strong>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginTop: 4 }}>
                      <div><span>HTF trend:</span> <strong style={{ color: plan.session_context.htf_trend === "BULLISH" ? "var(--green)" : plan.session_context.htf_trend === "BEARISH" ? "var(--red)" : "var(--muted)" }}>{plan.session_context.htf_trend}</strong></div>
                      <div><span>M15 trend:</span> <strong>{plan.session_context.m15_trend}</strong></div>
                      <div><span>Location:</span> <strong>{plan.session_context.location}</strong></div>
                      <div><span>Liquidity swept:</span> <strong>{plan.session_context.liquidity_swept ? "YES" : "no"}</strong></div>
                      <div><span>Sweep dir:</span> <strong>{plan.session_context.sweep_direction || "—"}</strong></div>
                      <div><span>Displacement:</span> <strong>{plan.session_context.displacement_confirmed ? "confirmed" : "—"}</strong></div>
                      <div><span>FVG active:</span> <strong>{plan.session_context.fvg_active ? "YES" : "no"}</strong></div>
                      <div><span>OB active:</span> <strong>{plan.session_context.ob_active ? "YES" : "no"}</strong></div>
                      <div><span>Sessions:</span> <strong>{plan.session_context.active_sessions.join(", ") || "—"}</strong></div>
                    </div>
                  </div>
                )}

                {plan.setup_pattern_id && (
                  <div style={{ marginTop: 8, fontSize: 9, color: "var(--muted)" }}>
                    Pattern tracked: {plan.setup_pattern_id} (forward-validation prospective)
                  </div>
                )}
              </div>
            )}

            {isTradeable && (
              <>
                <div className="two-col">
                  <div className="panel">
                    <div className="panel-kicker">ENTRY ZONE</div>
                    <h2>{plan.entry_type || "—"}</h2>
                    <strong style={{ fontSize: 18 }}>
                      {fmtPrice(plan.entry_low)} – {fmtPrice(plan.entry_high)}
                    </strong>
                    <p style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                      Reference (midpoint): {fmtPrice(plan.entry_reference)}
                    </p>
                  </div>

                  <div className="panel">
                    <div className="panel-kicker">STOP / INVALIDATION</div>
                    <h2>{fmtPrice(plan.stop_loss)}</h2>
                    <p style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                      Structural level: {fmtPrice(plan.invalidation_level)} · SL distance: {fmtPrice(plan.sl_distance)}
                    </p>
                    <p style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                      Reason: {plan.invalidation_reason || "—"}
                    </p>
                  </div>
                </div>

                <div className="panel" style={{ marginTop: 12 }}>
                  <div className="panel-kicker">TARGETS</div>
                  <table className="data-table" style={{ width: "100%", marginTop: 8, fontSize: 11 }}>
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Level</th>
                        <th>Reward</th>
                        <th>R:R</th>
                        <th>Reason</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        { num: 1, level: plan.tp1, reward: plan.reward_tp1, rr: plan.rr_tp1, reason: plan.tp1_reason, reached: detail?.outcome?.tp1_reached },
                        { num: 2, level: plan.tp2, reward: plan.reward_tp2, rr: plan.rr_tp2, reason: plan.tp2_reason, reached: detail?.outcome?.tp2_reached },
                        { num: 3, level: plan.tp3, reward: plan.reward_tp3, rr: plan.rr_tp3, reason: plan.tp3_reason, reached: detail?.outcome?.tp3_reached },
                        { num: 4, level: plan.tp4, reward: plan.reward_tp4, rr: plan.rr_tp4, reason: plan.tp4_reason, reached: detail?.outcome?.tp4_reached },
                      ].map((t) => (
                        <tr key={t.num}>
                          <td>TP{t.num}</td>
                          <td>{fmtPrice(t.level)}</td>
                          <td>{fmtPrice(t.reward)}</td>
                          <td>{fmtRR(t.rr)}</td>
                          <td style={{ fontSize: 9, color: "var(--muted)" }}>{t.reason || "—"}</td>
                          <td>
                            {t.reached ? (
                              <span style={{ color: "var(--green)", fontSize: 10 }}>REACHED</span>
                            ) : (
                              <span style={{ color: "var(--muted)", fontSize: 10 }}>pending</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="panel" style={{ marginTop: 12 }}>
                  <div className="panel-kicker">TRADE MANAGEMENT</div>
                  <p style={{ fontSize: 11, marginTop: 4 }}>
                    {plan.management_instructions || "—"}
                  </p>
                  <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 6 }}>
                    Advisory only — no automatic execution.
                  </p>
                </div>

                <div className="panel" style={{ marginTop: 12 }}>
                  <div className="panel-kicker">HISTORICAL CONTEXT</div>
                  <p style={{ fontSize: 11, marginTop: 4 }}>
                    Similarity run: {plan.historical_similarity_run_id || "—"} ·
                    Alignment: <strong>{plan.historical_context || "INSUFFICIENT_DATA"}</strong>
                  </p>
                  <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 6 }}>
                    Historical context is INFORMATIONAL ONLY — does not influence the Brain&apos;s BUY/SELL/WAIT.
                    probability_calibrated remains FALSE.
                  </p>
                </div>

                {/* Phase 5.7: MAX OBJECTIVE (only for actionable ICT plans) */}
                {plan.plan_engine_version === "ict-plan-v0.1" && plan.max_objective && (
                  <div className="panel" style={{ marginTop: 12, background: "#0d131b", borderLeft: "3px solid var(--accent)" }}>
                    <div className="panel-kicker">MAX OBJECTIVE</div>
                    <strong style={{ fontSize: 18, color: "var(--accent)" }}>{fmtPrice(plan.max_objective)}</strong>
                    {plan.max_objective_reason && (
                      <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 4 }}>
                        {plan.max_objective_reason}
                      </p>
                    )}
                    <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 4 }}>
                      MAX OBJECTIVE is the highest structurally justified objective — not a guaranteed profit target.
                    </p>
                  </div>
                )}

                {detail?.outcome && (
                  <div className="panel" style={{ marginTop: 12 }}>
                    <div className="panel-kicker">FORWARD VALIDATION (PROSPECTIVE)</div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, fontSize: 10, marginTop: 8 }}>
                      <div>
                        <span>Entry touched:</span>
                        <strong>{detail.outcome.entry_touched ? "YES" : "no"}</strong>
                        {detail.outcome.entry_touched_at && (
                          <div style={{ fontSize: 9, color: "var(--muted)" }}>at {fmtTs(detail.outcome.entry_touched_at)}</div>
                        )}
                      </div>
                      <div>
                        <span>SL before target:</span>
                        <strong>
                          {detail.outcome.sl_before_target == null ? "—" : detail.outcome.sl_before_target ? "YES" : "no"}
                        </strong>
                      </div>
                      <div>
                        <span>Final status:</span>
                        <strong>{detail.outcome.final_status || "live"}</strong>
                      </div>
                      <div>
                        <span>MFE (max favorable):</span>
                        <strong>{fmtPrice(detail.outcome.max_favorable_excursion)}</strong>
                      </div>
                      <div>
                        <span>MAE (max adverse):</span>
                        <strong>{fmtPrice(detail.outcome.max_adverse_excursion)}</strong>
                      </div>
                      <div>
                        <span>Time to entry:</span>
                        <strong>
                          {detail.outcome.time_to_entry_seconds == null
                            ? "—"
                            : `${(detail.outcome.time_to_entry_seconds / 60).toFixed(1)} min`}
                        </strong>
                      </div>
                    </div>
                  </div>
                )}

                {detail?.lifecycle_events && detail.lifecycle_events.length > 0 && (
                  <div className="panel" style={{ marginTop: 12 }}>
                    <div className="panel-kicker">LIFECYCLE EVENTS</div>
                    <table className="data-table" style={{ width: "100%", marginTop: 8, fontSize: 10 }}>
                      <thead>
                        <tr>
                          <th>Time</th>
                          <th>From</th>
                          <th>To</th>
                          <th>Reason</th>
                          <th>Price</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.lifecycle_events.map((e, i) => (
                          <tr key={i}>
                            <td>{fmtTs(e.event_at)}</td>
                            <td>{e.from_state || "—"}</td>
                            <td>{e.to_state}</td>
                            <td style={{ fontSize: 9, color: "var(--muted)" }}>{e.reason}</td>
                            <td>{fmtPrice(e.market_price)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </section>

      {/* Position sizing — collapsed, moved below history per Phase 5.7.1 UI priority */}
      <details className="panel" style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", fontSize: 11, fontWeight: 700, color: "var(--muted)" }}>
          ▸ Optional position sizing (collapsed — not the main feature)
        </summary>
        <div className="panel-kicker" style={{ marginTop: 8 }}>OPTIONAL — CALCULATE LOT SIZE</div>
        <p style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
          Provide your account equity, risk %, and SL distance. Requires broker XAUUSD contract spec
          (XAUUSD_CONTRACT_SIZE / XAUUSD_TICK_SIZE / XAUUSD_TICK_VALUE) to be configured on the backend.
          If not set, returns POSITION_SIZE_UNAVAILABLE.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr auto", gap: 8, marginTop: 12 }}>
          <label style={{ fontSize: 10 }}>
            Account equity (USD)
            <input
              type="number"
              value={accountEquity}
              onChange={(e) => setAccountEquity(e.target.value)}
              placeholder="10000"
              style={{ width: "100%", padding: 6, background: "#0d131b", border: "1px solid var(--border)", color: "#fff", borderRadius: 4 }}
            />
          </label>
          <label style={{ fontSize: 10 }}>
            Risk %
            <input
              type="number"
              step="0.1"
              value={riskPercent}
              onChange={(e) => setRiskPercent(e.target.value)}
              placeholder="1.0"
              style={{ width: "100%", padding: 6, background: "#0d131b", border: "1px solid var(--border)", color: "#fff", borderRadius: 4 }}
            />
          </label>
          <label style={{ fontSize: 10 }}>
            SL distance (price)
            <input
              type="number"
              step="0.01"
              value={slDistance}
              onChange={(e) => setSlDistance(e.target.value)}
              placeholder="2.00"
              style={{ width: "100%", padding: 6, background: "#0d131b", border: "1px solid var(--border)", color: "#fff", borderRadius: 4 }}
            />
          </label>
          <button
            onClick={onCalculatePositionSize}
            style={{
              alignSelf: "end",
              border: "1px solid var(--border)",
              background: "#171f29",
              color: "#cbd4df",
              borderRadius: 4,
              padding: "6px 12px",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 700,
            }}
          >
            Calculate
          </button>
        </div>

        {posResult && (
          <div className="panel" style={{ marginTop: 12, background: "#0d131b" }}>
            <div className="panel-kicker">POSITION SIZE RESULT</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 8, fontSize: 11 }}>
              <div>
                <span>Status:</span>
                <strong style={{ color: posResult.status === "OK" ? "var(--green)" : "var(--red)" }}>
                  {posResult.status}
                </strong>
              </div>
              <div>
                <span>Risk amount:</span>
                <strong>{posResult.risk_amount != null ? `$${posResult.risk_amount.toFixed(2)}` : "—"}</strong>
              </div>
              <div>
                <span>Lot size:</span>
                <strong>{posResult.lot_size != null ? posResult.lot_size.toFixed(4) : "—"}</strong>
              </div>
              <div>
                <span>Contract size:</span>
                <strong>{posResult.contract_size ?? "—"}</strong>
              </div>
              <div>
                <span>Tick size:</span>
                <strong>{posResult.tick_size ?? "—"}</strong>
              </div>
              <div>
                <span>Tick value:</span>
                <strong>{posResult.tick_value != null ? `$${posResult.tick_value}` : "—"}</strong>
              </div>
            </div>
            {posResult.reason && (
              <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 8 }}>{posResult.reason}</p>
            )}
          </div>
        )}
      </details>

      {/* Performance — Phase 5.7.1: WAIT separated from actionable */}
      {perf && (
        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-kicker">PERFORMANCE</div>
          <h2>Aggregate metrics</h2>
          {/* Version display — MIXED if both engine versions present */}
          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4, marginBottom: 8 }}>
            <strong>Version:</strong> <span style={{ color: perf.version_display === "MIXED" ? "var(--yellow)" : "var(--accent)" }}>{perf.version_display}</span>
            {Object.keys(perf.by_engine_version || {}).length > 1 && (
              <span style={{ marginLeft: 8 }}>
                ({Object.entries(perf.by_engine_version).map(([v, c]) => `${v}: ${c}`).join(", ")})
              </span>
            )}
          </div>
          {/* Phase 5.7.1: WAIT separated from actionable BUY/SELL */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 8, marginTop: 8, fontSize: 11 }}>
            <div>
              <span style={{ color: "var(--muted)" }}>WAIT decisions:</span>
              <strong style={{ color: "var(--muted)" }}>{perf.wait_count}</strong>
            </div>
            <div>
              <span style={{ color: "var(--green)" }}>Actionable BUY:</span>
              <strong style={{ color: "var(--green)" }}>{perf.actionable_buy_count}</strong>
            </div>
            <div>
              <span style={{ color: "var(--red)" }}>Actionable SELL:</span>
              <strong style={{ color: "var(--red)" }}>{perf.actionable_sell_count}</strong>
            </div>
            <div>
              <span>Actionable total:</span>
              <strong>{perf.actionable_total}</strong>
            </div>
          </div>
          <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 6 }}>
            WAIT decisions are informational market-state records, not trading performance. Outcome metrics below track only actionable BUY/SELL plans.
          </div>
          {/* Outcome summary — only actionable plans */}
          {perf.actionable_total > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 12, fontSize: 11, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
              <div>
                <span>Entries touched:</span>
                <strong>{perf.outcome_summary.entry_touched_count}</strong>
              </div>
              <div>
                <span>SL before target:</span>
                <strong>{perf.outcome_summary.sl_before_target_count}</strong>
              </div>
              <div>
                <span>TP1 reached:</span>
                <strong>{perf.outcome_summary.tp1_reached}</strong>
              </div>
              <div>
                <span>TP4 reached:</span>
                <strong>{perf.outcome_summary.tp4_reached}</strong>
              </div>
              <div>
                <span>Outcomes tracked:</span>
                <strong>{perf.outcome_summary.total_outcomes_tracked}</strong>
              </div>
              <div>
                <span>Total plans:</span>
                <strong>{perf.total_plans}</strong>
              </div>
            </div>
          )}
        </section>
      )}

      {/* History */}
      {history && history.plans.length > 0 && (
        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-kicker">HISTORICAL PLAN RESULTS</div>
          <h2>Previous plans</h2>
          <table className="data-table" style={{ width: "100%", marginTop: 8, fontSize: 10 }}>
            <thead>
              <tr>
                <th>Plan ID</th>
                <th>Created</th>
                <th>Decision</th>
                <th>Status</th>
                <th>Short Reason / Thesis</th>
                <th>Entry ref</th>
                <th>SL</th>
                <th>MAX obj</th>
                <th>Engine</th>
              </tr>
            </thead>
            <tbody>
              {history.plans.map((p) => (
                <tr key={p.plan_id}>
                  <td style={{ fontSize: 9 }}>{p.plan_id}</td>
                  <td>{fmtTs(p.created_at)}</td>
                  <td style={{ color: DECISION_COLORS[p.brain_decision] || "var(--muted)" }}>{p.brain_decision}</td>
                  <td style={{ color: STATUS_COLORS[p.plan_status] || "var(--muted)" }}>{p.plan_status}</td>
                  <td style={{ fontSize: 9, color: "var(--muted)" }}>{p.short_reason || p.setup_thesis || "—"}</td>
                  <td>{fmtPrice(p.entry_reference)}</td>
                  <td>{fmtPrice(p.stop_loss)}</td>
                  <td>{fmtPrice(p.max_objective ?? p.tp4)}</td>
                  <td style={{ fontSize: 8, color: "var(--accent)" }}>{p.plan_engine_version || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
