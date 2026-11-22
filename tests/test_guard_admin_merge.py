# SPDX-License-Identifier: MIT

"""no-admin-merge: `gh pr merge --admin` skips branch protection; plain and `--auto` merges pass."""

from __future__ import annotations

import pytest
from hookrun import bash

SCRIPT = "guard-admin-merge.py"


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 12 --squash --admin",
        "gh pr merge --admin",
        "gh -R o/r pr merge 3 --admin --delete-branch",
        "gh pr merge 3 --admin=true",
        "git push && gh pr merge 3 --squash --admin",
        "bash -c 'gh pr merge 3 --admin'",
        "env GH_REPO=o/r gh pr merge 3 --admin",
    ],
)
def test_an_admin_merge_is_refused(command: str) -> None:
    rc, err = bash(SCRIPT, command)
    assert rc == 2, err
    assert "[guards:no-admin-merge]" in err
    assert "--auto" in err


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 12 --squash --delete-branch",
        "gh pr merge 12 --auto --squash",
        "gh pr view 12 --json state",
        "git commit -m 'never use gh pr merge --admin'",
        "echo gh pr merge --admin",
        "gh pr merge 12 --body 'no --admin here' --squash",
        "gh api repos/o/r/collaborators/x/permission --jq .permission  # admin",
    ],
)
def test_other_commands_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


def test_another_tool_passes() -> None:
    from hookrun import run_hook

    assert run_hook(
        SCRIPT, {"tool_name": "Read", "tool_input": {"file_path": "x"}}
    ) == (0, "")
    assert run_hook(SCRIPT, {}) == (0, "")
