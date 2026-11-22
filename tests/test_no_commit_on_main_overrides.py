# SPDX-License-Identifier: MIT

"""The three guards behind `no-commit-on-main` honour the overrides file for that id. The stash guard,
which still honours it too, is in test_no_stash_overrides.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from hookrun import bash, turn_off

RULE = "no-commit-on-main"


def _repo(path: Path, branch: str = "main") -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    path.mkdir()
    git("init", "-q", "-b", branch)
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (path / "f.txt").write_text("one\n")
    git("add", "f.txt")
    git("commit", "-q", "--no-verify", "-m", "one")
    # New staged content, so a `--no-verify` commit is one the hooks have not seen.
    (path / "g.txt").write_text("two\n")
    git("add", "g.txt")
    return path


CASES = [
    ("guard-commit-on-main.py", "git commit -m x"),
    ("guard-no-verify.py", "git commit --no-verify -m x"),
    ("guard-hard-reset.py", "git reset --hard HEAD"),
]


@pytest.mark.parametrize(("script", "command"), CASES)
def test_the_rule_off_with_a_reason_lets_the_command_through(
    tmp_path: Path, script: str, command: str
) -> None:
    repo = _repo(tmp_path / "r")
    assert bash(script, command, cwd=repo)[0] == 2
    turn_off(repo, RULE, "a repo that works on its trunk")
    assert bash(script, command, cwd=repo) == (0, "")


@pytest.mark.parametrize(("script", "command"), CASES)
def test_without_a_reason_it_stays_on_and_names_the_rule(
    tmp_path: Path, script: str, command: str
) -> None:
    repo = _repo(tmp_path / "r")
    turn_off(repo, RULE, reason=None)
    rc, err = bash(script, command, cwd=repo)
    assert rc == 2
    assert f"[guards:{RULE}]" in err


@pytest.mark.parametrize(("script", "command"), CASES)
def test_one_repos_override_does_not_cover_the_repo_the_command_acts_in(
    tmp_path: Path, script: str, command: str
) -> None:
    off = _repo(tmp_path / "off")
    other = _repo(tmp_path / "other")
    turn_off(off, RULE)
    assert bash(script, f"cd {other} && {command}", cwd=off)[0] == 2
    git_c = command.replace("git ", f"git -C {other} ", 1)
    assert bash(script, git_c, cwd=off)[0] == 2
