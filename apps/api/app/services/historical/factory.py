"""Phase 3: Provider factory.

Selection order:
  1. If `TWELVE_DATA_API_KEY` is set in backend env, use Twelve Data (XAU/USD spot OHLC).
  2. Otherwise, fall back to Yahoo Finance (no key, GC=F gold futures).

This makes the system modular: adding a new provider does not require
changing the sync orchestrator or any consumer — only registering it here
and having it implement HistoricalMarketDataProvider.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.historical.base import (
    HistoricalMarketDataProvider,
    ProviderHealth,
)
from app.services.historical.twelve_data_historical import TwelveDataHistoricalProvider
from app.services.historical.yahoo_finance import YahooFinanceHistoricalProvider


def get_historical_provider() -> HistoricalMarketDataProvider:
    """Return the active historical provider based on backend env config.

    Twelve Data takes priority because it serves true XAU/USD spot rather
    than gold futures. Yahoo is the always-available fallback.
    """
    if settings.twelve_data_api_key:
        return TwelveDataHistoricalProvider()
    return YahooFinanceHistoricalProvider()


async def list_available_providers() -> list[ProviderHealth]:
    """Return health for every provider the installation knows about.

    The frontend /data page calls this via /api/data/status to show which
    providers are available vs which is currently active.
    """
    providers: list[HistoricalMarketDataProvider] = [
        YahooFinanceHistoricalProvider(),
        TwelveDataHistoricalProvider(),
    ]
    results: list[ProviderHealth] = []
    for p in providers:
        try:
            results.append(await p.health_check())
        except Exception as exc:  # never let one bad provider break the listing
            results.append(
                ProviderHealth(
                    provider_name=p.provider_name,
                    reachable=False,
                    requires_api_key=p.requires_api_key,
                    has_api_key=bool(getattr(p, "_api_key", None)),
                    last_error=str(exc),
                )
            )
    return results
