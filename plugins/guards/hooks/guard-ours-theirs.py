#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook (no-wholesale-ours-theirs): refuse taking one side of a conflict wholesale.

`git checkout`/`git restore --ours|--theirs`, `git merge|rebase|cherry-pick|pull|revert -X ours|theirs` and
the `-s ours` strategy throw away the other side's every change, conflicting or not. Each hunk is resolved for its intent.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-wholesale-ours-theirs"
SIDES = {"ours", "theirs"}
FILE_LEVEL = {"checkout", "restore"}
STRATEGY = {"merge", "rebase", "cherry-pick", "pull", "revert"}


def strategy_side(args: list[str]) -> str | None:
    words = iter(args)
    for word in words:
        if word in ("-X", "--strategy-option"):
            value = next(words, "")
        elif word.startswith("--strategy-option="):
            value = word.split("=", 1)[1]
        elif word.startswith("-X"):
            value = word[2:]
        elif word in ("-s", "--strategy"):
            # The `ours` strategy keeps none of the other side, conflicting or not.
            value = next(words, "")
        elif word.startswith("--strategy="):
            value = word.split("=", 1)[1]
        elif word.startswith("-s") and len(word) > 2:
            value = word[2:]
        else:
            continue
        if value in SIDES:
            return value
    return None


def refusal(event: dict) -> str | None:
    for command in guardlib.commands_for(RULE, event):
        git = guardlib.git_args(command.argv)
        if git is None:
            continue
        sub, args, _ = git
        what = None
        if sub in FILE_LEVEL:
            side = next((word for word in args if word in ("--ours", "--theirs")), None)
            if side and "--" in args[: args.index(side)]:
                side = None
            what = side and f"`git {sub} {side}`"
        elif sub in STRATEGY:
            side = strategy_side(args)
            what = side and f"`git {sub}` taking `{side}` wholesale"
        if what:
            return (
                f"{what}: it keeps one side of every conflict and drops the other side's changes.\n\n"
                "Instead: resolve each conflict hunk by reading both sides for intent, then run the\n"
                "affected tests. A generated file (a lockfile) is regenerated, not picked."
            )
    return None


if __name__ == "__main__":
    sys.exit(guardlib.run(RULE, refusal))
