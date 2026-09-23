"""Phase 3: ABC for historical XAU/USD market-data providers.

Every concrete provider implements this ABC. The factory in `factory.py`
selects one based on backend env configuration. API keys are NEVER
exposed to the frontend — they live only in `app.core.config.settings`
and the provider instance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.models.market import Candle


@dataclass(frozen=True)
class ProviderHealth:
    """Health-check result returned by `HistoricalMarketDataProvider.health_check`."""

    provider_name: str
    reachable: bool
    requires_api_key: bool
    has_api_key: bool
    last_error: str | None = None
    extra: dict | None = None


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a provider cannot serve requests because its key/URL is missing."""


class HistoricalMarketDataProvider(ABC):
    """Abstract base class for every historical XAU/USD market-data provider.

    Implementations MUST be idempotent: calling `get_history` with the same
    parameters must return the same candle set (deterministic). Implementations
    MUST NOT silently repair source data — if the upstream returns out-of-order
    candles, duplicates, or invalid OHLC, the provider passes them through and
    the validator (`validator.py`) records the defects.
    """

    #: Public name shown on /data and in CandleRecord.provider
    provider_name: str = "abstract"

    #: True if this provider needs an API key (Twelve Data) / False if free (Yahoo)
    requires_api_key: bool = False

    #: Symbol this provider serves. Most providers serve one symbol per instance
    #: (XAU/USD). Multi-symbol providers can be added later by parameterizing
    #: `get_history` with an explicit symbol arg — out of scope for Phase 3.
    default_symbol: str = "XAU/USD"

    @abstractmethod
    async def get_history(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Fetch historical OHLC candles for the given timeframe.

        Args:
            symbol: Market symbol (e.g. "XAU/USD").
            timeframe: One of M1, M5, M15, M30, H1, H4, D1.
            start: Optional inclusive start datetime (UTC). If None, provider
                decides (typically as far back as the source allows).
            end: Optional exclusive end datetime (UTC). If None, "now".

        Returns:
            List of `Candle` objects ordered ascending by timestamp. The
            provider MUST set `provider` on every returned Candle to
            `self.provider_name` so downstream code can attribute the data.

            The list MAY be empty if the provider has no data for the range.
            It MUST NOT raise on empty results.
        """

    @abstractmethod
    async def get_latest_available_timestamp(
        self,
        symbol: str,
        timeframe: str,
    ) -> datetime | None:
        """Return the most recent timestamp the provider can serve for the
        given symbol/timeframe, or None if the provider cannot determine it.

        Used by the sync orchestrator to decide whether a refresh is needed.
        """

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Probe provider reachability without performing a large fetch.

        Implementations should make a small, cheap request (e.g. one candle
        for the D1 timeframe) and report reachability + any error message.
        """
