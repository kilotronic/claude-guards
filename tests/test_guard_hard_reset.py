# SPDX-License-Identifier: MIT

"""The hard-reset guard's own suite, against real throwaway repositories."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "guard_hard_reset",
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "guards"
    / "hooks"
    / "guard-hard-reset.py",
)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _repo(path: Path, branch: str) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    path.mkdir()
    git("init", "-q", "-b", branch)
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (path / "f.txt").write_text("one\n")
    git("add", "f.txt")
    git("commit", "-q", "--no-verify", "-m", "one")
    return path


def _run(command: str, cwd: Path) -> tuple[int, str]:
    payload = json.dumps({"tool_input": {"command": command}, "cwd": str(cwd)})
    buf = io.StringIO()
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(payload)
        with redirect_stderr(buf):
            rc = guard.main()
    finally:
        sys.stdin = old_stdin
    return rc, buf.getvalue()


@pytest.mark.parametrize(
    "command",
    [
        "git reset --hard origin/main",
        "git reset -q --hard HEAD~1",
        "git fetch origin && git reset --hard origin/x",
    ],
)
def test_a_clean_feature_branch_resets(tmp_path: Path, command: str) -> None:
    repo = _repo(tmp_path / "r", "feature")
    assert _run(command, repo)[0] == 0


def test_untracked_files_do_not_block_a_reset(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "feature")
    (repo / "new.txt").write_text("kept by a hard reset\n")
    assert _run("git reset --hard HEAD", repo)[0] == 0


def test_uncommitted_changes_are_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "feature")
    (repo / "f.txt").write_text("edited\n")
    rc, err = _run("git reset --hard HEAD", repo)
    assert rc == 2
    assert "uncommitted" in err


@pytest.mark.parametrize("trunk", ["main", "master"])
def test_the_trunk_is_refused(tmp_path: Path, trunk: str) -> None:
    repo = _repo(tmp_path / "r", trunk)
    rc, err = _run(f"git reset --hard origin/{trunk}", repo)
    assert rc == 2
    assert f"`{trunk}`" in err


@pytest.mark.parametrize(
    "command",
    [
        "cd {main} && git reset --hard HEAD",
        "git -C {main} reset --hard HEAD",
        "cd {feature}; cd ../m && git reset --hard HEAD",
    ],
)
def test_the_target_is_where_the_command_moves_to(tmp_path: Path, command: str) -> None:
    main = _repo(tmp_path / "m", "main")
    feature = _repo(tmp_path / "f", "feature")
    assert _run(command.format(main=main, feature=feature), feature)[0] == 2


@pytest.mark.parametrize(
    "command",
    [
        "git reset --soft HEAD~1",
        "git reset HEAD f.txt",
        "git log --oneline | grep reset",
        "echo 'git reset' && git status --hard-to-say",
    ],
)
def test_other_commands_pass_even_on_main(tmp_path: Path, command: str) -> None:
    repo = _repo(tmp_path / "r", "main")
    (repo / "f.txt").write_text("edited\n")
    assert _run(command, repo)[0] == 0


def test_the_refusal_prints_no_refused_command(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "main")
    _, err = _run("git reset --hard HEAD", repo)
    for line in err.splitlines():
        assert guard.target_dir(line, str(repo)) is None, line
