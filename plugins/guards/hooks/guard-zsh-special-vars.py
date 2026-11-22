#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook (no-zsh-special-vars): refuse setting `path`, `status`, `UID` or `GID`.

When the Bash tool runs in zsh, `path` is tied to PATH (setting it loses every command), `status` is
read-only, and `UID`/`GID` try to change the user. Caught: a shell assignment (alone or before a command),
`local`/`export`/`typeset`/... of one, and a `for` loop variable. Case matters: `PATH=` and `STATUS=` pass.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-zsh-special-vars"
SPECIAL = {"path", "status", "UID", "GID"}
DECLARERS = {
    "local",
    "export",
    "typeset",
    "declare",
    "readonly",
    "integer",
    "float",
    "private",
}


def named(word: str) -> str:
    return word.split("=", 1)[0].removesuffix("+")


def refusal(event: dict) -> str | None:
    for command in guardlib.commands_for(RULE, event):
        names = [named(word) for word in command.assignments]
        if command.keyword == "for" and command.words:
            names.append(command.words[0])
        argv = command.argv
        if argv and argv[0] in DECLARERS:
            names += [
                named(word) for word in argv[1:] if not word.startswith(("-", "+"))
            ]
        hit = sorted(SPECIAL.intersection(names))
        if hit:
            return (
                f"setting `{hit[0]}`: zsh treats these as special (`path` is PATH, `status` is read-only,\n"
                "`UID`/`GID` change the user).\n\n"
                "Instead: name it e.g. wt (or rc for an exit status)."
            )
    return None


if __name__ == "__main__":
    sys.exit(guardlib.run(RULE, refusal))
