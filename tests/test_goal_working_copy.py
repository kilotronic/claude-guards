# SPDX-License-Identifier: MIT

"""goal reads the working copy; only claim marks come from the default branch GitHub names."""

from __future__ import annotations

from pathlib import Path

from workrun import IDENTITY, TODO, claimed, git, make_project


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


def test_a_claim_mark_only_in_the_working_copy_is_not_counted(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.edit_local(
        "docs/todo.md",
        "- **The timer skips a second.** It jumps",
        "- **The timer skips a second.** [claimed by agent-b on box2, 2026-11-21]\n  It jumps",
    )
    rc, out = p.json()
    assert rc == 0
    assert out["in_progress"]["count"] == 0
    assert out["next"]["item"]["title"] == "The timer skips a second"
    assert any("not yet merged" in w for w in out["warnings"])
    assert out["unmerged_claims"] == ["The timer skips a second"]


def test_your_own_local_only_claim_is_not_yours_yet(tmp_path: Path) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-a", "2026-11-22")
    p = make_project(tmp_path)
    (p.clone / "docs" / "todo.md").write_text(todo)
    _, out = p.json()
    assert out["next"]["kind"] != "own"
    assert out["in_progress"]["count"] == 0


def test_a_merged_claim_is_counted_even_when_the_working_copy_lacks_it(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    p.edit_local("docs/todo.md", " [claimed by agent-b on box1, 2026-11-21]", "")
    _, out = p.json()
    assert out["in_progress"]["count"] == 1
    assert out["in_progress"]["claims"][0]["who"] == "agent-b"


def test_a_stale_origin_head_does_not_change_whose_claims_are_read(
    tmp_path: Path,
) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    # A second branch on origin with no claim, which the clone's origin/HEAD wrongly points at.
    git(p.clone, "checkout", "-q", "-B", "legacy", "origin/main")
    (p.clone / "docs" / "todo.md").write_text(TODO)
    git(p.clone, "add", "-A")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", "docs: old")
    git(p.clone, "push", "-q", "origin", "HEAD:refs/heads/legacy")
    git(p.clone, "checkout", "-q", "--detach", "origin/main")
    git(p.clone, "fetch", "-q", "origin")
    git(
        p.clone,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/legacy",
    )
    p.default_branch_is("main")
    _, out = p.json()
    assert out["in_progress"]["count"] == 1
    assert out["claims_from"] == "origin/main"
    p.default_branch_is("legacy")
    _, out = p.json()
    assert out["in_progress"]["count"] == 0
    assert out["claims_from"] == "origin/legacy"


def test_github_unreachable_says_claims_were_not_checked(tmp_path: Path) -> None:
    todo = claimed(TODO, "Add a snooze button", "agent-b", "2026-11-21")
    p = make_project(tmp_path, todo=todo)
    p.edit_local("README.md", "M2, the phone app", "M3, the watch app")
    p.gh_fails()
    rc, out = p.json()
    assert rc == 0
    assert out["goal"]["milestone"] == "M3, the watch app"
    assert out["next"]["item"]["title"] == "The timer skips a second"
    assert out["claims_from"] is None
    assert any("claims weren't checked" in w for w in out["warnings"])
    rc, text, _ = p.run()
    assert "claims weren't checked" in text
