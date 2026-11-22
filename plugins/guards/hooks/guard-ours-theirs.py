#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook (no-wholesale-ours-theirs): refuse taking one side of a conflict wholesale.

`git checkout`/`git restore --ours|--theirs`, `git merge|rebase|cherry-pick|pull|revert -X ours|theirs` and
the `-s ours` strategy throw away the other side's every change, conflicting or not. Each hunk is resolved for its intent.
A binary file has no hunks, so one side of it is allowed: `checkout`/`restore` naming only conflicted files that
git merged as binary (`binary_sides`), with no git variable or repo option in the call.
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
# Git's environment (GIT_DIR, GIT_WORK_TREE, GIT_CONFIG_*, ...) and global options can point it at another
# repo, index or work tree than the one the guard checks: a call that mentions any of them gets no exception.
GIT_ENVIRONMENT = "GIT_"
GLOBAL_REDIRECTS = ("-c", "--config-env", "--git-dir", "--work-tree", "--namespace")
# How far git's merge looks for a NUL byte when it decides a side is binary.
BINARY_PROBE = 8000
# The `merge` attribute values that leave git's own choice of text or binary merge in place.
BUILT_IN_MERGE = {"unspecified", "set", "text"}
# Each git call's limit: two calls stay well under the hook's own 10 s, and a timeout refuses.
GIT_TIMEOUT = 3


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
        if word.startswith("--pathspec-from-file"):
            return None
        if word in VALUE_OPTIONS:
            next(words, None)
        elif not word.startswith("-"):
            out.append(word)
    return out


def has_markers(path: str, size: int) -> bool:
    """Whether the file has a conflict marker of *size* at the start of a line: git wrote it a text merge."""
    marker = b"\n" + b"<" * size + b" "
    tail = b"\n"
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                if marker in tail + chunk:
                    return True
                tail = chunk[-len(marker) :]
    except OSError:
        return True
    return False


def binary_blobs(place: str, blobs: list[str]) -> set[str] | None:
    """The *blobs* with a NUL byte in their first BINARY_PROBE bytes, git's merge test; None on an error."""
    try:
        with subprocess.Popen(
            ["git", "-C", place, "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as git:
            git.stdin.write("".join(f"{b}\n" for b in blobs).encode())
            git.stdin.close()
            found = set()
            for blob in blobs:
                header = git.stdout.readline().split()
                if len(header) != 3 or header[1] != b"blob":
                    git.kill()
                    return None
                left = int(header[2]) + 1  # the content, then a newline
                head = git.stdout.read(min(left, BINARY_PROBE))
                left -= len(head)
                while left > 0:
                    chunk = git.stdout.read(min(left, 1 << 20))
                    if not chunk:
                        return None
                    left -= len(chunk)
                if b"\0" in head[: min(BINARY_PROBE, int(header[2]))]:
                    found.add(blob)
            return found
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def binary_sides(place: str, named: list[str]) -> bool:
    """Whether every path in *named* is one conflicted file that git merged as binary: no hunks to resolve.

    git merges a file as binary when its `merge` attribute is unset or `binary`, or, with git's own merge,
    when a side has a NUL byte in its first 8000 bytes. A file with a merge driver of its own, or whose working
    copy has conflict markers (the attributes changed after the merge), is not."""
    files = [
        os.path.normpath(os.path.relpath(p, place) if os.path.isabs(p) else p)
        for p in named
    ]
    if any(not os.path.isfile(os.path.join(place, f)) for f in files):
        return False
    listed = _git(place, "ls-files", "-u", "-z", "--", *files)
    if listed is None:
        return False
    # `<mode> <blob> <stage>\t<path>` per side.
    sides: dict[str, list[str]] = {}
    for entry in filter(None, listed.split(b"\0")):
        info, name = entry.decode(errors="replace").split("\t", 1)
        sides.setdefault(name, []).append(info.split()[1])
    if set(sides) != set(files):
        return False  # one isn't conflicted, or a pattern matched other files
    attrs = _git(
        place, "check-attr", "-z", "merge", "conflict-marker-size", "--", *files
    )
    if attrs is None:
        return False
    fields = attrs.decode(errors="replace").split("\0")
    attr = {
        (fields[i], fields[i + 1]): fields[i + 2] for i in range(0, len(fields) - 2, 3)
    }
    size = {f: attr.get((f, "conflict-marker-size"), "") for f in files}
    if any(
        has_markers(os.path.join(place, f), int(n) if n.isdigit() else 7)
        for f, n in size.items()
    ):
        return False
    merge = {f: attr.get((f, "merge"), "unspecified") for f in files}
    if any(v not in BUILT_IN_MERGE | {"unset", "binary"} for v in merge.values()):
        return False  # a merge driver of its own
    by_content = [f for f in files if merge[f] in BUILT_IN_MERGE]
    blobs = sorted({b for f in by_content for b in sides[f]})
    binary = binary_blobs(place, blobs) if blobs else set()
    if binary is None:
        return False
    return all(binary.intersection(sides[f]) for f in by_content)


def binary_only(
    command: guardlib.Command, git: tuple[str, list[str], list[str]], text: str
) -> bool:
    """Whether the command names only conflicted binary files, in a repo the guard can see. *text* is the
    whole call: a git variable set or exported anywhere in it counts."""
    if GIT_ENVIRONMENT in text:
        return False
    _, args, dirs = git
    options = command.argv[1 : len(command.argv) - len(args) - 1]
    if any(w.startswith(GLOBAL_REDIRECTS) for w in options):
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


def call_text(event: dict) -> str:
    command = (event.get("tool_input") or {}).get("command")
    return command if isinstance(command, str) else ""


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
            if side and binary_only(command, git, call_text(event)):
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
