#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash|Monitor hook (no-github-polling): refuse fast GitHub polling.

`gh run watch` and `gh pr checks --watch` poll every few seconds by default, and a shell loop around `gh`
polls as fast as it sleeps; both burn the GitHub API rate limit shared by every agent on the account. One
watch with `--interval 60` or more is the way to wait. Refused: such a watch without that interval; a
`while`/`until`/`repeat` loop that runs `gh` (except `... | while read`, which walks a list once); a `for`
loop whose `gh` call ignores the loop variable (the same query each pass); `watch ... gh ...`. One-off `gh` calls pass.
The loop variable is followed through the body: into a variable assigned from it, into `$1`... after
`set -- $var`, and into the names a `read ... <<< "$var"` reads.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-github-polling"
MIN_INTERVAL = 60
TOOLS = {"Bash", "Monitor"}
# A variable a word expands to its value: `$x`, `${x}`, `${x%...}`; not `${#x}`, its length, or
# `${x:+...}`, which is the same text whatever x is (the parser's own rule for `refs`).
REF = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_]|:?\+)|\$([A-Za-z_][A-Za-z0-9_]*)"
)
# A positional parameter: `$1`, `${2}`, `$@`, `$*`.
POSITIONAL = re.compile(r"\$\{?[1-9@*]")


def interval(args: list[str]) -> int | None:
    """The `--interval`/`-i` seconds in *args*, or None when absent or not a number."""
    value = None
    words = iter(args)
    for word in words:
        if word in ("--interval", "-i"):
            value = next(words, None)
        elif word.startswith("--interval="):
            value = word.split("=", 1)[1]
        elif word.startswith("-i") and word[2:].isdigit():
            value = word[2:]
    return int(value) if value is not None and value.isdigit() else None


def fast_watch(args: list[str]) -> bool:
    watching = args[:2] == ["run", "watch"] or (
        args[:2] == ["pr", "checks"] and "--watch" in args
    )
    return watching and (interval(args) or 0) < MIN_INTERVAL


def polls(loop: guardlib.Loop, commands: list[guardlib.Command]) -> bool:
    """A loop around `gh` that asks the same thing each pass, rather than walking a list of things."""
    calls = [c for c in commands if loop in c.loop_refs and guardlib.gh_args(c.argv)]
    if not calls:
        return False
    if (
        loop.kind in ("while", "until")
        and loop.condition[:1] != ["read"]
        or loop.kind == "repeat"
    ):
        return True
    body = [c for c in commands if loop in c.loop_refs]
    walks, positional = carried(set(loop.vars), body)

    def uses(command: guardlib.Command) -> bool:
        if walks.intersection(v for refs in command.refs for v in refs):
            return True
        return positional and any(POSITIONAL.search(w) for w in command.argv[1:])

    # A `cd "$d"` in the body moves each pass to another repo.
    if any(c.argv[:1] == ["cd"] and uses(c) for c in body):
        return False
    return any(not uses(c) for c in calls)


def refs_in(text: str) -> set[str]:
    return {a or b for a, b in REF.findall(text)}


def carried(names: set[str], body: list[guardlib.Command]) -> tuple[set[str], bool]:
    """The variables that change with the loop variables *names* in a loop's *body*, and whether the
    positional parameters do (`set -- $var`). Followed until nothing new, since the body runs every pass."""
    names, positional = set(names), False
    while True:
        before = (len(names), positional)
        for command in body:
            refs = {v for word in command.refs for v in word}
            # `r=$p gh ...` sets r only in gh's environment; its words still see the old r.
            # The parser lifts only the first of `do a=1 b=2`; the rest stay words.
            only_settings = all(guardlib.ASSIGNMENT.match(w) for w in command.words)
            for text in command.assignments + command.words if only_settings else ():
                name, _, value = text.partition("=")
                if names.intersection(refs_in(value)):
                    names.add(name.rstrip("+").split("[")[0])
            argv = command.argv
            if argv[:1] == ["set"] and names & refs:
                positional = True
            if argv[:1] == ["read"] and any(
                op.endswith("<<<") and names.intersection(refs_in(target))
                for op, target in command.redirects
            ):
                names.update(w for w in argv[1:] if guardlib.NAME.fullmatch(w))
        if (len(names), positional) == before:
            return names, positional


def watches_gh(argv: list[str]) -> bool:
    if argv[:1] != ["watch"]:
        return False
    script = " ".join(
        word for word in argv[1:] if not word.startswith("-") and not word.isdigit()
    )
    return any(guardlib.gh_args(c.argv) for c in guardlib.parse(script))


def refusal(event: dict) -> str | None:
    if event.get("tool_name") not in TOOLS:
        return None
    commands = guardlib.bash_commands(event)
    loops = {id(loop): loop for c in commands for loop in c.loop_refs}
    what = None
    for command in commands:
        gh = guardlib.gh_args(command.argv)
        if gh and fast_watch(gh[0]):
            what = f"`gh {' '.join(gh[0][:2])}` without `--interval {MIN_INTERVAL}` or more"
        elif watches_gh(command.argv):
            what = "`watch` around `gh`"
    if what is None and any(polls(loop, commands) for loop in loops.values()):
        what = "a shell loop that polls `gh`"
    if what is None:
        return None
    return (
        f"{what}: fast polling burns the GitHub rate limit every agent shares.\n\n"
        f"Instead: one `gh pr checks <n> --watch --interval {MIN_INTERVAL}` (or\n"
        f"`gh run watch <id> --interval {MIN_INTERVAL} --exit-status`), or a single `gh` call now and another later."
    )


if __name__ == "__main__":
    sys.exit(guardlib.run(RULE, refusal))
