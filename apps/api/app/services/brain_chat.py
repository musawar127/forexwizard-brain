from __future__ import annotations

from app.models.market import AskResponse, BrainAnalysis
from app.services.research import recent_research


def _fmt_zone(zone) -> str:
    if not zone:
        return "not established yet"
    return f"{zone.low:.2f}–{zone.high:.2f}"


async def answer_question(question: str, analysis: BrainAnalysis | None) -> AskResponse:
    q = question.lower().strip()
    news = await recent_research(6)
    sources = [
        {"title": item["title"], "url": item["url"], "domain": item["domain"]}
        for item in news[:5]
    ]

    if analysis is None:
        return AskResponse(
            answer="The Brain does not have a valid market snapshot yet. Keep the backend running until a current XAU/USD quote is collected.",
            analysis=None,
            sources=sources,
        )

    if any(term in q for term in ("buy", "sell", "trade", "entry", "should i")):
        answer = (
            f"Current Brain decision: {analysis.decision} with {analysis.confidence:.0f}% model confidence and "
            f"{analysis.readiness:.0f}% data readiness. Market regime: {analysis.regime}. "
            f"Support: {_fmt_zone(analysis.support)}. Resistance: {_fmt_zone(analysis.resistance)}. "
            f"{analysis.message}"
        )
    elif any(term in q for term in ("what is gold doing", "market doing", "trend", "direction")):
        tf = ", ".join(f"{x.timeframe}: {x.trend}" for x in analysis.timeframes)
        answer = (
            f"XAU/USD is currently classified as {analysis.regime}. Decision: {analysis.decision}. "
            f"Timeframes — {tf}. {analysis.message}"
        )
    elif "why" in q:
        pros = " ".join(analysis.reasons_for[:5]) or "No directional evidence is strong enough yet."
        cons = " ".join(analysis.reasons_against[:5]) or "No major contradiction is currently recorded."
        answer = f"Evidence: {pros} Counter-evidence: {cons}"
    elif "support" in q or "resistance" in q or "zone" in q:
        answer = f"Current locally observed support is {_fmt_zone(analysis.support)} and resistance is {_fmt_zone(analysis.resistance)}. Zones are derived from this installation's stored sampled price history."
    elif any(term in q for term in ("learn", "news", "research", "internet")):
        if news:
            headlines = "; ".join(x["title"] for x in news[:4])
            answer = f"The research collector has recently stored these gold/macro items: {headlines}. The current rules engine does not treat headlines as proof of price direction; they are retained as research context."
        else:
            answer = "The research collector has not stored any recent articles yet. It will keep trying the configured GDELT query in the background."
    else:
        answer = (
            "I can currently answer questions about live XAU/USD price state, BUY/SELL/WAIT status, trend, support/resistance, "
            "why the Brain is waiting, and recent stored gold/macro research. An optional LLM layer can later expand this into open-ended research Q&A without changing the market-data engine."
        )

    return AskResponse(answer=answer, analysis=analysis, sources=sources)
