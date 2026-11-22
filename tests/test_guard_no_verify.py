# SPDX-License-Identifier: MIT

"""The no-verify guard's own suite.

The rule it encodes is CLAUDE.md § Commits: never bypass pre-commit for NEW
changes; fine when squashing, rewording or amending commits that already
passed. Flags cannot tell those apart — `--amend` with staged fixups is new
content, and the sanctioned squash recipe is a plain `git commit --no-verify`
after a `git reset --soft`. The index TREE can: it equals HEAD's on a reword and
ORIG_HEAD's on a squash, and neither on anything pre-commit has not seen.

The first draft read the line with a regex and its /code-review found five
bypasses in one pass — `git add … && git commit --no-verify` (the index is
inspected before the add runs), a `;` inside `-m` truncating the capture, a
bare pathspec, `cd X;` not being followed, and a heredoc that merely MENTIONED
the recipe being refused. The second pass found the next layer: newline-joined
commands, `bash -c`, `env VAR=`, `--no-veri`, redirections read as pathspecs.
Each is a named case below, and that is where the guard stops: it refuses what
an agent actually types and fails open on the rest.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "guards"
    / "hooks"
    / "guard-no-verify.py"
)
_spec = importlib.util.spec_from_file_location("guard_no_verify", HOOK)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

BLOCK = 2
ALLOW = 0


def _clean_env() -> dict[str, str]:
    # GIT_* stripped for the reason test_guard_code_review_scope.py documents:
    # pre-commit runs this suite inside `git commit`, and a leaked GIT_INDEX_FILE
    # retargets every git call below at the REAL repository's index.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    return env


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, env=_clean_env()
    )


def run_hook(command: str, cwd: Path) -> int:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        timeout=60,
        env=_clean_env(),
    )
    return result.returncode


def _init_repo(work: Path) -> Path:
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(work)],
        check=True,
        capture_output=True,
        env=_clean_env(),
    )
    (work / "a.txt").write_text("a\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    _git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    (work / "b.txt").write_text("b\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feature")
    return work


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Two commits on a branch that is one ahead of origin/main."""
    return _init_repo(tmp_path / "work")


def _bypass(command: str) -> str | None:
    for parsed in guard.guardlib.parse(command):
        git = guard._Git.parse(parsed)
        if git and git.bypass():
            return git.bypass()
    return None


# ── Spellings ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("git commit --no-verify -m x", "--no-verify"),
        ("git commit -n -m x", "-n"),
        ("git commit -nm x", "-nm"),
        ("git commit -m x --no-verify", "--no-verify"),
        ("git -C ../other commit --no-verify", "--no-verify"),
        ("make format && git commit --no-verify -m x", "--no-verify"),
        ("SKIP=pyright git commit -m x", "SKIP="),
        ("git -c core.hooksPath=/dev/null commit -m x", "core.hooksPath"),
        ('git commit -m "fix: a; b" --no-verify', "--no-verify"),
        ('git commit -m "one && two | three" --no-verify', "--no-verify"),
        ("timeout 60 git commit --no-verify -m x", "--no-verify"),
        ("env GIT_EDITOR=true git commit --no-verify -m x", "--no-verify"),
        ("nice -n 5 git commit --no-verify -m x", "--no-verify"),
        ("env SKIP=pyright git commit -m x", "SKIP="),
        ("git commit --no-veri -m x", "--no-veri"),
        ("git commit --no-verify -m x > /dev/null 2>&1", "--no-verify"),
    ],
)
def test_names_every_bypass_spelling(command: str, kind: str) -> None:
    assert _bypass(command) == kind


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x",
        "git commit -am x",
        "git commit --amend --no-edit",
        'git commit -m "use --no-verify never"',
        "git commit -m '-n is not a flag here'",
        "git log -n 3",
        "git config --get core.hooksPath",
        "git status",
        "sed -n 's/git commit --no-verify -m/X/' file",
        # A bypass-shaped prefix on a NON-commit is nothing to refuse.
        "SKIP=pyright git status",
        "git -c core.hooksPath=/dev/null log -5",
        "cat > doc.md <<'EOF'\ngit commit --amend --no-edit --no-verify\nEOF",
    ],
)
def test_ignores_commits_that_run_the_hooks_and_mere_mentions(command: str) -> None:
    assert _bypass(command) is None


