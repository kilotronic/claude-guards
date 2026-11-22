#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook (no-wholesale-ours-theirs): refuse taking one side of a conflict wholesale.

`git checkout`/`git restore --ours|--theirs`, `git merge|rebase|cherry-pick|pull|revert -X ours|theirs` and
the `-s ours` strategy throw away the other side's every change, conflicting or not. Each hunk is resolved for its intent.
A binary file has no hunks, so one side of it is allowed: `checkout`/`restore` naming only conflicted files that
git merged as binary (`binary_sides`).
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "no-wholesale-ours-theirs"
SIDES = {"ours", "theirs"}
FILE_LEVEL = {"checkout", "restore"}
STRATEGY = {"merge", "rebase", "cherry-pick", "pull", "revert"}
# Options of `checkout`/`restore` that take the next word as their value.
VALUE_OPTIONS = {"-s", "--source", "--conflict", "--pathspec-from-file"}
# Variables that point git at another repo or index than the one the guard would check.
REPO_VARIABLES = ("GIT_DIR=", "GIT_WORK_TREE=", "GIT_INDEX_FILE=", "GIT_COMMON_DIR=")
# The `merge` attribute values that leave git's own choice of text or binary merge in place.
BUILT_IN_MERGE = {"unspecified", "set", "text"}
# Each git call's limit: two calls stay well under the hook's own 10 s, and a timeout refuses.
GIT_TIMEOUT = 3
MARKER = b"<<<<<<< "


def _git(place: str, *args: str) -> bytes | None:
    try:
        done = subprocess.run(
            ["git", "-C", place, *args],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def paths(args: list[str]) -> list[str] | None:
    """The paths a `checkout`/`restore` names, or None when it reads them from elsewhere."""
    if "--" in args:
        return args[args.index("--") + 1 :]
    out, words = [], iter(args)
    for word in words:
        if word in VALUE_OPTIONS:
            next(words, None)
        elif word.startswith("--pathspec-from-file"):
            return None
        elif not word.startswith("-"):
            out.append(word)
    return out


def has_markers(path: str) -> bool:
    """Whether the file has a conflict marker at the start of a line: git wrote it a text merge."""
    tail = b"\n"
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                if b"\n" + MARKER in tail + chunk:
                    return True
                tail = chunk[-len(MARKER) :]
    except OSError:
        return True
    return False


def binary_sides(place: str, named: list[str]) -> bool:
    """Whether every path in *named* is one conflicted file that git merged as binary: no hunks to resolve.

    git merges a file as binary when its `merge` attribute is unset or `binary`, or, with git's own merge,
    when a side looks binary (`ls-files --eol` reports `i/-text`). A file with a merge driver of its own, or
    whose working copy has conflict markers (the attributes changed after the merge), is not."""
    files = [os.path.normpath(p) for p in named]
    if any(not os.path.isfile(os.path.join(place, f)) for f in files):
        return False
    if any(has_markers(os.path.join(place, f)) for f in files):
        return False
    listed = _git(place, "ls-files", "-u", "--eol", "-z", "--", *files)
    attrs = _git(place, "check-attr", "-z", "merge", "--", *files)
    if listed is None or attrs is None:
        return False
    # `<mode> <blob> <stage>\t<eol info>\t<path>` per side.
    looks_binary: dict[str, bool] = {}
    for entry in filter(None, listed.split(b"\0")):
        _, eol, name = entry.decode(errors="replace").split("\t", 2)
        side = eol.split()[0] == "i/-text"
        looks_binary[name] = looks_binary.get(name, False) or side
    if set(looks_binary) != set(files):
        return False  # one isn't conflicted, or a pattern matched other files
    fields = attrs.decode(errors="replace").split("\0")
    merge = {fields[i]: fields[i + 2] for i in range(0, len(fields) - 2, 3)}
    for name in files:
        value = merge.get(name, "unspecified")
        if value in ("unset", "binary"):
            continue
        if value not in BUILT_IN_MERGE or not looks_binary[name]:
            return False
    return True


def binary_only(
    command: guardlib.Command, git: tuple[str, list[str], list[str]]
) -> bool:
    """Whether the command names only conflicted binary files, in a repo the guard can see."""
    _, args, dirs = git
    if any(w.startswith(("--git-dir", "--work-tree")) for w in command.argv):
        return False
    if any(w.startswith(REPO_VARIABLES) for w in command.assignments + command.words):
        return False
    place = guardlib.where(command, dirs)
    named = paths(args)
    if not place or not named:
        return False
    return binary_sides(place, named)


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
            if side and binary_only(command, git):
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
