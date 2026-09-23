from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, select

from app.db.models import CandleRecord, TickRecord
from app.db.session import SessionLocal
from app.models.market import Candle


INTERVALS: dict[str, int] = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1h": 3600,
    "4h": 14400,
    "1day": 86400,
}


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_time(dt: datetime, seconds: int) -> datetime:
    dt = ensure_utc(dt)
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


async def persist_tick(symbol: str, price: float, market_timestamp: datetime, received_at: datetime, provider: str) -> None:
    market_timestamp = ensure_utc(market_timestamp).replace(tzinfo=None)
    received_at = ensure_utc(received_at).replace(tzinfo=None)
    with SessionLocal() as session:
        existing = session.scalar(
            select(TickRecord.id).where(
                and_(TickRecord.symbol == symbol, TickRecord.market_timestamp == market_timestamp)
            )
        )
        if existing is None:
            session.add(
                TickRecord(
                    symbol=symbol,
                    price=price,
                    market_timestamp=market_timestamp,
                    received_at=received_at,
                    provider=provider,
                )
            )
            session.commit()


async def rebuild_recent_candles(symbol: str = "XAU/USD", lookback_hours: int = 120) -> None:
    """Aggregate locally sampled spot quotes into OHLC candles.

    These are explicitly marked as sampled candles. They are not represented as
    exchange-native tick-complete OHLC data.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=lookback_hours)
    start_naive = start.replace(tzinfo=None)
    with SessionLocal() as session:
        rows = session.scalars(
            select(TickRecord)
            .where(TickRecord.symbol == symbol, TickRecord.market_timestamp >= start_naive)
            .order_by(TickRecord.market_timestamp.asc())
        ).all()

        if not rows:
            return

        for interval, seconds in INTERVALS.items():
            buckets: dict[datetime, list[TickRecord]] = defaultdict(list)
            for row in rows:
                buckets[floor_time(ensure_utc(row.market_timestamp), seconds)].append(row)

            interval_start = floor_time(start, seconds).replace(tzinfo=None)
            session.execute(
                delete(CandleRecord).where(
                    CandleRecord.symbol == symbol,
                    CandleRecord.interval == interval,
                    CandleRecord.provider == "Local sampled Gold API",
                    CandleRecord.timestamp >= interval_start,
                )
            )

            for ts, samples in buckets.items():
                prices = [float(s.price) for s in samples]
                session.add(
                    CandleRecord(
                        symbol=symbol,
                        interval=interval,
                        timestamp=ts.replace(tzinfo=None),
                        open=prices[0],
                        high=max(prices),
                        low=min(prices),
                        close=prices[-1],
                        volume=None,
                        sample_count=len(prices),
                        provider="Local sampled Gold API",
                        received_at=now.replace(tzinfo=None),
                    )
                )
        session.commit()


async def get_candles(interval: str, limit: int = 200, symbol: str = "XAU/USD") -> list[Candle]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(CandleRecord)
            .where(CandleRecord.symbol == symbol, CandleRecord.interval == interval)
            .order_by(CandleRecord.timestamp.desc())
            .limit(limit)
        ).all()

    rows.reverse()
    return [
        Candle(
            symbol=r.symbol,
            interval=r.interval,
            timestamp=ensure_utc(r.timestamp),
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
            sample_count=r.sample_count,
            provider=r.provider,
        )
        for r in rows
    ]


async def candle_counts(symbol: str = "XAU/USD") -> dict[str, int]:
    result: dict[str, int] = {}
    for interval in INTERVALS:
        result[interval] = len(await get_candles(interval, 5000, symbol))
    return result
