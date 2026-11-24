# SPDX-License-Identifier: MIT

"""The work's own pull request is the claim: an item is in progress while an open pull request's branch carries
its mark, and only then."""

from __future__ import annotations

from pathlib import Path

from workrun import TODO, claimed, make_project


def test_an_open_pull_request_carrying_the_mark_claims_the_item(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.open_claim("The timer skips a second", "agent-b", 7)
    rc, out = p.json()
    assert rc == 0
    assert out["in_progress"]["count"] == 1
    (claim,) = out["in_progress"]["claims"]
    assert claim["title"] == "The timer skips a second"
    assert (claim["who"], claim["machine"]) == ("agent-b", "box1")
    assert claim["pr"] == 7
    assert claim["author"] == "agent-b-login"
    assert claim["url"] == "https://github.com/example/widgets/pull/7"
    assert claim["stale"] is False
    # The item is taken, so the next defect waits and the next item is taken instead.
    assert out["next"]["item"]["title"] == "Add a snooze button"
    _, text, _ = p.run("--list")
    assert "The timer skips a second: agent-b on box1, 2026-11-21, #7" in text


def test_a_draft_pull_request_carrying_the_mark_claims_the_item(
    tmp_path: Path,
) -> None:
    """work-start opens the work's pull request as a draft, so a draft holds the item like any open one."""
    p = make_project(tmp_path)
    p.open_claim("The timer skips a second", "agent-b", 7, draft=True)
    rc, out = p.json()
    assert rc == 0
    (claim,) = out["in_progress"]["claims"]
    assert (claim["title"], claim["pr"]) == ("The timer skips a second", 7)
    assert out["next"]["item"]["title"] == "Add a snooze button"
    (call,) = [c for c in p.gh_calls() if c[:2] == ["pr", "list"]]
    assert "--draft" not in call  # gh's --draft would list drafts only


def test_your_own_open_pull_request_is_your_item(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.open_claim("Show the time left in the title", "agent-a", 7)
    _, out = p.json()
    assert out["next"]["kind"] == "own"
    assert out["next"]["item"]["title"] == "Show the time left in the title"


def test_the_list_asks_github_for_open_pull_requests_into_the_default_branch(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.json()
    (call,) = [c for c in p.gh_calls() if c[:2] == ["pr", "list"]]
    assert call[call.index("--state") + 1] == "open"
    assert call[call.index("--base") + 1] == "main"


def test_a_pull_request_closed_without_merging_frees_the_item(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    pr = p.open_claim("The timer skips a second", "agent-b", 7)
    # Closed: GitHub lists it as closed, not open, though its branch still carries the mark.
    p.prs("open", [])
    p.prs("closed", [pr])
    _, out = p.json()
    assert out["in_progress"]["count"] == 0
    assert out["next"]["kind"] == "defect"
    assert out["next"]["item"]["title"] == "The timer skips a second"


def test_a_merged_pull_request_removes_the_item(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    pr = p.open_claim("The timer skips a second", "agent-b", 7, then_delete=True)
    # It merged: the entry it deleted is gone from main, and it is no longer open.
    p.push_files(
        {"docs/todo.md": TODO.replace(TODO.splitlines()[4] + "\n", "")},
        "feat: finish it",
    )
    p.prs("open", [])
    p.prs("merged", [pr])
    _, out = p.json("--list")
    assert out["in_progress"]["count"] == 0
    titles = [i["title"] for i in out["free"]]
    assert "The timer skips a second" not in titles
    assert out["next"]["item"]["title"] == "The bell is silent on mute"


def test_an_open_pull_request_that_already_deleted_the_entry_still_holds_it(
    tmp_path: Path,
) -> None:
    """work-finish deletes the entry in the work's last commit; the claim holds until the pull request merges."""
    p = make_project(tmp_path)
    p.open_claim("The timer skips a second", "agent-b", 7, then_delete=True)
    _, out = p.json()
    assert out["in_progress"]["count"] == 1
    assert out["in_progress"]["claims"][0]["pr"] == 7
    assert out["next"]["item"]["title"] == "Add a snooze button"


def test_two_pull_requests_marking_one_item_are_a_conflict_naming_both(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-b", 7)
    p.open_claim("Add a snooze button", "agent-c", 9)
    rc, out = p.json()
    assert rc == 0
    assert out["in_progress"]["count"] == 1
    assert out["in_progress"]["claims"][0]["pr"] == 7
    (conflict,) = out["conflicts"]
    assert conflict["title"] == "Add a snooze button"
    assert conflict["holder"]["pr"] == 7
    assert [c["pr"] for c in conflict["later"]] == [9]
    _, text, _ = p.run()
    line = next(ln for ln in text.splitlines() if "#9" in ln)
    assert "#7" in line and "should stop" in line


def test_the_later_of_two_claims_is_told_to_stop_and_is_not_its_item(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-b", 7)
    p.open_claim("Add a snooze button", "agent-a", 9)
    _, out = p.json()
    assert out["next"]["kind"] != "own"
    assert any("you hold the later claim" in w and "#9" in w for w in out["warnings"])


def test_a_pull_request_with_no_activity_for_three_days_is_flagged_not_freed(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-b", 7, updated="2026-11-19")
    rc, out = p.json()
    assert rc == 0
    (claim,) = out["in_progress"]["claims"]
    assert claim["stale"] is True
    assert out["stale"] == ["Add a snooze button"]
    assert out["in_progress"]["count"] == 1
    _, text, _ = p.run()
    assert 'stale: "Add a snooze button" in #7 by agent-b on box1' in text


def test_a_pull_request_active_two_days_ago_is_not_stale(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-b", 7, updated="2026-11-20")
    _, out = p.json()
    assert out["in_progress"]["claims"][0]["stale"] is False


def test_github_unreachable_says_claims_were_not_checked_and_answers_from_local_files(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.open_claim("The timer skips a second", "agent-b", 7)
    p.edit_local("README.md", "M2, the phone app", "M3, the watch app")
    p.edit_local(
        "docs/todo.md",
        "- **The bell is silent on mute.** The ring uses the media volume.",
        "- **The bell is silent on mute.** The ring uses the media volume. [claimed by agent-c on box2, 2026-11-21]",
    )
    p.gh_fails()
    rc, out = p.json()
    assert rc == 0
    assert out["goal"]["milestone"] == "M3, the watch app"
    assert out["claims_from"] is None
    assert any("claims weren't checked" in w for w in out["warnings"])
    # The working copy's own marks stand in for the claims it couldn't check.
    assert [c["title"] for c in out["in_progress"]["claims"]] == [
        "The bell is silent on mute"
    ]
    assert out["next"]["item"]["title"] == "Add a snooze button"
    _, text, _ = p.run()
    assert "claims weren't checked" in text


def test_pull_requests_that_cannot_be_listed_leave_claims_unchecked(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.pr_list_fails()
    rc, out = p.json()
    assert rc == 0
    assert out["claims_from"] is None
    assert any("claims weren't checked" in w for w in out["warnings"])
    assert out["next"]["item"]["title"] == "The timer skips a second"


def test_a_mark_on_the_default_branch_still_holds_and_comes_first(
    tmp_path: Path,
) -> None:
    """Claims made before claims moved to pull requests sit on the default branch; an upgrade must not drop them."""
    todo = claimed(TODO, "The timer skips a second", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    # A work branch made from main carries main's mark too; it counts once, as main's.
    p.open_claim("Add a snooze button", "agent-c", 7)
    _, out = p.json()
    claims = {c["title"]: c for c in out["in_progress"]["claims"]}
    assert set(claims) == {"The timer skips a second", "Add a snooze button"}
    assert claims["The timer skips a second"]["pr"] is None
    assert claims["The timer skips a second"]["who"] == "agent-b"
    assert out["conflicts"] == []
    assert any(
        "The timer skips a second" in w and "before claims moved" in w
        for w in out["warnings"]
    )
    # A pull request that marks it again is the later claim.
    p.open_claim("The timer skips a second", "agent-a", 9)
    _, out = p.json()
    assert out["conflicts"][0]["holder"]["pr"] is None
    assert any("you hold the later claim" in w for w in out["warnings"])


def test_a_mark_on_the_default_branch_goes_stale_by_its_date(tmp_path: Path) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-19")
    p = make_project(tmp_path, todo=todo)
    _, out = p.json()
    assert out["stale"] == ["Add a snooze button"]


def test_a_removed_mark_on_the_default_branch_does_not_come_back_under_other_branches(
    tmp_path: Path,
) -> None:
    """A branch made while the old mark was on main carries it; once main drops it, that is not this branch's claim."""
    todo = claimed(TODO, "The timer skips a second", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    p.open_claim("Add a snooze button", "agent-c", 7)
    p.push_files({"docs/todo.md": TODO}, "chore: release the claim")
    _, out = p.json()
    assert [c["title"] for c in out["in_progress"]["claims"]] == ["Add a snooze button"]
    assert out["next"]["item"]["title"] == "The timer skips a second"


def test_a_stale_mark_on_the_default_branch_names_no_pull_request(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-19")
    p = make_project(tmp_path, todo=todo)
    _, text, _ = p.run()
    line = next(ln for ln in text.splitlines() if ln.startswith("stale:"))
    assert "#None" not in line
    assert "mark on the default branch" in line and "--holder" in line


def test_a_pull_request_from_a_fork_never_holds_an_item(tmp_path: Path) -> None:
    """Anyone can open one, even at a commit origin already has."""
    p = make_project(tmp_path)
    pr = p.open_claim("Add a snooze button", "agent-b", 7)
    p.prs("open", [{**pr, "isCrossRepository": True}])
    _, out = p.json()
    assert out["in_progress"]["count"] == 0
    assert not any("#7" in w for w in out["warnings"])


def test_a_mark_only_in_the_working_copy_is_not_counted(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.edit_local(
        "docs/todo.md",
        "- **The timer skips a second.** It jumps from 10 to 8 on a slow phone.",
        "- **The timer skips a second.** It jumps from 10 to 8 on a slow phone. [claimed by agent-a on box1, 2026-11-22]",
    )
    _, out = p.json()
    assert out["in_progress"]["count"] == 0
    assert out["next"]["kind"] == "defect"
    assert out["uncounted_claims"] == ["The timer skips a second"]
    assert any("no open pull request carries it" in w for w in out["warnings"])
