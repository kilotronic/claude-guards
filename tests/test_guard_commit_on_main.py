# SPDX-License-Identifier: MIT

"""The commit-on-main guard's own suite, against real throwaway repositories."""

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
    "guard_commit_on_main",
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "guards"
    / "hooks"
    / "guard-commit-on-main.py",
)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _repo(path: Path, branch: str) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "-C", str(path), "init", "-q", "-b", branch],
        check=True,
        capture_output=True,
    )
    return path


def _run(command: str, cwd: Path | str) -> tuple[int, str]:
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
        "git commit -m x",
        "git add -A && git commit -m x",
        "git commit --amend --no-edit",
        "git -c user.name=x commit -m x",
        "make format; git commit -am x",
    ],
)
def test_a_commit_on_main_is_refused(tmp_path: Path, command: str) -> None:
    repo = _repo(tmp_path / "r", "main")
    rc, err = _run(command, repo)
    assert rc == 2
    assert "worktree" in err


def test_a_commit_on_a_feature_branch_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "feature")
    assert _run("git commit -m x", repo)[0] == 0


@pytest.mark.parametrize(
    "command",
    [
        "cd {main} && git commit -m x",
        "git -C {main} commit -m x",
        "cd {feature}; cd ../m && git commit -m x",
    ],
)
def test_the_target_is_where_the_command_moves_to(tmp_path: Path, command: str) -> None:
    main = _repo(tmp_path / "m", "main")
    feature = _repo(tmp_path / "f", "feature")
    assert _run(command.format(main=main, feature=feature), feature)[0] == 2


def test_cd_to_a_feature_branch_from_main_passes(tmp_path: Path) -> None:
    main = _repo(tmp_path / "m", "main")
    feature = _repo(tmp_path / "f", "feature")
    assert _run(f"cd {feature} && git commit -m x", main)[0] == 0


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline | grep commit",
        "git commit-tree HEAD^{{tree}}",
        "echo 'git commit' > /dev/null",
        "git push origin main",
    ],
)
def test_other_commands_pass_even_on_main(tmp_path: Path, command: str) -> None:
    repo = _repo(tmp_path / "r", "main")
    assert _run(command, repo)[0] == 0


def test_git_errors_fail_open(tmp_path: Path) -> None:
    assert _run("git commit -m x", tmp_path)[0] == 0  # not a repository
    assert _run("git commit -m x", tmp_path / "missing")[0] == 0


def test_an_unreadable_payload_fails_open() -> None:
    old = sys.stdin
    try:
        sys.stdin = io.StringIO("not json")
        assert guard.main() == 0
    finally:
        sys.stdin = old


def test_the_refusal_prints_no_refused_command(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "main")
    _, err = _run("git commit -m x", repo)
    for line in err.splitlines():
        assert guard.target_dir(line, str(repo)) is None, line


def test_a_quoted_worktree_path_is_followed(tmp_path: Path) -> None:
    hub = _repo(tmp_path / "hub", "main")
    feature = _repo(tmp_path / "feature x", "feat")
    for command in (
        f'cd "{feature}" && git commit -m "x"',
        f"git -C '{feature}' commit -m x",
    ):
        assert _run(command, hub)[0] == 0, command
    assert _run(f'cd "{hub}" && git commit -m x', feature)[0] == 2


def test_a_heredoc_that_mentions_a_commit_is_no_commit(tmp_path: Path) -> None:
    hub = _repo(tmp_path / "hub", "main")
    command = "cat > /tmp/notes.txt <<'EOF'\nthen git commit -m done\nEOF"
    assert _run(command, hub)[0] == 0


@pytest.mark.parametrize(
    "opener",
    ["cat <<<foo", "echo $((1<<2))", 'echo "a<<EOF"'],
)
def test_no_unclosed_heredoc_hides_a_later_commit(tmp_path: Path, opener: str) -> None:
    hub = _repo(tmp_path / "hub", "main")
    assert _run(f"{opener}\ngit commit -m x", hub)[0] == 2


def test_a_commit_on_master_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "master")
    rc, err = _run("git commit -m x", repo)
    assert rc == 2
    assert "`master`" in err


def test_a_repo_that_works_on_its_trunk_opts_out(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", "main")
    (repo / ".main-edit-ok").touch()
    assert _run("git commit -m x", repo)[0] == 0


@pytest.mark.parametrize(
    "command",
    [
        "git -C {feature} commit -m y; git commit -m x",
        "(cd {feature} && ls); git commit -m x",
        "cd {feature} | cat; git commit -m x",
        "bash -c 'git commit -m wip'",
        "bash -lc 'git commit -m wip'",
    ],
)
def test_every_commit_on_main_is_refused(tmp_path: Path, command: str) -> None:
    main = _repo(tmp_path / "main", "main")
    feature = _repo(tmp_path / "feature", "feature")
    rc, _ = _run(command.format(feature=feature), main)
    assert rc == 2, command


@pytest.mark.parametrize(
    "command",
    [
        "echo git commit -m x",
        'git log --grep "git commit"',
        "cat <<'EOF'\ngit commit\nEOF",
    ],
)
def test_a_commit_that_is_only_text_passes_on_main(
    tmp_path: Path, command: str
) -> None:
    main = _repo(tmp_path / "main", "main")
    assert _run(command, main)[0] == 0, command
