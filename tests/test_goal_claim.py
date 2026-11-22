# SPDX-License-Identifier: MIT

"""`goal claim` and `goal release`: a tiny pull request that marks or unmarks one todo line, with git as the lock."""

from __future__ import annotations

import subprocess
from pathlib import Path

from workrun import IDENTITY, TODO, claimed, git, make_project


def test_a_claim_pushes_a_branch_marking_only_the_entry_line(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button")
    assert rc == 0, err
    marked = p.origin_file("docs/todo.md", "claim/add-a-snooze-button")
    assert marked == claimed(TODO, "Add a snooze button", "agent-a", "2026-11-22")
    # main is untouched until the pull request merges.
    assert p.origin_file("docs/todo.md") == TODO
    assert "https://github.com/example/widgets/pull/7" in out


def test_the_claim_commit_touches_only_the_todo_file(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.run("claim", "Add a snooze button")
    changed = git(
        p.origin, "diff", "--name-only", "main", "claim/add-a-snooze-button"
    ).split()
    assert changed == ["docs/todo.md"]
    subject = git(
        p.origin, "log", "-1", "--format=%s", "claim/add-a-snooze-button"
    ).strip()
    assert subject == "chore: claim Add a snooze button"


def test_a_claim_opens_a_pull_request_and_arms_auto_merge(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.run("claim", "Add a snooze button")
    calls = p.gh_calls()
    create = next(c for c in calls if c[:2] == ["pr", "create"])
    assert create[create.index("--head") + 1] == "claim/add-a-snooze-button"
    assert create[create.index("--base") + 1] == "main"
    merge = next(c for c in calls if c[:2] == ["pr", "merge"])
    assert "--auto" in merge and "--squash" in merge
    assert "--admin" not in merge


def test_no_merge_leaves_the_pull_request_unarmed(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.run("claim", "Add a snooze button", "--no-merge")
    assert not any(c[:2] == ["pr", "merge"] for c in p.gh_calls())


def test_the_merge_method_comes_from_the_settings(tmp_path: Path) -> None:
    p = make_project(tmp_path, extra={".claude/work.toml": 'merge_method = "rebase"\n'})
    p.run("claim", "Add a snooze button")
    merge = next(c for c in p.gh_calls() if c[:2] == ["pr", "merge"])
    assert "--rebase" in merge


def test_claiming_an_item_already_claimed_on_main_fails_naming_the_holder(
    tmp_path: Path,
) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-b", "2026-11-21")
    )
    rc, out, err = p.run("claim", "Add a snooze button")
    assert rc == 3
    assert "agent-b" in out + err
    assert not any(c[:2] == ["pr", "create"] for c in p.gh_calls())


def test_a_second_claim_while_the_first_is_open_fails(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    assert p.run("claim", "Add a snooze button")[0] == 0
    rc, out, err = p.run("claim", "Add a snooze button", who="agent-b")
    assert rc == 3
    assert "claim/add-a-snooze-button" in out + err


def test_two_claims_on_one_line_conflict_so_the_later_cannot_merge(
    tmp_path: Path,
) -> None:
    """Git is the lock: both claims edit the same line, so once one merges the other conflicts."""
    p = make_project(tmp_path)
    work = tmp_path / "w"
    git(p.clone, "worktree", "add", "-q", "--detach", str(work), "origin/main")
    for who in ("agent-a", "agent-b"):
        text = claimed(TODO, "Add a snooze button", who, "2026-11-22")
        git(work, "checkout", "-q", "-B", who, "origin/main")
        (work / "docs" / "todo.md").write_text(text)
        git(work, *IDENTITY, "commit", "-q", "-am", f"chore: claim as {who}")
    git(work, "checkout", "-q", "-B", "merged", "agent-a")
    done = subprocess.run(
        ["git", "-C", str(work), *IDENTITY, "merge", "agent-b"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
    assert "CONFLICT" in done.stdout


def test_an_item_not_in_the_todo_file_is_unclear_and_lists_close_titles(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    rc, out, err = p.run("claim", "snooze")
    assert rc == 2
    assert "Add a snooze button" in out + err


def test_an_item_is_matched_by_its_title_ignoring_case_and_end_punctuation(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    rc, _, err = p.run("claim", "add a SNOOZE button.")
    assert rc == 0, err


def test_your_own_claim_on_main_is_already_yours(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-a", "2026-11-21")
    )
    rc, out, _ = p.run("claim", "Add a snooze button")
    assert rc == 0
    assert "already yours" in out


def test_a_release_removes_the_mark_in_its_own_branch(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-a", "2026-11-21")
    )
    rc, _, err = p.run("release", "Add a snooze button")
    assert rc == 0, err
    assert p.origin_file("docs/todo.md", "release/add-a-snooze-button") == TODO
    subject = git(
        p.origin, "log", "-1", "--format=%s", "release/add-a-snooze-button"
    ).strip()
    assert subject == "chore: release the claim on Add a snooze button"


def test_releasing_someone_elses_claim_needs_their_name(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-b", "2026-11-01")
    )
    rc, out, err = p.run("release", "Add a snooze button")
    assert rc == 3
    assert "agent-b" in out + err
    rc, _, err = p.run("release", "Add a snooze button", "--holder", "agent-b")
    assert rc == 0, err


def test_releasing_an_unclaimed_item_is_an_error(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, out, err = p.run("release", "Add a snooze button")
    assert rc == 1
    assert "not claimed" in out + err


def test_the_claim_leaves_no_worktree_behind(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    before = git(p.clone, "worktree", "list")
    p.run("claim", "Add a snooze button")
    assert git(p.clone, "worktree", "list") == before
