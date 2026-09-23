"""Phase 3 + 3.1 + 3.2: Historical candle validator.

This module NEVER repairs source data. It only:
  * identifies duplicates (same symbol + interval + timestamp)
  * identifies invalid OHLC (high < low, zero/negative prices, NaN/inf)
  * identifies out-of-order timestamps
  * identifies gaps (missing periods between consecutive candles
    given the interval's expected spacing)
  * identifies missing periods across a desired [earliest, latest] window

Phase 3.2 — gap classification:
  Each detected gap is classified into one of:
    EXPECTED_MARKET_CLOSURE   — falls on a weekend or CME-observed US holiday
    EXPECTED_SESSION_BREAK    — falls in the daily 1-hour CME maintenance
                                 window (17:00-18:00 ET, Mon-Thu)
    UNEXPECTED_GAP            — any other missing period (genuine data loss)
    INVALID_DATA              — placeholder for OHLC-corruption gaps

  Integrity status (Phase 3.2 semantics):
    HEALTHY   — only EXPECTED_MARKET_CLOSURE / EXPECTED_SESSION_BREAK gaps,
                0 unexpected gaps, 0 invalid candles
    DEGRADED  — at least one UNEXPECTED_GAP, 0 invalid candles
    INVALID   — invalid OHLC, zero/negative prices, or broken ordering

The caller (sync orchestrator) decides what to do with the report. The
default behavior is to keep the first valid candle of any duplicate
group and reject invalid candles, but the report itself is always
returned so it can be persisted for the /data page.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.models.market import Candle

# Reuse the canonical interval spacing from the engine module.
from app.engine.candles import INTERVALS


# ---------------------------------------------------------------------------
# Phase 3.2: Exchange/session-aware gap classification
# ---------------------------------------------------------------------------

# CME-observed US federal holidays for the years the historical backfill
# actually spans (2024-2027). These are the days GC=F futures do NOT trade
# (or trade abbreviated hours). Generated from the CME holiday calendar.
# When a gap's expected timestamp falls on one of these dates, the gap
# is classified as EXPECTED_MARKET_CLOSURE rather than UNEXPECTED_GAP.
_CME_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1),   # New Year's Day
    date(2024, 1, 15),  # MLK Day
    date(2024, 2, 19),  # Washington's Birthday
    date(2024, 3, 29),  # Good Friday
    date(2024, 5, 27),  # Memorial Day
    date(2024, 6, 19),  # Juneteenth
    date(2024, 7, 4),   # Independence Day
    date(2024, 9, 2),   # Labor Day
    date(2024, 11, 28), # Thanksgiving
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
    # 2027
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 4, 2),
    date(2027, 5, 31),
    date(2027, 6, 18),  # observed
    date(2027, 7, 5),   # observed
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),  # observed Christmas Eve
}


def _classify_gap(gap_ts: datetime, interval: str) -> str:
    """Classify a single gap timestamp.

    Returns one of:
      EXPECTED_MARKET_CLOSURE — weekend (Sat UTC, or Sun before 22:00 UTC)
                                or CME-observed US federal holiday
      EXPECTED_SESSION_BREAK  — daily 1-hour CME maintenance window
                                (21:00-22:00 UTC Mon-Thu, equivalent to
                                17:00-18:00 ET)
      UNEXPECTED_GAP         — any other missing period
    """
    if gap_ts.tzinfo is None:
        gap_ts = gap_ts.replace(tzinfo=timezone.utc)
    else:
        gap_ts = gap_ts.astimezone(timezone.utc)
    weekday = gap_ts.weekday()  # 0=Mon, 5=Sat, 6=Sun

    # Saturday: full market closure
    if weekday == 5:
        return "EXPECTED_MARKET_CLOSURE"
    # Sunday: closed until ~22:00 UTC (varies with DST; we use 22:00 as
    # the conservative open — anything before that is closure, anything
    # after is a normal trading session).
    if weekday == 6:
        if gap_ts.hour < 22:
            return "EXPECTED_MARKET_CLOSURE"
        return "UNEXPECTED_GAP"  # gap inside Sunday trading — unexpected
    # CME-observed US holiday (full closure)
    if gap_ts.date() in _CME_HOLIDAYS:
        return "EXPECTED_MARKET_CLOSURE"
    # Mon-Thu 21:00-22:00 UTC = daily 1-hour maintenance break (17:00-18:00 ET)
    if weekday in (0, 1, 2, 3) and gap_ts.hour == 21:
        return "EXPECTED_SESSION_BREAK"
    # Friday after 21:00 UTC (5pm ET) = daily close
    if weekday == 4 and gap_ts.hour >= 21:
        return "EXPECTED_MARKET_CLOSURE"
    return "UNEXPECTED_GAP"


# ---------------------------------------------------------------------------
# Validation reports
# ---------------------------------------------------------------------------

@dataclass
class CandleValidationReport:
    """Result of validating a batch of candles.

    All counts refer to the input batch. The `valid_candles` list is the
    post-validation deduplicated + ordered list ready for storage.

    Phase 3.2: `integrity_status` now uses HEALTHY/DEGRADED/INVALID
    semantics instead of the old OK/DEGRADED/EMPTY.
    """

    total_input: int = 0
    duplicates: int = 0
    invalid_ohlc: int = 0
    out_of_order: int = 0
    zero_or_negative_price: int = 0
    valid_candles: list[Candle] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)
    invalid_examples: list[str] = field(default_factory=list)

    @property
    def invalid_candle_count(self) -> int:
        """Total candles rejected due to any form of corruption."""
        return self.invalid_ohlc + self.zero_or_negative_price

    @property
    def integrity_status(self) -> str:
        """Phase 3.2 semantics — HEALTHY / DEGRADED / INVALID / EMPTY."""
        if self.total_input == 0:
            return "EMPTY"
        if self.invalid_candle_count > 0 or self.out_of_order > 0:
            return "INVALID"
        if self.duplicates > 0:
            return "DEGRADED"
        return "HEALTHY"


def validate_candles(candles: list[Candle]) -> CandleValidationReport:
    """Validate + deduplicate a list of candles.

    Rules:
      1. Reject candles with NaN / inf in any OHLC field.
      2. Reject candles with open/high/low/close <= 0.
      3. Reject candles where high < low, high < max(open, close),
         or low > min(open, close).
      4. Deduplicate on (symbol, interval, timestamp) — keep the FIRST
         occurrence in input order. Subsequent duplicates are counted.
      5. Sort the surviving candles by timestamp ascending. Candles that
         arrived out of order are counted but NOT silently repaired.
    """
    report = CandleValidationReport(total_input=len(candles))
    seen_keys: set[tuple[str, str, datetime]] = set()
    valid: list[Candle] = []

    for c in candles:
        key = (c.symbol, c.interval, c.timestamp)
        if key in seen_keys:
            report.duplicates += 1
            report.duplicate_keys.append(f"{c.symbol}|{c.interval}|{c.timestamp.isoformat()}")
            continue
        seen_keys.add(key)

        # Invalid OHLC checks
        if any(
            (v is None) or (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
            for v in (c.open, c.high, c.low, c.close)
        ):
            report.invalid_ohlc += 1
            report.invalid_examples.append(f"NaN/inf OHLC at {key}")
            continue
        if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
            report.zero_or_negative_price += 1
            report.invalid_examples.append(f"non-positive price at {key}")
            continue
        if c.high < c.low or c.high < max(c.open, c.close) or c.low > min(c.open, c.close):
            report.invalid_ohlc += 1
            report.invalid_examples.append(f"inconsistent H/L/O/C at {key}")
            continue
        valid.append(c)

    # Detect out-of-order timestamps (on the surviving candles, in their
    # original arrival order). This count does NOT count duplicates.
    last_ts: datetime | None = None
    for c in valid:
        if last_ts is not None and c.timestamp < last_ts:
            report.out_of_order += 1
        last_ts = c.timestamp

    # Final pass: sort ascending. The validator reports out_of_order above
    # but the persisted list is canonical (ascending). This is "presentation
    # ordering" not "data repair": the timestamps themselves are untouched.
    valid.sort(key=lambda c: c.timestamp)
    report.valid_candles = valid
    return report


@dataclass
class GapReport:
    """Result of gap/missing-period detection across a candle list.

    Phase 3.2: gaps are now classified into EXPECTED_MARKET_CLOSURE,
    EXPECTED_SESSION_BREAK, UNEXPECTED_GAP. The previous aggregate
    `missing_periods` is preserved for backward compat = sum of all
    classified gaps.

    `integrity_status` is computed from the gap mix:
      HEALTHY  — only expected closures/breaks
      DEGRADED — at least one UNEXPECTED_GAP
      INVALID  — never set here (invalidity is a CandleValidationReport concern)
    """

    interval: str
    interval_seconds: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    actual_count: int
    expected_periods: int
    gaps: list[datetime] = field(default_factory=list)
    # Phase 3.2: classified gap lists
    expected_market_closures: list[datetime] = field(default_factory=list)
    expected_session_breaks: list[datetime] = field(default_factory=list)
    unexpected_gaps: list[datetime] = field(default_factory=list)
    invalid_data_gaps: list[datetime] = field(default_factory=list)

    @property
    def missing_periods(self) -> int:
        """Backward-compat: total gap count across all classifications."""
        return len(self.gaps)

    @property
    def expected_gap_count(self) -> int:
        """Count of EXPECTED_MARKET_CLOSURE + EXPECTED_SESSION_BREAK gaps."""
        return len(self.expected_market_closures) + len(self.expected_session_breaks)

    @property
    def unexpected_gap_count(self) -> int:
        """Count of UNEXPECTED_GAP gaps only."""
        return len(self.unexpected_gaps)

    @property
    def invalid_candle_count(self) -> int:
        """Count of INVALID_DATA gaps (currently always 0 — invalidity
        is reported by the CandleValidationReport, not the GapReport)."""
        return len(self.invalid_data_gaps)

    @property
    def completeness_pct(self) -> float:
        if self.expected_periods <= 0:
            return 0.0
        return round(100.0 * self.actual_count / self.expected_periods, 2)

    @property
    def integrity_status(self) -> str:
        """Phase 3.2: HEALTHY if only expected closures, DEGRADED if any
        unexpected gap. INVALID is not set by GapReport — see
        CandleValidationReport.invalid_candle_count for that signal."""
        if self.unexpected_gap_count > 0:
            return "DEGRADED"
        return "HEALTHY"


def find_gaps(candles: list[Candle], interval: str) -> GapReport:
    """Detect gaps between consecutive candles for a given interval.

    The candle list MUST be sorted ascending by timestamp — call
    `validate_candles()` first to guarantee this.

    Phase 3.2: each detected gap is classified by exchange/session
    awareness (weekend, holiday, daily maintenance break, or unexpected).
    """
    if interval not in INTERVALS:
        return GapReport(
            interval=interval,
            interval_seconds=0,
            first_timestamp=None,
            last_timestamp=None,
            actual_count=len(candles),
            expected_periods=0,
        )
    seconds = INTERVALS[interval]
    if not candles:
        return GapReport(
            interval=interval,
            interval_seconds=seconds,
            first_timestamp=None,
            last_timestamp=None,
            actual_count=0,
            expected_periods=0,
        )
    first = candles[0].timestamp
    last = candles[-1].timestamp
    span_seconds = int((last - first).total_seconds())
    expected_periods = max(1, span_seconds // seconds + 1)

    gaps: list[datetime] = []
    expected_closures: list[datetime] = []
    expected_breaks: list[datetime] = []
    unexpected_gaps: list[datetime] = []

    expected_next = first
    for c in candles:
        # If a candle is exactly where we expect it, advance.
        if c.timestamp == expected_next:
            expected_next = c.timestamp + timedelta(seconds=seconds)
        elif c.timestamp > expected_next:
            # Record every missed period between expected_next and c.timestamp.
            gap_cursor = expected_next
            while gap_cursor < c.timestamp:
                gaps.append(gap_cursor)
                classification = _classify_gap(gap_cursor, interval)
                if classification == "EXPECTED_MARKET_CLOSURE":
                    expected_closures.append(gap_cursor)
                elif classification == "EXPECTED_SESSION_BREAK":
                    expected_breaks.append(gap_cursor)
                else:
                    unexpected_gaps.append(gap_cursor)
                gap_cursor = gap_cursor + timedelta(seconds=seconds)
            expected_next = c.timestamp + timedelta(seconds=seconds)
        else:
            # c.timestamp < expected_next: duplicate/overlap, ignored by gap logic.
            pass

    return GapReport(
        interval=interval,
        interval_seconds=seconds,
        first_timestamp=first,
        last_timestamp=last,
        actual_count=len(candles),
        expected_periods=expected_periods,
        gaps=gaps,
        expected_market_closures=expected_closures,
        expected_session_breaks=expected_breaks,
        unexpected_gaps=unexpected_gaps,
    )


def find_duplicates(candles: list[Candle]) -> list[tuple[str, int]]:
    """Return (key, count) for every (symbol, interval, timestamp) that
    appears more than once in the input list."""
    counter: Counter[str] = Counter()
    for c in candles:
        counter[f"{c.symbol}|{c.interval}|{c.timestamp.isoformat()}"] += 1
    return [(key, count) for key, count in counter.items() if count > 1]
