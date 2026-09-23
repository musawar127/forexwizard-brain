"""Phase 3: Historical market-data provider abstraction.

This package contains the HistoricalMarketDataProvider ABC and its
concrete implementations. The Brain BUY/SELL/WAIT engine is NOT modified
to consume historical statistics in Phase 3 — this layer exists purely
to give the installation genuine historical market memory.

Public surface:
    HistoricalMarketDataProvider  — ABC every provider implements
    get_historical_provider()      — factory selecting the active provider
    CandleValidationReport        — result of OHLC validation
    GapReport                      — result of gap/missing-period detection
    aggregate_candles              — deterministic higher-TF aggregation
    sync_historical_candles         — backfill orchestrator (single shot)
    data_quality_summary           — /api/data/status payload builder
    compute_feature_snapshot       — historical Brain-feature snapshot
"""

from __future__ import annotations

from app.services.historical.base import (
    HistoricalMarketDataProvider,
    ProviderHealth,
    ProviderNotConfiguredError,
)
from app.services.historical.factory import get_historical_provider, list_available_providers
from app.services.historical.yahoo_finance import YahooFinanceHistoricalProvider
from app.services.historical.twelve_data_historical import TwelveDataHistoricalProvider
from app.services.historical.validator import (
    CandleValidationReport,
    validate_candles,
    find_duplicates,
    find_gaps,
)
from app.services.historical.aggregator import aggregate_candles
from app.services.historical.sync import sync_historical_candles
from app.services.historical.data_quality import data_quality_summary, timeframe_breakdown
from app.services.historical.features import compute_feature_snapshot

__all__ = [
    "HistoricalMarketDataProvider",
    "ProviderHealth",
    "ProviderNotConfiguredError",
    "YahooFinanceHistoricalProvider",
    "TwelveDataHistoricalProvider",
    "get_historical_provider",
    "list_available_providers",
    "CandleValidationReport",
    "validate_candles",
    "find_duplicates",
    "find_gaps",
    "aggregate_candles",
    "sync_historical_candles",
    "data_quality_summary",
    "timeframe_breakdown",
    "compute_feature_snapshot",
]
