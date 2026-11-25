# SPDX-License-Identifier: MIT

"""`goal merged <branch>`: the merged-branch test that finish-work and branch-prune share.

Merged means every commit is on main as the same patch (`git cherry`), or a merged pull request's head is the
branch's tip, which is how a squash merge shows. Anything else is not proven merged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import goal
import pytest
from workrun import IDENTITY, git, make_project


def test_a_branch_whose_patches_are_on_main_is_merged(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.branch("work/snooze", "2026-11-21", {"snooze.txt": "snooze\n"})
    # Land the same patch on main under a new SHA, as a rebase merge does.
    git(p.clone, "checkout", "-q", "-B", "tmp", "origin/main")
    git(p.clone, *IDENTITY, "cherry-pick", "origin/work/snooze")
    git(p.clone, "push", "-q", "origin", "HEAD:main")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 0
    assert out["merged"] is True


def test_a_branch_with_new_work_is_not_merged(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.branch("work/snooze", "2026-11-21")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3
    assert out["merged"] is False


def test_a_squash_merged_branch_is_merged_by_its_pull_request(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    tip = p.branch("work/snooze", "2026-11-21")
    p.prs("merged", [{"number": 9, "headRefOid": tip}])
    rc, out = p.json("merged", "work/snooze")
    assert rc == 0
    assert "#9" in out["reason"]


def test_commits_after_the_pull_request_merged_are_not_merged(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.prs("merged", [{"number": 9, "headRefOid": "0" * 40}])
    p.branch("work/snooze", "2026-11-21")
    rc, _ = p.json("merged", "work/snooze")
    assert rc == 3


def test_without_github_a_branch_ahead_is_not_proven(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.branch("work/snooze", "2026-11-21")
    p.gh_fails()
    rc, out = p.json("merged", "work/snooze")
    assert rc == 2
    assert "not checked" in out["reason"]


def test_a_local_branch_with_no_remote_is_checked_too(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    git(p.clone, "branch", "local-only", "origin/main")
    rc, _ = p.json("merged", "local-only")
    assert rc == 0


def test_an_unknown_branch_is_an_error(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, _, err = p.run("merged", "nope")
    assert rc == 1
    assert "nope" in err


def test_unpushed_local_commits_on_a_merged_branch_are_not_merged(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    tip = p.branch("work/snooze", "2026-11-21")
    p.prs("merged", [{"number": 9, "headRefOid": tip}])
    git(p.clone, "checkout", "-q", "work/snooze")
    (p.clone / "more.txt").write_text("more\n")
    git(p.clone, "add", "more.txt")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", "feat: more")
    rc, _ = p.json("merged", "work/snooze")
    assert rc == 3


def test_a_branch_name_that_looks_like_an_option_is_refused(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    args = argparse.Namespace(branch="-x", C=str(p.clone), no_fetch=True)
    with pytest.raises(RuntimeError, match="not a branch name"):
        goal.merged(args)


def test_a_local_branch_behind_a_squash_merged_one_is_merged(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    tip = p.branch("work/snooze", "2026-11-21")
    first = git(p.clone, "rev-parse", f"{tip}~0").strip()
    git(p.clone, "checkout", "-q", "work/snooze")
    (p.clone / "two.txt").write_text("two\n")
    git(p.clone, "add", "two.txt")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", "feat: two")
    git(p.clone, "push", "-q", "origin", "HEAD:refs/heads/work/snooze")
    head = git(p.clone, "rev-parse", "HEAD").strip()
    git(p.clone, "checkout", "-q", "--detach", "origin/main")
    git(
        p.clone, "branch", "-f", "work/snooze", first
    )  # the local copy is one commit behind
    p.prs("merged", [{"number": 9, "headRefOid": head}])
    rc, out = p.json("merged", "work/snooze")
    assert rc == 0, out
