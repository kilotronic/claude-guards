# SPDX-License-Identifier: MIT

"""next-item reads the working copy; only claims come from GitHub, as the open pull requests into its default branch."""

from __future__ import annotations

from pathlib import Path

from workrun import TODO, claimed, git, make_project


def test_a_locally_edited_milestone_is_the_one_shown(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.edit_local("README.md", "M2, the phone app", "M3, the watch app")
    rc, out = p.json()
    assert rc == 0
    assert out["goal"]["milestone"] == "M3, the watch app"
    assert out["source"] == "working copy"


def test_a_locally_added_todo_entry_is_seen(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.edit_local(
        "docs/todo.md", "## Defects\n", "## Defects\n\n- **A new local defect.**\n"
    )
    _, out = p.json()
    assert out["next"]["item"]["title"] == "A new local defect"


def test_a_local_work_toml_is_read(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    (p.clone / ".claude").mkdir()
    (p.clone / ".claude" / "work.toml").write_text('defects = ["Next"]\n')
    _, out = p.json()
    assert out["next"]["item"]["title"] == "Add a snooze button"
    assert out["next"]["kind"] == "defect"


def test_an_open_claim_is_counted_even_when_the_working_copy_lacks_it(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    p.open_claim("Add a snooze button", "agent-b", 7)
    _, out = p.json()
    assert out["in_progress"]["count"] == 1
    assert out["in_progress"]["claims"][0]["who"] == "agent-b"


def test_a_stale_origin_head_does_not_change_whose_claims_are_read(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    # A second branch on origin, which the clone's origin/HEAD wrongly points at.
    git(p.clone, "push", "-q", "origin", "origin/main:refs/heads/legacy")
    git(p.clone, "fetch", "-q", "origin")
    git(
        p.clone,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/legacy",
    )
    p.default_branch_is("main")
    _, out = p.json()
    assert out["claims_from"] == "origin/main"
    p.default_branch_is("legacy")
    _, out = p.json()
    assert out["claims_from"] == "origin/legacy"
    bases = [c[c.index("--base") + 1] for c in p.gh_calls() if c[:2] == ["pr", "list"]]
    assert bases == ["main", "legacy"]


def test_unreachable_github_keeps_the_marks_the_working_copy_has(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "The timer skips a second", "agent-a", "2026-11-22")
    p = make_project(tmp_path, todo=todo)
    p.gh_fails()
    _, out = p.json()
    assert out["next"]["kind"] == "own"
    assert out["in_progress"]["count"] == 1
    assert any("claims weren't checked" in w for w in out["warnings"])
