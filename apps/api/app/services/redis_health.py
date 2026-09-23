from __future__ import annotations

from app.core.config import settings


async def redis_status() -> dict:
    try:
        import redis.asyncio as redis  # optional dependency at runtime
    except Exception:
        return {"status": "OPTIONAL_NOT_INSTALLED"}
    try:
        client = redis.from_url(settings.redis_url, socket_connect_timeout=0.5, decode_responses=True)
        await client.ping()
        await client.aclose()
        return {"status": "CONNECTED"}
    except Exception:
        return {"status": "OPTIONAL_OFFLINE"}
