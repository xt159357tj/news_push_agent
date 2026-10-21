from __future__ import annotations

"""Sandboxed shell tool.

Design goals
------------
1. Never hand a raw string to a shell. We parse the command with ``shlex``,
   validate every token, and execute through ``create_subprocess_exec`` (no
   shell interpretation) so ``;``, ``&&``, ``|``, backticks and ``$()`` cannot
   chain a second command.
2. Allow only a small, read-only allowlist.
3. Work consistently on Windows and POSIX: ``echo`` / ``pwd`` / ``date`` /
   ``ls`` are shell builtins that may not exist as executables, so they are
   implemented in-process.
4. Off by default; enable with ``BASH_ENABLED=true``.
"""

import asyncio
import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

# Commands that may run, with how many extra arguments they accept.
_SIMPLE_COMMANDS = {"whoami"}
_SUBCOMMAND_ALLOWED = {
    ("python", "--version"),
    ("python3", "--version"),
    ("python", "-V"),
    ("python3", "-V"),
    ("git", "status"),
}
_NATIVE_COMMANDS = {"echo", "pwd", "date", "ls"}

# Rejected before parsing. These are the characters a shell would interpret.
_SHELL_METACHARACTERS = set(";&|<>`$\n\r\x00")

_TIMEOUT_SECONDS = 10
_MAX_STDOUT = 8000
_MAX_STDERR = 2000


def _split(command: str) -> list[str]:
    """Split a command into argv without letting a shell reinterpret it."""
    posix = os.name != "nt"
    try:
        argv = shlex.split(command, posix=posix)
    except ValueError as exc:
        raise ValueError(f"invalid command syntax: {exc}") from exc
    if not posix:
        # shlex with posix=False keeps surrounding quotes; strip them.
        argv = [
            t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"" else t
            for t in argv
        ]
    return argv


def _reject_metacharacters(command: str) -> str | None:
    bad = sorted({ch for ch in command if ch in _SHELL_METACHARACTERS})
    if not bad:
        return None
    readable = ", ".join(repr(ch) for ch in bad)
    return (
        f"Shell metacharacters are not allowed: {readable}. "
        "Pass a single command without pipes, redirects or chaining."
    )


def _check_allowlist(argv: list[str]) -> str | None:
    if not argv:
        return "Empty command"
    cmd = argv[0]
    if cmd in _NATIVE_COMMANDS:
        return None
    if cmd in _SIMPLE_COMMANDS and len(argv) == 1:
        return None
    if len(argv) >= 2 and (cmd, argv[1]) in _SUBCOMMAND_ALLOWED:
        return None
    allowed = sorted(
        _NATIVE_COMMANDS
        | _SIMPLE_COMMANDS
        | {f"{c} {s}" for c, s in _SUBCOMMAND_ALLOWED}
    )
    return f"Command not in allowlist: {cmd}. Allowed: {', '.join(allowed)}"


def _ok(stdout: str = "", stderr: str = "", returncode: int = 0) -> dict[str, Any]:
    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "stdout": stdout[:_MAX_STDOUT],
        "stderr": stderr[:_MAX_STDERR],
    }


def _run_native(argv: list[str]) -> dict[str, Any] | None:
    """Implement shell builtins in-process (cross-platform, no subprocess)."""
    cmd, args = argv[0], argv[1:]
    if cmd == "echo":
        return _ok(stdout=" ".join(args))
    if cmd == "pwd":
        if args:
            return {"ok": False, "error": "pwd takes no arguments"}
        return _ok(stdout=str(Path.cwd()))
    if cmd == "date":
        if args:
            return {"ok": False, "error": "date takes no arguments"}
        return _ok(stdout=datetime.now().astimezone().isoformat())
    if cmd == "ls":
        return _native_ls(args)
    return None


def _native_ls(args: list[str]) -> dict[str, Any]:
    show_hidden = False
    paths: list[str] = []
    for arg in args:
        if arg.startswith("-"):
            flags = set(arg[1:])
            if flags - set("al"):
                return {"ok": False, "error": f"Unsupported ls flag: {arg}"}
            show_hidden = show_hidden or "a" in flags
        else:
            paths.append(arg)
    if len(paths) > 1:
        return {"ok": False, "error": "ls accepts at most one path"}
    target = Path(paths[0]) if paths else Path.cwd()
    if not target.exists():
        return {"ok": False, "error": f"Path does not exist: {target}"}
    if target.is_file():
        return _ok(stdout=target.name)
    entries = [
        child.name + ("/" if child.is_dir() else "")
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower())
        if show_hidden or not child.name.startswith(".")
    ]
    return _ok(stdout="\n".join(entries))


async def bash(command: str, enabled: bool | None = None) -> dict[str, Any]:
    """Execute a single, allowlisted, read-only command.

    Commands are parsed and executed without a shell, so shell operators cannot
    inject a second command. Disabled unless ``BASH_ENABLED=true``.
    """
    if enabled is None:
        enabled = settings.bash_enabled
    if not enabled:
        return {
            "ok": False,
            "error": (
                "bash tool is disabled by configuration. "
                "Set BASH_ENABLED=true to enable it."
            ),
        }

    raw = (command or "").strip()
    if not raw:
        return {"ok": False, "error": "Empty command"}

    problem = _reject_metacharacters(raw)
    if problem:
        return {"ok": False, "error": problem}

    try:
        argv = _split(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    problem = _check_allowlist(argv)
    if problem:
        return {"ok": False, "error": problem}

    native = _run_native(argv)
    if native is not None:
        return native

    executable = shutil.which(argv[0])
    if executable is None:
        return {"ok": False, "error": f"Command not found on this system: {argv[0]}"}

    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": f"Command timed out after {_TIMEOUT_SECONDS}s"}
        return _ok(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            returncode=proc.returncode or 0,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
