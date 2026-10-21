from __future__ import annotations

"""Chat-facing tools: generate a digest, browse history, read current settings.

They open their own DB session so the LLM tool loop can call them like any
other tool. The app is single-user (first user), matching the REST API.
"""

from typing import Any

from sqlalchemy import select

from app.db.models import (
    Digest,
    DigestItem,
    GenerationJob,
    JobStatus,
    Subscription,
    User,
)
from app.db.session import AsyncSessionLocal
from app.services.time_utils import utc_isoformat


def _digest_dict(d: Digest, items: list[DigestItem] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": d.id,
        "title": d.title,
        "overview": d.overview,
        "generated_at": utc_isoformat(d.generated_at),
        "language": d.language,
        "trigger": d.trigger,
    }
    if items is not None:
        out["items"] = [
            {"rank": i.rank, "title": i.title, "summary": i.summary, "reason": i.reason, "url": i.url}
            for i in items
        ]
    return out


async def _items(session, digest_id: int) -> list[DigestItem]:
    return list(
        (
            await session.scalars(
                select(DigestItem).where(DigestItem.digest_id == digest_id).order_by(DigestItem.rank)
            )
        ).all()
    )


def _sub_dict(s: Subscription) -> dict[str, Any]:
    return {
        "id": s.id,
        "topics": [t for t in s.topics.split(",") if t],
        "keywords": [k for k in s.keywords.split(",") if k],
        "language": s.language,
        "max_items": s.max_items,
        "delivery_time": s.delivery_time,
        "notify_email": s.notify_email,
    }


async def get_settings() -> dict[str, Any]:
    """Return the current profile and latest subscription."""
    async with AsyncSessionLocal() as session:
        user = (await session.scalars(select(User).limit(1))).first()
        if user is None:
            return {"ok": True, "profile": None, "subscription": None}
        sub = (
            await session.scalars(
                select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.id.desc())
            )
        ).first()
        return {
            "ok": True,
            "profile": {"name": user.name, "email": user.email, "timezone": user.timezone},
            "subscription": _sub_dict(sub) if sub else None,
        }


async def generate_digest() -> dict[str, Any]:
    """Generate a digest now from the saved subscription preferences."""
    # Imported lazily: job_runner imports the agent runtime, which imports the
    # tool registry that imports this module.
    from app.services import job_runner

    async with AsyncSessionLocal() as session:
        user = (await session.scalars(select(User).limit(1))).first()
        sub = None
        if user is not None:
            sub = (
                await session.scalars(
                    select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.id.desc())
                )
            ).first()
        if user is None or sub is None:
            return {"ok": False, "error": "还没有订阅偏好，请先通过顶部「订阅偏好」按钮保存。"}

        job = GenerationJob(
            subscription_id=sub.id, user_id=user.id, status=JobStatus.PENDING, trigger="manual"
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        await job_runner.run_job(session, job)
        await session.commit()

        if job.status != JobStatus.SUCCEEDED or job.digest_id is None:
            return {"ok": False, "error": job.error or "生成失败", "job_id": job.id}
        digest = await session.get(Digest, job.digest_id)
        return {
            "ok": True,
            "job_id": job.id,
            "digest": _digest_dict(digest, await _items(session, digest.id)),
        }


async def list_digests(limit: int = 10) -> dict[str, Any]:
    """List recent digests (newest first)."""
    limit = max(1, min(int(limit), 50))
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(select(Digest).order_by(Digest.generated_at.desc()).limit(limit))
        ).all()
        digests = [_digest_dict(d) for d in rows]
        return {"ok": True, "count": len(digests), "digests": digests}


async def get_digest(digest_id: int) -> dict[str, Any]:
    """Return one digest with all of its items."""
    async with AsyncSessionLocal() as session:
        d = await session.get(Digest, int(digest_id))
        if d is None:
            return {"ok": False, "error": f"简报不存在 (id={digest_id})"}
        return {"ok": True, "digest": _digest_dict(d, await _items(session, d.id))}
