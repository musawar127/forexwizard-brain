"""Phase 4: outcome window calculator.

For a historical state at time T, computes the future price behavior
at each horizon (15m / 30m / 1h / 2h / 4h / 8h / 24h).

CRITICAL: outcomes are computed from candles with timestamp >= T.
NEVER used to influence the feature vector at T (would be look-ahead).

Direction classification (UP / DOWN / NEUTRAL):
  Uses volatility-aware threshold:
    if percentage_change >= +X * ATR_pct  => UP
    if percentage_change <= -X * ATR_pct  => DOWN
    else                                   => NEUTRAL
  X = NEUTRAL_X_DEFAULT (0.5 — conservative, half an ATR in either
  direction is treated as noise).

MFE = Maximum Favorable Excursion (max high - entry price over the horizon)
MAE = Maximum Adverse Excursion  (entry price - min low over the horizon)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.engine.candles import INTERVALS, get_candles
from app.models.market import Candle


@dataclass(frozen=True)
class OutcomeWindow:
    """Outcome measurement for a state at one horizon."""

    horizon_minutes: int
    future_price: float | None
    absolute_change: float | None
    percentage_change: float | None
    mfe: float | None           # max favorable excursion
    mae: float | None            # max adverse excursion
    maximum_up_move: float | None
    maximum_down_move: float | None
    direction: str | None        # UP / DOWN / NEUTRAL / NULL


class OutcomeCalculator:
    """Computes OutcomeWindow at each horizon for a historical state."""

    def __init__(self, *, neutral_x: float = 0.5) -> None:
        self.neutral_x = neutral_x

    async def compute_all(
        self,
        state_timestamp: datetime,
        state_price: float,
        state_atr: float | None,
        instrument: str = "GC_FRONT_MONTH",
        horizons: tuple[int, ...] = (15, 30, 60, 120, 240, 480, 1440),
    ) -> dict[int, OutcomeWindow]:
        """Compute outcome windows for all horizons.

        Returns a dict {horizon_minutes: OutcomeWindow}. Missing data =>
        outcome fields are None and direction = "NULL".
        """
        ts_utc = state_timestamp if state_timestamp.tzinfo else state_timestamp.replace(tzinfo=timezone.utc)
        results: dict[int, OutcomeWindow] = {}

        # Fetch H1 candles (covers all horizons >= 60m). For sub-hour
        # horizons we'd ideally use M1/M5/M15/M30 — but we only have ~5d
        # of those, so sub-hour outcomes are limited to recent states.
        h1_candles = await get_candles("1h", 5000, "XAU/USD")
        h1_forward = [c for c in h1_candles if c.timestamp > ts_utc]

        for horizon in horizons:
            window = self._compute_window(
                ts_utc=ts_utc,
                state_price=state_price,
                state_atr=state_atr,
                h1_forward=h1_forward,
                horizon_minutes=horizon,
            )
            results[horizon] = window

        return results

    def _compute_window(
        self,
        *,
        ts_utc: datetime,
        state_price: float,
        state_atr: float | None,
        h1_forward: list[Candle],
        horizon_minutes: int,
    ) -> OutcomeWindow:
        # Compute how many H1 candles cover the horizon.
        h1_seconds = INTERVALS["1h"]
        candles_needed = max(1, horizon_minutes * 60 // h1_seconds)
        if len(h1_forward) < candles_needed:
            # Not enough forward data for this horizon.
            return OutcomeWindow(
                horizon_minutes=horizon_minutes,
                future_price=None, absolute_change=None, percentage_change=None,
                mfe=None, mae=None, maximum_up_move=None, maximum_down_move=None,
                direction="NULL",
            )

        # Take the next N candles covering the horizon.
        window = h1_forward[:candles_needed]
        future_price = window[-1].close
        absolute_change = future_price - state_price
        percentage_change = (absolute_change / state_price) * 100.0 if state_price > 0 else None

        # MFE = max favorable excursion (max high over window) - entry price
        max_high = max(c.high for c in window)
        min_low = min(c.low for c in window)
        mfe = round(max_high - state_price, 4)
        mae = round(state_price - min_low, 4)
        maximum_up_move = mfe
        maximum_down_move = mae

        # Direction classification — volatility-aware
        direction = self._classify_direction(absolute_change, state_price, state_atr)

        return OutcomeWindow(
            horizon_minutes=horizon_minutes,
            future_price=round(future_price, 4),
            absolute_change=round(absolute_change, 4),
            percentage_change=round(percentage_change, 4) if percentage_change is not None else None,
            mfe=mfe,
            mae=mae,
            maximum_up_move=maximum_up_move,
            maximum_down_move=maximum_down_move,
            direction=direction,
        )

    def _classify_direction(self, absolute_change: float, state_price: float, state_atr: float | None) -> str:
        """Volatility-aware direction classification.

        Threshold: |absolute_change| / state_atr >= neutral_x => UP or DOWN, else NEUTRAL.
        Falls back to a fixed 0.05% threshold if ATR is unavailable.
        """
        if state_atr is not None and state_atr > 0:
            normalized = abs(absolute_change) / state_atr
            threshold = self.neutral_x
            if normalized < threshold:
                return "NEUTRAL"
            return "UP" if absolute_change > 0 else "DOWN"
        # Fallback: 0.05% of price
        pct_threshold = 0.0005 * state_price
        if abs(absolute_change) < pct_threshold:
            return "NEUTRAL"
        return "UP" if absolute_change > 0 else "DOWN"
