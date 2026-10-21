from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Digest,
    DigestItem,
    GenerationJob,
    JobStatus,
    Subscription,
    User,
)
from app.db.session import get_session
from app.services import job_runner
from app.services.time_utils import utc_isoformat

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["digests"])


class GenerateIn(BaseModel):
    subscription_id: int


class JobOut(BaseModel):
    id: int
    status: str
    digest_id: int | None = None
    error: str = ""


@router.post("/generate", response_model=JobOut)
async def generate_digest(payload: GenerateIn, session: AsyncSession = Depends(get_session)):
    sub = await session.get(Subscription, payload.subscription_id)
    if sub is None:
        raise HTTPException(404, "订阅不存在")
    user = await session.get(User, sub.user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")

    job = GenerationJob(
        subscription_id=sub.id,
        user_id=sub.user_id,
        status=JobStatus.PENDING,
        trigger="manual",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    await job_runner.run_job(session, job)
    await session.commit()

    return JobOut(id=job.id, status=job.status.value, digest_id=job.digest_id, error=job.error)


@router.get("/digests")
async def list_digests(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Digest).order_by(Digest.generated_at.desc()).limit(50))).scalars().all()
    result = []
    for d in rows:
        result.append(
            {
                "id": d.id,
                "title": d.title,
                "overview": d.overview,
                "generated_at": utc_isoformat(d.generated_at),
                "trigger": d.trigger,
                "language": d.language,
            }
        )
    return result


@router.get("/digests/{digest_id}")
async def get_digest(digest_id: int, session: AsyncSession = Depends(get_session)):
    d = await session.get(Digest, digest_id)
    if d is None:
        raise HTTPException(404, "简报不存在")
    items = (
        await session.execute(
            select(DigestItem).where(DigestItem.digest_id == digest_id).order_by(DigestItem.rank)
        )
    ).scalars().all()
    return {
        "id": d.id,
        "title": d.title,
        "overview": d.overview,
        "generated_at": utc_isoformat(d.generated_at),
        "language": d.language,
        "items": [
            {
                "rank": i.rank,
                "title": i.title,
                "summary": i.summary,
                "reason": i.reason,
                "url": i.url,
            }
            for i in items
        ],
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return JobOut(id=job.id, status=job.status.value, digest_id=job.digest_id, error=job.error)
