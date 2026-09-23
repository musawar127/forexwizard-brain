import type { BrainAnalysis } from "@/lib/types";

const ALIGNMENT_COLOR: Record<string, string> = {
  SUPPORTS: "var(--green)",
  CONTRADICTS: "var(--red)",
  NEUTRAL: "var(--amber)",
  INSUFFICIENT_DATA: "var(--muted)",
};

export function DecisionCard({ brain }: { brain: BrainAnalysis | null }) {
  const decision = brain?.decision || "WAIT";
  const tone = decision === "BUY" ? "buy" : decision === "SELL" ? "sell" : "wait";
  // Phase 3.2: display "Technical score" rather than "Confidence" so the
  // number is not mistaken for a calibrated probability. The value
  // (brain.technical_score) is the SAME number as brain.confidence —
  // renamed for display only. Calculation is unchanged.
  const technicalScore = brain?.technical_score ?? brain?.confidence ?? 0;
  const instrumentConsistency = brain?.instrument_consistency;
  const isMixed = instrumentConsistency === "MIXED";

  // Phase 4: historical_alignment + populated statistical fields.
  // Informational only — does NOT influence the BUY/SELL/WAIT decision.
  const alignment = brain?.historical_alignment;
  const alignmentColor = alignment ? ALIGNMENT_COLOR[alignment] || "var(--muted)" : null;
  const hasHistoricalStats = brain?.historical_sample_size != null && (brain.historical_sample_size || 0) > 0;

  return (
    <section className={`decision-card ${tone}`}>
      <div className="panel-kicker">BRAIN DECISION</div>
      <div className="decision-word">{brain ? decision : "STARTING"}</div>
      <div className="confidence-row">
        <span>Technical score</span><strong>{brain ? `${technicalScore.toFixed(1)} / 100` : "—"}</strong>
      </div>
      <div className="meter"><span style={{ width: `${technicalScore}%` }} /></div>
      <p>{brain?.message || "The Brain is waiting for the first valid market snapshot."}</p>
      <div className="decision-meta">
        <span><small>Regime</small>{brain?.regime || "—"}</span>
        <span><small>Risk</small>{brain?.risk || "—"}</span>
        <span><small>Readiness</small>{brain ? `${brain.readiness.toFixed(0)}%` : "—"}</span>
      </div>
      {isMixed && (
        <div className="mixed-instrument-notice">
          <strong>Instrument context: MIXED</strong>
          <span>Live spot and futures historical context are both present. Historical futures observations are treated as a separate instrument — statistics are not combined.</span>
        </div>
      )}
      {hasHistoricalStats && brain?.historical_analogue_instrument && (
        <div className="prob-pending-notice" style={{ borderStyle: "solid", color: "#9aa6b4", fontStyle: "normal" }}>
          <small style={{ display: "block", color: "var(--gold)", fontWeight: 700, letterSpacing: "0.06em", marginBottom: "4px" }}>
            HISTORICAL {brain.historical_analogue_instrument} ANALOGUE — {brain.historical_analogue_horizon_minutes}m horizon
          </small>
          {brain.historical_sample_size != null && (
            <span style={{ display: "block", fontSize: "10px", color: "var(--muted)" }}>
              Sample: <strong style={{ color: "var(--text)" }}>{brain.historical_sample_size}</strong> independent analogues
              {" · "}Direction rate: <strong style={{ color: "var(--text)" }}>{brain.historical_direction_rate != null ? `${(brain.historical_direction_rate * 100).toFixed(1)}%` : "—"}</strong>
              {" · "}Median MFE: <strong style={{ color: "var(--green)" }}>{brain.historical_mfe != null ? `$${brain.historical_mfe.toFixed(2)}` : "—"}</strong>
              {" · "}Median MAE: <strong style={{ color: "var(--red)" }}>{brain.historical_mae != null ? `$${brain.historical_mae.toFixed(2)}` : "—"}</strong>
            </span>
          )}
          {alignment && alignmentColor && (
            <span style={{ display: "block", marginTop: "5px", fontSize: "10px" }}>
              Historical alignment: <strong style={{ color: alignmentColor }}>{alignment}</strong>
              <span style={{ color: "var(--muted)" }}> (informational — does NOT influence the technical decision)</span>
            </span>
          )}
          <small style={{ display: "block", marginTop: "5px", color: "var(--amber)", fontStyle: "italic" }}>
            probability_calibrated = {brain.probability_calibrated ? "TRUE" : "FALSE"} — descriptive stats, not prediction certainty.
          </small>
        </div>
      )}
      {!hasHistoricalStats && brain && (
        <div className="prob-pending-notice">
          <small>Statistical probability: not yet calculated (build states via /learning page)</small>
        </div>
      )}
    </section>
  );
}
