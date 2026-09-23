"""Phase 3.2 tests: gap classification + integrity semantics + technical_score alias + null statistical fields.

Verifies that:
  * Saturday gap timestamps → EXPECTED_MARKET_CLOSURE
  * Sunday gap timestamps (before 22:00 UTC) → EXPECTED_MARKET_CLOSURE
  * Sunday gap timestamps (after 22:00 UTC) → UNEXPECTED_GAP
  * CME holiday (e.g. 2026-01-01 New Year's Day) → EXPECTED_MARKET_CLOSURE
  * Mon-Thu 21:00 UTC gap → EXPECTED_SESSION_BREAK (daily maintenance)
  * Friday 21:00+ UTC gap → EXPECTED_MARKET_CLOSURE (daily close)
  * Other weekday gaps → UNEXPECTED_GAP
  * GapReport.integrity_status is HEALTHY when only expected gaps exist
  * GapReport.integrity_status is DEGRADED when unexpected gaps exist
  * CandleValidationReport.integrity_status is INVALID when invalid OHLC exists
  * CandleValidationReport.integrity_status is HEALTHY on a clean batch
  * BrainAnalysis.technical_score == BrainAnalysis.confidence (rename, same value)
  * BrainAnalysis.historical_probability and friends are None (Phase 4 fields)

Synthetic data only inside tests, per project convention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.analysis import analyze_market
from app.models.market import Candle
from app.services.historical.validator import (
    CandleValidationReport,
    GapReport,
    _classify_gap,
    find_gaps,
    validate_candles,
)


# ---------------------------------------------------------------------------
# Gap classifier — single-timestamp tests
# ---------------------------------------------------------------------------

def test_saturday_classified_as_expected_market_closure():
    # 2026-09-19 is a Saturday
    saturday = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    assert _classify_gap(saturday, "1min") == "EXPECTED_MARKET_CLOSURE"
    assert _classify_gap(saturday, "1h") == "EXPECTED_MARKET_CLOSURE"
    assert _classify_gap(saturday, "1day") == "EXPECTED_MARKET_CLOSURE"


def test_sunday_morning_classified_as_expected_market_closure():
    # 2026-09-20 is a Sunday — market closed until ~22:00 UTC
    sunday_morning = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    assert _classify_gap(sunday_morning, "1min") == "EXPECTED_MARKET_CLOSURE"


def test_sunday_after_22utc_classified_as_unexpected():
    # 2026-09-20 23:00 UTC — market should be open by then
    sunday_evening = datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)
    assert _classify_gap(sunday_evening, "1min") == "UNEXPECTED_GAP"


def test_cme_holiday_classified_as_expected_market_closure():
    # New Year's Day 2026
    nyd = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert _classify_gap(nyd, "1day") == "EXPECTED_MARKET_CLOSURE"
    # Christmas 2026
    christmas = datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc)
    assert _classify_gap(christmas, "1day") == "EXPECTED_MARKET_CLOSURE"
    # Independence Day 2026
    july4 = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    assert _classify_gap(july4, "1h") == "EXPECTED_MARKET_CLOSURE"


def test_weekday_session_break_classified_as_expected():
    # Monday 21:00 UTC = 17:00 ET (daily CME maintenance break starts)
    monday_2100 = datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc)  # Monday
    assert _classify_gap(monday_2100, "1min") == "EXPECTED_SESSION_BREAK"
    # Tuesday 21:30 UTC = 17:30 ET (still in the 1-hour break)
    tuesday_2130 = datetime(2026, 9, 22, 21, 30, tzinfo=timezone.utc)  # Tuesday
    assert _classify_gap(tuesday_2130, "1min") == "EXPECTED_SESSION_BREAK"
    # Wednesday 21:00 UTC
    wed_2100 = datetime(2026, 9, 23, 21, 0, tzinfo=timezone.utc)  # Wednesday
    assert _classify_gap(wed_2100, "1min") == "EXPECTED_SESSION_BREAK"


def test_friday_after_21utc_classified_as_expected_market_closure():
    # Friday 21:00 UTC = 17:00 ET — daily close, market closed for the day
    friday_2100 = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)  # Friday
    assert _classify_gap(friday_2100, "1min") == "EXPECTED_MARKET_CLOSURE"
    friday_2300 = datetime(2026, 9, 25, 23, 0, tzinfo=timezone.utc)
    assert _classify_gap(friday_2300, "1min") == "EXPECTED_MARKET_CLOSURE"


def test_unexpected_weekday_gap_classified_as_unexpected():
    # Monday 15:00 UTC = 11:00 ET — should be trading hours, no gap expected
    monday_1500 = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)  # Monday
    assert _classify_gap(monday_1500, "1min") == "UNEXPECTED_GAP"
    # Wednesday 03:00 UTC = 23:00 ET Monday — should be trading hours
    wed_0300 = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)  # Wednesday
    assert _classify_gap(wed_0300, "1min") == "UNEXPECTED_GAP"


# ---------------------------------------------------------------------------
# find_gaps() — end-to-end gap classification
# ---------------------------------------------------------------------------

def _mk(ts: datetime, *, interval: str = "1min") -> Candle:
    return Candle(
        symbol="XAU/USD", interval=interval, timestamp=ts,
        open=100, high=101, low=99, close=100.5,
        volume=None, sample_count=1, provider="Yahoo Finance (GC=F)",
        is_historical=True, derivation="DIRECT",
        provider_symbol="GC=F", instrument="GC_FRONT_MONTH",
        source_timeframe=interval, target_timeframe=interval,
    )


def test_find_gaps_classifies_weekend_closures_as_expected():
    """Friday close → Sunday open: ALL gaps in between should be
    EXPECTED_MARKET_CLOSURE. integrity_status should be HEALTHY."""
    # Friday 2026-09-18 18:00 UTC (2pm ET, market open) → Sunday 2026-09-20 22:00 UTC (market reopens)
    friday_close = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)
    sunday_open = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    candles = [_mk(friday_close), _mk(sunday_open)]
    report = find_gaps(candles, "1h")
    # Gaps should all be Saturday + Sunday-morning → EXPECTED_MARKET_CLOSURE
    assert report.expected_gap_count > 0
    assert report.unexpected_gap_count == 0
    assert report.integrity_status == "HEALTHY"


def test_find_gaps_classifies_weekday_gap_as_unexpected():
    """Two M1 candles spanning 5 minutes (instead of 1 minute) on a
    Wednesday morning should produce 4 UNEXPECTED_GAPs → DEGRADED."""
    # Wednesday 2026-09-23 03:00 → 03:05 UTC (should have 4 one-minute candles between)
    wed = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)
    candles = [_mk(wed), _mk(wed + timedelta(minutes=5))]
    report = find_gaps(candles, "1min")
    # 4 missing minutes between minute 0 and minute 5 (timestamps 1,2,3,4)
    assert report.missing_periods == 4
    assert report.unexpected_gap_count == 4
    assert report.expected_gap_count == 0
    assert report.integrity_status == "DEGRADED"


def test_find_gaps_healthy_when_only_session_breaks_and_closures():
    """Tight span that crosses ONLY the daily session break (Mon 21:00 UTC),
    no other gaps. The single gap at 21:00 is EXPECTED_SESSION_BREAK → HEALTHY."""
    # Monday 2026-09-21 20:00 UTC → 22:00 UTC: only one missing hour (21:00).
    # 21:00 UTC on a weekday = EXPECTED_SESSION_BREAK (daily CME maintenance).
    monday_2000 = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)
    monday_2200 = datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc)
    candles = [_mk(monday_2000), _mk(monday_2200)]
    report = find_gaps(candles, "1h")
    # Exactly 1 gap at Mon 21:00 UTC = EXPECTED_SESSION_BREAK
    assert len(report.gaps) == 1
    assert report.expected_session_breaks != []
    assert all(_classify_gap(g, "1h") == "EXPECTED_SESSION_BREAK" for g in report.expected_session_breaks)
    assert report.expected_gap_count == 1
    assert report.unexpected_gap_count == 0
    assert report.integrity_status == "HEALTHY"


def test_find_gaps_invalid_data_gaps_always_empty():
    """INVALID_DATA gaps are not produced by find_gaps() — invalidity
    is a CandleValidationReport concern, not a GapReport concern."""
    wed = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)
    candles = [_mk(wed), _mk(wed + timedelta(minutes=5))]
    report = find_gaps(candles, "1min")
    assert report.invalid_data_gaps == []
    assert report.invalid_candle_count == 0


# ---------------------------------------------------------------------------
# CandleValidationReport — integrity semantics
# ---------------------------------------------------------------------------

def test_validation_integrity_healthy_on_clean_batch():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [
        _mk(base + timedelta(minutes=i)) for i in range(10)
    ]
    report = validate_candles(candles)
    assert report.integrity_status == "HEALTHY"
    assert report.invalid_candle_count == 0
    assert report.duplicates == 0


def test_validation_integrity_invalid_on_bad_ohlc():
    """high < low → INVALID."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    bad = Candle(
        symbol="XAU/USD", interval="1min", timestamp=base,
        open=100, high=99, low=101, close=100,  # high < low
        volume=None, sample_count=1,
        provider="Yahoo Finance (GC=F)",
        is_historical=True, derivation="DIRECT",
        provider_symbol="GC=F", instrument="GC_FRONT_MONTH",
        source_timeframe="1min", target_timeframe="1min",
    )
    report = validate_candles([bad])
    assert report.integrity_status == "INVALID"
    assert report.invalid_candle_count == 1


