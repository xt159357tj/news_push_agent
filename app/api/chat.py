from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runtime import AgentRuntime
from app.db.models import ChatMessage, User
from app.db.session import get_session
from app.services.time_utils import utc_isoformat

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# Aliased so tests can monkeypatch the runtime factory.
ChatRuntime = AgentRuntime

_HISTORY_LIMIT = 20


class ChatIn(BaseModel):
    message: str
    session_id: str = "default"


class ChatOut(BaseModel):
    reply: str
    session_id: str
    tool_calls: list[dict] = Field(default_factory=list)


async def _current_user(session: AsyncSession) -> User:
    user = (await session.scalars(select(User).limit(1))).first()
    if user is None:
        user = User(name="访客", email="guest@example.com", timezone="Asia/Shanghai")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@router.post("", response_model=ChatOut)
async def send_chat(payload: ChatIn, session: AsyncSession = Depends(get_session)) -> ChatOut:
    text = (payload.message or "").strip()
    if not text:
        raise HTTPException(400, "消息不能为空")

    user = await _current_user(session)
    session_id = payload.session_id or "default"

    session.add(
        ChatMessage(user_id=user.id, session_id=session_id, role="user", content=text)
    )
    await session.commit()

    rows = (
        await session.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.user_id == user.id,
                ChatMessage.session_id == session_id,
            )
            .order_by(ChatMessage.id.desc())
            .limit(_HISTORY_LIMIT)
        )
    ).all()
    history = [{"role": r.role, "content": r.content} for r in reversed(rows)]

    runtime = ChatRuntime()
    result = await runtime.run_chat(
        history,
        user_name=user.name,
        timezone=user.timezone,
    )

    trace_json = json.dumps(result.tool_calls, ensure_ascii=False) if result.tool_calls else ""
    session.add(
        ChatMessage(
            user_id=user.id,
            session_id=session_id,
            role="assistant",
            content=result.text,
            tool_trace=trace_json,
        )
    )
    await session.commit()

    return ChatOut(reply=result.text, session_id=session_id, tool_calls=result.tool_calls)


@router.get("/history")
async def get_history(
    session_id: str = "default",
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
    ).all()

    result = []
    for row in reversed(rows):
        trace = []
        if row.tool_trace:
            try:
                trace = json.loads(row.tool_trace)
            except json.JSONDecodeError:
                trace = []
        result.append(
            {
                "role": row.role,
                "content": row.content,
                "tool_calls": trace,
                "created_at": utc_isoformat(row.created_at),
            }
        )
    return result
