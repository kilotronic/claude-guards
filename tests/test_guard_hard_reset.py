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


@pytest.mark.parametrize(
    "command",
    [
        'git commit -am "explain git reset --hard"',
        'echo "git reset --hard"',
    ],
)
def test_a_hard_reset_that_is_only_quoted_passes(tmp_path: Path, command: str) -> None:
    repo = _repo(tmp_path / "r", "feature")
    (repo / "f.txt").write_text("dirty\n")
    assert guard.target_dir(command, str(repo)) is None


def test_a_hard_reset_after_a_subshell_cd_acts_in_the_outer_directory(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "r", "main")
    command = "(cd /tmp && ls); git reset --hard"
    assert guard.target_dir(command, str(repo)) == str(repo)


def test_every_hard_reset_in_a_command_is_checked(tmp_path: Path) -> None:
    main = _repo(tmp_path / "main", "main")
    feature = _repo(tmp_path / "feature", "feature")
    command = f"git -C {feature} reset --hard && git reset --hard"
    assert guard.target_dir(command, str(main)) == str(main)


def _dirty(path: Path, branch: str = "feature") -> Path:
    repo = _repo(path, branch)
    (repo / "f.txt").write_text("edited\n")
    return repo


@pytest.mark.parametrize(
    "command",
    [
        "git commit -am wip && git reset --hard HEAD~1",
        "git commit -q -a -m wip && git reset -q --hard HEAD~1",
        "git commit --all --message=wip && git reset --hard HEAD~1",
        "git add -A && git commit -m wip && git reset --hard HEAD~1",
        "git add --all && git commit -qm wip && git log --oneline -1 && git reset --hard HEAD~1",
        "git add . && git commit -m 'a b' && git branch -f tmp HEAD~1 && git reset -q --hard tmp",
        "git add -u && git commit -m wip &&\n  git reset --hard origin/x",
        "bash -c 'git commit -am wip && git reset --hard HEAD~1'",
        "git commit -am \"$(cat <<'EOF'\nwip\nEOF\n)\" && git reset --hard HEAD~1",
        "cd sub && git commit -am wip >/dev/null 2>&1 && git reset --hard HEAD~1",
    ],
)
def test_a_reset_after_a_commit_that_saves_every_change_passes(
    tmp_path: Path, command: str
) -> None:
    repo = _dirty(tmp_path / "r")
    (repo / "sub").mkdir()
    assert _run(command, repo)[0] == 0


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m wip && git reset --hard HEAD~1",
        "git add g.txt && git commit -m wip && git reset --hard HEAD~1",
        "git add -A g.txt && git commit -m wip && git reset --hard HEAD~1",
        "git add -n . && git commit -m wip && git reset --hard HEAD~1",
        "git commit -am wip; git reset --hard HEAD~1",
        "git commit -am wip || git reset --hard HEAD~1",
        "git commit -am wip & git reset --hard HEAD~1",
        "git commit -am wip | cat && git reset --hard HEAD~1",
        "git add -A; git commit -m wip && git reset --hard HEAD~1",
        "git commit -am wip f.txt && git reset --hard HEAD~1",
        "git commit -a -m wip -- f.txt && git reset --hard HEAD~1",
        "git commit -p -m wip && git reset --hard HEAD~1",
        "git commit --dry-run -am wip && git reset --hard HEAD~1",
        "git commit -am wip && echo x > f.txt && git reset --hard HEAD~1",
        "git commit -am wip && git add new.txt && git reset --hard HEAD~1",
        "(git commit -am wip) && git reset --hard HEAD~1",
        "bash -c 'git commit -am wip' && git reset --hard HEAD~1",
        "! git commit -am wip && git reset --hard HEAD~1",
        "true || git commit -am wip && git reset --hard HEAD~1",
        "GIT_DIR=/elsewhere git commit -am wip && git reset --hard HEAD~1",
        "git --work-tree=/elsewhere commit -am wip && git reset --hard HEAD~1",
        "git commit -am wip && git log --output=f.txt && git reset --hard HEAD~1",
        'git commit -am wip && git tag "$(echo x > f.txt)" && git reset --hard HEAD~1',
        "git commit -am wip > f.txt && git reset --hard HEAD~1",
    ],
)
def test_a_reset_after_a_commit_that_may_leave_changes_is_refused(
    tmp_path: Path, command: str
) -> None:
    repo = _dirty(tmp_path / "r")
    (repo / "g.txt").write_text("two\n")
    subprocess.run(["git", "-C", str(repo), "add", "g.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "two", "--no-verify"],
        check=True,
        capture_output=True,
    )
    (repo / "g.txt").write_text("edited\n")
    rc, err = _run(command, repo)
    assert rc == 2, command
    assert "uncommitted" in err


def test_a_commit_in_another_repo_does_not_save_this_one(tmp_path: Path) -> None:
    repo = _dirty(tmp_path / "r")
    other = _dirty(tmp_path / "o")
    assert (
        _run(f"git -C {other} commit -am wip && git reset --hard HEAD~1", repo)[0] == 2
    )


def test_git_add_dot_in_a_subdirectory_does_not_save_the_rest(tmp_path: Path) -> None:
    repo = _dirty(tmp_path / "r")
    (repo / "sub").mkdir()
    command = "git add . && git commit -m wip && git reset --hard HEAD~1"
    assert _run(command, repo / "sub")[0] == 2


def test_a_commit_first_does_not_unlock_the_trunk(tmp_path: Path) -> None:
    repo = _dirty(tmp_path / "r", "main")
    rc, err = _run("git commit -am wip && git reset --hard HEAD~1", repo)
    assert rc == 2
    assert "`main`" in err
