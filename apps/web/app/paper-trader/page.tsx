"use client";

// Phase 6A: Paper Trader — self-learning paper trading dashboard.
// PAPER / SHADOW TRADING ONLY. No real broker orders.

import { useCallback, useEffect, useState } from "react";
import { API } from "@/lib/api";

function fmt(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

function fmtTs(v: string | null | undefined): string {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch { return v; }
}

type PaperStatus = {
  paper_account_id: string;
  starting_equity: number;
  current_equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  max_equity: number;
  max_drawdown: number;
  paper_trade_count: number;
  win_count: number;
  loss_count: number;
  breakeven_count: number;
  active_paper_trades: number;
  completed_trades: number;
  last_paper_evaluation: string | null;
  last_post_trade_review: string | null;
  paper_trader_status: string;
  version: string;
  probability_calibrated: boolean;
};

type PaperTrade = {
  paper_trade_id: string;
  direction: string;
  status: string;
  actual_paper_entry: number | null;
  stop_loss: number | null;
  tp1: number | null;
  max_objective: number | null;
  realized_r_multiple: number | null;
  mfe: number | null;
  mae: number | null;
  exit_reason: string | null;
  created_at: string | null;
  entry_timestamp: string | null;
  exit_timestamp: string | null;
};

type Performance = {
  total_trades: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate: number;
  average_r: number;
  avg_mfe: number;
  avg_mae: number;
  current_equity: number;
  max_drawdown: number;
  sample_quality: string;
  probability_calibrated: boolean;
};

type MistakePattern = {
  pattern_id: string;
  mistake_type: string;
  occurrence_count: number;
  loss_count: number;
  win_count: number;
  avg_r: number | null;
  status: string;
  sample_quality: string;
};

type CandidateRule = {
  candidate_id: string;
  description: string;
  source_trade_count: number;
  status: string;
  sample_quality: string;
  created_at: string | null;
};

const DECISION_COLORS: Record<string, string> = {
  BUY: "var(--green)", SELL: "var(--red)", WAIT: "var(--muted)",
};
const STATUS_COLORS: Record<string, string> = {
  WAITING_FOR_ENTRY: "var(--yellow)",
  ACTIVE: "var(--green)",
  TP1_REACHED: "var(--green)", TP2_REACHED: "var(--green)",
  TP3_REACHED: "var(--green)", MAX_REACHED: "var(--green)",
  STOPPED: "var(--red)", BREAKEVEN: "var(--muted)",
  EXPIRED: "var(--muted)", INVALIDATED: "var(--red)", CANCELLED: "var(--muted)",
};

export default function PaperTraderPage() {
  const [status, setStatus] = useState<PaperStatus | null>(null);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [mistakes, setMistakes] = useState<MistakePattern[]>([]);
  const [candidates, setCandidates] = useState<CandidateRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, t, p, m, c] = await Promise.all([
        fetch(`${API}/api/paper-trader/status`, { cache: "no-store" }).then(r => r.json()),
        fetch(`${API}/api/paper-trader/trades?limit=20`, { cache: "no-store" }).then(r => r.json()),
        fetch(`${API}/api/paper-trader/performance`, { cache: "no-store" }).then(r => r.json()),
        fetch(`${API}/api/paper-trader/mistakes`, { cache: "no-store" }).then(r => r.json()),
        fetch(`${API}/api/paper-trader/candidates`, { cache: "no-store" }).then(r => r.json()),
      ]);
      setStatus(s);
      setTrades(t.trades || []);
      setPerf(p);
      setMistakes(m.patterns || []);
      setCandidates(c.candidates || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load paper trader");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  const onCreateFromPlan = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await fetch(`${API}/api/paper-trader/create-from-current-plan`, { method: "POST", cache: "no-store" });
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create paper trade");
    } finally {
      setLoading(false);
    }
  }, [refresh]);

  return (
    <div className="page-content">
      <div className="page-title">
        <div>
          <span>PHASE 6A</span>
          <h2>Paper Trader — Self-Learning Shadow Trading</h2>
        </div>
        <p>
          PAPER / SHADOW TRADING ONLY. No real broker orders. No MT5 credentials.
          Virtually executes actionable ICT trade plans, tracks entry/SL/TP lifecycle,
          runs post-trade review, stores mistake patterns, creates candidate improvements
          (NEVER auto-promoted).
        </p>
      </div>

      {/* Paper Account */}
      {status && (
        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-head">
            <div>
              <div className="panel-kicker">PAPER ACCOUNT</div>
              <h2>Account: {status.paper_account_id}</h2>
            </div>
            <button
              onClick={onCreateFromPlan}
              disabled={loading}
              style={{
                border: "1px solid var(--border)", background: "#171f29", color: "#cbd4df",
                borderRadius: 9, padding: "9px 16px", cursor: "pointer", fontSize: 11, fontWeight: 700,
              }}
            >
              Create paper trade from current plan
            </button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 8, marginTop: 12, fontSize: 11 }}>
            <div><span>Starting equity:</span> <strong>${fmt(status.starting_equity)}</strong></div>
            <div><span>Current equity:</span> <strong style={{ color: status.current_equity >= status.starting_equity ? "var(--green)" : "var(--red)" }}>${fmt(status.current_equity)}</strong></div>
            <div><span>Realized PnL:</span> <strong style={{ color: status.realized_pnl >= 0 ? "var(--green)" : "var(--red)" }}>${fmt(status.realized_pnl)}</strong></div>
            <div><span>Max drawdown:</span> <strong style={{ color: "var(--red)" }}>${fmt(status.max_drawdown)}</strong></div>
            <div><span>Total trades:</span> <strong>{status.paper_trade_count}</strong></div>
            <div><span>Wins:</span> <strong style={{ color: "var(--green)" }}>{status.win_count}</strong></div>
            <div><span>Losses:</span> <strong style={{ color: "var(--red)" }}>{status.loss_count}</strong></div>
            <div><span>Breakeven:</span> <strong>{status.breakeven_count}</strong></div>
            <div><span>Active trades:</span> <strong style={{ color: "var(--yellow)" }}>{status.active_paper_trades}</strong></div>
            <div><span>Completed:</span> <strong>{status.completed_trades}</strong></div>
            <div><span>Status:</span> <strong>{status.paper_trader_status}</strong></div>
            <div><span>probability_calibrated:</span> <strong>{String(status.probability_calibrated)}</strong></div>
          </div>
          {status.paper_trader_status === "WAITING_FOR_ACTIONABLE_SETUP" && (
            <div className="empty-state" style={{ marginTop: 12 }}>
              PAPER_TRADER_WAITING_FOR_ACTIONABLE_SETUP — no actionable BUY/SELL plan exists yet.
              WAIT plans do NOT create paper trades.
            </div>
          )}
        </section>
      )}

      {/* Performance */}
      {perf && (
        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-kicker">PERFORMANCE</div>
          <h2>Aggregate metrics</h2>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 12, fontSize: 11 }}>
            <div><span>Win rate:</span> <strong>{perf.win_rate}%</strong></div>
            <div><span>Average R:</span> <strong style={{ color: perf.average_r >= 0 ? "var(--green)" : "var(--red)" }}>{fmt(perf.average_r)}R</strong></div>
            <div><span>Sample quality:</span> <strong style={{ color: "var(--yellow)" }}>{perf.sample_quality}</strong></div>
            <div><span>Avg MFE:</span> <strong>{fmt(perf.avg_mfe)}</strong></div>
            <div><span>Avg MAE:</span> <strong>{fmt(perf.avg_mae)}</strong></div>
            <div><span>Max DD:</span> <strong style={{ color: "var(--red)" }}>${fmt(perf.max_drawdown)}</strong></div>
          </div>
          <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 8 }}>
            Sample quality: INSUFFICIENT_SAMPLE (&lt;10) / EARLY (10-29) / DEVELOPING (30-99) / MORE_ESTABLISHED (100+).
            These are evidence-quality labels only — not statistical certainty.
            probability_calibrated remains FALSE.
          </p>
        </section>
      )}

      {/* Current/Recent Trades */}
      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-kicker">PAPER TRADES</div>
        <h2>Recent virtual trades</h2>
        {trades.length === 0 ? (
          <div className="empty-state">No paper trades yet. Click "Create paper trade from current plan".</div>
        ) : (
          <table className="data-table" style={{ width: "100%", marginTop: 8, fontSize: 10 }}>
            <thead>
              <tr>
                <th>ID</th>
                <th>Direction</th>
                <th>Status</th>
                <th>Entry</th>
                <th>SL</th>
                <th>TP1</th>
                <th>MAX</th>
                <th>R</th>
                <th>MFE</th>
                <th>MAE</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.paper_trade_id}>
                  <td style={{ fontSize: 9 }}>{t.paper_trade_id}</td>
                  <td style={{ color: DECISION_COLORS[t.direction] || "var(--muted)" }}>{t.direction}</td>
                  <td style={{ color: STATUS_COLORS[t.status] || "var(--muted)" }}>{t.status}</td>
                  <td>{fmt(t.actual_paper_entry)}</td>
                  <td>{fmt(t.stop_loss)}</td>
                  <td>{fmt(t.tp1)}</td>
                  <td>{fmt(t.max_objective)}</td>
                  <td style={{ color: (t.realized_r_multiple ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>{t.realized_r_multiple != null ? `${t.realized_r_multiple}R` : "—"}</td>
                  <td>{fmt(t.mfe)}</td>
                  <td>{fmt(t.mae)}</td>
                  <td>{fmtTs(t.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Mistake Patterns */}
      {mistakes.length > 0 && (
        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-kicker">REPEATING MISTAKE PATTERNS</div>
          <h2>Observed patterns from post-trade review</h2>
          <table className="data-table" style={{ width: "100%", marginTop: 8, fontSize: 10 }}>
            <thead>
              <tr>
                <th>Mistake Type</th>
                <th>Occurrences</th>
                <th>Losses</th>
                <th>Wins</th>
                <th>Avg R</th>
                <th>Status</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              {mistakes.map((m) => (
                <tr key={m.pattern_id}>
                  <td style={{ color: "var(--red)" }}>{m.mistake_type}</td>
                  <td>{m.occurrence_count}</td>
                  <td style={{ color: "var(--red)" }}>{m.loss_count}</td>
                  <td style={{ color: "var(--green)" }}>{m.win_count}</td>
                  <td>{m.avg_r != null ? `${m.avg_r}R` : "—"}</td>
                  <td style={{ color: m.status === "ACTIONABLE_CANDIDATE" ? "var(--yellow)" : "var(--muted)" }}>{m.status}</td>
                  <td style={{ color: "var(--yellow)" }}>{m.sample_quality}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 8 }}>
            A single loss does NOT create a production rule. Patterns become REPEATING after 3+ occurrences,
            UNDER_REVIEW after 5+ occurrences AND 3+ losses. Candidate rules are EXPERIMENTAL —
            NEVER auto-promoted into production BUY/SELL engine.
          </p>
        </section>
      )}

      {/* Candidate Rules */}
      {candidates.length > 0 && (
        <section className="panel" style={{ marginTop: 12 }}>
          <div className="panel-kicker">CANDIDATE STRATEGY IMPROVEMENTS</div>
          <h2>Experimental rules (NOT auto-promoted)</h2>
          <table className="data-table" style={{ width: "100%", marginTop: 8, fontSize: 10 }}>
            <thead>
              <tr>
                <th>ID</th>
                <th>Description</th>
                <th>Source Trades</th>
                <th>Status</th>
                <th>Sample</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.candidate_id}>
                  <td style={{ fontSize: 9 }}>{c.candidate_id}</td>
                  <td style={{ fontSize: 9 }}>{c.description}</td>
                  <td>{c.source_trade_count}</td>
                  <td style={{ color: c.status === "VALIDATED_CANDIDATE" ? "var(--yellow)" : "var(--muted)" }}>{c.status}</td>
                  <td style={{ color: "var(--yellow)" }}>{c.sample_quality}</td>
                  <td>{fmtTs(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 8 }}>
            Candidates are EXPERIMENTAL. Actual production integration happens in a later gated phase.
            Out-of-sample protection: discovery sample is separated from validation sample.
          </p>
        </section>
      )}

      {error && (
        <div className="empty-state" style={{ color: "var(--red)", marginTop: 12 }}>
          {error}
        </div>
      )}
    </div>
  );
}
