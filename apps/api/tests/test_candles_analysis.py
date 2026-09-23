from datetime import datetime, timedelta, timezone

import pytest

from app.db.base import Base
from app.db.session import engine, SessionLocal
from app.db.models import TickRecord
from app.engine.candles import rebuild_recent_candles, get_candles
from app.engine.analysis import analyze_market


@pytest.mark.asyncio
async def test_local_ticks_build_candles_and_analysis_runs():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=5)
    with SessionLocal() as session:
        for i in range(600):
            ts = start + timedelta(seconds=i * 30)
            price = 4300 + i * 0.02
            session.add(TickRecord(symbol="XAU/USD", price=price, market_timestamp=ts.replace(tzinfo=None), received_at=ts.replace(tzinfo=None), provider="test"))
        session.commit()

    await rebuild_recent_candles(lookback_hours=8)
    m1 = await get_candles("1min", 1000)
    m5 = await get_candles("5min", 1000)
    m15 = await get_candles("15min", 1000)
    assert len(m1) >= 200
    assert len(m5) >= 50
    assert len(m15) >= 15

    analysis = await analyze_market(m1[-1].close, "RECENT", "CONNECTED")
    assert analysis.decision in {"BUY", "SELL", "WAIT"}
    assert analysis.readiness >= 50
    assert analysis.price is not None
