"use client";

// Phase 5.6: TradePlanCard — shown on Overview below the DecisionCard.
//
// Loads the most recent trade plan from /api/trade-plan/current and shows
// a compact summary. For WAIT decisions, shows NO_TRADE. For BUY/SELL,
// shows entry zone, SL, TP1-4 with R:R. Click "Open full plan" goes to
// /trade-plan for details, lifecycle history, and position sizing.

import Link from "next/link";
import { API } from "@/lib/api";
import { useEffect, useState } from "react";

type Plan = {
  plan_id: string;
  brain_decision: string;
  plan_status: string;
  lifecycle_state: string;
  entry_low: number | null;
  entry_high: number | null;
  entry_reference: number | null;
  stop_loss: number | null;
  tp1: number | null;
  tp2: number | null;
  tp3: number | null;
  tp4: number | null;
  rr_tp1: number | null;
  rr_tp2: number | null;
  rr_tp3: number | null;
  rr_tp4: number | null;
  historical_context: string | null;
  plan_version: string;
};

type CurrentResponse = { plan: Plan | null; reason?: string };

function fmt(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

const DECISION_COLORS: Record<string, string> = {
  BUY: "var(--green)",
  SELL: "var(--red)",
  WAIT: "var(--muted)",
  NO_DECISION: "var(--muted)",
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

export function TradePlanCard() {
  const [data, setData] = useState<CurrentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/trade-plan/current`, { cache: "no-store" });
        const j: CurrentResponse = await r.json();
        if (!mounted) return;
        setData(j);
        setError(null);
      } catch (e: unknown) {
        if (mounted) setError(e instanceof Error ? e.message : "Failed to load trade plan");
      }
    };
    load();
    const id = window.setInterval(load, 15000);
    return () => { mounted = false; window.clearInterval(id); };
  }, []);

  const plan = data?.plan ?? null;

  return (
    <section className="panel trade-plan-card" style={{ marginTop: 12 }}>
      <div className="panel-head">
        <div>
          <div className="panel-kicker">PHASE 5.6 — TRADE PLAN ENGINE</div>
          <h2>XAU/USD advisory trade plan</h2>
        </div>
        <Link href="/trade-plan" className="mini-chip" style={{ cursor: "pointer", textDecoration: "none" }}>
          Open full plan →
        </Link>
      </div>

      {error && (
        <div className="empty-state" style={{ color: "var(--red)" }}>
          {error}
        </div>
      )}

      {!plan && !error && <div className="empty-state">Loading trade plan…</div>}

      {plan && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
            <strong style={{ fontSize: 20, color: DECISION_COLORS[plan.brain_decision] || "var(--muted)" }}>
              {plan.brain_decision}
            </strong>
            <span className="mini-chip" style={{ color: STATUS_COLORS[plan.plan_status] || "var(--muted)" }}>
              {plan.plan_status}
            </span>
            <span className="mini-chip">{plan.lifecycle_state}</span>
            <span className="mini-chip">v{plan.plan_version}</span>
          </div>

          {plan.plan_status === "NO_TRADE" && (
            <div className="empty-state">
              Brain is in WAIT — no trade plan emitted. Do not force a trade.
            </div>
          )}

          {plan.plan_status === "STALE" && (
            <div className="empty-state">Market data was stale at plan generation time.</div>
          )}

          {plan.plan_status === "INSUFFICIENT_DATA" && (
            <div className="empty-state">Insufficient data to derive a valid plan.</div>
          )}

          {plan.plan_status === "NO_VALID_ENTRY" && (
            <div className="empty-state">No actionable entry zone derivable from current structure.</div>
          )}

          {(plan.plan_status === "ACTIONABLE" || plan.plan_status === "WAIT_FOR_ENTRY") && (
            <>
              <div className="two-col" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div className="panel" style={{ background: "#0d131b", padding: 8 }}>
                  <div className="panel-kicker">ENTRY ZONE</div>
                  <strong style={{ fontSize: 14 }}>
                    {fmt(plan.entry_low)} – {fmt(plan.entry_high)}
                  </strong>
                  <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 4 }}>
                    ref: {fmt(plan.entry_reference)}
                  </div>
                </div>
                <div className="panel" style={{ background: "#0d131b", padding: 8 }}>
                  <div className="panel-kicker">STOP / INVALIDATION</div>
                  <strong style={{ fontSize: 14 }}>{fmt(plan.stop_loss)}</strong>
                </div>
              </div>

              <table className="data-table" style={{ width: "100%", marginTop: 8, fontSize: 10 }}>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>TP</th>
                    <th>R:R</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { num: 1, level: plan.tp1, rr: plan.rr_tp1 },
                    { num: 2, level: plan.tp2, rr: plan.rr_tp2 },
                    { num: 3, level: plan.tp3, rr: plan.rr_tp3 },
                    { num: 4, level: plan.tp4, rr: plan.rr_tp4 },
                  ].map((t) => (
                    <tr key={t.num}>
                      <td>TP{t.num}</td>
                      <td>{fmt(t.level)}</td>
                      <td>{t.rr == null ? "—" : t.rr.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <p style={{ fontSize: 9, color: "var(--muted)", marginTop: 8 }}>
                Historical context: {plan.historical_context || "INSUFFICIENT_DATA"} · probability_calibrated: FALSE
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