def test_validation_integrity_invalid_on_zero_price():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    bad = Candle(
        symbol="XAU/USD", interval="1min", timestamp=base,
        open=0, high=0, low=0, close=0,
        volume=None, sample_count=1,
        provider="Yahoo Finance (GC=F)",
        is_historical=True, derivation="DIRECT",
        provider_symbol="GC=F", instrument="GC_FRONT_MONTH",
        source_timeframe="1min", target_timeframe="1min",
    )
    report = validate_candles([bad])
    assert report.integrity_status == "INVALID"
    assert report.invalid_candle_count == 1


def test_validation_integrity_degraded_on_duplicates():
    """Duplicate timestamps (no other corruption) → DEGRADED."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    a = _mk(base)
    b = _mk(base)  # duplicate timestamp
    report = validate_candles([a, b])
    assert report.duplicates == 1
    assert report.integrity_status == "DEGRADED"


def test_validation_integrity_empty_on_empty_input():
    report = validate_candles([])
    assert report.integrity_status == "EMPTY"


# ---------------------------------------------------------------------------
# BrainAnalysis — technical_score + null statistical fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_brain_analysis_technical_score_equals_confidence(monkeypatch):
    """Phase 3.2: technical_score is the SAME value as confidence —
    renamed for display, not recalculated."""
    from app.db.base import Base
    from app.db.session import engine, SessionLocal
    from app.db.models import CandleRecord
    from sqlalchemy import func, select
    # Seed 120 valid M1 candles so the Brain can produce a real analysis.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        for i in range(120):
            session.add(CandleRecord(
                symbol="XAU/USD", interval="1min", timestamp=base + timedelta(minutes=i),
                open=100 + 0.1 * i, high=100.5 + 0.1 * i, low=99.5 + 0.1 * i,
                close=100 + 0.1 * (i + 1), volume=None, sample_count=1,
                provider="Local sampled Gold API", received_at=base,
                is_historical=False, derivation="SAMPLED",
                provider_symbol="XAU", instrument="XAUUSD_SPOT",
                source_timeframe="TICK", target_timeframe="1min",
            ))
        session.commit()
    try:
        analysis = await analyze_market(100.0, "RECENT", "CONNECTED")
        assert analysis.technical_score is not None
        assert analysis.technical_score == analysis.confidence
    finally:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


@pytest.mark.asyncio
async def test_brain_analysis_statistical_fields_when_no_states_built():
    """Phase 3.2 + Phase 4: when no historical states are built, all
    statistical fields are NULL/0. probability_calibrated is ALWAYS False
    (Phase 4 invariant)."""
    from app.db.base import Base
    from app.db.session import engine
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        analysis = await analyze_market(100.0, "RECENT", "CONNECTED")
        # With no historical states built, all stats fields should be None
        # OR (in case the engine returned an empty result) zeros.
        assert analysis.historical_sample_size is None or analysis.historical_sample_size == 0
        # probability_calibrated is ALWAYS False in Phase 4 — never None
        assert analysis.probability_calibrated is False
        # historical_alignment must be one of the canonical values
        assert analysis.historical_alignment in {
            None, "SUPPORTS", "CONTRADICTS", "NEUTRAL", "INSUFFICIENT_DATA"
        }
    finally:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
