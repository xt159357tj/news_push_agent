from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.retrieval.hybrid import get_retriever, reset_retriever

# Simple in-memory cache for article fetches within one process.
_article_cache: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(title: str, url: str) -> str:
    return hashlib.sha256(f"{title}|{url}".encode("utf-8")).hexdigest()


async def collect_rss(
    feeds: list[dict] | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Collect recent items from RSS feeds (default: configured feeds).

    Collected items are written into the hybrid retrieval corpus so a following
    ``search_news`` can do FAISS + BM25 search over them.
    """
    selected = feeds or [f.model_dump() for f in settings.rss_feeds]
    results: list[dict] = []
    errors: list[str] = []

    async def _one(feed: dict) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": "DailyAINewsAgent/0.1 (+https://example.com)"},
            ) as client:
                resp = await client.get(feed["url"])
                resp.raise_for_status()
            parsed = _parse_rss_xml(resp.text, feed)
            results.extend(parsed)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{feed.get('name')}: {exc}")

    await asyncio.gather(*(_one(f) for f in selected))

    results.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    items = results[:limit]
    # Seed the hybrid retrieval corpus so a following search_news is semantic.
    get_retriever().upsert_many(items)
    return {
        "ok": True,
        "count": len(items),
        "errors": errors,
        "items": items,
    }


def _parse_rss_xml(xml_text: str, feed: dict) -> list[dict]:
    """Minimal RSS/Atom parser without external deps (keeps imports light)."""
    import re
    import xml.etree.ElementTree as ET

    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    def _local(tag: str) -> str:
        return tag.split("}", 1)[-1]

    entry_nodes = []
    for child in root.iter():
        if _local(child.tag) in {"item", "entry"}:
            entry_nodes.append(child)

    for node in entry_nodes[:60]:
        title = ""
        link = ""
        summary = ""
        published = None
        for child in node.iter():
            name = _local(child.tag)
            text = (child.text or "").strip()
            if name == "title" and not title:
                title = text
            elif name == "link":
                link = text or child.get("href") or ""
            elif name in {"description", "summary", "content"} and not summary:
                summary = text
            elif name == "pubDate" and not published:
                published = text
            elif name in {"published", "updated"} and not published:
                published = text
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "url": link,
                "summary": summary[:600],
                "source_name": feed.get("name", ""),
                "source_category": feed.get("category", ""),
                "published_at": _parse_published(published),
                "fingerprint": _fingerprint(title, link),
            }
        )
    return items


def _parse_published(value: str | None) -> str:
    """Normalize RSS/Atom dates to an ISO-8601 UTC string."""
    if not value:
        return ""
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


async def search_news(
    query: str,
    from_time: str | None = None,
    to_time: str | None = None,
    language: str = "zh",
    limit: int = 10,
) -> dict[str, Any]:
    """Search news using hybrid retrieval (FAISS vector + BM25).

    Corpus is the latest collected RSS batch; call ``collect_rss`` first so the
    retriever has items to search over (the agent loop does this naturally).
    """
    retriever = get_retriever()

    # Build the corpus if nothing has been collected yet in this process.
    if retriever.corpus_size == 0:
        collected = await collect_rss(limit=200)
        if collected["count"] == 0 and collected["errors"]:
            return {
                "ok": False,
                "query": query,
                "error": "RSS 采集失败，无法检索: " + "; ".join(collected["errors"][:3]),
            }

    result = await retriever.search(query, top_k=limit)
    items = result["items"]

    # Optional time-window filtering is applied post-retrieval (metadata based).
    filtered = _filter_by_window(items, from_time, to_time) if (from_time or to_time) else items
    result["items"] = filtered[:limit]
    result["count"] = len(filtered[:limit])
    if result.get("degraded"):
        result["note"] = "检索降级为单一索引（向量或 BM25 之一不可用）"
    return result


def _filter_by_window(
    items: list[dict],
    from_time: str | None,
    to_time: str | None,
) -> list[dict]:
    if not (from_time or to_time):
        return items

    def _parse(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    start = _parse(from_time) if from_time else None
    end = _parse(to_time) if to_time else None
    out = []
    for it in items:
        pub = _parse(it.get("published_at") or "")
        if pub is None:
            continue  # unknown timestamp cannot be windowed reliably
        if start and pub < start:
            continue
        if end and pub > end:
            continue
        out.append(it)
    return out


async def fetch_article(url: str, max_chars: int | None = None) -> dict[str, Any]:
    """Fetch and extract the main text of an article (with cache + limits)."""
    max_chars = max_chars or settings.max_article_chars
    cache_key = url.strip()
    if cache_key in _article_cache:
        return {**_article_cache[cache_key], "cached": True}

    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            max_redirects=settings.max_redirects,
            headers={"User-Agent": "DailyAINewsAgent/0.1 (+https://example.com)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        text = resp.text[:200_000]
        content = _extract_main_text(text)
        result = {
            "ok": True,
            "url": url,
            "title": _extract_title(text) or "",
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
            "cached": False,
        }
        _article_cache[cache_key] = result
        return result
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": str(exc)}


def _extract_title(html: str) -> str:
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def _extract_main_text(html: str) -> str:
    """Very small readability-ish extractor. Production can use trafilatura."""
    import re

    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    # Keep paragraphs/list items as lines, strip tags.
    html = re.sub(r"</p>|</li>|</h[1-6]>|</div>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:20_000]