# ── The content rule ─────────────────────────────────────────────────────────


def test_allows_a_reword_with_nothing_staged(repo: Path) -> None:
    assert run_hook("git commit --amend --no-edit --no-verify", repo) == ALLOW


def test_allows_the_sanctioned_squash_recipe(repo: Path) -> None:
    _git(repo, "reset", "--soft", "origin/main")
    assert run_hook('git commit --no-verify -m "squashed"', repo) == ALLOW


def test_allows_the_squash_recipe_on_one_line(repo: Path) -> None:
    command = 'git reset --soft origin/main && git commit --no-verify -m "squashed"'
    assert run_hook(command, repo) == ALLOW


def test_refuses_new_staged_content(repo: Path) -> None:
    (repo / "c.txt").write_text("c\n")
    _git(repo, "add", "c.txt")
    assert run_hook('git commit --no-verify -m "new"', repo) == BLOCK


def test_refuses_an_amend_that_folds_in_staged_fixups(repo: Path) -> None:
    (repo / "b.txt").write_text("b2\n")
    _git(repo, "add", "b.txt")
    assert run_hook("git commit --amend --no-edit --no-verify", repo) == BLOCK


@pytest.mark.parametrize(
    "command",
    [
        "git add -A && git commit --no-verify -m x",
        "git add c.txt; git commit --no-verify -m x",
        "git rm --cached a.txt && git commit -n -m x",
        "git reset --mixed HEAD~1 && git commit --no-verify -m x",
        # Multi-line tool calls arrive newline-joined; a heredoc before them
        # must not swallow the add into its own segment.
        "git add c.txt\ngit commit --no-verify -m x",
        "cat > d.txt <<'EOF'\nstuff\nEOF\ngit add c.txt && git commit --no-verify -m y",
        "bash -c 'git add c.txt && git commit --no-verify -m y'",
        'sh -c "git add c.txt; git commit -n -m y"',
        "eval 'git add c.txt && git commit --no-verify -m y'",
        "export SKIP=ruff; git add c.txt; git commit -m x",
        "git config core.hooksPath /dev/null && git add c.txt && git commit -m x",
    ],
)
def test_refuses_an_index_write_in_the_same_command(repo: Path, command: str) -> None:
    (repo / "c.txt").write_text("c\n")
    assert run_hook(command, repo) == BLOCK


@pytest.mark.parametrize(
    "command",
    [
        "git commit -a --no-verify -m x",
        "git commit --no-verify -m x b.txt",
        "git commit --no-verify -m x -- b.txt",
        "git commit --only --no-verify -m x",
    ],
)
def test_refuses_content_the_index_cannot_vouch_for(repo: Path, command: str) -> None:
    (repo / "b.txt").write_text("b2\n")
    assert run_hook(command, repo) == BLOCK


@pytest.mark.parametrize(
    "command",
    [
        "git commit --amend --no-edit --no-verify > /dev/null",
        "git commit --amend --no-edit --no-verify 2>&1 | tail -1",
        'git commit --amend --no-edit --no-verify --trailer "Co-Authored-By: x"',
        'git commit --amend --no-edit --no-verify -m "multi\nline\nmessage"',
    ],
)
def test_a_redirected_or_trailered_reword_is_still_a_reword(
    repo: Path, command: str
) -> None:
    assert run_hook(command, repo) == ALLOW


@pytest.fixture
def aliased(repo: Path) -> Path:
    """The repo with the commit aliases a developer's global gitconfig carries."""
    _git(repo, "config", "alias.cm", "commit -m")
    _git(repo, "config", "alias.amend", "commit --amend --no-edit")
    _git(repo, "config", "alias.st", "status")
    _git(repo, "config", "alias.a", "add")
    _git(repo, "config", "alias.c", "cm")  # a chain: c -> cm -> commit -m
    return repo


@pytest.mark.parametrize(
    "command",
    [
        "SKIP=pyright git cm x",
        "git -c core.hooksPath=/dev/null cm x",
        "git amend --no-verify",
        "git c x --no-verify",  # chained alias (c -> cm -> commit -m)
        "git -c alias.k=commit k --no-verify -m x",  # inline alias
        "git -c core.hooksPath=/dev/null -c alias.k=commit k -m x",
    ],
)
def test_a_commit_alias_is_judged_as_the_commit_it_expands_to(
    aliased: Path, command: str
) -> None:
    (aliased / "c.txt").write_text("c\n")
    _git(aliased, "add", "c.txt")
    assert run_hook(command, aliased) == BLOCK


