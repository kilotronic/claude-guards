# SPDX-License-Identifier: MIT

"""no-wholesale-ours-theirs: take each conflict hunk on its merits, not one side of the whole file."""

from __future__ import annotations

import subprocess
from pathlib import Path

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


def _conflicted(path: Path) -> Path:
    """A repo mid-merge with a conflict in a binary font, a text file, and a text file marked binary."""

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    def write(side: bytes) -> None:
        (path / "fonts").mkdir(exist_ok=True)
        (path / "fonts" / "a.woff2").write_bytes(b"wOF2\x00\x01" + side)
        (path / "notes.txt").write_text(f"line {side.decode()}\n")
        (path / "data.bin").write_text(f"plain {side.decode()}\n")

    path.mkdir()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (path / ".gitattributes").write_text("data.bin binary\n")
    write(b"base")
    git("add", ".")
    git("commit", "-q", "--no-verify", "-m", "base")
    git("checkout", "-q", "-b", "other")
    write(b"theirs")
    git("commit", "-q", "--no-verify", "-am", "theirs")
    git("checkout", "-q", "main")
    write(b"ours")
    git("commit", "-q", "--no-verify", "-am", "ours")
    subprocess.run(
        ["git", "-C", str(path), "merge", "other"], check=False, capture_output=True
    )
    return path


@pytest.mark.parametrize(
    "command",
    [
        "git checkout --theirs fonts/a.woff2",
        "git checkout --theirs -- fonts/a.woff2",
        "git restore --ours fonts/a.woff2",
        "git restore --source=HEAD --theirs fonts/a.woff2",
        "git checkout --ours data.bin fonts/a.woff2",
        "cd fonts && git checkout --theirs a.woff2",
    ],
)
def test_one_side_of_a_binary_file_passes(tmp_path: Path, command: str) -> None:
    repo = _conflicted(tmp_path / "r")
    assert bash(SCRIPT, command, cwd=repo) == (0, "")


def test_a_binary_file_in_another_repo_by_c_passes(tmp_path: Path) -> None:
    repo = _conflicted(tmp_path / "r")
    command = f"git -C {repo} checkout --theirs fonts/a.woff2"
    assert bash(SCRIPT, command, cwd=tmp_path) == (0, "")


@pytest.mark.parametrize(
    "command",
    [
        "git checkout --theirs notes.txt",
        "git checkout --theirs fonts/a.woff2 notes.txt",
        "git checkout --theirs -- .",
        "git checkout --theirs fonts",
        "git checkout --theirs",
        "git checkout --theirs missing.woff2",
        "git checkout --theirs '*.woff2'",
        "git restore --ours -s fonts/a.woff2 notes.txt",
        "git merge -X theirs other",
    ],
)
def test_a_text_file_or_anything_wider_is_still_refused(
    tmp_path: Path, command: str
) -> None:
    repo = _conflicted(tmp_path / "r")
    assert bash(SCRIPT, command, cwd=repo)[0] == 2


def test_a_dot_slash_path_to_a_binary_file_passes(tmp_path: Path) -> None:
    repo = _conflicted(tmp_path / "r")
    assert bash(SCRIPT, "git checkout --theirs ./fonts/a.woff2", cwd=repo) == (0, "")


@pytest.mark.parametrize("prefix", ["", "env "])
def test_a_repo_named_by_the_environment_is_refused(
    tmp_path: Path, prefix: str
) -> None:
    # Here notes.txt is binary by attribute; in the repo the variables name it is a text conflict.
    here = _conflicted(tmp_path / "here")
    (here / ".gitattributes").write_text("data.bin binary\nnotes.txt binary\n")
    there = _conflicted(tmp_path / "there")
    command = f"{prefix}GIT_DIR={there}/.git GIT_WORK_TREE={there} git checkout --theirs notes.txt"
    assert bash(SCRIPT, command, cwd=here)[0] == 2


def test_a_text_conflict_marked_binary_after_the_merge_is_refused(
    tmp_path: Path,
) -> None:
    repo = _conflicted(tmp_path / "r")
    (repo / ".git" / "info" / "attributes").write_text("notes.txt merge=binary\n")
    assert bash(SCRIPT, "git checkout --theirs notes.txt", cwd=repo)[0] == 2


def test_a_binary_file_with_a_merge_driver_of_its_own_is_refused(
    tmp_path: Path,
) -> None:
    repo = _conflicted(tmp_path / "r")
    (repo / ".git" / "info" / "attributes").write_text("*.woff2 merge=fonts\n")
    assert bash(SCRIPT, "git checkout --theirs fonts/a.woff2", cwd=repo)[0] == 2


@pytest.mark.parametrize(
    "setup",
    [
        "export GIT_DIR={there}/.git GIT_WORK_TREE={there}; ",
        "GIT_DIR={there}/.git; GIT_WORK_TREE={there}; export GIT_DIR GIT_WORK_TREE; ",
        "git -c core.worktree={there} ",
        "git --config-env=core.worktree=WT ",
    ],
)
def test_a_repo_named_earlier_in_the_call_or_by_config_is_refused(
    tmp_path: Path, setup: str
) -> None:
    # Here notes.txt passes as binary (marked so, its markers gone); there it is a text conflict.
    here = _conflicted(tmp_path / "here")
    (here / ".gitattributes").write_text("data.bin binary\nnotes.txt binary\n")
    (here / "notes.txt").write_text("resolved\n")
    assert bash(SCRIPT, "git checkout --theirs notes.txt", cwd=here) == (0, "")
    there = _conflicted(tmp_path / "there")
    prefix = setup.format(there=there)
    git = "" if prefix.startswith("git ") else "git "
    command = f"{prefix}{git}checkout --theirs notes.txt"
    assert bash(SCRIPT, command, cwd=here)[0] == 2


def _text_merge_of_a_late_nul(path: Path) -> Path:
    """A file git merges as text (its NUL is past byte 8000) with long conflict markers."""

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    def write(side: str) -> None:
        (path / "f.dat").write_bytes(b"a\n" * 4500 + b"\0\n" + f"{side}\n".encode())

    path.mkdir()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (path / ".gitattributes").write_text("f.dat conflict-marker-size=12\n")
    write("base")
    git("add", ".")
    git("commit", "-q", "--no-verify", "-m", "base")
    git("checkout", "-q", "-b", "other")
    write("theirs")
    git("commit", "-q", "--no-verify", "-am", "theirs")
    git("checkout", "-q", "main")
    write("ours")
    git("commit", "-q", "--no-verify", "-am", "ours")
    subprocess.run(
        ["git", "-C", str(path), "merge", "other"], check=False, capture_output=True
    )
    return path


def test_a_file_git_merged_as_text_is_refused_though_it_holds_a_nul(
    tmp_path: Path,
) -> None:
    repo = _text_merge_of_a_late_nul(tmp_path / "r")
    assert b"<" * 12 in (repo / "f.dat").read_bytes()
    assert bash(SCRIPT, "git checkout --theirs f.dat", cwd=repo)[0] == 2


def test_a_binary_file_by_its_absolute_path_passes(tmp_path: Path) -> None:
    repo = _conflicted(tmp_path / "r")
    command = f"git checkout --theirs {repo}/fonts/a.woff2"
    assert bash(SCRIPT, command, cwd=repo) == (0, "")


def test_paths_read_from_a_file_are_refused(tmp_path: Path) -> None:
    repo = _conflicted(tmp_path / "r")
    (repo / "list").write_text("notes.txt\n")
    command = "git restore --ours --pathspec-from-file list fonts/a.woff2"
    assert bash(SCRIPT, command, cwd=repo)[0] == 2
