#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook — refuse `git commit` in a worktree that is on `main`.

Never work on the trunk (`main` or `master`); `no-commit-on-main` off in the overrides file, or
a `.main-edit-ok` file at the repo root opts a repo out, as for block-main-edits.py. Every commit in the command is
checked, each in the worktree the shared parser finds for it (the payload's `cwd` moved by `cd` and
`git -C`). Fails open on an unreadable payload or a git error.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-commit-on-main"


def target_dirs(command: str, cwd: str) -> list[str]:
    """The worktrees the commits in *command* act in (after `cd` and `git -C`). Text that is only quoted,
    echoed or in a heredoc body never counts; `bash -c` and `$(...)` do."""
    out = []
    for parsed in guardlib.parse(command, cwd=cwd):
        git = guardlib.git_args(parsed.argv)
        if git and git[0] == "commit":
            out.append(guardlib.where(parsed, git[2]) or cwd)
    return out


def refused(where: str) -> bool:
    return branch(where) in TRUNK_BRANCHES and not opted_out(where)


def target_dir(command: str, cwd: str) -> str | None:
    """The first worktree a commit in *command* would be refused in, else the first it acts in, or None for
    no commit."""
    places = target_dirs(command, cwd)
    return next((p for p in places if refused(p)), places[0] if places else None)


def branch(where: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", where, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


TRUNK_BRANCHES = {"main", "master"}


def opted_out(where: str) -> bool:
    """A `.main-edit-ok` file at the repo root: this repo works on its trunk (as block-main-edits.py)."""
    try:
        top = subprocess.run(
            ["git", "-C", where, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(top) and os.path.exists(os.path.join(top, ".main-edit-ok"))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open: an unreadable payload is not evidence of a commit
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input"), dict):
        return 0
    command = payload["tool_input"].get("command")
    if not isinstance(command, str):
        return 0
    where = target_dir(command, payload.get("cwd") or os.getcwd())
    if where is None or not refused(where):
        return 0
    if guardlib.off(RULE, where):
        return 0
    return guardlib.refuse(
        RULE,
        f"Refusing a commit in {where}: this worktree is on `{branch(where)}`.\n\n"
        "Instead: work in a feature worktree off the trunk:\n"
        "`git worktree add <path> -b <feature-branch> origin/main`.\n"
        "If the owner said to commit on the trunk, the owner runs that commit with the `!` prefix,\n"
        "or turns `no-commit-on-main` off, with a reason, for a repo that works on its trunk, in\n"
        f"{guardlib.WHERE}.\n",
        where,
    )


if __name__ == "__main__":
    sys.exit(main())