def test_an_index_writer_alias_in_the_same_command_counts(aliased: Path) -> None:
    (aliased / "c.txt").write_text("c\n")
    assert run_hook("git a c.txt && git commit --no-verify -m x", aliased) == BLOCK


def test_an_aliased_reword_with_nothing_staged_is_still_allowed(aliased: Path) -> None:
    assert run_hook("git amend --no-verify", aliased) == ALLOW


def test_a_non_commit_alias_with_a_bypass_prefix_is_nothing_to_refuse(
    aliased: Path,
) -> None:
    (aliased / "c.txt").write_text("c\n")
    _git(aliased, "add", "c.txt")
    assert run_hook("SKIP=pyright git st", aliased) == ALLOW


def test_fails_open_outside_a_repository(tmp_path: Path) -> None:
    assert run_hook("git commit --no-verify -m x", tmp_path) == ALLOW


@pytest.mark.parametrize(
    "shape",
    [
        "cd {repo} && git commit --no-verify -m new",
        "cd {repo}; git commit --no-verify -m new",
        "(cd {repo} && git commit --no-verify -m new)",
        "git -C {repo} commit --no-verify -m new",
    ],
)
def test_follows_every_cd_shape_to_the_worktree(
    repo: Path, tmp_path: Path, shape: str
) -> None:
    (repo / "c.txt").write_text("c\n")
    _git(repo, "add", "c.txt")
    assert run_hook(shape.format(repo=repo), tmp_path) == BLOCK


def test_a_reword_in_a_sibling_worktree_is_judged_by_that_worktree(
    tmp_path: Path,
) -> None:
    dirty = _init_repo(tmp_path / "dirty")
    (dirty / "c.txt").write_text("c\n")
    _git(dirty, "add", "c.txt")
    clean = _init_repo(tmp_path / "clean")
    command = f"cd {clean}; git commit --amend --no-edit --no-verify"
    assert run_hook(command, dirty) == ALLOW


def _staged(repo: Path) -> Path:
    (repo / "new.txt").write_text("new\n")
    _git(repo, "add", "new.txt")
    return repo


@pytest.mark.parametrize(
    "command",
    [
        "(cd /tmp && ls) && git commit --no-verify -m x",
        "bash -lc 'git commit --no-verify -m x'",
        "bash -euo pipefail -c 'git commit --no-verify -m x'",
        "cat <<EOF | git commit --no-verify -F -\nbody\nEOF",
        "git commit -nm x",
        "git commit --no-veri -m x",
        "echo | xargs git commit --no-verify -m x",
        "caffeinate git commit -n -m x",
        "stdbuf -o0 git commit -n -m x",
    ],
)
def test_a_bypass_of_new_content_is_refused(repo: Path, command: str) -> None:
    assert run_hook(command, _staged(repo)) == BLOCK, command


@pytest.mark.parametrize(
    "command",
    [
        "echo git commit -n",
        "git commit -mfinal",
        "git commit -uno -m x",
        "git commit -m 'x' --no-verbose",
    ],
)
def test_text_and_flag_values_are_not_a_bypass(repo: Path, command: str) -> None:
    assert run_hook(command, _staged(repo)) == ALLOW, command


@pytest.mark.parametrize("flag", ["-p", "--patch", "--interactive", "-pn"])
def test_a_bypass_that_stages_hunks_during_the_commit_is_refused(
    repo: Path, flag: str
) -> None:
    """The index matches HEAD before the commit, but `-p` stages new hunks into it."""
    (repo / "a.txt").write_text("changed\n")
    assert run_hook(f"git commit --amend --no-edit --no-verify {flag}", repo) == BLOCK


def test_git_dash_c_with_a_tilde_is_judged_in_that_repo(
    repo: Path, monkeypatch
) -> None:
    home = repo.parent
    _staged(repo)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"git -C ~/{repo.name} commit -n -m x"},
        "cwd": "/",
    }
    env = {**_clean_env(), "HOME": str(home)}
    done = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )
    assert done.returncode == BLOCK
