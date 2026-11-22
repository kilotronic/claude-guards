# SPDX-License-Identifier: MIT

"""main-checkout: the main checkout stays on its trunk; branches live in worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from hookrun import bash, turn_off

SCRIPT = "guard-branch-switch.py"


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


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "m"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "f.txt").write_text("one\n")
    (main / "sub").mkdir()
    (main / "sub" / "g.txt").write_text("g\n")
    _git(main, "add", ".")
    _git(main, "commit", "-q", "-m", "one")
    _git(main, "branch", "feature")
    _git(main, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
    linked = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(linked), "-b", "other")
    return main, linked


@pytest.mark.parametrize(
    "command",
    [
        "git switch feature",
        "git switch -c new",
        "git switch -",
        "git switch --detach HEAD~0",
        "git checkout feature",
        "git checkout -b new",
        "git checkout -B new origin/main",
        "git checkout -q feature",
        "git checkout HEAD~0",
        "git checkout --orphan gh-pages",
        "git fetch && git checkout feature",
        "cd sub && git checkout feature",
        "gh pr checkout 12",
        "git checkout remote-only",
        "git switch remote-only",
    ],
)
def test_switching_the_main_checkout_is_refused(
    repos: tuple[Path, Path], command: str
) -> None:
    main, _ = repos
    rc, err = bash(SCRIPT, command, cwd=main)
    assert rc == 2, err
    assert "[guards:main-checkout]" in err
    assert "git worktree add" in err


def test_git_dash_c_into_the_main_checkout_is_refused(repos: tuple[Path, Path]) -> None:
    main, linked = repos
    assert bash(SCRIPT, f"git -C {main} switch feature", cwd=linked)[0] == 2


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -- f.txt",
        "git checkout f.txt",
        "git checkout .",
        "git checkout HEAD -- f.txt",
        "git checkout HEAD f.txt",
        "git checkout -p f.txt",
        "git checkout sub/g.txt",
        "git switch main",
        "git checkout -B main origin/main",
        "git switch -C main HEAD",
        "git checkout main",
        "git status",
        "git commit -m 'git switch feature'",
        "echo git checkout -b x",
    ],
)
def test_restoring_files_and_returning_to_the_trunk_pass(
    repos: tuple[Path, Path], command: str
) -> None:
    main, _ = repos
    assert bash(SCRIPT, command, cwd=main) == (0, "")


@pytest.mark.parametrize(
    "command", ["git switch feature", "git checkout -b new", "gh pr checkout 12"]
)
def test_a_linked_worktree_switches_freely(
    repos: tuple[Path, Path], command: str
) -> None:
    _, linked = repos
    assert bash(SCRIPT, command, cwd=linked) == (0, "")


def test_outside_a_repo_passes(tmp_path: Path) -> None:
    assert bash(SCRIPT, "git switch feature", cwd=tmp_path) == (0, "")


@pytest.mark.parametrize(
    "command",
    [
        "cd sub && cd {wt} && cd - && git switch feature",
        "g=git; $g checkout feature",
        "g=git && ${{g}} switch feature",
    ],
)
def test_cd_dash_and_git_through_a_variable_are_refused(
    repos: tuple[Path, Path], command: str
) -> None:
    main, linked = repos
    rc, err = bash(SCRIPT, command.format(wt=linked), cwd=main)
    assert rc == 2, err
    assert "[guards:main-checkout]" in err


@pytest.mark.parametrize(
    "command",
    [
        "cd {main} && cd {wt} && cd - && cd - && git switch feature",
        "$g checkout feature",
    ],
)
def test_cd_dash_back_to_a_worktree_and_unknown_variables_pass(
    repos: tuple[Path, Path], command: str
) -> None:
    main, linked = repos
    assert bash(SCRIPT, command.format(main=main, wt=linked), cwd=main) == (0, "")


def test_main_edit_ok_still_opts_the_repo_out(repos: tuple[Path, Path]) -> None:
    main, _ = repos
    (main / ".main-edit-ok").write_text("")
    assert bash(SCRIPT, "git switch feature", cwd=main) == (0, "")


def test_the_overrides_file_turn_main_checkout_off_with_a_reason(
    repos: tuple[Path, Path],
) -> None:
    main, _ = repos
    turn_off(main, "main-checkout", "dotfiles repo")
    assert bash(SCRIPT, "git switch feature", cwd=main) == (0, "")


def test_one_repos_opt_out_does_not_cover_another(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    main, _ = repos
    other = tmp_path / "o"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _git(other, "commit", "-q", "--allow-empty", "-m", "x")
    turn_off(other, "main-checkout", "dotfiles", key="octo/other")
    command = f"git -C {other} checkout -b y && git -C {main} checkout -b x"
    assert bash(SCRIPT, command, cwd=tmp_path)[0] == 2


def test_cd_dash_with_no_earlier_cd_stays_in_the_main_checkout(
    repos: tuple[Path, Path],
) -> None:
    main, _ = repos
    assert bash(SCRIPT, "cd - && git checkout -b x", cwd=main)[0] == 2


@pytest.mark.parametrize(
    "command",
    [
        "git checkout feature --",
        "git checkout -m feature",
        "git checkout --merge feature",
        "git checkout --conflict=diff3 feature",
        "git checkout -bnew",
        "git checkout -Bnew",
        "git checkout --orphan=gh-pages",
        "git switch -cnew",
        "git switch -Cnew",
        "git switch --create=new",
        "git switch --force-create=new",
        "git switch --orphan=gh-pages",
        "git checkout --conflict diff3 feature",
        "git switch -- feature",
        "git switch -d main",
    ],
)
def test_more_spellings_of_a_switch_are_refused(
    repos: tuple[Path, Path], command: str
) -> None:
    main, _ = repos
    rc, err = bash(SCRIPT, command, cwd=main)
    assert rc == 2, (command, err)
    assert "[guards:main-checkout]" in err


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -m f.txt",
        "git checkout --merge -- f.txt",
        "git checkout feature -- f.txt",
        "git checkout -Bmain origin/main",
        "git switch --create=main",
        "git checkout main --",
        "git checkout --pathspec-from-file=list.txt feature",
        "git switch --conflict diff3 main",
        "git switch -t origin/main",
    ],
)
def test_more_restores_and_trunk_returns_pass(
    repos: tuple[Path, Path], command: str
) -> None:
    main, _ = repos
    assert bash(SCRIPT, command, cwd=main) == (0, ""), command
