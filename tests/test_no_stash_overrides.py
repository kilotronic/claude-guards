# SPDX-License-Identifier: MIT

"""The stash guard's rule is `no-stash`; `no-commit-on-main`, which used to cover it, still turns it off."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from hookrun import bash, turn_off

SCRIPT = "guard-shared-stash.py"
RULE = "no-stash"
OLD = "no-commit-on-main"
STASH = "git " + "stash"


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


def _lock(*rules: str) -> None:
    path = Path(os.environ["CLAUDE_PLUGIN_ROOT"]) / "overrides.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data["_locked"] = list(rules)
    path.write_text(json.dumps(data))


def test_a_stash_is_refused_under_its_own_rule(tmp_path: Path) -> None:
    rc, err = bash(SCRIPT, STASH, cwd=_repo(tmp_path / "r"))
    assert rc == 2
    assert f"[guards:{RULE}]" in err
    assert OLD not in err


@pytest.mark.parametrize("rule", [RULE, OLD])
def test_either_id_off_with_a_reason_lets_a_stash_through(
    tmp_path: Path, rule: str
) -> None:
    repo = _repo(tmp_path / "r")
    turn_off(repo, rule, "a repo with one agent")
    assert bash(SCRIPT, STASH, cwd=repo) == (0, "")


@pytest.mark.parametrize("rule", [RULE, OLD])
def test_without_a_reason_it_stays_on(tmp_path: Path, rule: str) -> None:
    repo = _repo(tmp_path / "r")
    turn_off(repo, rule, reason=None)
    assert bash(SCRIPT, STASH, cwd=repo)[0] == 2


@pytest.mark.parametrize("rule", [RULE, OLD])
def test_a_locked_no_stash_stays_on_whichever_id_is_off(
    tmp_path: Path, rule: str
) -> None:
    repo = _repo(tmp_path / "r")
    turn_off(repo, rule)
    _lock(RULE)
    rc, err = bash(SCRIPT, STASH, cwd=repo)
    assert rc == 2
    assert f"[guards:{RULE}]" in err


def test_one_repos_override_does_not_cover_another(tmp_path: Path) -> None:
    off = _repo(tmp_path / "off")
    other = _repo(tmp_path / "other")
    turn_off(off, RULE)
    assert bash(SCRIPT, f"cd {other} && {STASH}", cwd=off)[0] == 2
    assert bash(SCRIPT, f"git -C {other} stash", cwd=off)[0] == 2


@pytest.mark.parametrize("rule", [RULE, OLD])
def test_a_stash_aimed_at_another_repo_by_git_dir_is_still_refused(
    tmp_path: Path, rule: str
) -> None:
    off = _repo(tmp_path / "off")
    other = _repo(tmp_path / "other")
    turn_off(off, rule)
    command = f"git --git-dir={other}/.git --work-tree={other} stash"
    assert bash(SCRIPT, command, cwd=off)[0] == 2
