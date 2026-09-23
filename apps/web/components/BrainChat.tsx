"use client";

import { useState } from "react";
import { API } from "@/lib/api";

const examples = ["What is gold doing?", "Should I buy or sell?", "Why are you waiting?", "What did you learn today?"];

export function BrainChat() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("Ask the Brain about the current XAU/USD state.");
  const [loading, setLoading] = useState(false);

  async function ask(text?: string) {
    const q = (text || question).trim();
    if (!q || loading) return;
    setQuestion(q); setLoading(true);
    try {
      const res = await fetch(`${API}/api/brain/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
      const payload = await res.json();
      setAnswer(payload.answer || "No answer returned.");
    } catch {
      setAnswer("The Brain API is unreachable. Make sure the FastAPI backend is running on port 8000.");
    } finally { setLoading(false); }
  }

  return <section className="panel chat-panel">
    <div className="panel-head"><div><div className="panel-kicker">ASK THE BRAIN</div><h2>Market Q&A</h2></div><span className="mini-chip">LOCAL CONTEXT</span></div>
    <div className="brain-answer">{loading ? "Analyzing stored market context…" : answer}</div>
    <div className="prompt-row"><input value={question} onChange={(e)=>setQuestion(e.target.value)} onKeyDown={(e)=>{if(e.key==="Enter") ask();}} placeholder="Why is the Brain waiting?" /><button onClick={()=>ask()}>Ask</button></div>
    <div className="suggestions">{examples.map((x)=><button key={x} onClick={()=>ask(x)}>{x}</button>)}</div>
  </section>;
}
