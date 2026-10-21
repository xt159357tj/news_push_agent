from __future__ import annotations

import asyncio
import shlex
from typing import Any

# Bash tool is disabled by default for safety.
# When enabled, use a command allowlist + timeout.
ALLOWED_PREFIXES = ("ls", "pwd", "whoami", "date", "echo", "python --version", "git status")


async def bash(command: str, enabled: bool = False) -> dict[str, Any]:
    """Execute a shell command (disabled by default)."""
    if not enabled:
        return {
            "ok": False,
            "error": "bash tool is disabled by configuration. Use filesystem/search tools instead.",
        }
    cmd = command.strip()
    if not cmd:
        return {"ok": False, "error": "Empty command"}
    if not any(cmd.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return {
            "ok": False,
            "error": f"Command not in allowlist. Allowed prefixes: {ALLOWED_PREFIXES}",
        }
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "Command timed out"}
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[:8000],
            "stderr": stderr.decode("utf-8", errors="replace")[:2000],
        }
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": str(exc)}