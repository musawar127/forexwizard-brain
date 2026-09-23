"""Phase 4: historical state builder with NO look-ahead bias.

For a state at time T, ALL features are computed from candles with
timestamp <= T. There is no leakage — the state describes the world
AS IT WAS at time T, before any future candle existed.

CRITICAL INVARIANT (tested in test_phase4_no_lookahead.py):
  For state at T, the feature vector is identical whether or not any
  candle with ts > T exists in the database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from app.engine.candles import get_candles
from app.engine.indicators import atr, ema, rsi, slope
from app.models.market import Candle


# ---------------------------------------------------------------------------
# Normalized feature vector
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureVector:
    """Normalized, scale-free feature representation of a market state.

    Continuous features are all in roughly [0, 1] or [-1, +1] so the
    weighted-distance similarity treats them comparably.

    Categorical features use string codes; the SimilarityEngine maps
    them to 0 (exact match) or 1 (mismatch) before weighting.
    """

    # Continuous, normalized
    rsi_normalized: float | None                  # rsi / 100, 0..1
    ema_distance_atr: float | None                # (fast - slow) / atr
    atr_pct: float | None                          # atr / price * 100
    distance_to_support_atr: float | None          # (price - support_high) / atr
    distance_to_resistance_atr: float | None       # (resistance_low - price) / atr
    volatility_percentile: float | None            # 0..100, rank within recent history
    timeframe_alignment_score: float | None        # 0..1

    # Categorical (string codes — engine maps to 0/1 distance)
    trend: str | None                              # BULLISH / BEARISH / RANGE / INSUFFICIENT_DATA
    market_regime: str | None                      # TREND_UP / TREND_DOWN / MIXED / RANGE / LEARNING
    swing_structure: str | None                    # HH_HL / LH_LL / HH_LL / LH_HL / UNKNOWN
    h1_direction: str | None                       # BULLISH / BEARISH / RANGE / INSUFFICIENT_DATA
    h4_direction: str | None
    d1_direction: str | None
    session: str | None                            # ASIA / EU / US / OFF


# ---------------------------------------------------------------------------
# State builder
# ---------------------------------------------------------------------------

class HistoricalStateBuilder:
    """Builds a FeatureVector for a given (instrument, base_tf, timestamp).

    Strictly uses candles with timestamp <= T (no look-ahead).
    """

    def __init__(self, base_timeframe: str = "1h", min_history: int = 30) -> None:
        self.base_timeframe = base_timeframe
        self.min_history = min_history

    async def build_at(
        self,
        instrument: str,
        timestamp: datetime,
        *,
        candles: list[Candle] | None = None,
        h1_candles: list[Candle] | None = None,
        h4_candles: list[Candle] | None = None,
        d1_candles: list[Candle] | None = None,
    ) -> FeatureVector | None:
        """Build a feature vector at time T.

        Args:
            instrument: filter candles by instrument
            timestamp: state timestamp T (UTC)
            candles: optional pre-fetched base-TF candle list (must be sorted
                ascending and filtered to instrument). If None, fetches from DB.
            h1_candles / h4_candles / d1_candles: optional pre-fetched multi-TF
                candle lists. If None, falls back to per-TF DB fetch (SLOW —
                only for ad-hoc single-state builds, NOT for batch builds).

        Returns:
            FeatureVector, or None if insufficient history (need >= min_history
            candles with ts <= T to compute reliable indicators).
        """
        ts_utc = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)

        if candles is None:
            # Fetch all candles for this TF and filter to <= T.
            all_candles = await get_candles(self.base_timeframe, 5000, "XAU/USD")
            candles = [c for c in all_candles if c.timestamp <= ts_utc]
        else:
            # Caller-supplied list — filter defensively.
            candles = [c for c in candles if c.timestamp <= ts_utc]

        if len(candles) < self.min_history:
            return None

        closes = [c.close for c in candles]
        last = candles[-1]
        price = last.close

        # EMA fast / slow (deterministic, no future leakage — uses only closes <= T)
        fast_period = 8
        slow_period = 20
        ema_fast = ema(closes, fast_period) if len(closes) >= fast_period else None
        ema_slow = ema(closes, slow_period) if len(closes) >= slow_period else None

        # RSI (no look-ahead)
        rsi_val = rsi(closes, 14) if len(closes) >= 15 else None

        # ATR (no look-ahead)
        atr_val = atr(candles, 14) if len(candles) >= 15 else None

        # Trend (deterministic — same logic as analysis._trend)
        trend = self._trend(candles)

        # Regime (same logic as features.py from Phase 3)
        regime = self._regime(candles)

        # Support/resistance zones (no look-ahead — uses only recent candles <= T)
        support_low, support_high = self._support_zone(candles, price)
        resistance_low, resistance_high = self._resistance_zone(candles, price)
        distance_to_support_atr = None
        if support_high is not None and atr_val and atr_val > 0:
            distance_to_support_atr = round((price - support_high) / atr_val, 3)
        distance_to_resistance_atr = None
        if resistance_low is not None and atr_val and atr_val > 0:
            distance_to_resistance_atr = round((resistance_low - price) / atr_val, 3)

        # Swing structure (last 20 candles — looks at highs/lows only)
        swing_structure, hh, hl, lh, ll = self._swing_structure(candles[-20:])

        # Volatility percentile (rank of current ATR vs last 200 ATRs)
        volatility_percentile = self._volatility_percentile(candles)

        # Session (UTC hour bucket)
        session = self._session(last.timestamp)

        # Multi-timeframe direction at T — use pre-fetched lists if available
        # (avoid the catastrophic N+1 DB query pattern in batch builds).
        h1_dir = self._trend([c for c in (h1_candles or []) if c.timestamp <= ts_utc]) if h1_candles else await self._tf_direction_at("1h", ts_utc)
        h4_dir = self._trend([c for c in (h4_candles or []) if c.timestamp <= ts_utc]) if h4_candles else await self._tf_direction_at("4h", ts_utc)
        d1_dir = self._trend([c for c in (d1_candles or []) if c.timestamp <= ts_utc]) if d1_candles else await self._tf_direction_at("1day", ts_utc)
        if h1_dir is not None and len([c for c in (h1_candles or candles) if c.timestamp <= ts_utc]) < 6:
            h1_dir = None
        if h4_dir is not None and h4_candles is not None and len([c for c in h4_candles if c.timestamp <= ts_utc]) < 6:
            h4_dir = None
        if d1_dir is not None and d1_candles is not None and len([c for c in d1_candles if c.timestamp <= ts_utc]) < 6:
            d1_dir = None

        # Timeframe alignment (fraction of available TFs agreeing with the base trend)
        directions = [d for d in [h1_dir, h4_dir, d1_dir] if d and d != "INSUFFICIENT_DATA"]
        if directions and trend and trend != "INSUFFICIENT_DATA":
            matching = sum(1 for d in directions if d == trend)
            alignment = round(matching / len(directions), 3)
        else:
            alignment = None

        # Normalized continuous features
        rsi_normalized = rsi_val / 100.0 if rsi_val is not None else None
        ema_distance_atr = None
        if ema_fast is not None and ema_slow is not None and atr_val and atr_val > 0:
            ema_distance_atr = round((ema_fast - ema_slow) / atr_val, 3)
        atr_pct = round((atr_val / price) * 100.0, 4) if atr_val and price > 0 else None

        return FeatureVector(
            rsi_normalized=rsi_normalized,
            ema_distance_atr=ema_distance_atr,
            atr_pct=atr_pct,
            distance_to_support_atr=distance_to_support_atr,
            distance_to_resistance_atr=distance_to_resistance_atr,
            volatility_percentile=volatility_percentile,
            timeframe_alignment_score=alignment,
            trend=trend,
            market_regime=regime,
            swing_structure=swing_structure,
            h1_direction=h1_dir,
            h4_direction=h4_dir,
            d1_direction=d1_dir,
            session=session,
        )

    # ----- Helpers (all use ONLY candles with ts <= T) -----

    def _trend(self, candles: list[Candle]) -> str:
        closes = [c.close for c in candles]
        if len(closes) < 6:
            return "INSUFFICIENT_DATA"
        fast = ema(closes, 8)
        slow = ema(closes, min(20, max(5, len(closes) // 2)))
        sl = slope(closes, min(6, len(closes)))
        av = atr(candles, min(14, max(5, len(candles) - 1))) if len(candles) >= 7 else None
        if fast is None or slow is None or sl is None:
            return "INSUFFICIENT_DATA"
        threshold = max((av or 0) * 0.03, 0.01)
        if fast > slow and sl > threshold:
            return "BULLISH"
        if fast < slow and sl < -threshold:
            return "BEARISH"
        return "RANGE"

    def _regime(self, candles: list[Candle]) -> str:
        if len(candles) < 20:
            return "LEARNING"
        window = candles[-20:]
        bullish = sum(1 for c in window if c.close > c.open)
        bearish = sum(1 for c in window if c.close < c.open)
        if bullish >= 15 and bearish == 0:
            return "TREND_UP"
        if bearish >= 15 and bullish == 0:
            return "TREND_DOWN"
        if bullish and bearish:
            return "MIXED"
        return "RANGE"

    def _support_zone(self, candles: list[Candle], price: float) -> tuple[float | None, float | None]:
        recent = candles[-min(50, len(candles)):]
        if len(recent) < 5:
            return None, None
        lows = sorted(c.low for c in recent if c.low <= price)
        if not lows:
            return None, None
        avg_range = sum(max(c.high - c.low, 0.01) for c in recent) / len(recent)
        pad = max(avg_range * 0.25, price * 0.00015)
        anchor = max(lows)
        return anchor - pad, anchor + pad

    def _resistance_zone(self, candles: list[Candle], price: float) -> tuple[float | None, float | None]:
        recent = candles[-min(50, len(candles)):]
        if len(recent) < 5:
            return None, None
        highs = sorted((c.high for c in recent if c.high >= price), reverse=True)
        if not highs:
            return None, None
        avg_range = sum(max(c.high - c.low, 0.01) for c in recent) / len(recent)
        pad = max(avg_range * 0.25, price * 0.00015)
        anchor = min(highs)
        return anchor - pad, anchor + pad

    def _swing_structure(self, window: list[Candle]) -> tuple[str, bool | None, bool | None, bool | None, bool | None]:
        """Classify swing structure over the last N candles.

        HH_HL = higher highs + higher lows (uptrend)
        LH_LL = lower highs + lower lows (downtrend)
        HH_LL / LH_HL = divergence / range
        UNKNOWN = insufficient data
        """
        if len(window) < 10:
            return "UNKNOWN", None, None, None, None
        mid = len(window) // 2
        first_half = window[:mid]
        second_half = window[mid:]
        first_high = max(c.high for c in first_half)
        first_low = min(c.low for c in first_half)
        second_high = max(c.high for c in second_half)
        second_low = min(c.low for c in second_half)
        higher_high = second_high > first_high
        lower_low = second_low < first_low
        higher_low = second_low > first_low
        lower_high = second_high < first_high
        if higher_high and higher_low:
            structure = "HH_HL"
        elif lower_high and lower_low:
            structure = "LH_LL"
        elif higher_high and lower_low:
            structure = "HH_LL"
        elif lower_high and higher_low:
            structure = "LH_HL"
        else:
            structure = "UNKNOWN"
        return structure, higher_high, higher_low, lower_high, lower_low

    def _volatility_percentile(self, candles: list[Candle]) -> float | None:
        if len(candles) < 20:
            return None
        atrs: list[float] = []
        # Compute rolling ATR over the last 200 candles (or fewer if not enough).
        max_window = min(200, len(candles) - 14)
        if max_window < 5:
            return None
        for i in range(max_window):
            window = candles[:len(candles) - max_window + i + 1]
            if len(window) <= 14:
                continue
            a = atr(window, 14)
            if a is not None:
                atrs.append(a)
        if not atrs:
            return None
        current_atr = atrs[-1]
        rank = sum(1 for a in atrs if a <= current_atr) / len(atrs)
        return round(rank * 100.0, 2)

    def _session(self, ts: datetime) -> str:
        h = ts.hour
        if 0 <= h < 7:
            return "ASIA"
        if 7 <= h < 13:
            return "EU"
        if 13 <= h < 21:
            return "US"
        return "OFF"

    async def _tf_direction_at(self, tf: str, ts_utc: datetime) -> str | None:
        """Compute trend for tf at time T — uses ONLY candles <= T."""
        all_candles = await get_candles(tf, 5000, "XAU/USD")
        candles_at_t = [c for c in all_candles if c.timestamp <= ts_utc]
        if len(candles_at_t) < 6:
            return None
        return self._trend(candles_at_t)
