from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileIn(BaseModel):
    name: str
    email: EmailStr
    timezone: str = "Asia/Shanghai"


class ProfileOut(ProfileIn):
    id: int


@router.get("", response_model=ProfileOut)
async def get_or_create_profile(session: AsyncSession = Depends(get_session)) -> ProfileOut:
    # MVP: single-user mode uses the first user.
    user = (await session.scalars(select(User).limit(1))).first()
    if user is None:
        user = User(name="访客", email="guest@example.com", timezone="Asia/Shanghai")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return ProfileOut(id=user.id, name=user.name, email=user.email, timezone=user.timezone)


@router.put("", response_model=ProfileOut)
async def update_profile(payload: ProfileIn, session: AsyncSession = Depends(get_session)) -> ProfileOut:
    user = (await session.scalars(select(User).limit(1))).first()
    if user is None:
        user = User(name=payload.name, email=payload.email, timezone=payload.timezone)
        session.add(user)
    else:
        user.name = payload.name
        user.email = payload.email
        user.timezone = payload.timezone
    await session.commit()
    await session.refresh(user)
    return ProfileOut(id=user.id, name=user.name, email=user.email, timezone=user.timezone)