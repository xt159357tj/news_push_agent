from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import GenerationJob, JobStatus, Subscription
from app.services import digest_service
from app.services.time_utils import resolve_timezone

logger = logging.getLogger(__name__)


def is_due(sub: Subscription, tz_name: str | None, now: datetime) -> bool:
    """True when the subscription's delivery_time matches the current local minute.

    The scheduler runs on the top of every minute, so we match on HH:MM only.
    Repeat triggers within the same minute are guarded by the idempotency check.
    """
    try:
        local = now.astimezone(resolve_timezone(tz_name))
    except Exception:  # noqa: BLE001
        return False
    try:
        hour_s, minute_s = (sub.delivery_time or "08:00").split(":")
        return int(hour_s) == local.hour and int(minute_s) == local.minute
    except (ValueError, TypeError):
        return False


async def _already_queued_or_generated(
    session: AsyncSession,
    sub: Subscription,
    tz_name: str | None,
    now: datetime,
) -> bool:
    """Idempotency guard: one digest (or pending job) per subscription per local day."""
    if await digest_service.today_digest_exists(session, sub.id, now=now, tz_name=tz_name):
        return True
    queued = (
        await session.scalar(
            select(GenerationJob.id)
            .where(
                GenerationJob.subscription_id == sub.id,
                GenerationJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
            .limit(1)
        )
    )
    return queued is not None


async def scan_due_subscriptions(session: AsyncSession, now: datetime | None = None) -> list[int]:
    """Create one PENDING generation job per due subscription.

    Idempotent: a subscription that already has today's digest (local date) or a
    queued/running job is skipped, so repeated ticks never duplicate the day's run.
    Returns the list of launched subscription ids.
    """
    now = now or datetime.now(timezone.utc)
    subs = (
        await session.execute(
            select(Subscription)
            .where(Subscription.status == "active")
            .options(selectinload(Subscription.user))
        )
    ).scalars().all()
    launched: list[int] = []
    for sub in subs:
        tz_name = sub.user.timezone if sub.user is not None else None
        if not is_due(sub, tz_name, now):
            continue
        if await _already_queued_or_generated(session, sub, tz_name, now):
            logger.info("skip already-queued/generated subscription %s for today", sub.id)
            continue
        job = GenerationJob(
            subscription_id=sub.id,
            user_id=sub.user_id,
            status=JobStatus.PENDING,
            trigger="scheduled",
        )
        session.add(job)
        launched.append(sub.id)
    await session.commit()
    return launched


async def _minute_tick() -> None:
    """Every-minute cron entrypoint: scan due subs, then run pending jobs."""
    from app.db.session import AsyncSessionLocal
    from app.services.job_runner import run_job

    try:
        async with AsyncSessionLocal() as session:
            launched = await scan_due_subscriptions(session)
            if launched:
                logger.info("launched scheduled jobs for subscriptions: %s", launched)
            # Drain any PENDING jobs (just created or left over from a restart).
            pending = (
                await session.execute(
                    select(GenerationJob)
                    .where(GenerationJob.status == JobStatus.PENDING)
                    .order_by(GenerationJob.id)
                )
            ).scalars().all()
            for job in pending:
                await run_job(session, job)
                await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("scheduled generation tick failed")


def build_scheduler() -> AsyncIOScheduler:
    """Every-minute scheduler that scans due subscriptions and runs them."""
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        _minute_tick,
        "cron",
        hour="*",
        minute="*",
        id="minute_tick",
    )
    return scheduler
