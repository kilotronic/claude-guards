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
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-commit-on-main"


def _hard_reset(argv: list[str]) -> list[str] | None:
    """The `git -C` directories of a `git reset --hard`, or None for another command."""
    git = guardlib.git_args(argv)
    if git and git[0] == "reset" and "--hard" in git[1]:
        return git[2]
    return None


def target_dirs(command: str, cwd: str) -> list[str]:
    """The worktrees the hard resets in *command* act on (after `cd` and `git -C`). Quoted text, a heredoc
    body or an `echo` never counts."""
    out = []
    for parsed in guardlib.parse(command, cwd=cwd):
        dirs = _hard_reset(parsed.argv)
        if dirs is not None:
            out.append(guardlib.where(parsed, dirs) or cwd)
    return out


def target_dir(command: str, cwd: str) -> str | None:
    """The first worktree a hard reset in *command* would be refused in, else the first it acts on, or None
    for no hard reset."""
    places = target_dirs(command, cwd)
    return next((p for p in places if refusal(p)), places[0] if places else None)


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
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input"), dict):
        return 0
    command = payload["tool_input"].get("command")
    if not isinstance(command, str):
        return 0
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
