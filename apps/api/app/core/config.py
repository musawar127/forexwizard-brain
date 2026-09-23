from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ForexWizard AI Market Brain API"
    environment: str = "development"
    frontend_origin: str = "http://localhost:3000"

    # No-auth market source. Gold API asks clients to cache spot prices rather
    # than hammering the endpoint multiple times per second.
    gold_api_base_url: str = "https://api.gold-api.com"
    gold_api_symbol: str = "XAU"
    quote_refresh_seconds: int = 30
    stale_after_seconds: int = 90

    # The app works without these. Twelve Data can optionally bootstrap
    # historical candles if a key is supplied later.
    twelve_data_api_key: str = ""
    twelve_data_symbol: str = "XAU/USD"

    # Zero-setup default. PostgreSQL can replace this URL in production.
    database_url: str = "sqlite:///./forexwizard.db"
    redis_url: str = "redis://localhost:6379/0"

    # News/research metadata source. GDELT DOC API does not require an API key.
    gdelt_base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    research_query: str = '(gold OR XAUUSD OR "Federal Reserve" OR "real yields")'
    research_refresh_seconds: int = 900
    research_timespan: str = "24h"
    research_max_records: int = 30

    # The free spot feed is sampled locally and converted into candles.
    # A newly installed system therefore needs time before longer timeframes
    # become statistically useful.
    analysis_min_candles: int = 14
    prediction_interval_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
