#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Warn before a merge that has no fresh review: a PreToolUse hook on Bash calls that run `gh pr merge`.

The `work:pr` skill's step 5 has a fresh reviewer agent post its findings as a pull request comment that starts
with the agent line. Before `gh pr merge` (with or without `--auto`) this hook reads the pull request's comments
once. None starts with the agent line: it warns the agent (and the person) to run step 5 first. Any agent comment
counts, since the reviewer's has no mark of its own. GitHub can't be asked (offline, `gh` missing or failing,
too slow): it warns that it could not check. Either way the merge goes ahead; set BLOCKING to refuse a merge with
no review comment instead (a failed check still only warns). Standard library only; never fails the call.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable

# True: refuse a merge whose pull request has no review comment. False: only warn.
BLOCKING = False

# How the reviewer's comment starts (the agent line from the `work:pr` skill's step 5).
MARKER = "🤖 **Agent update**"
# Seconds for one GitHub lookup, and for all of them in one command; the hook's limit in hooks.json is longer.
GH_TIMEOUT = 5
BUDGET = 9

# `gh pr merge` flags that take a value, so the next word is not the pull request.
VALUE_FLAGS = {
    "-A",
    "--author-email",
    "-b",
    "--body",
    "-F",
    "--body-file",
    "-t",
    "--subject",
    "--match-head-commit",
}
REPO_FLAGS = {"-R", "--repo"}
PUNCTUATION = ";&|()"
# A redirection and its target (`2>&1`, `> out`, `2>/dev/null`): not an argument of the command.
REDIRECTION = re.compile(r"\d*(?:>>|>&|<&|>|<)\s*[^\s;&|()]+")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# A quoted string, left as it is when redirections are taken out.
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
# Words that start a command without changing it.
WRAPPERS = {"env", "command"}

Fetch = Callable[[list[str], "str | None", float], "dict | None"]


def merges(command: str, cwd: str | None) -> list[tuple[list[str], str | None]]:
    """Each `gh pr merge` in the command: the `gh pr view` arguments naming the same pull request, and the
    directory it runs in (after any `cd` before it)."""
    text = command.replace("\\\n", " ").replace("\n", " ; ")
    parts = QUOTED.split(text)
    quoted = QUOTED.findall(text) + [""]
    text = "".join(
        REDIRECTION.sub(" ", part) + quote for part, quote in zip(parts, quoted)
    )
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for word in words:
        if word and set(word) <= set(PUNCTUATION):
            segments.append([])
        else:
            segments[-1].append(word)
    found = []
    for segment in segments:
        env_repo: list[str] = []
        while segment and (segment[0] in WRAPPERS or ASSIGNMENT.match(segment[0])):
            if segment[0].startswith("GH_REPO="):
                env_repo = ["--repo", segment[0].split("=", 1)[1]]
            if segment[0] == "env":
                segment = segment[1:]
                while segment and segment[0].startswith("-"):
                    segment = (
                        segment[2:]
                        if segment[0] in ("-u", "--unset", "-C", "--chdir")
                        else segment[1:]
                    )
                continue
            segment = segment[1:]
        if segment[:1] == ["cd"] and len(segment) > 1:
            where = os.path.expanduser(segment[1])
            if os.path.isabs(where):
                cwd = os.path.normpath(where)
            elif cwd:
                cwd = os.path.normpath(os.path.join(cwd, where))
            continue
        if not segment or os.path.basename(segment[0]) != "gh":
            continue
        if segment[1:3] != ["pr", "merge"] or "--disable-auto" in segment:
            continue
        target: list[str] = []
        repo = env_repo
        args = iter(segment[3:])
        for word in args:
            name = word.split("=", 1)[0]
            if name in REPO_FLAGS:
                repo = [
                    "--repo",
                    word.split("=", 1)[1] if "=" in word else next(args, ""),
                ]
            elif name in VALUE_FLAGS:
                if "=" not in word:
                    next(args, None)
            elif not word.startswith("-") and not target:
                target = [word]
        found.append((target + repo, cwd))
    return found


def fetch_pr(selector: list[str], cwd: str | None, timeout: float) -> dict | None:
    """The pull request's comment bodies, or None when GitHub could not be asked."""
    try:
        done = subprocess.run(
            ["gh", "pr", "view", *selector, "--json", "comments"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if done.returncode != 0:
            return None
        return {
            "comments": [
                comment["body"] for comment in json.loads(done.stdout)["comments"]
            ]
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def has_review(pr: dict) -> bool:
    return any(body.lstrip().startswith(MARKER) for body in pr["comments"])


def describe(selector: list[str]) -> str:
    target = selector[0] if selector and selector[0] != "--repo" else "for this branch"
    return target + (f" in {selector[-1]}" if "--repo" in selector else "")


def warn(text: str) -> dict:
    return {
        "systemMessage": text,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
        },
    }


def decide(
    event: dict, fetch: Fetch = fetch_pr, clock: Callable[[], float] = time.monotonic
) -> dict | None:
    if event.get("tool_name") != "Bash":
        return None
    command = (event.get("tool_input") or {}).get("command") or ""
    found = merges(command, event.get("cwd"))
    if not found:
        return None
    start = clock()
    unchecked, unreviewed = [], []
    for selector, cwd in found:
        left = BUDGET - (clock() - start)
        pr = fetch(selector, cwd, min(GH_TIMEOUT, left)) if left > 0 else None
        if pr is None:
            unchecked.append(describe(selector))
        elif not has_review(pr):
            unreviewed.append(describe(selector))
    lines = [
        f"work: could not check pull request {name} for a fresh-review comment (GitHub did not answer). "
        "Make sure the `work:pr` skill's step 5 ran: a fresh reviewer agent posted its findings as a comment."
        for name in unchecked
    ] + [
        f"work: pull request {name} has no fresh-review comment (one starting with the agent line). "
        "Run the `work:pr` skill's step 5 first: a fresh reviewer agent reviews it and posts its findings as a comment."
        for name in unreviewed
    ]
    if not lines:
        return None
    text = "\n".join(lines)
    if BLOCKING and unreviewed:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": text,
            }
        }
    return warn(text)


def main() -> None:
    try:
        answer = decide(json.load(sys.stdin))
    except (ValueError, AttributeError, TypeError):
        return
    if answer:
        print(json.dumps(answer))


if __name__ == "__main__":
    main()
