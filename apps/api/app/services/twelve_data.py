from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.models.market import Candle


class TwelveDataProvider:
    """Optional historical bootstrap provider.

    The core app does not depend on this service. If a user later adds a free
    or paid Twelve Data key, these candles can seed the database immediately.
    """

    base_url = "https://api.twelvedata.com"
    name = "Twelve Data"

    async def get_candles(self, interval: str = "5min", outputsize: int = 120) -> list[Candle]:
        if not settings.twelve_data_api_key:
            return []
        params = {
            "symbol": settings.twelve_data_symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC",
            "order": "ASC",
            "apikey": settings.twelve_data_api_key,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/time_series", params=params)
            response.raise_for_status()
            data = response.json()
        if data.get("status") == "error":
            raise RuntimeError(data.get("message", "Twelve Data returned an error"))

        result: list[Candle] = []
        for item in data.get("values", []):
            ts = datetime.fromisoformat(item["datetime"]).replace(tzinfo=timezone.utc)
            volume = item.get("volume")
            result.append(
                Candle(
                    symbol="XAU/USD",
                    interval=interval,
                    timestamp=ts,
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=float(volume) if volume not in (None, "") else None,
                    sample_count=1,
                    provider=self.name,
                )
            )
        return result
