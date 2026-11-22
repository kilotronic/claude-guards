#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook (no-admin-merge): refuse `gh pr merge --admin`.

`--admin` merges past branch protection: failing or missing checks and reviews. A plain merge and
`--auto` are untouched.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-admin-merge"


def refusal(event: dict) -> str | None:
    for command in guardlib.bash_commands(event):
        gh = guardlib.gh_args(command.argv)
        if gh is None or gh[0][:2] != ["pr", "merge"]:
            continue
        if any(word == "--admin" or word.startswith("--admin=") for word in gh[0][2:]):
            return (
                "`gh pr merge --admin`: it merges past branch protection (checks and reviews).\n\n"
                "Instead: fix what blocks the merge, then `gh pr merge <n> --squash --delete-branch`,\n"
                "or arm it with `gh pr merge <n> --auto --squash`. If protection itself is wrong, ask the repo's owner."
            )
    return None


if __name__ == "__main__":
    sys.exit(guardlib.run(RULE, refusal))
