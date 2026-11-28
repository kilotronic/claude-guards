#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""Refuse a pull request that carries code and no claim: a PreToolUse hook on Bash calls that run `gh pr create`.

Rule `claim-before-pr`. The `start-work` skill commits a claim mark as the work branch's first commit, and the
open pull request then holds the item; when that is skipped, the todo file still shows the item free and a second
session takes it. Before `gh pr create` (or `gh pr new`) this hook asks `next-item claimed` about the branch, the
one place that knows a claim mark, a docs-only change and a repo with no todo file. It answers no (exit 3): the
call is refused in one line, unless the pull request's body carries ESCAPE on a line of its own, for work the
owner asked for directly. Anything else it answers, or no answer in TIMEOUT seconds, warns and lets the call
through. `--web` and `--dry-run` open nothing here and are not checked, nor is a pull request into another repo.
A repo turns the rule off in the guards' overrides file. Standard library only; never fails the call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guardlib

RULE = "claim-before-pr"
# The line a pull request's body carries when no todo entry covers the work. Case and a final full stop don't matter.
ESCAPE = "No backlog item: the owner asked for this directly."
NEXT_ITEM = os.path.join(os.path.dirname(HERE), "scripts", "next-item")
# Seconds for next-item's answer; with review-check's 9 s budget it stays under the dispatcher's limit in hooks.json.
TIMEOUT = 10
NO = 3
# `gh pr create` flags that take a value, so the next word is not a flag.
VALUE_FLAGS = {
    "-a",
    "--assignee",
    "-B",
    "--base",
    "-b",
    "--body",
    "-l",
    "--label",
    "-m",
    "--milestone",
    "-p",
    "--project",
    "--recover",
    "-r",
    "--reviewer",
    "-T",
    "--template",
    "-t",
    "--title",
}
HEAD_FLAGS = {"-H", "--head"}
BODY_FILE_FLAGS = {"-F", "--body-file"}
# These open no pull request from here: the browser's form does, or nothing does.
UNCHECKED_FLAGS = {"-w", "--web", "--dry-run", "-h", "--help"}
UNKNOWN = "only the shell knows its folder or branch"
# The hook runs before the call does, so what these leave checked out is not there to judge yet.
MOVED = "an earlier command in the same call changes the branch"
MOVES = {
    "am",
    "checkout",
    "cherry-pick",
    "commit",
    "merge",
    "pull",
    "rebase",
    "reset",
    "revert",
    "switch",
}


def options(args: list[str]) -> tuple[str | None, list[str], bool]:
    """(the `--head` branch or None, the `--body-file` paths, whether a flag means it is not checked)."""
    head, files, unchecked = None, [], False
    words = iter(args)
    for word in words:
        name, has_value, value = word.partition("=")
        if name in UNCHECKED_FLAGS:
            unchecked = True
        elif name in HEAD_FLAGS:
            head = value if has_value else next(words, "")
        elif name in BODY_FILE_FLAGS:
            files.append(value if has_value else next(words, ""))
        elif name in VALUE_FLAGS and not has_value:
            next(words, None)
    return head, files, unchecked


def escaped(texts: list[str]) -> bool:
    want = ESCAPE.rstrip(".").lower()
    return any(
        line.strip().rstrip(".").lower() == want
        for text in texts
        for line in text.splitlines()
    )


def read(path: str, place: str) -> str:
    try:
        with open(
            os.path.join(place, os.path.expanduser(path)),
            encoding="utf-8",
            errors="replace",
        ) as f:
            return f.read(1_000_000)
    except OSError:
        return ""


def ask_next_item(place: str, head: str | None, session: str | None) -> tuple[int, str]:
    """(`next-item claimed`'s exit code, what it said); it never answers 3 when it could not tell."""
    env = dict(os.environ)
    if session and not env.get("CLAUDE_CODE_SESSION_ID"):
        env["CLAUDE_CODE_SESSION_ID"] = session
    try:
        done = subprocess.run(
            [
                sys.executable,
                NEXT_ITEM,
                "-C",
                place,
                "claimed",
                *([head] if head else []),
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 1, f"next-item did not answer: {type(error).__name__}"
    said = (done.stdout.strip() or done.stderr.strip()).splitlines()
    return done.returncode, said[-1] if said else f"next-item exited {done.returncode}"


def moves(command: guardlib.Command) -> bool:
    """The command can change which commits the branch holds: a git command in MOVES, or `next-item claim`."""
    git = guardlib.git_args(command.argv)
    if git is not None:
        return git[0] in MOVES
    return os.path.basename((command.argv or [""])[0]) == "next-item"


def judge(
    command: guardlib.Command, text: str, session: str | None, moved: bool
) -> tuple[int, str] | None:
    """`next-item claimed`'s answer for one command, or None when the command is not one to check."""
    gh = guardlib.gh_args(command.argv)
    if gh is None or gh[0][:2] not in (["pr", "create"], ["pr", "new"]):
        return None
    args, repo = gh
    repo = repo or next(
        (a.split("=", 1)[1] for a in command.assignments if a.startswith("GH_REPO=")),
        None,
    )
    head, files, unchecked = options(args[2:])
    place = command.cwd
    if unchecked or not place:
        return None
    if not os.path.isdir(place):
        return 1, UNKNOWN
    root = guardlib.repo_root(place)
    if not root or guardlib.off(RULE, place):
        return None
    if repo and guardlib.slug("/" + repo) != guardlib.origin_repo(root):
        return None
    values = [word.partition("=")[2] for word in args]
    if escaped([text, *args, *values, *(read(path, place) for path in files)]):
        return None
    if moved:
        return 1, MOVED
    if head is not None:
        head = head.rsplit(":", 1)[-1]
        if not head or head.startswith("-") or set(head) & set("$`"):
            return 1, UNKNOWN
    return ask_next_item(place, head, session)


def decide(event: dict) -> dict | None:
    if event.get("tool_name") != "Bash":
        return None
    text = (event.get("tool_input") or {}).get("command")
    if not isinstance(text, str) or "gh" not in text:
        return None
    session = event.get("session_id")
    refused, unchecked, place = [], [], None
    moved = False
    for command in guardlib.bash_commands(event):
        found = judge(
            command, text, session if isinstance(session, str) else None, moved
        )
        moved = moved or moves(command)
        if found is None or found[0] == 0:
            continue
        if found[0] == NO:
            refused.append(found[1])
            place = place or command.cwd
        else:
            unchecked.append(found[1])
    if refused:
        try:
            note = guardlib.rule_setting(RULE, place)[1]
        except Exception:  # noqa: BLE001 - the note is extra; the refusal stands without it
            note = None
        reason = (
            f"work: refusing `gh pr create`: {'; '.join(refused)}. Run /work:start-work to claim the item first; or, for"
            f" work the owner asked for directly that has no todo entry, add this line to the pull request's body, given as `--body` text or a `--body-file` the hook can read: {ESCAPE}"
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{reason}{chr(10) + note if note else ''}\n{guardlib.label(RULE)}",
            }
        }
    if unchecked:
        warning = (
            f"work: could not check the pull request's branch for a claim mark ({'; '.join(unchecked)}); it opens"
            " unchecked. If it changes code, make sure /work:start-work claimed its item."
        )
        return {
            "systemMessage": warning,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": warning,
            },
        }
    return None


def main() -> None:
    try:
        answer = decide(json.load(sys.stdin))
    except Exception:  # noqa: BLE001 - bad input (even nesting deep enough to recurse) never fails the call
        return
    if answer:
        print(json.dumps(answer))


if __name__ == "__main__":
    main()
