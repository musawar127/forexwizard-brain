import type { BrainAnalysis } from "@/lib/types";

export function DecisionCard({ brain }: { brain: BrainAnalysis | null }) {
  const decision = brain?.decision || "WAIT";
  const tone = decision === "BUY" ? "buy" : decision === "SELL" ? "sell" : "wait";
  return (
    <section className={`decision-card ${tone}`}>
      <div className="panel-kicker">BRAIN DECISION</div>
      <div className="decision-word">{brain ? decision : "STARTING"}</div>
      <div className="confidence-row">
        <span>Confidence</span><strong>{brain ? `${brain.confidence.toFixed(0)}%` : "—"}</strong>
      </div>
      <div className="meter"><span style={{ width: `${brain?.confidence || 0}%` }} /></div>
      <p>{brain?.message || "The Brain is waiting for the first valid market snapshot."}</p>
      <div className="decision-meta">
        <span><small>Regime</small>{brain?.regime || "—"}</span>
        <span><small>Risk</small>{brain?.risk || "—"}</span>
        <span><small>Readiness</small>{brain ? `${brain.readiness.toFixed(0)}%` : "—"}</span>
      </div>
    </section>
  );
}
