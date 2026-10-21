from __future__ import annotations

"""Job runner: executes a GenerationJob via the Agent runtime and persists results.

Shared by both the manual API endpoint and the scheduled worker so the two
entry points always run identical logic. A digest is always saved to history;
email delivery is a best-effort follow-up that is skipped when SMTP is not
configured (this never blocks generation).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runtime import AgentRequest, AgentRuntime
from app.agent.schemas import DigestOut
from app.db.models import (
    Digest,
    GenerationJob,
    JobStatus,
    NewsItem,
    Subscription,
    User,
)
from app.services import delivery_service, digest_service
from app.services.time_utils import local_date_in_tz

logger = logging.getLogger(__name__)


def build_request(sub: Subscription, user: User) -> AgentRequest:
    return AgentRequest(
        user_name=user.name,
        timezone=user.timezone,
        topics=[t for t in sub.topics.split(",") if t],
        keywords=[k for k in sub.keywords.split(",") if k],
        language=sub.language,
        max_items=sub.max_items,
    )


async def _persist_digest(
    session: AsyncSession,
    sub: Subscription,
    out: DigestOut,
    *,
    trigger: str,
    user_tz: str | None,
) -> Digest:
    """Persist the digest + items. Look up matching NewsItem ids by url when available."""
    items_out: list[dict] = []
    for item in out.items:
        news = await session.scalar(select(NewsItem).where(NewsItem.url == item.url))
        items_out.append(
            {
                "news_id": news.id if news else None,
                "title": item.title,
                "summary": item.summary,
                "reason": item.reason,
                "url": item.url,
            }
        )
    today = local_date_in_tz(datetime.now(timezone.utc), user_tz)
    return await digest_service.save_digest(
        session,
        user_id=sub.user_id,
        subscription=sub,
        title=out.title,
        overview=out.overview,
        language=out.language,
        trigger=trigger,
        items=items_out,
        day_key=f"sub:{sub.id}:{today}",
    )


async def run_job(
    session: AsyncSession,
    job: GenerationJob,
    *,
    runtime: AgentRuntime | None = None,
) -> GenerationJob:
    """Run one generation job to completion and persist the result in place.

    The caller is responsible for committing; this updates ``job`` fields so the
    outer transaction decides when to flush/commit.
    """
    sub = await session.get(Subscription, job.subscription_id)
    if sub is None:
        job.status = JobStatus.FAILED
        job.error = f"订阅不存在 (id={job.subscription_id})"
        return job
    user = await session.get(User, sub.user_id)
    if user is None:
        job.status = JobStatus.FAILED
        job.error = f"用户不存在 (id={sub.user_id})"
        return job

    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(timezone.utc)
    await session.flush()

    request = build_request(sub, user)
    audit = {"job_id": job.id}
    rt = runtime or AgentRuntime()
    try:
        result = await rt.run(request, audit=audit)
        job.steps = result.steps
        if result.digest is None:
            raise RuntimeError(f"Agent 未产出有效简报: {result.output_text[:500]}")
        digest = await _persist_digest(
            session, sub, result.digest, trigger=job.trigger, user_tz=user.timezone
        )
        job.digest_id = digest.id
        job.status = JobStatus.SUCCEEDED
        # Best-effort delivery; never raises. Records SKIPPED when no SMTP.
        await delivery_service.deliver_digest(session, digest, sub, user)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Digest generation failed (job=%s)", job.id)
        job.status = JobStatus.FAILED
        job.error = str(exc)[:2000]
    finally:
        job.finished_at = datetime.now(timezone.utc)
    return job
