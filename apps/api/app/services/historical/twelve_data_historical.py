"""Phase 3: Twelve Data historical OHLC provider for XAU/USD.

Twelve Data is a paid/commercial provider with a free tier (800 API
credits/day, 8 credits/minute). It supports genuine XAU/USD spot OHLC
(not futures) for timeframes 1min, 5min, 15min, 30min, 45min, 1h, 2h, 4h,
1day, 1week, 1month. API key is required and supplied via the
`TWELVE_DATA_API_KEY` env var.

When no key is configured, this provider raises `ProviderNotConfiguredError`
on any data call and `health_check` reports `has_api_key=False`.

Yahoo Finance (no key needed) is the default provider. Twelve Data is the
optional upgrade path for users who want spot XAU/USD (not futures) OHLC
and longer intraday history (Twelve Data allows more pagination).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.models.market import Candle
from app.services.historical.base import (
    HistoricalMarketDataProvider,
    ProviderHealth,
    ProviderNotConfiguredError,
)

# Mapping of ForexWizard canonical timeframes to Twelve Data interval strings.
TWELVE_INTERVALS: dict[str, str | None] = {
    "1min": "1min",
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
    "1h": "1h",
    "4h": "4h",      # Twelve Data supports 4h natively
    "1day": "1day",
}

TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"


class TwelveDataHistoricalProvider(HistoricalMarketDataProvider):
    provider_name = "Twelve Data (XAU/USD spot)"
    requires_api_key = True
    default_symbol = "XAU/USD"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = TWELVE_DATA_BASE_URL,
        symbol: str = "XAU/USD",
        request_timeout: float = 25.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.twelve_data_api_key
        self._base_url = base_url
        self._symbol = symbol
        self._request_timeout = request_timeout

    def _require_key(self) -> str:
        if not self._api_key:
            raise ProviderNotConfiguredError(
                "Twelve Data provider requires TWELVE_DATA_API_KEY in backend env."
            )
        return self._api_key

    async def _time_series(
        self,
        interval: str,
        *,
        outputsize: int = 5000,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        api_key = self._require_key()
        params: dict[str, str] = {
            "symbol": self._symbol,
            "interval": interval,
            "outputsize": str(min(max(outputsize, 1), 5000)),
            "timezone": "UTC",
            "order": "ASC",
            "format": "JSON",
            "apikey": api_key,
        }
        if start is not None:
            params["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end is not None:
            params["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")
        async with httpx.AsyncClient(timeout=self._request_timeout) as client:
            response = await client.get(f"{self._base_url}/time_series", params=params)
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(data.get("message") or "Twelve Data error")
        return data.get("values") or []

    async def get_history(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        td_interval = TWELVE_INTERVALS.get(timeframe)
        if td_interval is None:
            raise ProviderNotConfiguredError(
                f"Twelve Data does not serve timeframe {timeframe}."
            )
        values = await self._time_series(td_interval, start=start, end=end)
        candles: list[Candle] = []
        for item in values:
            try:
                ts = datetime.fromisoformat(item["datetime"]).replace(tzinfo=timezone.utc)
            except (KeyError, ValueError, TypeError):
                continue
            try:
                o = float(item["open"])
                h = float(item["high"])
                l = float(item["low"])
                c = float(item["close"])
            except (KeyError, ValueError, TypeError):
                continue
            v_raw = item.get("volume")
            v = float(v_raw) if v_raw not in (None, "") else None
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
                )
            )
        return candles

    async def get_latest_available_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        td_interval = TWELVE_INTERVALS.get(timeframe)
        if td_interval is None:
            return None
        try:
            values = await self._time_series(td_interval, outputsize=1)
        except ProviderNotConfiguredError:
            return None
        except Exception:
            return None
        if not values:
            return None
        try:
            return datetime.fromisoformat(values[-1]["datetime"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError, TypeError):
            return None

    async def health_check(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(
                provider_name=self.provider_name,
                reachable=False,
                requires_api_key=self.requires_api_key,
                has_api_key=False,
                last_error="TWELVE_DATA_API_KEY not set in backend env",
                extra={"base_url": self._base_url, "symbol": self._symbol},
            )
        try:
            ts = await self.get_latest_available_timestamp(self.default_symbol, "1day")
            return ProviderHealth(
                provider_name=self.provider_name,
                reachable=ts is not None,
                requires_api_key=self.requires_api_key,
                has_api_key=True,
                last_error=None if ts is not None else "no candles returned",
                extra={"symbol": self._symbol, "latest_daily": ts.isoformat() if ts else None},
            )
        except Exception as exc:
            return ProviderHealth(
                provider_name=self.provider_name,
                reachable=False,
                requires_api_key=self.requires_api_key,
                has_api_key=True,
                last_error=str(exc),
                extra={"symbol": self._symbol},
            )
