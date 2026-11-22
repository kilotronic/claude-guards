#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""PreToolUse:Bash hook (main-checkout): keep the main checkout on its trunk.

In a repo's main checkout (its first worktree: `git rev-parse --git-common-dir` is its `--git-dir`),
`git switch <branch>`, `git checkout <branch>`/`-b`, and `gh pr checkout` move the checkout every other
session reads off the trunk. Allowed: returning to `main`/`master`, restoring files (`git checkout --
<file>`, `git checkout <tree> <file>`, `-p`), and anything in a linked worktree. A `.main-edit-ok` file
at the repo root (as for block-main-edits.py) or `main-checkout` off in the overrides file opts a repo out.
Fails open on git errors.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardlib

RULE = "main-checkout"
LEGACY_OPT_OUT = ".main-edit-ok"
TRUNKS = {"main", "master"}
# Options that name the branch to create; a short one may carry it attached (`-bnew`), a long one after `=`.
CREATE = {
    "switch": {"-c", "-C", "--create", "--force-create", "--orphan"},
    "checkout": {"-b", "-B", "--orphan"},
}
# `git switch -t <start>`: the start point follows, and switching to it creates a local branch.
SWITCH_TRACK = {"-t", "--track"}
# File modes: with these, `git checkout` only restores files. (`-m`, `--merge` and `--conflict` also work
# with a branch, so the operands decide.)
FILE_MODE = {"-p", "--patch", "--ours", "--theirs", "--pathspec-from-file"}
# Options whose value is the next word when it isn't attached with `=`.
VALUE_OPTIONS = {"--conflict"}


def _git(place: str, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", place, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def is_main_checkout(place: str | None) -> bool:
    if not place or not os.path.isdir(place):
        return False
    out = _git(
        place, "rev-parse", "--is-bare-repository", "--git-dir", "--git-common-dir"
    )
    if out is None or len(out.splitlines()) != 3:
        return False
    bare, git_dir, common = out.splitlines()
    if bare == "true":
        return False
    return os.path.realpath(os.path.join(place, git_dir)) == os.path.realpath(
        os.path.join(place, common)
    )


def _created(sub: str, word: str, words) -> str | None:
    """The branch *word* (an option of `git <sub>`) creates, or None when it creates none."""
    flags = CREATE[sub]
    if word in flags:
        return next(words, "")
    flag, eq, value = word.partition("=")
    if eq and flag in flags and flag.startswith("--"):
        return value
    if len(word) > 2 and word[1] != "-" and word[:2] in flags:
        return word[2:]
    return None


def switches(sub: str, args: list[str], place: str) -> bool:
    """Whether `git <sub> <args>` moves the checkout to another branch or commit."""
    positional: list[str] = []
    paths: list[str] = []
    words = iter(args)
    for word in words:
        if word == "--":
            paths = list(words)
            if (
                sub == "switch"
            ):  # `git switch` takes no paths: what follows `--` is the branch
                positional += paths
                paths = []
            break
        created = _created(sub, word, words)
        if created is not None:
            # `git checkout -B main origin/main` resets the trunk and stays on it.
            return created not in TRUNKS
        if sub == "switch" and word in SWITCH_TRACK:
            return next(words, None) not in TRUNKS
        if sub == "checkout" and (
            word in FILE_MODE or word.startswith("--pathspec-from-file=")
        ):
            return False
        if word in VALUE_OPTIONS:
            next(words, None)
            continue
        if word in ("--detach", "-") or (sub == "switch" and word == "-d"):
            return True
        if not word.startswith("-"):
            positional.append(word)
    if sub == "switch":
        return bool(positional) and positional[0] not in TRUNKS
    # `git checkout <branch> --` switches; `<tree> -- <paths>` and `<tree> <paths>` restore files.
    if paths or len(positional) != 1 or positional[0] in TRUNKS:
        return False
    name = positional[0]
    if _git(place, "rev-parse", "--verify", "--quiet", name + "^{commit}") is not None:
        return True
    # git checks out a branch that exists only on a remote as a new local branch tracking it.
    remote = _git(
        place,
        "for-each-ref",
        "--count=1",
        "--format=%(refname)",
        f"refs/remotes/*/{name}",
    )
    return bool(remote)


def refusal(event: dict) -> str | None:
    for command in guardlib.bash_commands(event):
        git = guardlib.git_args(command.argv)
        gh = guardlib.gh_args(command.argv)
        if git and git[0] in ("switch", "checkout"):
            place = guardlib.where(command, git[2])
            hit = is_main_checkout(place) and switches(git[0], git[1], place)
            what = f"`git {git[0]}`"
        elif gh and gh[0][:2] == ["pr", "checkout"]:
            place = guardlib.where(command)
            hit = is_main_checkout(place)
            what = "`gh pr checkout`"
        else:
            continue
        root = guardlib.repo_root(place) if hit else None
        if root and os.path.exists(os.path.join(root, LEGACY_OPT_OUT)):
            continue
        if hit and guardlib.off(RULE, place):
            continue
        if hit:
            return (
                (
                    f"{what} to another branch in the main checkout ({place}): every other session\n"
                    "expects it on the trunk.\n\n"
                    "Instead: work in a linked worktree:\n"
                    "`git worktree add ../<repo>-wt-<topic> -b <branch> origin/main` (or an existing branch\n"
                    "without `-b`). Restoring a file (`git checkout -- <file>`) and `git switch main` still work here."
                ),
                place,
            )
    return None


if __name__ == "__main__":
    sys.exit(guardlib.run(RULE, refusal))
