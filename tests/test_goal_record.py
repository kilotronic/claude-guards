# SPDX-License-Identifier: MIT

"""The record of work: the README's **Work is recorded in:** line names a todo file or GitHub issues, and `goal`
says which (`record`), so every skill follows the same rule."""

from __future__ import annotations

from pathlib import Path

from workrun import README, git, make_project

TODO_LINE = "- **Work is recorded in:** `docs/todo.md`"


def issues_readme(where: str) -> str:
    return README.replace(TODO_LINE, f"- **Work is recorded in:** {where}").replace(
        "- **Work in progress:** at most 2 at once; defect fixes one at a time\n", ""
    )


def test_a_todo_file_is_the_record(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    rc, out = p.json()
    assert rc == 0
    assert out["record"] == "todo"
    assert out["next"]["kind"] == "defect"


def test_github_issues_are_the_record(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, readme=issues_readme("GitHub issues on the team board"), todo=None
    )
    rc, out = p.json()
    assert rc == 0, out
    assert out["record"] == "issues"
    assert out["next"]["kind"] == "issues"
    assert out["next"]["item"] is None
    assert out["goal"]["recorded_in"] == "GitHub issues on the team board"
    assert out["goal"]["milestone"] == "M2, the phone app"


def test_a_link_to_the_issues_board_is_not_a_todo_file(tmp_path: Path) -> None:
    where = "[GitHub issues](https://github.com/orgs/example/projects/1)"
    p = make_project(tmp_path, readme=issues_readme(where), todo=None)
    rc, out = p.json()
    assert rc == 0, out
    assert out["record"] == "issues"


def test_the_text_answer_says_issues(tmp_path: Path) -> None:
    p = make_project(tmp_path, readme=issues_readme("GitHub issues"), todo=None)
    rc, text, _ = p.run()
    assert rc == 0
    assert "goal: M2, the phone app" in text
    assert "record: GitHub issues" in text
    assert "next: this repo records its work in GitHub issues" in text


def test_a_record_that_is_neither_is_named_as_unclear(tmp_path: Path) -> None:
    p = make_project(tmp_path, readme=issues_readme("the team wiki"), todo=None)
    rc, out = p.json()
    assert rc == 2
    assert any(
        "neither a todo file" in m and "nor GitHub issues" in m for m in out["missing"]
    )


def test_claim_in_an_issues_repo_points_at_the_issues(tmp_path: Path) -> None:
    p = make_project(tmp_path, readme=issues_readme("GitHub issues"), todo=None)
    wt = tmp_path / "wt"
    git(p.clone, "worktree", "add", "-q", "-b", "work/x", str(wt), "origin/main")
    rc, out, err = p.run("claim", "Anything", cwd=wt)
    assert rc == 2
    assert "GitHub issues" in out + err
    assert "frozen" not in out + err
