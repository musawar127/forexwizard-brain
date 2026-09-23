"""Phase 3: Deterministic higher-timeframe candle aggregation.

Given a list of lower-timeframe candles (e.g. M1), produce a list of
higher-timeframe candles (e.g. M5, M15, M30, H1, H4, D1).

Rules (deterministic, testable):
  1. Floor each input candle's timestamp to the target interval's bucket.
  2. Group candles by bucket.
  3. For each non-empty bucket, emit ONE aggregated candle:
       open  = open of the FIRST candle in the bucket (by ascending ts)
       high  = max(high) of all candles in the bucket
       low   = min(low) of all candles in the bucket
       close = close of the LAST candle in the bucket
       volume = sum(volume) where volume is not None
       sample_count = number of candles aggregated
       provider = "<source provider> (aggregated to <tf>)"
  4. The output is sorted ascending by timestamp.

This is the same OHLC-aggregation pattern already used in
`app.engine.candles.rebuild_recent_candles`, generalized so it works
for any source/target pair where the target is a multiple of the source.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.engine.candles import INTERVALS, ensure_utc, floor_time
from app.models.market import Candle


def _can_aggregate(source_interval: str, target_interval: str) -> bool:
    if source_interval not in INTERVALS or target_interval not in INTERVALS:
        return False
    if target_interval == source_interval:
        return False
    source_seconds = INTERVALS[source_interval]
    target_seconds = INTERVALS[target_interval]
    # Target must be a clean integer multiple of source.
    return target_seconds > source_seconds and target_seconds % source_seconds == 0


def aggregate_candles(
    source_candles: list[Candle],
    target_interval: str,
    *,
    symbol: str | None = None,
    source_interval: str | None = None,
    provider_label: str | None = None,
) -> list[Candle]:
    """Aggregate lower-timeframe candles up to `target_interval`.

    Args:
        source_candles: candles at a lower timeframe (e.g. M1) — already
            validated and sorted ascending by timestamp. The function does
            NOT validate; callers should call `validate_candles()` first.
        target_interval: one of M1/M5/M15/M30/H1/H4/D1 (must be a multiple
            of the source interval, otherwise returns []).
        symbol: optional symbol override (defaults to the source candle's symbol).
        source_interval: optional source interval hint. If None, inferred
            from the first source candle's `interval` field.
        provider_label: optional override for the `provider` field on the
            aggregated candle. Defaults to `"<src provider> (aggregated to <tf>)"`.

    Returns:
        Sorted list of aggregated candles (may be empty).

    Phase 3.1: each aggregated candle preserves full source lineage —
    derivation="AGGREGATED", provider_symbol/instrument inherited from
    the source candles, source_timeframe = the lower TF, target_timeframe
    = the higher TF.
    """
    if not source_candles:
        return []
    if source_interval is None:
        source_interval = source_candles[0].interval
    if not _can_aggregate(source_interval, target_interval):
        return []
    if symbol is None:
        symbol = source_candles[0].symbol
    if provider_label is None:
        src_provider = source_candles[0].provider
        provider_label = f"{src_provider} (aggregated to {target_interval})"

    # Inherit instrument + provider_symbol from source candles so the
    # aggregated output keeps full lineage. All source candles should
    # have the same instrument (homogeneous) — if not, we still use the
    # first one (the validator will catch mixed-instrument batches).
    instrument = source_candles[0].instrument
    provider_symbol = source_candles[0].provider_symbol

    target_seconds = INTERVALS[target_interval]
    buckets: dict[datetime, list[Candle]] = defaultdict(list)
    for c in source_candles:
        bucket = floor_time(ensure_utc(c.timestamp), target_seconds)
        buckets[bucket].append(c)

    out: list[Candle] = []
    for bucket_ts in sorted(buckets.keys()):
        members = sorted(buckets[bucket_ts], key=lambda c: c.timestamp)
        opens = [m.open for m in members]
        highs = [m.high for m in members]
        lows = [m.low for m in members]
        closes = [m.close for m in members]
        volumes = [m.volume for m in members if m.volume is not None]
        out.append(
            Candle(
                symbol=symbol,
                interval=target_interval,
                timestamp=bucket_ts,
                open=opens[0],
                high=max(highs),
                low=min(lows),
                close=closes[-1],
                volume=sum(volumes) if volumes else None,
                sample_count=len(members),
                provider=provider_label,
                is_historical=True,
                derivation="AGGREGATED",
                provider_symbol=provider_symbol,
                instrument=instrument,
                source_timeframe=source_interval,
                target_timeframe=target_interval,
            )
        )
    return out
