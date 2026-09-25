"""Phase 5.7: ICT/SMC market structure engine.

Detects:
  - swing highs / swing lows (5-candle fractal by default)
  - HH / HL / LH / LL sequences
  - BOS (Break of Structure) — continuation
  - CHoCH (Change of Character) — reversal
  - MSS (Market Structure Shift) — reversal with displacement

All detection is deterministic and per-timeframe. Structures are
persisted as ict_structures rows.

A swing high requires candle[i].high > candle[i-N..i-1].high AND
candle[i].high > candle[i+1..i+N].high (fractal pattern).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app.models.market import Candle


# ---------- swing detection ----------

@dataclass
class Swing:
    """A detected swing high or low."""
    timestamp: datetime
    price: float
    kind: Literal["SWING_HIGH", "SWING_LOW"]
    candle_index: int
    # Quality: how many candles on each side confirm the swing (1-5).
    # Higher = stronger swing.
    strength: int = 2


def detect_swings(candles: list[Candle], fractal_size: int = 2) -> list[Swing]:
    """Detect swing highs and lows using a fractal pattern.

    A swing high at index i requires:
      candle[i].high > candle[j].high for all j in [i-fractal_size, i+fractal_size], j != i
    A swing low is the symmetric inverse.
    """
    swings: list[Swing] = []
    n = len(candles)
    if n < 2 * fractal_size + 1:
        return swings

    for i in range(fractal_size, n - fractal_size):
        c = candles[i]
        is_high = True
        is_low = True
        for k in range(1, fractal_size + 1):
            if c.high <= candles[i - k].high or c.high <= candles[i + k].high:
                is_high = False
            if c.low >= candles[i - k].low or c.low >= candles[i + k].low:
                is_low = False
            if not is_high and not is_low:
                break
        if is_high:
            swings.append(Swing(
                timestamp=c.timestamp,
                price=float(c.high),
                kind="SWING_HIGH",
                candle_index=i,
                strength=fractal_size,
            ))
        if is_low:
            swings.append(Swing(
                timestamp=c.timestamp,
                price=float(c.low),
                kind="SWING_LOW",
                candle_index=i,
                strength=fractal_size,
            ))
    return swings


# ---------- HH/HL/LH/LL classification ----------

@dataclass
class StructureSequence:
    """A sequence of swings classified into HH/HL/LH/LL."""
    timestamp: datetime
    price: float
    structure_type: Literal["HH", "HL", "LH", "LL"]
    swing_kind: Literal["SWING_HIGH", "SWING_LOW"]


def classify_structure(swings: list[Swing]) -> list[StructureSequence]:
    """Classify each swing as HH/HL/LH/LL relative to the previous swing of the same kind.

    HH = Higher High (current swing high > previous swing high)
    LH = Lower High  (current swing high < previous swing high)
    HL = Higher Low  (current swing low > previous swing low)
    LL = Lower Low   (current swing low < previous swing low)
    """
    out: list[StructureSequence] = []
    last_high: Swing | None = None
    last_low: Swing | None = None
    for s in swings:
        if s.kind == "SWING_HIGH":
            if last_high is None:
                # First swing high — no classification yet
                last_high = s
                continue
            if s.price > last_high.price:
                out.append(StructureSequence(
                    timestamp=s.timestamp, price=s.price,
                    structure_type="HH", swing_kind="SWING_HIGH",
                ))
            else:
                out.append(StructureSequence(
                    timestamp=s.timestamp, price=s.price,
                    structure_type="LH", swing_kind="SWING_HIGH",
                ))
            last_high = s
        else:  # SWING_LOW
            if last_low is None:
                last_low = s
                continue
            if s.price > last_low.price:
                out.append(StructureSequence(
                    timestamp=s.timestamp, price=s.price,
                    structure_type="HL", swing_kind="SWING_LOW",
                ))
            else:
                out.append(StructureSequence(
                    timestamp=s.timestamp, price=s.price,
                    structure_type="LL", swing_kind="SWING_LOW",
                ))
            last_low = s
    return out


# ---------- BOS / CHoCH / MSS ----------

@dataclass
class StructureEvent:
    """A BOS / CHoCH / MSS event."""
    timestamp: datetime
    price: float
    event_type: Literal["BOS", "CHoCH", "MSS"]
    direction: Literal["BULLISH", "BEARISH"]
    # The swing level that was broken
    broken_level: float
    broken_kind: Literal["SWING_HIGH", "SWING_LOW"]
    # Quality: 'WEAK' / 'MODERATE' / 'STRONG' based on displacement
    quality: Literal["WEAK", "MODERATE", "STRONG"] = "MODERATE"


def detect_structure_events(
    candles: list[Candle],
    swings: list[Swing],
    displacement_atr_mult: float = 1.0,
    atr_value: float | None = None,
) -> list[StructureEvent]:
    """Detect BOS / CHoCH / MSS events as price breaks previous swing levels.

    Logic:
      1. Walk forward through candles.
      2. Maintain "last swing high" and "last swing low" as they appear
         (using the swing index from `swings`).
      3. When a candle closes above the last swing high → BULLISH event.
         - If prevailing trend was bearish (LH/LL sequence), this is a CHoCH.
         - Otherwise it's a BOS (continuation).
      4. When a candle closes below the last swing low → BEARISH event (mirror).
      5. If the displacement (close - broken_level) exceeds displacement_atr_mult * ATR
         the event is upgraded to MSS (Market Structure Shift).

    The event_type is determined as:
      - BOS   = continuation in the prevailing direction
      - CHoCH = first counter-trend break (reversal signal)
      - MSS   = strong counter-trend break with displacement (>= displacement_atr_mult * ATR)
    """
    events: list[StructureEvent] = []
    if not swings:
        return events

    # Sort swings by candle_index (already in order from detect_swings)
    swing_by_idx = {s.candle_index: s for s in swings}

    last_high: Swing | None = None
    last_low: Swing | None = None
    prev_trend: Literal["BULLISH", "BEARISH"] | None = None  # inferred from HH/HL vs LH/LL

    for i, c in enumerate(candles):
        # Update last_high/last_low as new swings appear
        if i in swing_by_idx:
            s = swing_by_idx[i]
            if s.kind == "SWING_HIGH":
                if last_high is not None:
                    # Update trend based on HH vs LH
                    if s.price > last_high.price:
                        prev_trend = "BULLISH"
                    else:
                        prev_trend = "BEARISH"
                last_high = s
            else:
                if last_low is not None:
                    if s.price > last_low.price:
                        prev_trend = "BULLISH"
                    else:
                        prev_trend = "BEARISH"
                last_low = s

        # Check for break of last_high (bullish break)
        if last_high is not None and c.close > last_high.price and i > last_high.candle_index:
            # Has this level already been broken? (avoid duplicate events)
            already_broken = any(
                e.broken_kind == "SWING_HIGH" and e.broken_level == last_high.price
                for e in events
            )
            if not already_broken:
                # Determine event type
                event_type, quality = _classify_break(
                    direction="BULLISH",
                    prev_trend=prev_trend,
                    displacement=abs(c.close - last_high.price),
                    atr_value=atr_value,
                    displacement_atr_mult=displacement_atr_mult,
                )
                events.append(StructureEvent(
                    timestamp=c.timestamp,
                    price=float(c.close),
                    event_type=event_type,
                    direction="BULLISH",
                    broken_level=last_high.price,
                    broken_kind="SWING_HIGH",
                    quality=quality,
                ))

        # Check for break of last_low (bearish break)
        if last_low is not None and c.close < last_low.price and i > last_low.candle_index:
            already_broken = any(
                e.broken_kind == "SWING_LOW" and e.broken_level == last_low.price
                for e in events
            )
            if not already_broken:
                event_type, quality = _classify_break(
                    direction="BEARISH",
                    prev_trend=prev_trend,
                    displacement=abs(last_low.price - c.close),
                    atr_value=atr_value,
                    displacement_atr_mult=displacement_atr_mult,
                )
                events.append(StructureEvent(
                    timestamp=c.timestamp,
                    price=float(c.close),
                    event_type=event_type,
                    direction="BEARISH",
                    broken_level=last_low.price,
                    broken_kind="SWING_LOW",
                    quality=quality,
                ))

    return events


def _classify_break(
    direction: Literal["BULLISH", "BEARISH"],
    prev_trend: Literal["BULLISH", "BEARISH"] | None,
    displacement: float,
    atr_value: float | None,
    displacement_atr_mult: float,
) -> tuple[Literal["BOS", "CHoCH", "MSS"], Literal["WEAK", "MODERATE", "STRONG"]]:
    """Classify a structure break as BOS / CHoCH / MSS based on direction vs trend."""
    is_continuation = (direction == prev_trend) if prev_trend else False
    strong_displacement = (
        atr_value is not None
        and atr_value > 0
        and displacement >= displacement_atr_mult * atr_value
    )

    if not is_continuation and strong_displacement:
        return "MSS", "STRONG"
    if not is_continuation:
        return "CHoCH", "MODERATE" if strong_displacement else "WEAK"
    # Continuation
    if strong_displacement:
        return "BOS", "STRONG"
    return "BOS", "MODERATE"


# ---------- full per-timeframe analysis ----------

@dataclass
class TimeframeStructure:
    """Complete structure analysis for one timeframe."""
    timeframe: str
    swings: list[Swing] = field(default_factory=list)
    sequence: list[StructureSequence] = field(default_factory=list)
    events: list[StructureEvent] = field(default_factory=list)
    # Latest inferred trend (BULLISH / BEARISH / RANGE)
    trend: Literal["BULLISH", "BEARISH", "RANGE"] = "RANGE"


def analyze_timeframe(candles: list[Candle], timeframe: str, atr_value: float | None = None) -> TimeframeStructure:
    """Run the full structure analysis pipeline on one timeframe's candles."""
    swings = detect_swings(candles, fractal_size=2)
    sequence = classify_structure(swings)
    events = detect_structure_events(candles, swings, atr_value=atr_value)

    # Infer trend from the latest events
    trend: Literal["BULLISH", "BEARISH", "RANGE"] = "RANGE"
    if events:
        last_event = events[-1]
        # Look at last 3 events for trend direction
        recent = events[-3:]
        bullish_count = sum(1 for e in recent if e.direction == "BULLISH")
        bearish_count = sum(1 for e in recent if e.direction == "BEARISH")
        if bullish_count > bearish_count:
            trend = "BULLISH"
        elif bearish_count > bullish_count:
            trend = "BEARISH"

    return TimeframeStructure(
        timeframe=timeframe,
        swings=swings,
        sequence=sequence,
        events=events,
        trend=trend,
    )
