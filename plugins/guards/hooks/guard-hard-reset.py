#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook — refuse a `git reset --hard` that would lose work.

A permission rule may allow `git reset` so an agent can reset its own branch
without a prompt; this guard keeps the two cases that cost something: a worktree
on `main`, and uncommitted tracked changes (untracked files survive a hard reset).
`no-commit-on-main` off in the overrides file turns it off for a repo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-commit-on-main"

_HARD_RESET = re.compile(
    r"(?<![\w.-])git\b"
    + r"((?:\s+-C\s+\S+|\s+-c\s+\S+|\s+--no-pager)*)"
    + r"\s+reset\b[^;&|\n]*\s--hard\b"
)
_DASH_C = re.compile(r"-C\s+(\S+)")
_CD = re.compile(r"(?<![\w.-])cd\s+([^\s;&|]+)")


def target_dir(command: str, cwd: str) -> str | None:
    """The worktree a hard reset in *command* acts on, or None for no hard reset."""
    match = _HARD_RESET.search(command)
    if match is None:
        return None
    dash_c = _DASH_C.findall(match.group(1))
    cds = _CD.findall(command[: match.start()])
    where = cwd
    for step in cds + dash_c:
        where = os.path.join(where, os.path.expanduser(step.strip("'\"")))
    return os.path.normpath(where)


def _git(where: str, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", where, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def refusal(where: str) -> str | None:
    """Why a hard reset in *where* is refused, or None to allow it."""
    trunk = _git(where, "rev-parse", "--abbrev-ref", "HEAD")
    if trunk in ("main", "master"):
        return f"this worktree is on `{trunk}`"
    if _git(where, "status", "--porcelain", "--untracked-files=no"):
        return "this worktree has uncommitted changes it would discard"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open: an unreadable payload is not evidence of a reset
    command = (payload.get("tool_input") or {}).get("command") or ""
    where = target_dir(command, payload.get("cwd") or os.getcwd())
    reason = None if where is None else refusal(where)
    if reason is None or guardlib.off(RULE, where):
        return 0
    return guardlib.refuse(
        RULE,
        f"Refusing a hard reset in {where}: {reason}.\n\n"
        "Instead: commit the edits first (the commit stays in the reflog); a clean\n"
        "feature branch resets freely. Never reset `main`:\n"
        "`git merge --ff-only origin/main` updates it.\n",
        where,
    )


if __name__ == "__main__":
    sys.exit(main())
