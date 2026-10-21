from __future__ import annotations

from pydantic import BaseModel, Field


class DigestItemOut(BaseModel):
    title: str
    url: str
    summary: str
    reason: str = ""


class DigestOut(BaseModel):
    title: str = "AI 新闻简报"
    overview: str = ""
    language: str = "zh"
    items: list[DigestItemOut] = Field(default_factory=list)