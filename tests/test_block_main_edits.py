"""The main-checkout edit guard's own suite, run as Claude runs it: the script, a JSON payload on stdin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hookrun import turn_off

HOOK = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "guards"
    / "hooks"
    / "block-main-edits.py"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _repo(path: Path, branch: str = "main") -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", branch)
    (path / "f.txt").write_text("one\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "one")
    return path


def _run(project: Path, file_path: Path, tool: str = "Edit") -> tuple[int, str]:
    payload = json.dumps(
        {"tool_name": tool, "tool_input": {"file_path": str(file_path)}}
    )
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    done = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return done.returncode, done.stderr


@pytest.mark.parametrize("tool", ["Edit", "Write"])
@pytest.mark.parametrize("branch", ["main", "master"])
def test_an_edit_in_the_main_checkout_on_the_trunk_is_blocked(
    tmp_path: Path, tool: str, branch: str
) -> None:
    repo = _repo(tmp_path / "r", branch)
    for target in (repo / "f.txt", repo / "new.txt", repo / "sub" / "deep.py"):
        rc, err = _run(repo, target, tool)
        assert rc == 2, target
        assert "Blocked" in err and "git worktree add" in err


def test_an_edit_on_a_feature_branch_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "feature")
    assert _run(repo, repo / "f.txt") == (0, "")


def test_the_main_edit_ok_marker_opts_the_repo_out(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    (repo / ".main-edit-ok").touch()
    assert _run(repo, repo / "f.txt") == (0, "")


def test_an_edit_in_a_worktree_nested_inside_the_main_checkout_is_allowed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "r")
    _git(repo, "worktree", "add", "-q", str(repo / "wt"), "-b", "feat")
    assert _run(repo, repo / "wt" / "f.txt") == (0, "")
    # The nested worktree's sibling paths are still the main checkout.
    assert _run(repo, repo / "wtx.txt")[0] == 2


def test_an_edit_to_a_gitignored_file_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    (repo / ".gitignore").write_text("*.local\n")
    assert _run(repo, repo / "notes.local") == (0, "")


def test_an_edit_outside_the_repo_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    assert _run(repo, tmp_path / "elsewhere.txt") == (0, "")


def test_other_tools_and_bad_payloads_pass_through(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    assert _run(repo, repo / "f.txt", "Read") == (0, "")
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    done = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == 0


def test_the_overrides_file_turn_main_checkout_off_with_a_reason(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "r")
    turn_off(repo, "main-checkout", "dotfiles: main is the working branch")
    assert _run(repo, repo / "f.txt") == (0, "")


def test_an_override_without_a_reason_keeps_the_block(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "r")
    turn_off(repo, "main-checkout", reason=None)
    rc, err = _run(repo, repo / "f.txt")
    assert rc == 2
    assert "[guards:main-checkout]" in err
