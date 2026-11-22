#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook — refuse `git commit` in a worktree that is on `main`.

Never work on the trunk (`main` or `master`); `no-commit-on-main` off in the overrides file, or
a `.main-edit-ok` file at the repo root opts a repo out, as for block-main-edits.py. The target worktree is
the payload's `cwd` moved by any `cd` before the commit and `git -C`, as in
`guard-hard-reset.py`. Fails open on an unreadable payload or a git error.
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

_COMMIT = re.compile(
    r"(?<![\w.-])git\b"
    + r"((?:\s+-C\s+\S+|\s+-c\s+\S+|\s+--no-pager)*)"
    + r"\s+commit(?![\w-])"
)
_DASH_C = re.compile(r"-C\s+(\S+)")
_CD = re.compile(r"(?<![\w.-])cd\s+([^\s;&|]+)")
_QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
_HEREDOC = re.compile(
    r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1[^\n]*\n(.*?)\n\2$", re.DOTALL | re.MULTILINE
)


def _mask(command: str) -> str:
    """*command* with heredoc bodies and quoted text blanked, the same length.

    A quoted word keeps its quotes around non-space filler, so it stays one word
    whose span reads the original path back.
    """
    for match in _HEREDOC.finditer(command):
        a, b = match.span(3)
        command = command[:a] + " " * (b - a) + command[b:]
    return _QUOTED.sub(lambda m: m[0][0] + "x" * (len(m[0]) - 2) + m[0][-1], command)


def target_dir(command: str, cwd: str) -> str | None:
    """The worktree a commit in *command* acts on, or None for no commit."""
    masked = _mask(command)
    match = _COMMIT.search(masked)
    if match is None:
        return None
    flags = match.span(1)
    spans = [m.span(1) for m in _CD.finditer(masked, 0, match.start())]
    spans += [m.span(1) for m in _DASH_C.finditer(masked, *flags)]
    where = cwd
    for a, b in spans:
        where = os.path.join(where, os.path.expanduser(command[a:b].strip("'\"")))
    return os.path.normpath(where)


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
    command = (payload.get("tool_input") or {}).get("command") or ""
    where = target_dir(command, payload.get("cwd") or os.getcwd())
    if where is None or branch(where) not in TRUNK_BRANCHES or opted_out(where):
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
