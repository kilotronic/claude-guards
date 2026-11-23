# SPDX-License-Identifier: MIT

"""`goal claim` and `goal release`: a commit on the work's own branch that marks or unmarks one todo line. The
work's pull request, once open, carries the mark, and that is the claim."""

from __future__ import annotations

import json
from pathlib import Path

from workrun import README, TODO, Project, claimed, git, make_project


def work_tree(p: Project, tmp: Path, branch: str = "work/snooze") -> Path:
    """A worktree on a new work branch off origin/main, as work-start makes it."""
    wt = tmp / "wt"
    git(p.clone, "worktree", "add", "-q", "-b", branch, str(wt), "origin/main")
    return wt


def pr_tree(p: Project, tmp: Path, pr: dict) -> Path:
    """A worktree on the branch an open pull request comes from (open_claim made it in the clone)."""
    name = pr["headRefName"]
    wt = tmp / name.replace("/", "-")
    git(p.clone, "worktree", "add", "-q", str(wt), name)
    return wt


def test_a_claim_commits_the_mark_on_the_work_branch(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 0, err
    want = claimed(TODO, "Add a snooze button", "agent-a", "2026-11-22")
    assert (wt / "docs" / "todo.md").read_text() == want
    assert git(wt, "show", "HEAD:docs/todo.md") == want
    assert git(wt, "log", "-1", "--format=%s").strip() == (
        "chore: claim Add a snooze button"
    )
    assert git(wt, "diff", "--name-only", "origin/main", "HEAD").split() == [
        "docs/todo.md"
    ]
    assert "open" in out and "pull request" in out


def test_a_claim_opens_no_pull_request_of_its_own_and_pushes_nothing(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    assert p.run("claim", "Add a snooze button", cwd=wt)[0] == 0
    assert not any(c[:2] in (["pr", "create"], ["pr", "merge"]) for c in p.gh_calls())
    assert "work/snooze" not in git(p.origin, "branch", "--list")


def test_a_claim_on_the_default_branch_is_refused(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    git(p.clone, "checkout", "-q", "-B", "main", "origin/main")
    rc, out, err = p.run("claim", "Add a snooze button")
    assert rc == 1
    assert "work's own branch" in out + err
    assert git(p.clone, "status", "--porcelain") == ""


def test_a_claim_on_a_detached_head_is_refused(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button")
    assert rc == 1
    assert "work's own branch" in out + err


def test_an_item_an_open_pull_request_holds_is_refused_naming_it(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-b", 7)
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 3
    assert "agent-b" in out + err and "#7" in out + err
    assert git(wt, "log", "-1", "--format=%s", "HEAD").strip() == "chore: start"


def test_your_own_open_pull_request_is_already_yours(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    pr = p.open_claim("Add a snooze button", "agent-a", 7)
    rc, out, _ = p.run("claim", "Add a snooze button", cwd=pr_tree(p, tmp_path, pr))
    assert rc == 0
    assert "already yours" in out and "#7" in out


def test_the_same_name_on_another_branch_is_not_yours(tmp_path: Path) -> None:
    """Two sessions can share a name (subagents of one session, one stable $GOAL_AS); a branch tells them apart."""
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-a", 7)
    rc, out, err = p.run("claim", "Add a snooze button", cwd=work_tree(p, tmp_path))
    assert rc == 3
    assert "#7" in out + err


def test_after_opening_a_later_pull_request_the_claim_says_to_close_it(
    tmp_path: Path,
) -> None:
    """The tie rule: another pull request marked the item first, so ours, the higher number, closes itself."""
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-b", 7)
    later = p.open_claim("Add a snooze button", "agent-a", 9)
    rc, out, err = p.run(
        "claim", "Add a snooze button", cwd=pr_tree(p, tmp_path, later)
    )
    assert rc == 3
    assert "#7" in out + err
    assert "gh pr close 9 --comment 'Closing: #7 marked this item first.'" in out + err


def test_after_opening_the_lower_pull_request_the_claim_holds(tmp_path: Path) -> None:
    """The same tie the other way round: ours is the lower number, so it holds, and the other closes itself."""
    p = make_project(tmp_path)
    first = p.open_claim("Add a snooze button", "agent-a", 7)
    later = p.open_claim("Add a snooze button", "agent-b", 9)
    rc, out, err = p.run(
        "claim", "Add a snooze button", cwd=pr_tree(p, tmp_path, first)
    )
    assert rc == 0, err
    assert "already yours" in out and "#7" in out
    rc, out, err = p.run(
        "claim", "Add a snooze button", who="agent-b", cwd=pr_tree(p, tmp_path, later)
    )
    assert rc == 3
    assert "gh pr close 9" in out + err


def test_a_tie_between_two_sessions_with_one_name_goes_by_branch(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    first = p.open_claim("Add a snooze button", "agent-a", 7)
    later = p.open_claim("Add a snooze button", "agent-a", 9)
    rc, out, err = p.run(
        "claim", "Add a snooze button", cwd=pr_tree(p, tmp_path, later)
    )
    assert rc == 3
    assert "gh pr close 9" in out + err
    rc, out, _ = p.run("claim", "Add a snooze button", cwd=pr_tree(p, tmp_path, first))
    assert rc == 0
    assert "already yours" in out


def test_claiming_twice_on_the_branch_commits_once(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    assert p.run("claim", "Add a snooze button", cwd=wt)[0] == 0
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 0, err
    assert "already marked" in out
    assert git(wt, "rev-list", "--count", "origin/main..HEAD").strip() == "1"


def test_an_item_a_mark_on_main_holds_is_refused(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-b", "2026-11-01")
    )
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 3
    assert "agent-b" in out + err and "default branch" in out + err


def test_a_pull_request_whose_branch_cannot_be_read_stops_a_claim(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.prs(
        "open",
        [
            {
                "number": 7,
                "headRefName": "work/not-fetched",
                "headRefOid": "0" * 40,
                "isCrossRepository": False,
            }
        ],
    )
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 1
    assert "#7" in out + err
    assert (wt / "docs" / "todo.md").read_text() == TODO


def test_github_unreachable_refuses_the_claim(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    p.gh_fails()
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 1
    assert "claims weren't checked" in out + err
    assert (wt / "docs" / "todo.md").read_text() == TODO


def test_uncommitted_changes_to_the_todo_file_are_refused(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    path = wt / "docs" / "todo.md"
    path.write_text(path.read_text() + "\n- **A new entry.**\n")
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 1
    assert "uncommitted" in out + err


def test_an_item_not_in_the_todo_file_is_unclear_and_lists_close_titles(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("claim", "snooze", cwd=wt)
    assert rc == 2
    assert "Add a snooze button" in out + err


def test_an_item_is_matched_by_its_title_ignoring_case_and_end_punctuation(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    rc, _, err = p.run("claim", "add a SNOOZE button.", cwd=wt)
    assert rc == 0, err


def test_an_entry_needing_the_owner_cannot_be_claimed(tmp_path: Path) -> None:
    todo = TODO.replace(
        "- **Add a snooze button.**", "- **Add a snooze button.** (owner)"
    )
    p = make_project(tmp_path, todo=todo)
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 3
    assert "owner" in out + err


def test_a_claim_keeps_windows_line_endings(tmp_path: Path) -> None:
    p = make_project(tmp_path, todo=TODO.replace("\n", "\r\n"))
    wt = work_tree(p, tmp_path)
    rc, _, err = p.run("claim", "Add a snooze button", cwd=wt)
    assert rc == 0, err
    stat = git(wt, "diff", "--numstat", "origin/main", "HEAD").split()
    assert stat[:2] == ["1", "1"]


def test_options_before_the_subcommand_still_count(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rc, out, err = p.run(
        "-C", str(wt), "--json", "claim", "Add a snooze button", cwd=elsewhere
    )
    assert rc == 0, err
    assert json.loads(out)["branch"] == "work/snooze"


def test_a_claim_without_a_name_to_claim_as_is_unclear(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("claim", "Add a snooze button", who=None, cwd=wt)
    assert rc == 2
    assert "GOAL_AS" in out + err
    rc, out, _ = p.run(who=None)
    assert rc == 0
    assert "GOAL_AS" in out


def test_a_release_commits_the_mark_away_on_the_branch(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    assert p.run("claim", "Add a snooze button", cwd=wt)[0] == 0
    rc, out, err = p.run("release", "Add a snooze button", cwd=wt)
    assert rc == 0, err
    assert (wt / "docs" / "todo.md").read_text() == TODO
    assert git(wt, "log", "-1", "--format=%s").strip() == (
        "chore: release the claim on Add a snooze button"
    )
    assert "close" in out


def test_releasing_someone_elses_mark_needs_their_name(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-b", "2026-11-01")
    )
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("release", "Add a snooze button", cwd=wt)
    assert rc == 3
    assert "agent-b" in out + err
    rc, _, err = p.run("release", "Add a snooze button", "--holder", "agent-b", cwd=wt)
    assert rc == 0, err
    assert (wt / "docs" / "todo.md").read_text() == TODO


def test_releasing_an_unmarked_item_is_an_error(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    wt = work_tree(p, tmp_path)
    rc, out, err = p.run("release", "Add a snooze button", cwd=wt)
    assert rc == 1
    assert "not marked" in out + err


def test_a_claim_can_be_released_while_the_milestone_is_overdue(tmp_path: Path) -> None:
    p = make_project(
        tmp_path,
        readme=README.replace("**By:** no date", "**By:** 2026-01-01"),
        todo=claimed(TODO, "Add a snooze button", "agent-a", "2026-11-21"),
    )
    wt = work_tree(p, tmp_path)
    rc, _, err = p.run("release", "Add a snooze button", cwd=wt)
    assert rc == 0, err


def test_goal_on_another_work_branch_is_not_told_to_finish_a_same_named_claim(
    tmp_path: Path,
) -> None:
    """Plain `goal` decides "yours" by branch on a work branch, as `goal claim` does."""
    p = make_project(tmp_path)
    pr = p.open_claim("Add a snooze button", "agent-a", 7)
    _, out = p.json(cwd=work_tree(p, tmp_path))
    assert out["next"]["kind"] != "own"
    _, out = p.json(cwd=pr_tree(p, tmp_path, pr))
    assert out["next"]["kind"] == "own"
    # Off any work branch, the name still decides, so a stable $GOAL_AS resumes its claim.
    _, out = p.json()
    assert out["next"]["kind"] == "own"


def test_only_the_later_pull_requests_branch_is_told_to_stop(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    first = p.open_claim("Add a snooze button", "agent-a", 7)
    later = p.open_claim("Add a snooze button", "agent-a", 9)
    _, out = p.json(cwd=pr_tree(p, tmp_path, first))
    assert not any("you hold the later claim" in w for w in out["warnings"])
    _, out = p.json(cwd=pr_tree(p, tmp_path, later))
    assert any("you hold the later claim" in w and "#9" in w for w in out["warnings"])


def test_your_own_mark_on_main_is_already_yours(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-a", "2026-11-21")
    )
    rc, out, err = p.run("claim", "Add a snooze button", cwd=work_tree(p, tmp_path))
    assert rc == 0, err
    assert "already yours" in out


def test_a_pull_request_later_than_a_mark_on_main_is_told_to_close(
    tmp_path: Path,
) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-b", "2026-11-21")
    )
    later = p.open_claim("Add a snooze button", "agent-a", 9, replace_mark=True)
    rc, out, err = p.run(
        "claim", "Add a snooze button", cwd=pr_tree(p, tmp_path, later)
    )
    assert rc == 3
    assert "already claimed on the default branch" in out + err
