# SPDX-License-Identifier: MIT

"""no-wholesale-ours-theirs: take each conflict hunk on its merits, not one side of the whole file."""

from __future__ import annotations

import pytest
from hookrun import bash, turn_off

SCRIPT = "guard-ours-theirs.py"


@pytest.mark.parametrize(
    "command",
    [
        "git checkout --ours src/app.py",
        "git checkout --theirs -- .",
        "git -C ../wt checkout --theirs a b",
        "git restore --ours f.txt",
        "git restore --source=HEAD --theirs f.txt",
        "git merge -X ours origin/main",
        "git merge -Xtheirs origin/main",
        "git rebase -X theirs origin/main",
        "git rebase --strategy-option=ours main",
        "git cherry-pick --strategy-option theirs abc123",
        "git pull -X theirs",
        "git fetch && git rebase -X ours origin/main",
        "git merge -s ours origin/feature",
        "git merge --strategy=ours origin/feature",
    ],
)
def test_wholesale_sides_are_refused(command: str) -> None:
    rc, err = bash(SCRIPT, command)
    assert rc == 2, err
    assert "[guards:no-wholesale-ours-theirs]" in err
    assert "hunk" in err


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -- f.txt",
        "git merge origin/main",
        "git rebase origin/main",
        "git merge -X patience origin/main",
        "git rebase -X ignore-space-change main",
        "git commit -m 'resolved by taking --theirs for the lockfile'",
        "echo git checkout --ours x",
        "git log --oneline ours",
        "git diff --ours",
        "git merge-file --ours a b c",
        "git merge -s recursive origin/main",
    ],
)
def test_other_commands_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


def test_the_settings_of_the_repo_the_command_acts_in_apply(tmp_path) -> None:
    import subprocess

    a, b = tmp_path / "a", tmp_path / "b"
    for repo in (a, b):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    turn_off(a, "no-wholesale-ours-theirs", "generated files")
    assert bash(SCRIPT, "git checkout --ours .", cwd=a) == (0, "")
    assert bash(SCRIPT, f"cd {b} && git checkout --ours .", cwd=a)[0] == 2
