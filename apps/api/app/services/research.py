from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.models import NewsRecord
from app.db.session import SessionLocal


def _parse_seen(value: str | None) -> datetime | None:
    if not value:
        return None
    formats = ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


async def fetch_gdelt_news() -> list[dict]:
    params = {
        "query": settings.research_query,
        "mode": "artlist",
        "maxrecords": settings.research_max_records,
        "format": "json",
        "timespan": settings.research_timespan,
        "sort": "datedesc",
    }
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        response = await client.get(settings.gdelt_base_url, params=params)
        response.raise_for_status()
        payload = response.json()
    return payload.get("articles", []) if isinstance(payload, dict) else []


async def refresh_research() -> tuple[int, str | None]:
    try:
        articles = await fetch_gdelt_news()
        inserted = 0
        with SessionLocal() as session:
            for item in articles:
                url = str(item.get("url") or "").strip()
                title = str(item.get("title") or "").strip()
                if not url or not title:
                    continue
                exists = session.scalar(select(NewsRecord.id).where(NewsRecord.url == url))
                if exists is not None:
                    continue
                domain = str(item.get("domain") or urlparse(url).netloc or "unknown")[:255]
                seen = _parse_seen(item.get("seendate"))
                session.add(
                    NewsRecord(
                        title=title,
                        url=url,
                        domain=domain,
                        seen_at=seen.replace(tzinfo=None) if seen else None,
                        language=(item.get("language") or None),
                        source_country=(item.get("sourcecountry") or None),
                        topic="gold-macro",
                        discovered_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    )
                )
                inserted += 1
            session.commit()
        return inserted, None
    except Exception as exc:
        return 0, str(exc)


async def recent_research(limit: int = 30) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(NewsRecord).order_by(NewsRecord.discovered_at.desc()).limit(limit)
        ).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "url": r.url,
            "domain": r.domain,
            "seen_at": r.seen_at.isoformat() if r.seen_at else None,
            "language": r.language,
            "source_country": r.source_country,
            "topic": r.topic,
            "discovered_at": r.discovered_at.isoformat(),
        }
        for r in rows
    ]
