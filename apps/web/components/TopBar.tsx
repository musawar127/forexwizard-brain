"use client";

import type { Snapshot } from "@/lib/types";

export function TopBar({ data }: { data: Snapshot | null }) {
  const live = data?.quote?.status === "RECENT" && data?.source_status === "CONNECTED";
  return (
    <header className="topbar">
      <div>
        <div className="eyebrow">LIVE INTELLIGENCE TERMINAL</div>
        <h1>Gold Market Brain</h1>
      </div>
      <div className="top-actions">
        <div className="status-chip"><span className={`live-dot ${live ? "ok" : "warn"}`} />{live ? "FEED RECENT" : "FEED CHECK"}</div>
        <div className="version-chip">rules-v0.1</div>
      </div>
    </header>
  );
}
