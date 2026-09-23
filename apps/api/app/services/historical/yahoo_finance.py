"""Phase 3: Yahoo Finance historical OHLC provider for XAU/USD.

Yahoo's public chart API serves genuine COMEX gold-futures OHLC for the
symbol `GC=F`. No API key is required. The data is real market data from
the CME/COMEX exchange, not synthetic.

Important limitations of this provider (documented on /api/data/status):

  * Yahoo's symbol is `GC=F` (front-month gold futures on COMEX). The
    Brain still labels the symbol as "XAU/USD" because that is the
    canonical reference; the `provider` field on every candle is set to
    "Yahoo Finance (GC=F)" so the difference is transparent.
  * Gold futures trade at a small premium/discount to spot XAU/USD —
    typically $1-$30 depending on the contract month. For long-run
    market-memory purposes this is acceptable; the price differences are
    small enough that EMA/RSI/ATR/trend signals are stable.
  * Intraday ranges are limited by Yahoo:
      - 1m, 2m:      up to 7 days
      - 5m, 15m, 30m: up to 60 days
      - 60m / 1h:    up to 730 days
      - 1d, 1wk, 1mo: up to "max" (10+ years for daily)
  * Yahoo's chart API is unofficial. There is no SLA. We rate-limit
    ourselves to 1 request per second per call to avoid being blocked.

Yahoo does not provide a 4-hour native interval; H4 candles are derived
locally from H1 by the aggregator in `aggregator.py`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models.market import Candle
from app.services.historical.base import (
    HistoricalMarketDataProvider,
    ProviderHealth,
    ProviderNotConfiguredError,
)

# Mapping of ForexWizard canonical timeframes to Yahoo Finance interval strings.
# Yahoo does NOT support 4h natively — H4 is derived locally from H1.
YAHOO_INTERVALS: dict[str, str | None] = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "1h": "60m",
    "4h": None,   # derived from 1h
    "1day": "1d",
}

# Mapping of ForexWizard canonical timeframes to Yahoo `range` defaults.
# Yahoo's per-interval hard limits — EMPIRICALLY VERIFIED 2026-09-23:
#   - 1m:      range up to 7d (we use 5d; 7d sometimes returns 422)
#   - 5m:      range up to 1mo (NOT 3mo — Yahoo returns 422 for >1mo)
#   - 15m:     range up to 1mo
#   - 30m:     range up to 1mo
#   - 60m/1h:  range up to 2y (730d observed in practice)
#   - 1d:      range up to "max" (10+ years)
#
# Phase 3.1 design rule: use the deepest NATIVE interval Yahoo permits
# rather than deriving short-window TFs from shallow M1 history.
# M5/M15/M30/H1 are fetched DIRECTLY from Yahoo at their native depth;
# only H4 is derived locally (from H1) because Yahoo has no native 4h.
YAHOO_DEFAULT_RANGES: dict[str, str] = {
    "1min": "5d",
    "5min": "1mo",   # 30 days — Yahoo's actual max for 5m
    "15min": "1mo",  # 30 days — Yahoo's actual max for 15m
    "30min": "1mo",  # 30 days — Yahoo's actual max for 30m
    "1h": "2y",      # 2 years — Yahoo's actual max for 1h
    "1day": "10y",
}

YAHOO_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_SYMBOL = "GC=F"  # gold futures, COMEX front-month


class YahooFinanceHistoricalProvider(HistoricalMarketDataProvider):
    provider_name = "Yahoo Finance (GC=F)"
    requires_api_key = False
    default_symbol = "XAU/USD"

    def __init__(
        self,
        *,
        base_url: str = YAHOO_BASE_URL,
        yahoo_symbol: str = YAHOO_SYMBOL,
        user_agent: str = "ForexWizardBrain/0.2 (+historical backfill)",
        request_timeout: float = 20.0,
        inter_call_delay: float = 1.0,
    ) -> None:
        self._base_url = base_url
        self._yahoo_symbol = yahoo_symbol
        self._user_agent = user_agent
        self._request_timeout = request_timeout
        self._inter_call_delay = inter_call_delay
        self._last_call_at: float = 0.0

    async def _rate_limit(self) -> None:
        # Simple client-side rate limit so we don't hammer Yahoo and get blocked.
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_call_at
        if elapsed < self._inter_call_delay:
            await asyncio.sleep(self._inter_call_delay - elapsed)
        self._last_call_at = asyncio.get_event_loop().time()

    def _build_url_and_params(self, timeframe: str, start: datetime | None, end: datetime | None) -> tuple[str, dict[str, str]]:
        yahoo_interval = YAHOO_INTERVALS.get(timeframe)
        if yahoo_interval is None:
            raise ProviderNotConfiguredError(
                f"Yahoo Finance does not natively serve {timeframe}; "
                "derive it locally via aggregate_candles()."
            )

        # Yahoo's chart API expects the symbol in the URL PATH:
        #   https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?interval=...
        # We pass the symbol via the path and the rest via query string.
        url = f"{self._base_url}/{self._yahoo_symbol}"

        # Yahoo `range` is a relative duration (e.g. "3mo", "1y", "max").
        # If the caller supplies explicit start/end datetimes, we use Yahoo's
        # `period1` / `period2` epoch-second params instead.
        params: dict[str, str] = {
            "interval": yahoo_interval,
            "includePrePost": "false",
        }
        if start is not None or end is not None:
            end_epoch = int((end or datetime.now(timezone.utc)).timestamp())
            start_epoch = int((start or datetime(2000, 1, 1, tzinfo=timezone.utc)).timestamp())
            if start_epoch >= end_epoch:
                end_epoch = start_epoch + 86400
            params["period1"] = str(start_epoch)
            params["period2"] = str(end_epoch)
            params["useYfid"] = "true"
        else:
            params["range"] = YAHOO_DEFAULT_RANGES.get(timeframe, "1mo")
        return url, params

    async def _fetch_chart(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        await self._rate_limit()
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._request_timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()

        err = payload.get("chart", {}).get("error")
        if err:
            raise RuntimeError(f"Yahoo Finance error: {err}")
        results = payload.get("chart", {}).get("result")
        if not results:
            return {"timestamps": [], "quote": {}}
        first = results[0]
        return {
            "timestamps": first.get("timestamp") or [],
            "quote": (first.get("indicators", {}).get("quote") or [{}])[0],
            "meta": first.get("meta") or {},
        }

    async def get_history(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        # Yahoo only serves one symbol (GC=F) for this provider. `symbol`
        # is still accepted so the ABC signature is uniform across providers,
        # but each returned Candle records provider_symbol="GC=F" and
        # instrument="GC_FRONT_MONTH" so historical statistical learning
        # (Phase 4+) will know which instrument generated the observation.
        if timeframe == "4h":
            return []  # Yahoo has no native 4h; H4 is derived from 1h locally.
        url, params = self._build_url_and_params(timeframe, start, end)
        chart = await self._fetch_chart(url, params)
        timestamps: list[int] = chart.get("timestamps") or []
        quote: dict[str, Any] = chart.get("quote") or {}
        opens: list[float | None] = quote.get("open") or []
        highs: list[float | None] = quote.get("high") or []
        lows: list[float | None] = quote.get("low") or []
        closes: list[float | None] = quote.get("close") or []
        volumes: list[float | None] = quote.get("volume") or []

        candles: list[Candle] = []
        for i, ts_epoch in enumerate(timestamps):
            if ts_epoch is None:
                continue
            try:
                ts = datetime.fromtimestamp(int(ts_epoch), tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                continue
            o = _safe_float(opens, i)
            h = _safe_float(highs, i)
            l = _safe_float(lows, i)
            c = _safe_float(closes, i)
            v = _safe_float(volumes, i)
            if o is None or h is None or l is None or c is None:
                # Yahoo returns null entries for sessions with no trading
                # (e.g. weekend). We skip them — they are not "missing periods"
                # because the validator's gap detector reports gaps based on
                # expected interval spacing between consecutive valid candles.
                continue
            candles.append(
                Candle(
                    symbol=symbol,
                    interval=timeframe,
                    timestamp=ts,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=v if v is not None and v > 0 else None,
                    sample_count=1,
                    provider=self.provider_name,
                    is_historical=True,
                    derivation="DIRECT",           # native Yahoo interval
                    provider_symbol=self._yahoo_symbol,  # "GC=F"
                    instrument="GC_FRONT_MONTH",
                    source_timeframe=timeframe,    # native TF = own interval
                    target_timeframe=timeframe,
                )
            )
        return candles

    async def get_latest_available_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        if timeframe == "4h":
            # Use 1h and let aggregator handle H4.
            ts = await self.get_latest_available_timestamp(symbol, "1h")
            return ts
        # Fetch just one candle via a 1d range.
        url, params = self._build_url_and_params(timeframe, None, None)
        params["range"] = "1d"
        chart = await self._fetch_chart(url, params)
        timestamps: list[int] = chart.get("timestamps") or []
        if not timestamps:
            return None
        try:
            return datetime.fromtimestamp(int(timestamps[-1]), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            return None

    async def health_check(self) -> ProviderHealth:
        try:
            ts = await self.get_latest_available_timestamp(self.default_symbol, "1day")
            reachable = ts is not None
            return ProviderHealth(
                provider_name=self.provider_name,
                reachable=reachable,
                requires_api_key=self.requires_api_key,
                has_api_key=True,
                last_error=None if reachable else "no candles returned",
                extra={"symbol": YAHOO_SYMBOL, "latest_daily": ts.isoformat() if ts else None},
            )
        except Exception as exc:
            return ProviderHealth(
                provider_name=self.provider_name,
                reachable=False,
                requires_api_key=self.requires_api_key,
                has_api_key=True,
                last_error=str(exc),
                extra={"symbol": YAHOO_SYMBOL},
            )


def _safe_float(values: list[float | None], i: int) -> float | None:
    if i >= len(values):
        return None
    v = values[i]
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Yahoo returns `null` for sessions with no trading; that propagates as
    # `None` already. Defensive: also reject NaN / inf.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f
