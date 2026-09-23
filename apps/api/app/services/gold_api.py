from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.models.market import Quote


class GoldApiProvider:
    name = "Gold API"

    async def get_quote(self) -> Quote:
        url = f"{settings.gold_api_base_url}/price/{settings.gold_api_symbol}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "ForexWizardBrain/0.2"})
            response.raise_for_status()
            data = response.json()

        price = float(data["price"])
        received = datetime.now(timezone.utc)
        market_ts = _parse_timestamp(data)
        return Quote(
            symbol="XAU/USD",
            price=price,
            provider=self.name,
            market_timestamp=market_ts,
            received_timestamp=received,
            age_seconds=(received - market_ts).total_seconds() if market_ts else 0.0,
            status="RECENT",
        )


def _parse_timestamp(data: dict) -> datetime | None:
    for key in ("updatedAt", "updated_at", "timestamp"):
        value = data.get(key)
        if value in (None, ""):
            continue
        try:
            if isinstance(value, (int, float)):
                # Be tolerant of seconds vs milliseconds.
                ts = float(value)
                if ts > 10_000_000_000:
                    ts /= 1000
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            text = str(value).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OSError):
            continue
    return None
