#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Let agents merge pull requests: a PreToolUse hook that allows a Bash call that is only `gh pr merge …`.

Claude Code's auto mode blocks `gh pr merge` as a destructive action even after the person said yes. This hook
answers "allow" for that one command, so the person's go-ahead is enough. It does not decide whether a merge is
wanted: the repo's rules still say when to ask first (for example, when a merge deploys).

Allowed only when the whole command is `gh pr merge` with its own arguments: no `;`, `&&`, `|`, redirection,
`$(…)`, backticks or newlines, so nothing else can ride along. Never for `--admin` (it merges past required
checks) or `--body-file`/`-F` (it reads a local file into the merge commit). Anything else gets no answer and the
normal permission check runs. Standard library only; never fails the call.
"""

from __future__ import annotations

import json
import shlex
import sys

# Characters that would let a second command, a substitution or a filename wildcard ride along with the merge
# (`^` and `#` are wildcards in zsh with extendedglob).
UNSAFE = set(";&|<>`$\n\r(){}*?[]^#")
# Long flags that never get an allow: --admin merges past required checks; --body-file reads a local file.
REFUSED_LONG = ("--admin", "--body-file")
# The short form of --body-file, alone or in a cluster such as -sF.
REFUSED_SHORT = "F"


def is_refused_flag(word: str) -> bool:
    if word.startswith("--"):
        return word.split("=", 1)[0] in REFUSED_LONG
    return word.startswith("-") and REFUSED_SHORT in word[1:]


def is_plain_merge(command: str) -> bool:
    if UNSAFE & set(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if words[:3] != ["gh", "pr", "merge"]:
        return False
    return not any(is_refused_flag(word) for word in words[3:])


def decide(event: dict) -> dict | None:
    if event.get("tool_name") != "Bash":
        return None
    command = (event.get("tool_input") or {}).get("command") or ""
    if not is_plain_merge(command):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "work: agents may merge pull requests (gh pr merge only)",
        }
    }


def main() -> None:
    try:
        answer = decide(json.load(sys.stdin))
    except (
        ValueError,
        AttributeError,
        TypeError,
    ):  # bad input: never fail the tool call
        return
    if answer:
        print(json.dumps(answer))


if __name__ == "__main__":
    main()
