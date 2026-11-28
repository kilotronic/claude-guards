# SPDX-License-Identifier: MIT

"""`next-item claimed [<branch>]`: may this branch's pull request open? The claim hook asks it at `gh pr create`.

Yes (exit 0) when the branch changes only docs, carries a claim mark it made, or a mark on the default branch
is yours; and when the repo has no todo file to mark. No (exit 3) when it changes code and carries no claim.
"""

from __future__ import annotations

from pathlib import Path

from workrun import IDENTITY, README, TODO, Project, claimed, git, make_project

ISSUES_README = README.replace("`docs/todo.md`", "GitHub issues")
FROZEN_README = (
    "# Widgets\n\n## How work runs here\n\n- **Frozen:** new work goes to gadgets.\n"
)


def work(p: Project, files: dict[str, str], name: str = "work/snooze") -> None:
    """Check out a work branch off origin/main (or stay on it) and commit `files` there."""
    if git(p.clone, "branch", "--show-current").strip() != name:
        git(p.clone, "checkout", "-q", "-B", name, "origin/main")
    for rel, text in files.items():
        path = p.clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(p.clone, "add", "-A")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", "feat: work")


def claim(p: Project, title: str = "Add a snooze button", name: str = "work/snooze"):
    git(p.clone, "checkout", "-q", "-B", name, "origin/main")
    rc, out, err = p.run("claim", title)
    assert rc == 0, out + err


def test_code_with_no_claim_mark_is_not_claimed(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    work(p, {"src/snooze.py": "x = 1\n", "README.md": README + "\nMore.\n"})
    rc, out = p.json("claimed")
    assert rc == 3
    assert (out["status"], out["claimed"], out["code"]) == (
        "no",
        False,
        ["src/snooze.py"],
    )
    assert "src/snooze.py" in out["message"]


def test_a_branch_that_starts_with_the_claim_commit_is_claimed(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    claim(p)
    work(p, {"src/snooze.py": "x = 1\n"})
    rc, out = p.json("claimed")
    assert (rc, out["claimed"]) == (0, True)


def test_the_claim_holds_once_finish_work_deletes_the_entry(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    claim(p)
    todo = (p.clone / "docs" / "todo.md").read_text()
    entry = next(ln for ln in todo.splitlines() if "Add a snooze button" in ln)
    work(
        p, {"src/snooze.py": "x = 1\n", "docs/todo.md": todo.replace(entry + "\n", "")}
    )
    rc, out = p.json("claimed")
    assert (rc, out["claimed"]) == (0, True)


def test_docs_only_needs_no_claim(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    work(p, {"README.md": README + "\nMore.\n", "docs/design/note.txt": "a note\n"})
    rc, out = p.json("claimed")
    assert (rc, out["claimed"]) == (0, None)
    assert "only docs" in out["message"]


def test_a_file_renamed_out_of_docs_is_code(tmp_path: Path) -> None:
    p = make_project(tmp_path, extra={"src/a.py": "x = 1\n"})
    git(p.clone, "checkout", "-q", "-B", "work/snooze", "origin/main")
    git(p.clone, "mv", "src/a.py", "docs/a.md")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", "refactor: move")
    rc, out = p.json("claimed")
    assert (rc, out["code"]) == (3, ["src/a.py"])


def test_the_repo_names_its_own_docs_paths(tmp_path: Path) -> None:
    p = make_project(
        tmp_path, extra={".claude/work.toml": 'docs = ["*.md", "notes/*"]\n'}
    )
    work(p, {"notes/deep/plan.txt": "a plan\n", "CHANGES.md": "x\n"})
    assert p.json("claimed")[0] == 0
    work(p, {"docs/build.sh": "echo hi\n"})
    rc, out = p.json("claimed")
    assert (rc, out["code"]) == (3, ["docs/build.sh"])


def test_a_repo_with_no_todo_file_to_mark_needs_no_claim(tmp_path: Path) -> None:
    for n, readme in enumerate(("# Widgets\n", ISSUES_README, FROZEN_README)):
        p = make_project(tmp_path / str(n), readme=readme, todo=None)
        work(p, {"src/snooze.py": "x = 1\n"})
        rc, out = p.json("claimed")
        assert (rc, out["claimed"]) == (0, None), readme
        assert out["message"].startswith("no claim needed"), readme


def test_a_mark_on_the_default_branch_counts_when_it_is_yours(tmp_path: Path) -> None:
    title = "Add a snooze button"
    p = make_project(tmp_path, todo=claimed(TODO, title, "agent-a", "2026-11-20"))
    work(p, {"src/snooze.py": "x = 1\n"})
    rc, out = p.json("claimed")
    assert (rc, out["claimed"]) == (0, True)
    assert "origin/main" in out["message"]
    # Someone else's mark came with the branch from main: it is not this branch's claim.
    assert p.json("claimed", who="agent-b")[0] == 3


def test_a_named_branch_is_judged_from_anywhere(tmp_path: Path) -> None:
    p = make_project(tmp_path)
    p.branch("work/code", "2026-11-21", {"code.py": "x = 1\n"})
    p.branch("work/docs", "2026-11-21", {"notes.md": "x\n"})
    assert p.json("claimed", "work/code")[0] == 3
    assert p.json("claimed", "work/docs")[0] == 0
    rc, _, err = p.run("claimed", "work/none")
    assert rc == 1 and "no branch work/none" in err


def test_it_reads_local_files_and_asks_github_only_for_the_default_branch(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    claim(p)
    work(p, {"src/snooze.py": "x = 1\n"})
    before = len(p.gh_calls())
    assert p.json("claimed")[0] == 0
    assert [c[:2] for c in p.gh_calls()[before:]] == [["repo", "view"]]
    p.gh_fails()
    assert p.json("claimed")[0] == 0


def test_settings_it_cannot_read_are_unclear(tmp_path: Path) -> None:
    p = make_project(tmp_path, extra={".claude/work.toml": "docs = 3\n"})
    work(p, {"src/snooze.py": "x = 1\n"})
    rc, out = p.json("claimed")
    assert rc == 2 and "docs" in out["missing"][0]


def test_the_todo_file_and_the_plan_rows_are_never_code(tmp_path: Path) -> None:
    readme = README.replace("`docs/todo.md`", "`TODO.txt`")
    row = "| # | Step |\n| --- | --- |\n| 1 | Build it |\n"
    p = make_project(
        tmp_path,
        readme=readme,
        todo=None,
        extra={
            "TODO.txt": TODO,
            ".claude/work.toml": 'rows = "plan"\n',
            "plan/1.md": row,
        },
    )
    work(p, {"TODO.txt": TODO + "- **One more.** Later.\n", "plan/2.txt": "x\n"})
    rc, out = p.json("claimed")
    assert (rc, out["claimed"]) == (0, None), out
