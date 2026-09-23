import type { BrainAnalysis } from "@/lib/types";

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
      {brain && brain.historical_probability == null && (
        <div className="prob-pending-notice">
          <small>Statistical probability: not yet calculated (Phase 4)</small>
        </div>
      )}
    </section>
  );
}
