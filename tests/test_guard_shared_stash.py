# SPDX-License-Identifier: MIT

"""The shared-stash guard's own suite.

Each spelling here is one an agent actually reaches for. The guard reads raw
text rather than tokenizing, so the interesting cases are the ones a tokenizer
would drop: wrappers, `-C`, glued separators, and a stash inside a chain.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "guard_shared_stash",
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "guards"
    / "hooks"
    / "guard-shared-stash.py",
)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.mark.parametrize(
    "command",
    [
        "git stash",
        "git stash push -u -m wip",
        "git stash save something",
        "git -C ../other-worktree stash",
        "timeout 5 git stash push",
        "make format && git stash",
        "git stash;",
    ],
)
def test_refuses_every_writing_spelling(command: str) -> None:
    assert guard.pushes_to_stash(command), command


@pytest.mark.parametrize(
    "command",
    [
        "git stash list",
        "git stash show -p",
        "git stash list | head -3",
        "git rebase origin/main",
        "git diff > /tmp/wip.patch",
        # The recovery recipe the refusal itself prints must not be refused.
        "git fsck --unreachable | grep commit",
        'git stash apply "$sha"',
        "sha=$(git rev-parse refs/stash)",
        "git add .claude/hooks/guard-shared-stash.py",
    ],
)
def test_allows_reading_and_the_alternatives(command: str) -> None:
    assert not guard.pushes_to_stash(command), command


def test_a_pop_is_refused_because_it_is_the_dangerous_half() -> None:
    # `pop` mutates the shared ref and is how another agent's payload is taken;
    # it is not in the read allowlist for that reason.
    assert guard.pushes_to_stash("git stash pop")


def test_refusal_does_not_print_a_blocked_stash_recipe() -> None:
    # PreToolUse follow-ups still go through this guard, so a printed
    # `git stash push` is unreachable — the agent that copies it is refused again.
    payload = json.dumps({"tool_input": {"command": "git stash"}})
    buf = io.StringIO()
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(payload)
        with redirect_stderr(buf):
            rc = guard.main()
    finally:
        sys.stdin = old_stdin
    assert rc == 2
    refusal = buf.getvalue()
    assert "git stash push" not in refusal
    for line in refusal.splitlines():
        stripped = line.strip()
        if stripped.startswith("git "):
            assert not guard.pushes_to_stash(stripped), stripped
