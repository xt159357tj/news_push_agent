from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from app.tools import fs_tools, news_tools, shell_tool

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

# OpenAI Responses API tool schema (type/function wrapper).
SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory entries inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 20000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "Search file contents under a workspace directory for a keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "dir": {"type": "string", "default": "."},
                    "max_results": {"type": "integer", "default": 20},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command (only when explicitly enabled).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_rss",
            "description": "Collect recent items from configured RSS feeds. Use to gather news candidates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_time": {"type": "string"},
                    "to_time": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "Search news using hybrid FAISS vector + BM25 ranking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "from_time": {"type": "string"},
                    "to_time": {"type": "string"},
                    "language": {"type": "string", "default": "zh"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_article",
            "description": "Fetch and extract the main text of an article URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["url"],
            },
        },
    },
]

_HANDLERS: dict[str, ToolFn] = {
    "list_dir": fs_tools.list_dir,
    "read_file": fs_tools.read_file,
    "search_content": fs_tools.search_content,
    "write_file": fs_tools.write_file,
    "bash": lambda command: shell_tool.bash(command, enabled=False),
    "collect_rss": news_tools.collect_rss,
    "search_news": news_tools.search_news,
    "fetch_article": news_tools.fetch_article,
}


def get_schemas(format: str = "openai") -> list[dict[str, Any]]:
    """Return tool schemas in the requested format.

    - "openai": OpenAI Responses API style ({type, function:{...}})
    - "flat": DeepSeek-compatible style ({name, description, parameters})
    """
    if format == "flat":
        return [s["function"] for s in SCHEMAS]
    return SCHEMAS


async def execute(
    name: str,
    arguments: dict[str, Any],
    *,
    audit: dict | None = None,
) -> dict[str, Any]:
    """Execute a tool by name with JSON-ish args, validating via the tool signature."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"Unknown tool: {name}"}

    start = time.perf_counter()
    try:
        result = await handler(**arguments)
    except ValidationError as exc:
        result = {"ok": False, "error": f"Invalid arguments: {exc}"}
    except PermissionError as exc:
        result = {"ok": False, "error": str(exc)}
    except TypeError as exc:
        result = {"ok": False, "error": f"Wrong arguments: {exc}"}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)}

    if audit is not None:
        audit["tool_name"] = name
        audit["arguments"] = str(arguments)[:2000]
        audit["result_summary"] = str(result)[:2000]
        audit["ok"] = result.get("ok", False)
        audit["duration_ms"] = int((time.perf_counter() - start) * 1000)
    return result
