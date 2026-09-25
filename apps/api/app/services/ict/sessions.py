"""Phase 5.7: ICT/SMC session engine — Asia / London / New York.

Sessions are computed in UTC and adjusted for DST:
  - Asia:     00:00 - 09:00 UTC (no DST)
  - London:   07:00 - 16:00 UTC in winter (GMT)
              06:00 - 15:00 UTC in summer (BST = UTC+1)
  - New York: 12:00 - 21:00 UTC in winter (EST = UTC-5)
              11:00 - 20:00 UTC in summer (EDT = UTC-4)

DST is determined by the date:
  - London BST: last Sunday of March -> last Sunday of October
  - NY EDT:     same dates (second Sunday of March -> first Sunday of November
                in US rules, but for trading sessions the London dates are
                close enough; we use a simple heuristic)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal


SESSION_NAMES: tuple[str, ...] = ("ASIA", "LONDON", "NEW_YORK")


@dataclass
class SessionWindow:
    """A trading session time window (UTC)."""
    name: str
    start_utc_hour: int
    end_utc_hour: int
    is_dst: bool


def _is_uk_dst(d: date) -> bool:
    """UK BST runs from the last Sunday of March to the last Sunday of October."""
    if d.month < 3 or d.month > 10:
        return False
    if d.month in (5, 6, 7, 8, 9):
        return True
    # Find last Sunday of March and October
    last_sunday_march = _last_sunday_of_month(d.year, 3)
    last_sunday_october = _last_sunday_of_month(d.year, 10)
    if d.month == 3:
        return d >= last_sunday_march
    if d.month == 10:
        return d < last_sunday_october
    return False


def _is_us_dst(d: date) -> bool:
    """US EDT runs from the second Sunday of March to the first Sunday of November."""
    if d.month < 3 or d.month > 11:
        return False
    if d.month in (4, 5, 6, 7, 8, 9, 10):
        return True
    second_sunday_march = _nth_sunday_of_month(d.year, 3, 2)
    first_sunday_november = _nth_sunday_of_month(d.year, 11, 1)
    if d.month == 3:
        return d >= second_sunday_march
    if d.month == 11:
        return d < first_sunday_november
    return False


def _last_sunday_of_month(year: int, month: int) -> date:
    """Return the last Sunday of the given month."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    days_back = (last_day.weekday() - 6) % 7  # Sunday is weekday 6
    return last_day - timedelta(days=days_back)


def _nth_sunday_of_month(year: int, month: int, n: int) -> date:
    """Return the nth Sunday of the given month (1-indexed)."""
    first = date(year, month, 1)
    days_to_first_sunday = (6 - first.weekday()) % 7
    first_sunday = first + timedelta(days=days_to_first_sunday)
    return first_sunday + timedelta(weeks=n - 1)


def get_session_window(session: str, d: date | None = None) -> SessionWindow:
    """Return the UTC session window for the given session name on date d."""
    d = d or datetime.now(timezone.utc).date()
    if session == "ASIA":
        return SessionWindow(name="ASIA", start_utc_hour=0, end_utc_hour=9, is_dst=False)
    if session == "LONDON":
        is_dst = _is_uk_dst(d)
        return SessionWindow(
            name="LONDON",
            start_utc_hour=6 if is_dst else 7,  # BST = UTC+1 in summer
            end_utc_hour=15 if is_dst else 16,
            is_dst=is_dst,
        )
    if session == "NEW_YORK":
        is_dst = _is_us_dst(d)
        return SessionWindow(
            name="NEW_YORK",
            start_utc_hour=11 if is_dst else 12,  # EDT = UTC-4 in summer
            end_utc_hour=20 if is_dst else 21,
            is_dst=is_dst,
        )
    raise ValueError(f"unknown session: {session}")


def current_session(dt: datetime | None = None) -> list[str]:
    """Return the list of sessions active at the given datetime (UTC)."""
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    d = dt.date()
    hour = dt.hour
    active: list[str] = []
    for name in SESSION_NAMES:
        w = get_session_window(name, d)
        if w.start_utc_hour <= hour < w.end_utc_hour:
            active.append(name)
    return active


@dataclass
class SessionRange:
    """High/low of a session on a given date."""
    name: str
    date: date
    high: float
    low: float
    start_utc_hour: int
    end_utc_hour: int
    is_dst: bool


def compute_session_range(
    candles: list,
    session: str,
    target_date: date | None = None,
) -> SessionRange | None:
    """Compute the high/low of a session on target_date (defaults to latest date)."""
    if not candles:
        return None
    if target_date is None:
        target_date = candles[-1].timestamp.astimezone(timezone.utc).date()
    window = get_session_window(session, target_date)
    session_candles = [
        c for c in candles
        if c.timestamp.astimezone(timezone.utc).date() == target_date
        and window.start_utc_hour <= c.timestamp.astimezone(timezone.utc).hour < window.end_utc_hour
    ]
    if not session_candles:
        return None
    return SessionRange(
        name=session,
        date=target_date,
        high=float(max(c.high for c in session_candles)),
        low=float(min(c.low for c in session_candles)),
        start_utc_hour=window.start_utc_hour,
        end_utc_hour=window.end_utc_hour,
        is_dst=window.is_dst,
    )
