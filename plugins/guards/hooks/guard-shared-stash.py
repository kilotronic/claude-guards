#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook — refuse a `git stash` that writes the shared ref.

`refs/stash` is ONE ref for the whole repository and agents run here in parallel,
so a concurrent stash races and a `pop` can carry off another agent's payload.
`no-stash` off in the overrides file turns it off for a repo. So does `no-commit-on-main`, the id that
covered this guard before it had its own, unless `no-stash` is locked.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-stash"
# The id this guard reported before it had its own: an override of it still counts.
OLD_RULE = "no-commit-on-main"

READS = (["list"], ["show"], ["apply"])
MESSAGE = (
    "Refusing `git stash`: refs/stash is ONE ref shared by every worktree in this\n"
    "repository, and agents run here in parallel. A concurrent stash races, and a\n"
    "`pop` can carry off another agent's payload.\n\n"
    "Instead:\n"
    "  • to rebase over uncommitted work: `git rebase --autostash` (its state lives in this\n"
    "    worktree's own rebase dir, so it cannot race).\n"
    "  • to park work: `git diff > /tmp/<name>.patch`, then `git apply` it back.\n\n"
    "`git stash list`, `show`, and `apply` are allowed — reading is how you recover.\n"
)


def _stashes(commands: list[guardlib.Command]) -> list[guardlib.Command]:
    """The commands that write the shared stash ref: `git stash` but for list/show/apply."""
    out = []
    for command in commands:
        git = guardlib.git_args(command.argv)
        if git and git[0] == "stash" and git[1][:1] not in READS:
            out.append(command)
    return out


def pushes_to_stash(command: str) -> bool:
    """True when the command would WRITE to the shared stash ref (quoted text doesn't count)."""
    return bool(_stashes(guardlib.parse(command)))


def stash_places(payload: dict) -> list[str | None]:
    """The directories the stashes in the call act in (after `cd` and `-C`); the call's cwd if none parse."""
    places = []
    for command in _stashes(guardlib.bash_commands(payload)):
        git = guardlib.git_args(command.argv)
        elsewhere = any(
            w.startswith(("--git-dir", "--work-tree")) for w in command.argv
        )
        # Another repo named by --git-dir: its setting is unknown here, so the rule stays on.
        places.append(None if elsewhere else guardlib.where(command, git[2]))
    return places


def off(place: str | None) -> bool:
    """Whether the overrides file turns the guard off for the repo at *place*, by either id."""
    if guardlib.off(RULE, place):
        return True
    return RULE not in guardlib.locked_rules() and guardlib.off(OLD_RULE, place)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open: an unreadable payload is not evidence of a stash
    if not isinstance(payload, dict):
        return 0
    places = stash_places(payload)
    if not places or all(off(place) for place in places):
        return 0
    return guardlib.refuse(RULE, MESSAGE, next((p for p in places if not off(p)), None))


if __name__ == "__main__":
    sys.exit(main())
