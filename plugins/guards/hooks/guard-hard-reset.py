#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook — refuse a `git reset --hard` that would lose work.

A permission rule may allow `git reset` so an agent can reset its own branch
without a prompt; this guard keeps the two cases that cost something: a worktree
on `main`, and uncommitted tracked changes (untracked files survive a hard reset).
`no-commit-on-main` off in the overrides file turns it off for a repo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-commit-on-main"


def _hard_reset(argv: list[str]) -> list[str] | None:
    """The `git -C` directories of a `git reset --hard`, or None for another command."""
    git = guardlib.git_args(argv)
    if git and git[0] == "reset" and "--hard" in git[1]:
        return git[2]
    return None


# A commit's options that take the next word as their value, and those that take none.
COMMIT_SHORT_VALUE = set("mFCct")
COMMIT_SHORT_FLAG = set("aqvnse")
COMMIT_LONG_VALUE = {
    *("--message", "--file", "--reuse-message", "--reedit-message", "--fixup"),
    *("--squash", "--author", "--date", "--template", "--cleanup", "--trailer"),
}
COMMIT_LONG_FLAG = {
    *("--all", "--quiet", "--verbose", "--no-verify", "--verify", "--amend", "--edit"),
    *(
        "--no-edit",
        "--allow-empty",
        "--allow-empty-message",
        "--signoff",
        "--no-signoff",
    ),
    *("--gpg-sign", "--no-gpg-sign", "--reset-author", "--no-post-rewrite", "--status"),
    "--no-status",
}
ADD_FLAGS = {"-A", "--all", "-u", "--update", "-v", "--verbose", "-f", "--force"}
# Commands that may sit between the commit and the reset: they change no file in the worktree.
BETWEEN = {"status", "log", "rev-parse", "branch", "tag", "fetch"}
SAFE_SUBSTITUTIONS = {"cat", "echo", "printf"}


def _commit_all(args: list[str]) -> bool | None:
    """Whether a `git commit` with *args* takes in every tracked change (`-a`), False when it takes in only
    what is staged, or None when it may take less than that (a pathspec, `-p`, `--dry-run`, an unknown
    option)."""
    every = False
    words = iter(args)
    for word in words:
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if "=" in word and (name in COMMIT_LONG_VALUE or name == "--gpg-sign"):
                continue
            if name in COMMIT_LONG_VALUE:
                next(words, None)
            elif name not in COMMIT_LONG_FLAG:
                return None
            every = every or word == "--all"
        elif word.startswith("-") and len(word) > 1:
            for k, letter in enumerate(word[1:], 1):
                if letter in COMMIT_SHORT_VALUE:
                    if k == len(word) - 1:
                        next(words, None)
                    break
                if letter == "S":  # a signing key, attached if any
                    break
                if letter not in COMMIT_SHORT_FLAG:
                    return None
                every = every or letter == "a"
        else:
            return None
    return every


def _adds_all(args: list[str], here: str) -> bool:
    """Whether a `git add` with *args*, run in *here*, stages every tracked change in the worktree."""
    if any(w not in ADD_FLAGS and w != "." for w in args) or args.count(".") > 1:
        return False
    if "." in args:
        top = _git(here, "rev-parse", "--show-toplevel")
        return top is not None and os.path.realpath(top) == os.path.realpath(here)
    return bool(set(args) & {"-A", "--all", "-u", "--update"})


def _quiet(command: guardlib.Command) -> bool:
    """Whether *command* writes no file through a redirect."""
    return all(
        op.lstrip("0123456789").startswith("<")
        or target in ("/dev/null", "&-")
        or target.startswith("&")
        for op, target in command.redirects
    )


def _same(a: str | None, b: str) -> bool:
    return a is not None and os.path.realpath(a) == os.path.realpath(b)


