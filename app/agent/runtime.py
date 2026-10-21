from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.agent.prompts import CHAT_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.agent.schemas import DigestOut
from app.config import settings
from app.tools import registry

logger = logging.getLogger(__name__)

# How many past chat turns to send back to the model.
_CHAT_HISTORY_LIMIT = 20
# How much of a tool result to keep in the JSON trace returned to the UI.
_TRACE_RESULT_CHARS = 600


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


@dataclass
class ChatResult:
    text: str
    steps: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


class AgentRuntime:
    """Drives an LLM function-calling loop where the model picks tool calls.

    Two entry points share the same loop:

    - ``run(AgentRequest)``: daily digest generation, expects a JSON digest.
    - ``run_chat(history)``: interactive chat, returns plain text.
    """

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self.client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )

    # ----- public entry points -----------------------------------------
    async def run(self, request: AgentRequest, audit: dict | None = None) -> AgentResult:
        messages = self._build_messages(request)
        text, steps, tool_trace, error = await self._run_loop(messages, audit=audit)
        if text is None:
            return AgentResult(None, steps, len(tool_trace), error)
        return AgentResult(self._parse_final(text), steps, len(tool_trace), text)

    async def run_chat(
        self,
        history: list[dict[str, Any]],
        *,
        user_name: str = "",
        timezone: str = "Asia/Shanghai",
        audit: dict | None = None,
    ) -> ChatResult:
        messages = self._build_chat_messages(history, user_name=user_name, timezone=timezone)
        text, steps, tool_trace, error = await self._run_loop(messages, audit=audit)
        if text is None:
            reply = error
        elif not text.strip():
            reply = "（模型没有返回内容，请重试）"
        else:
            reply = text
        return ChatResult(
            text=reply,
            steps=steps,
            tool_calls=tool_trace,
            error=error,
        )

    # ----- core loop ----------------------------------------------------
    async def _run_loop(
        self,
        messages: list[dict[str, Any]],
        *,
        audit: dict | None = None,
    ) -> tuple[str | None, int, list[dict[str, Any]], str]:
        """Run the tool loop. Returns (final_text, steps, tool_trace, error)."""
        steps = 0
        tool_trace: list[dict[str, Any]] = []

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
                return None, steps, tool_trace, f"LLM timeout: {exc}"
            except Exception as exc:  # noqa: BLE001
                logger.exception("LLM call failed")
                return None, steps, tool_trace, f"LLM error: {exc}"

            message = response.choices[0].message
            text = message.content or ""
            tool_calls_list = message.tool_calls or []

            if not tool_calls_list:
                return text, steps, tool_trace, ""

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
                name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = await registry.execute(name, arguments, audit=audit)
                tool_trace.append(
                    {
                        "name": name,
                        "arguments": arguments,
                        "ok": bool(result.get("ok", False)),
                        "result": _summarize_result(result),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return None, steps, tool_trace, "Exceeded max steps"

    # ----- message builders --------------------------------------------
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

    def _build_chat_messages(
        self,
        history: list[dict[str, Any]],
        *,
        user_name: str,
        timezone: str,
    ) -> list[dict[str, Any]]:
        context = (
            f"当前用户：{user_name or '访客'}；时区：{timezone or 'Asia/Shanghai'}。"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{CHAT_SYSTEM_PROMPT}\n{context}"}
        ]
        for item in history[-_CHAT_HISTORY_LIMIT:]:
            role = item.get("role")
            content = item.get("content") or ""
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        return messages

    def _parse_final(self, text: str) -> DigestOut | None:
        """Parse the model's final JSON into DigestOut (tolerating code fences)."""
        import re

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        data = None
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
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


def _summarize_result(result: dict[str, Any]) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= _TRACE_RESULT_CHARS else text[:_TRACE_RESULT_CHARS] + "…"
