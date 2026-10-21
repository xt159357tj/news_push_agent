from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription, User
from app.db.session import get_session

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


class SubscriptionIn(BaseModel):
    topics: list[str] = []
    keywords: list[str] = []
    language: str = "zh"
    max_items: int = 10
    delivery_time: str = "08:00"
    notify_email: bool = True


class SubscriptionOut(SubscriptionIn):
    id: int
    status: str


async def _first_user(session: AsyncSession) -> User | None:
    return (await session.scalars(select(User).limit(1))).first()


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(session: AsyncSession = Depends(get_session)):
    subs = (await session.scalars(select(Subscription))).all()
    return [
        SubscriptionOut(
            id=s.id,
            topics=s.topics.split(",") if s.topics else [],
            keywords=s.keywords.split(",") if s.keywords else [],
            language=s.language,
            max_items=s.max_items,
            delivery_time=s.delivery_time,
            notify_email=s.notify_email,
            status=s.status.value,
        )
        for s in subs
    ]


@router.post("", response_model=SubscriptionOut)
async def create_subscription(payload: SubscriptionIn, session: AsyncSession = Depends(get_session)):
    user = await _first_user(session)
    if user is None:
        raise HTTPException(400, "请先创建用户资料")
    sub = Subscription(
        user_id=user.id,
        topics=",".join(payload.topics),
        keywords=",".join(payload.keywords),
        language=payload.language,
        max_items=payload.max_items,
        delivery_time=payload.delivery_time,
        notify_email=payload.notify_email,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return SubscriptionOut(
        id=sub.id, topics=payload.topics, keywords=payload.keywords,
        language=sub.language, max_items=sub.max_items, delivery_time=sub.delivery_time,
        notify_email=sub.notify_email, status=sub.status.value,
    )


@router.put("/{sub_id}", response_model=SubscriptionOut)
async def update_subscription(sub_id: int, payload: SubscriptionIn, session: AsyncSession = Depends(get_session)):
    sub = await session.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(404, "订阅不存在")
    sub.topics = ",".join(payload.topics)
    sub.keywords = ",".join(payload.keywords)
    sub.language = payload.language
    sub.max_items = payload.max_items
    sub.delivery_time = payload.delivery_time
    sub.notify_email = payload.notify_email
    await session.commit()
    await session.refresh(sub)
    return SubscriptionOut(
        id=sub.id, topics=payload.topics, keywords=payload.keywords,
        language=sub.language, max_items=sub.max_items, delivery_time=sub.delivery_time,
        notify_email=sub.notify_email, status=sub.status.value,
    )
