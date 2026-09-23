from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import and_, select

from app.core.config import settings
from app.db.models import CandleRecord
from app.db.session import SessionLocal
from app.engine.analysis import analyze_market
from app.engine.candles import get_candles, persist_tick, rebuild_recent_candles
from app.engine.predictions import evaluate_outcomes, persist_prediction
from app.models.market import BrainAnalysis, Candle, Quote
from app.services.gold_api import GoldApiProvider
from app.services.research import refresh_research
from app.services.twelve_data import TwelveDataProvider


@dataclass
class MarketState:
    quote: Quote | None = None
    analysis: BrainAnalysis | None = None
    source_status: str = "DISCONNECTED"
    last_error: str | None = None
    research_status: str = "STARTING"
    research_error: str | None = None
    last_research_at: datetime | None = None
    bootstrapped: bool = False
    last_prediction_at: datetime | None = None
    candles_cache: dict[str, list[Candle]] = field(default_factory=dict)

    def quote_with_freshness(self) -> Quote | None:
        if self.quote is None:
            return None
        now = datetime.now(timezone.utc)
        reference = self.quote.market_timestamp or self.quote.received_timestamp
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - reference).total_seconds())
        status = "RECENT" if age <= settings.stale_after_seconds else "STALE"
        return self.quote.model_copy(update={"age_seconds": round(age, 1), "status": status})


state = MarketState()
price_provider = GoldApiProvider()
history_provider = TwelveDataProvider()


async def _optional_history_bootstrap() -> None:
    if state.bootstrapped:
        return
    state.bootstrapped = True
    if not settings.twelve_data_api_key:
        return
    for interval in ("1min", "5min", "15min", "30min", "1h", "4h"):
        try:
            candles = await history_provider.get_candles(interval=interval, outputsize=120)
            with SessionLocal() as session:
                for c in candles:
                    ts = c.timestamp.astimezone(timezone.utc).replace(tzinfo=None)
                    existing = session.scalar(
                        select(CandleRecord.id).where(
                            and_(
                                CandleRecord.symbol == c.symbol,
                                CandleRecord.interval == c.interval,
                                CandleRecord.timestamp == ts,
                            )
                        )
                    )
                    if existing is None:
                        session.add(
                            CandleRecord(
                                symbol=c.symbol,
                                interval=c.interval,
                                timestamp=ts,
                                open=c.open,
                                high=c.high,
                                low=c.low,
                                close=c.close,
                                volume=c.volume,
                                sample_count=c.sample_count,
                                provider=c.provider,
                                received_at=datetime.now(timezone.utc).replace(tzinfo=None),
                            )
                        )
                session.commit()
        except Exception as exc:
            state.last_error = f"Optional history bootstrap failed: {exc}"
            break


async def refresh_quote_once() -> None:
    try:
        quote = await price_provider.get_quote()
        market_ts = quote.market_timestamp or quote.received_timestamp
        await persist_tick(quote.symbol, quote.price, market_ts, quote.received_timestamp, quote.provider)
        state.quote = quote
        state.source_status = "CONNECTED"
        state.last_error = None
        await rebuild_recent_candles()
        for interval in ("1min", "5min", "15min", "30min", "1h", "4h", "1day"):
            state.candles_cache[interval] = await get_candles(interval, 160)
        fresh = state.quote_with_freshness()
        state.analysis = await analyze_market(
            quote.price,
            fresh.status if fresh else "NO_DATA",
            state.source_status,
        )
        now = datetime.now(timezone.utc)
        if (
            state.last_prediction_at is None
            or (now - state.last_prediction_at).total_seconds() >= settings.prediction_interval_seconds
        ):
            await persist_prediction(state.analysis)
            state.last_prediction_at = now
    except Exception as exc:
        state.source_status = "ERROR"
        state.last_error = str(exc)
        fresh = state.quote_with_freshness()
        state.analysis = await analyze_market(
            fresh.price if fresh else None,
            fresh.status if fresh else "NO_DATA",
            state.source_status,
        )


async def refresh_research_once() -> None:
    _, error = await refresh_research()
    state.last_research_at = datetime.now(timezone.utc)
    if error:
        state.research_status = "ERROR"
        state.research_error = error
    else:
        state.research_status = "CONNECTED"
        state.research_error = None


async def collector_loop(stop_event: asyncio.Event) -> None:
    await _optional_history_bootstrap()
    quote_elapsed = settings.quote_refresh_seconds
    research_elapsed = settings.research_refresh_seconds
    outcome_elapsed = 60

    while not stop_event.is_set():
        if quote_elapsed >= settings.quote_refresh_seconds:
            await refresh_quote_once()
            quote_elapsed = 0
        if research_elapsed >= settings.research_refresh_seconds:
            await refresh_research_once()
            research_elapsed = 0
        if outcome_elapsed >= 60:
            try:
                await evaluate_outcomes()
            except Exception as exc:
                state.last_error = f"Outcome evaluator: {exc}"
            outcome_elapsed = 0

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1)
        except asyncio.TimeoutError:
            quote_elapsed += 1
            research_elapsed += 1
            outcome_elapsed += 1
