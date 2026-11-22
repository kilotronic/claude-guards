# SPDX-License-Identifier: MIT

"""goal's answer, run as a command in a made-up project: the order, the limit, stale claims, unclear repos."""

from __future__ import annotations

from pathlib import Path

from workrun import README, TODO, claimed, make_project


def test_the_top_free_defect_comes_first(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, out = p.json()
    assert rc == 0
    assert out["status"] == "ok"
    assert out["next"]["kind"] == "defect"
    assert out["next"]["item"]["title"] == "The timer skips a second"
    assert out["next"]["item"]["branch"] == "work/the-timer-skips-a-second"
    assert out["goal"]["milestone"] == "M2, the phone app"
    assert out["in_progress"] == {"count": 0, "limit": 2, "claims": []}


def test_the_text_answer_says_goal_next_and_in_progress(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, out, _ = p.run()
    assert rc == 0
    assert "goal: M2, the phone app (done when: a timer rings on a locked phone)" in out
    assert 'next: fix "The timer skips a second"' in out
    assert "in progress: 0 of 2" in out


def test_your_own_claim_comes_before_everything(tmp_path: Path) -> None:
    todo = claimed(TODO, "Show the time left in the title", "agent-a", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    rc, out = p.json()
    assert rc == 0
    assert out["next"]["kind"] == "own"
    assert out["next"]["item"]["title"] == "Show the time left in the title"


def test_someone_elses_claim_is_not_yours(tmp_path: Path) -> None:
    todo = claimed(TODO, "Show the time left in the title", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    _, out = p.json()
    assert out["next"]["kind"] == "defect"


def test_defect_fixes_run_one_at_a_time(tmp_path: Path) -> None:
    todo = claimed(TODO, "The timer skips a second", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    rc, out = p.json()
    assert rc == 0
    # The second defect waits for the first fix; the next item is taken instead.
    assert out["next"]["kind"] == "next"
    assert out["next"]["item"]["title"] == "Add a snooze button"
    assert "one at a time" in out["next"]["reason"]


def test_an_empty_todo_file_answers_a_cleanup(tmp_path: Path) -> None:
    p = make_project(tmp_path, todo="# Todo\n\n## Defects\n\n## Next\n")
    rc, out = p.json()
    assert rc == 0
    assert out["next"]["kind"] == "cleanup"
    assert out["next"]["item"] is None


def test_the_last_item_is_taken(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo="## Defects\n\n## Next\n\n- **Add a snooze button.**\n"
    )
    _, out = p.json()
    assert out["next"]["kind"] == "next"
    assert out["next"]["item"]["title"] == "Add a snooze button"


def test_the_last_item_held_by_someone_else_leaves_a_cleanup(tmp_path: Path) -> None:
    todo = "## Defects\n\n## Next\n\n- **Add a snooze button.** [claimed by agent-b on box2, 2026-11-21]\n"
    p = make_project(tmp_path, todo=todo)
    _, out = p.json()
    assert out["next"]["kind"] == "cleanup"
    assert out["in_progress"]["count"] == 1


def test_one_under_the_limit_takes_the_next_item_without_a_warning(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "The timer skips a second", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    _, out = p.json()
    assert out["in_progress"]["count"] == 1
    assert out["next"]["kind"] == "next"
    assert out["warnings"] == []


def test_at_the_limit_the_next_item_comes_with_a_warning_not_a_block(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "The timer skips a second", "agent-b", "2026-11-21")
    todo = claimed(todo, "Add a snooze button", "agent-c", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    rc, out = p.json()
    assert rc == 0
    assert out["in_progress"]["count"] == 2
    assert out["next"]["kind"] == "next"
    assert out["next"]["item"]["title"] == "Show the time left in the title"
    assert out["warnings"] == ["taking this makes 3 in progress, over the limit of 2"]
    rc, text, _ = p.run()
    assert rc == 0
    assert "warning: taking this makes 3 in progress, over the limit of 2" in text


def test_a_claim_with_no_activity_for_three_days_is_flagged_stale(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-19")
    p = make_project(tmp_path, todo=todo)
    rc, out = p.json()
    assert rc == 0
    (claim,) = out["in_progress"]["claims"]
    assert claim["title"] == "Add a snooze button"
    assert claim["stale"] is True
    assert out["stale"] == ["Add a snooze button"]
    _, text, _ = p.run()
    assert 'stale: "Add a snooze button" claimed by agent-b on box1, 2026-11-19' in text
    # Flagged, never removed: the mark is still on main.
    assert "[claimed by agent-b" in p.origin_file("docs/todo.md")


def test_a_claim_two_days_old_is_not_stale(tmp_path: Path) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-20")
    p = make_project(tmp_path, todo=todo)
    _, out = p.json()
    assert out["in_progress"]["claims"][0]["stale"] is False


def test_an_open_pull_request_for_the_item_keeps_an_old_claim_fresh(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-01")
    p = make_project(tmp_path, todo=todo)
    p.prs(
        "open",
        [
            {
                "number": 3,
                "title": "feat: add a snooze button",
                "headRefName": "x",
                "body": "",
            }
        ],
    )
    _, out = p.json()
    assert out["in_progress"]["claims"][0]["stale"] is False


def test_recent_commits_on_the_items_branch_keep_an_old_claim_fresh(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-01")
    p = make_project(tmp_path, todo=todo)
    p.branch("work/add-a-snooze-button", "2026-11-21")
    _, out = p.json()
    assert out["in_progress"]["claims"][0]["stale"] is False


def test_old_commits_on_the_items_branch_do_not(tmp_path: Path) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-01")
    p = make_project(tmp_path, todo=todo)
    p.branch("work/add-a-snooze-button", "2026-11-10")
    _, out = p.json()
    assert out["in_progress"]["claims"][0]["stale"] is True


def test_when_github_cannot_be_reached_the_answer_says_so(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.gh_fails()
    rc, out = p.json()
    assert rc == 0
    assert "open pull requests not checked" in " ".join(out["warnings"])


def test_a_missing_readme_section_is_unclear_and_names_it(tmp_path: Path) -> None:
    p = make_project(tmp_path, readme="# Widgets\n\nKitchen timers.\n")
    rc, out = p.json()
    assert rc == 2
    assert out["status"] == "unclear"
    assert out["next"] is None
    assert any("How work runs here" in m for m in out["missing"])
    rc, text, _ = p.run()
    assert rc == 2
    assert "unclear:" in text


def test_a_section_without_a_limit_or_record_names_each(tmp_path: Path) -> None:
    readme = "## How work runs here\n\n- **Current milestone:** M1. **Done when:** it ships. **By:** no date\n"
    p = make_project(tmp_path, readme=readme)
    rc, out = p.json()
    assert rc == 2
    joined = " ".join(out["missing"])
    assert "Work is recorded in" in joined
    assert "Work in progress" in joined


def test_no_milestone_and_no_goals_file_is_unclear(tmp_path: Path) -> None:
    readme = README.replace(
        "- **Current milestone:** M2, the phone app. **Done when:** a timer rings on a locked phone. **By:** no date\n",
        "",
    )
    p = make_project(tmp_path, readme=readme)
    rc, out = p.json()
    assert rc == 2
    assert any("milestone" in m for m in out["missing"])


def test_a_goals_file_stands_in_for_the_milestone(tmp_path: Path) -> None:
    readme = README.replace(
        "- **Current milestone:** M2, the phone app. **Done when:** a timer rings on a locked phone. **By:** no date\n",
        "- **Goals:** [docs/goals.md](docs/goals.md)\n",
    )
    p = make_project(tmp_path, readme=readme, extra={"docs/goals.md": "# Goals\n"})
    rc, out, _ = p.run()
    assert rc == 0, out
    assert "goal: see docs/goals.md" in out


def test_a_milestone_past_its_date_is_unclear(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, readme=README.replace("**By:** no date", "**By:** 2026-11-01")
    )
    rc, out = p.json()
    assert rc == 2
    assert any("was due 2026-11-01" in m for m in out["missing"])


def test_a_missing_todo_file_is_unclear(tmp_path: Path) -> None:
    p = make_project(tmp_path, todo=None)
    rc, out = p.json()
    assert rc == 2
    assert any("docs/todo.md" in m for m in out["missing"])


def test_the_settings_file_is_read_from_main(tmp_path: Path) -> None:
    todo = "## Bugs\n\n- **(owner call) Pick a ring.**\n\n## Play\n\n- **Add a snooze button.**\n"
    p = make_project(
        tmp_path,
        todo=todo,
        extra={
            ".claude/work.toml": 'defects = ["Bugs"]\nnext = ["Play"]\nskip = ["(owner call)"]\n'
        },
    )
    _, out = p.json()
    assert out["next"]["item"]["title"] == "Add a snooze button"
    assert out["needs_owner"] == ["Pick a ring"]


def test_a_frozen_repo_answers_that_it_is_frozen(tmp_path: Path) -> None:
    p = make_project(
        tmp_path,
        readme="## How work runs here\n\n- **Frozen:** new work goes to example/gadgets.\n",
        todo=None,
    )
    rc, out = p.json()
    assert rc == 0
    assert out["next"]["kind"] == "frozen"


def test_list_prints_every_free_item_in_order(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, text, _ = p.run("--list")
    assert rc == 0
    order = [
        "The timer skips a second",
        "The bell is silent on mute",
        "Add a snooze button",
        "Show the time left",
    ]
    places = [text.index(t) for t in order]
    assert places == sorted(places)


def test_it_reads_main_not_the_checkout(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    (p.clone / "docs" / "todo.md").write_text("## Defects\n\n## Next\n")
    _, out = p.json()
    assert out["next"]["item"]["title"] == "The timer skips a second"


def test_outside_a_git_repo_it_is_an_error(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    alone = tmp_path / "alone"
    alone.mkdir()
    rc, _, err = p.run(cwd=alone)
    assert rc == 1
    assert "git" in err


def test_a_by_date_that_does_not_exist_is_unclear(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, readme=README.replace("**By:** no date", "**By:** 2026-02-30")
    )
    rc, out = p.json()
    assert rc == 2
    assert any("2026-02-30" in m for m in out["missing"])


def test_a_claim_date_that_does_not_exist_is_unclear(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, todo=claimed(TODO, "Add a snooze button", "agent-b", "2026-02-30")
    )
    rc, out = p.json()
    assert rc == 2
    assert any("2026-02-30" in m for m in out["missing"])