def _starts_a_list(commands: list[guardlib.Command], k: int) -> bool:
    """Whether the command at *k* runs whenever the line reaches it: not after `||` or in a pipeline."""
    j = k - 1
    while j >= 0 and commands[j].substituted:  # its own `$(...)`, which run first
        j -= 1
    return j < 0 or commands[j].ended_by not in ("||", "|", "|&")


def saved_first(commands: list[guardlib.Command], r: int, place: str) -> bool:
    """Whether a commit earlier on the line, joined to the reset at *r* by `&&` alone, takes in every
    uncommitted change in *place*: `git commit -a`, or a commit after `git add -A`/`-u`/`.` (at the top).
    Anything it cannot tell is safe makes it False."""
    committed = False
    for k in range(r - 1, -1, -1):
        command = commands[k]
        if command.substituted:
            safe = command.argv[:1] and command.argv[0] in SAFE_SUBSTITUTIONS
            if safe and _quiet(command) and not command.loops:
                continue
            return False
        if command.ended_by != "&&" or command.loops or command.negated:
            return False
        git = guardlib.git_args(command.argv)
        if git is None or not _quiet(command):
            return False
        sub, args, dirs = git
        here = guardlib.where(command, dirs)
        plain = not command.assignments and command.words == command.argv
        plain = plain and not any(
            w.startswith(("--git-dir", "--work-tree")) for w in command.argv
        )
        if sub == "commit":
            every = _commit_all(args)
            if every is None or not plain or not _same(here, place):
                return False
            if every:
                return _starts_a_list(commands, k)
            committed = True
        elif sub == "add":
            if not (committed and plain and _same(here, place)):
                return False
            if not _adds_all(args, here):
                return False
            return _starts_a_list(commands, k)
        elif sub not in BETWEEN or any(w.startswith("--output") for w in args):
            return False
    return False


def resets(command: str, cwd: str) -> list[tuple[str, bool]]:
    """Each hard reset in *command*: the worktree it acts on (after `cd` and `git -C`), and whether a
    commit earlier on the line saves that worktree's changes first. Quoted text, a heredoc body or an
    `echo` never counts."""
    commands = guardlib.parse(command, cwd=cwd)
    out = []
    for k, parsed in enumerate(commands):
        dirs = _hard_reset(parsed.argv)
        if dirs is not None:
            place = guardlib.where(parsed, dirs) or cwd
            out.append((place, saved_first(commands, k, place)))
    return out


def target_dir(command: str, cwd: str) -> str | None:
    """The first worktree a hard reset in *command* would be refused in, else the first it acts on, or None
    for no hard reset."""
    places = resets(command, cwd)
    return next(
        (p for p, saved in places if refusal(p, saved)),
        places[0][0] if places else None,
    )


def _git(where: str, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", where, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def refusal(where: str, saved: bool = False) -> str | None:
    """Why a hard reset in *where* is refused, or None to allow it. *saved*: a commit on the same line,
    before it, takes in every uncommitted change."""
    trunk = _git(where, "rev-parse", "--abbrev-ref", "HEAD")
    if trunk in ("main", "master"):
        return f"this worktree is on `{trunk}`"
    if not saved and _git(where, "status", "--porcelain", "--untracked-files=no"):
        return "this worktree has uncommitted changes it would discard"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open: an unreadable payload is not evidence of a reset
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input"), dict):
        return 0
    command = payload["tool_input"].get("command")
    if not isinstance(command, str):
        return 0
    for where, saved in resets(command, payload.get("cwd") or os.getcwd()):
        reason = refusal(where, saved)
        if reason is None or guardlib.off(RULE, where):
            continue
        return guardlib.refuse(
            RULE,
            f"Refusing a hard reset in {where}: {reason}.\n\n"
            "Instead: commit the edits first (the commit stays in the reflog), before\n"
            "this line or in it with `git commit -a ... &&`; a clean feature branch\n"
            "resets freely. Never reset `main`:\n"
            "`git merge --ff-only origin/main` updates it.\n",
            where,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
