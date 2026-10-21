from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.config import settings

WORKSPACE = Path(settings.workspace_root).resolve()


def _resolve(path: str) -> Path:
    """Resolve path and ensure it stays inside the workspace."""
    candidate = (WORKSPACE / path).resolve()
    if not candidate.is_relative_to(WORKSPACE):
        raise PermissionError(f"Path outside workspace: {path}")
    return candidate


async def list_dir(path: str = ".") -> dict[str, Any]:
    """List directory entries (name, type, size) inside the workspace."""
    target = _resolve(path)
    if not target.exists():
        return {"ok": False, "error": f"Path does not exist: {path}"}
    if not target.is_dir():
        return {"ok": False, "error": f"Not a directory: {path}"}
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        try:
            size = child.stat().st_size if child.is_file() else None
        except OSError:
            size = None
        entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file", "size": size})
    return {"ok": True, "path": str(target), "entries": entries}


async def read_file(path: str, max_chars: int = 20000) -> dict[str, Any]:
    """Read a text file inside the workspace (optionally truncated)."""
    target = _resolve(path)
    if not target.is_file():
        return {"ok": False, "error": f"Not a readable file: {path}"}
    content = target.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > max_chars
    return {
        "ok": True,
        "path": str(target),
        "content": content[:max_chars],
        "truncated": truncated,
    }


async def search_content(keyword: str, dir: str = ".", max_results: int = 20) -> dict[str, Any]:
    """Search file contents under a directory for a keyword (case-insensitive)."""
    root = _resolve(dir)
    if not root.is_dir():
        return {"ok": False, "error": f"Not a directory: {dir}"}
    results: list[dict] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".venv", "venv", "__pycache__", ".git", "node_modules"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if keyword.lower() in text.lower():
            results.append({"path": str(path.relative_to(WORKSPACE)), "hits": text.lower().count(keyword.lower())})
        if len(results) >= max_results:
            break
    return {"ok": True, "dir": str(root), "keyword": keyword, "results": results, "count": len(results)}


async def write_file(path: str, content: str) -> dict[str, Any]:
    """Write content to a file inside the workspace (creates parent dirs)."""
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(target), "bytes": len(content.encode("utf-8"))}