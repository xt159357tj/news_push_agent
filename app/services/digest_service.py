from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Digest, DigestItem, NewsItem, Subscription
from app.services.time_utils import local_date_in_tz


def normalize_url(url: str) -> str:
    """Normalize a URL for de-duplication (strip fragment + tracking params)."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
    ]
    normalized = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.params, urlencode(query), "")
    )
    return normalized.lower()


def make_fingerprint(title: str, url: str) -> str:
    """Stable-ish fingerprint based on title + URL."""
    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s or "").lower()
        s = re.sub(r"[\W_]+", "", s)
        return s.strip()

    raw = f"{_norm(title)}|{_norm(url)}"
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def dedupe_candidates(
    session: AsyncSession,
    candidates: Iterable[dict],
) -> list[dict]:
    """Filter candidates whose fingerprint already exists in news_items."""
    seen: list[dict] = []
    known = set()
    for cand in candidates:
        fp = make_fingerprint(cand.get("title", ""), cand.get("url", ""))
        res = await session.scalar(
            select(NewsItem.fingerprint).where(NewsItem.fingerprint == fp)
        )
        if res:
            continue
        if fp in known:
            continue
        known.add(fp)
        cand["fingerprint"] = fp
        seen.append(cand)
    return seen


async def save_candidates(
    session: AsyncSession,
    candidates: Iterable[dict],
) -> list[NewsItem]:
    items: list[NewsItem] = []
    for cand in candidates:
        item = NewsItem(
            url=normalize_url(cand.get("url", "")),
            source_name=cand.get("source_name", ""),
            source_category=cand.get("source_category", ""),
            title=cand.get("title", ""),
            summary=cand.get("summary", ""),
            content_snippet=cand.get("content_snippet", ""),
            published_at=cand.get("published_at"),
            fingerprint=cand.get("fingerprint", ""),
        )
        session.add(item)
        items.append(item)
    await session.flush()
    return items


async def save_digest(
    session: AsyncSession,
    *,
    user_id: int,
    subscription: Subscription | None,
    title: str,
    overview: str,
    language: str,
    trigger: str,
    items: list[dict],
    day_key: str = "",
) -> Digest:
    digest = Digest(
        user_id=user_id,
        subscription_id=subscription.id if subscription else None,
        title=title,
        overview=overview,
        language=language,
        trigger=trigger,
        day_key=day_key,
    )
    session.add(digest)
    await session.flush()

    for rank, item in enumerate(items, start=1):
        news_id = item.get("news_id")
        session.add(
            DigestItem(
                digest_id=digest.id,
                news_id=news_id,
                rank=rank,
                title=item.get("title", ""),
                summary=item.get("summary", ""),
                reason=item.get("reason", ""),
                url=item.get("url", ""),
            )
        )
    await session.commit()
    await session.refresh(digest)
    return digest


async def today_digest_exists(
    session: AsyncSession,
    subscription_id: int,
    *,
    now: datetime | None = None,
    tz_name: str | None = None,
) -> bool:
    """Whether a digest already exists today (user-local date) for a subscription.

    Used as the idempotency guard so a subscription only produces one digest per
    local day, even if the scheduler ticks more than once.
    """
    day = local_date_in_tz(now or datetime.now(timezone.utc), tz_name)
    day_key = f"sub:{subscription_id}:{day}"
    return (
        await session.scalar(select(Digest.id).where(Digest.day_key == day_key)) is not None
    )
