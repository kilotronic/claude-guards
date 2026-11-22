#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook (no-wholesale-ours-theirs): refuse taking one side of a conflict wholesale.

`git checkout`/`git restore --ours|--theirs`, `git merge|rebase|cherry-pick|pull|revert -X ours|theirs` and
the `-s ours` strategy throw away the other side's every change, conflicting or not. Each hunk is resolved for its intent.
A binary file has no hunks, so one side of it is allowed: `checkout`/`restore` naming only conflicted files that
git merges as binary (a NUL byte in a side's first 8000 bytes, or the `merge` attribute unset or `binary`).
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
# How far git looks for a NUL byte when it decides a file is binary.
BINARY_PROBE = 8000


def _git(place: str, *args: str) -> bytes | None:
    try:
        done = subprocess.run(
            ["git", "-C", place, *args], capture_output=True, timeout=10, check=False
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


def binary(place: str, path: str) -> bool:
    """Whether git merges the conflicted *path* at *place* as a binary file: no hunks to resolve."""
    if not os.path.isfile(os.path.join(place, path)):
        return False
    # `<mode> <blob> <stage>\t<path>` per side; one path only, so a pathspec naming several files counts as not binary.
    entries = [
        e.split(b"\t", 1)
        for e in (_git(place, "ls-files", "-u", "-z", "--", path) or b"").split(b"\0")
        if e
    ]
    if not entries or len({name for _, name in entries}) != 1:
        return False  # not conflicted, or several files
    blobs = [info.split()[1].decode() for info, _ in entries]
    attr = _git(place, "check-attr", "merge", "--", path) or b""
    if attr.rstrip().endswith((b": unset", b": binary")):
        return True
    for blob in blobs:
        content = _git(place, "cat-file", "blob", blob)
        if content is not None and b"\0" in content[:BINARY_PROBE]:
            return True
    return False


def binary_only(
    command: guardlib.Command, git: tuple[str, list[str], list[str]]
) -> bool:
    """Whether the command names only conflicted binary files, in a repo it can find."""
    _, args, dirs = git
    if any(w.startswith(("--git-dir", "--work-tree")) for w in command.argv):
        return False
    place = guardlib.where(command, dirs)
    named = paths(args)
    if not place or not named:
        return False
    return all(binary(place, p) for p in named)


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
