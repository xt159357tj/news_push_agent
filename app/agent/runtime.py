from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.schemas import DigestOut
from app.config import settings
from app.tools import registry

logger = logging.getLogger(__name__)


@dataclass
class AgentRequest:
    user_name: str
    timezone: str
    topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    language: str = "zh"
    max_items: int = 10
    recent_fingerprints: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    digest: DigestOut | None
    steps: int
    tool_calls: int
    output_text: str = ""


class AgentRuntime:
    """Drives an LLM function-calling loop where the model picks tool calls.

    Uses the Chat Completions API (broadly compatible with OpenAI / DeepSeek /
    other OpenAI-compatible providers) so tool calling works everywhere.
    """

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self.client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )

    async def run(self, request: AgentRequest, audit: dict | None = None) -> AgentResult:
        messages: list[dict[str, Any]] = self._build_messages(request)
        steps = 0
        tool_calls = 0

        while steps < settings.agent_max_steps:
            steps += 1
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=settings.llm_model,
                        messages=messages,
                        tools=registry.get_schemas(format="openai"),
                        temperature=0.2,
                    ),
                    timeout=settings.agent_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                return AgentResult(None, steps, tool_calls, f"LLM timeout: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("LLM call failed")
                return AgentResult(None, steps, tool_calls, f"LLM error: {exc}")

            message = response.choices[0].message
            text = message.content or ""
            tool_calls_list = message.tool_calls or []

            if not tool_calls_list:
                return AgentResult(self._parse_final(text), steps, tool_calls, text)

            # Append the assistant message with tool_calls so the provider has
            # the full conversation context, then append tool results.
            messages.append(
                {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls_list
                    ],
                }
            )

            for tc in tool_calls_list:
                tool_calls += 1
                name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = await registry.execute(name, arguments, audit=audit)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return AgentResult(None, steps, tool_calls, "Exceeded max steps")

    def _build_messages(self, request: AgentRequest) -> list[dict[str, Any]]:
        user_prompt = (
            f"用户：{request.user_name}（时区 {request.timezone}）\n"
            f"关注话题：{', '.join(request.topics) or '（无）'}\n"
            f"关键词：{', '.join(request.keywords) or '（无）'}\n"
            f"简报语言：{request.language}\n"
            f"最大条数：{request.max_items}\n"
            f"最近已报道指纹（避免重复）：{json.dumps(request.recent_fingerprints, ensure_ascii=False)}\n\n"
            "请开始检索并生成简报。"
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _parse_final(self, text: str) -> DigestOut | None:
        """Parse the model's final JSON into DigestOut (tolerating code fences)."""
        import re

        cleaned = text.strip()
        # Strip markdown code fences if present.
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        data = None
        # Try full-text JSON first.
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Fallback: extract the first balanced {...} block.
            start = cleaned.find("{")
            if start != -1:
                depth = 0
                for i in range(start, len(cleaned)):
                    ch = cleaned[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = cleaned[start : i + 1]
                            try:
                                data = json.loads(candidate)
                            except json.JSONDecodeError:
                                data = None
                            break

        if data is None:
            logger.warning("Final output is not valid JSON")
            return None
        try:
            return DigestOut.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Digest validation failed: %s", exc)
            return None
