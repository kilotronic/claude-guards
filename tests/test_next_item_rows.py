# SPDX-License-Identifier: MIT

"""A plan kept as row files: `next-item` offers its open rows with the todo entries, and the work's own pull request
claims a row the way it claims an entry, by a mark on the row's line."""

from __future__ import annotations

import json
from pathlib import Path

from workrun import IDENTITY, git, make_project

HEADER = "| # | Step | Home | Phase | Waits on |\n|---|---|---|---|---|\n"
SETTINGS = """\
defects = ["Defects"]
next = ["Next", "Later"]
rows = "docs/plan"
rows_name = "plan row"
rows_sections = ["soon=Next", "late=Later"]
"""
TODO = """\
# Todo

## Defects

- **The timer skips a second.** It jumps from 10 to 8 on a slow phone.

## Next

- **Add a snooze button.** Five more minutes from the ring.

## Later

- **Show the time left in the title.** So a background tab shows it.
"""


def row(n: int, step: str, phase: str, waits: str = "—", home: str = "plan.md") -> str:
    return HEADER + f"| {n} | {step} | {home} | {phase} | {waits} |\n"


def rows_project(tmp_path: Path, rows: dict[int, str] | None = None):
    files = {".claude/work.toml": SETTINGS}
    for n, text in (rows or {}).items():
        files[f"docs/plan/{n}.md"] = text
    return make_project(tmp_path, todo=TODO, extra=files)


def free(out: dict) -> list[str]:
    return [i["title"] for i in out["free"]]


def test_open_rows_come_with_their_section_before_its_entries(tmp_path: Path) -> None:
    p = rows_project(
        tmp_path,
        {3: row(3, "Wire the bell", "soon"), 4: row(4, "Polish the menu", "late")},
    )
    rc, out = p.json("--list")
    assert rc == 0, out
    assert free(out) == [
        "The timer skips a second",
        "plan row 3",
        "Add a snooze button",
        "plan row 4",
        "Show the time left in the title",
    ]
    item = next(i for i in out["free"] if i["title"] == "plan row 3")
    assert item["section"] == "Next"
    assert item["path"] == "docs/plan/3.md"
    _, text, _ = p.run("--list")
    assert "Next: plan row 3 (docs/plan/3.md line 3)" in text


def test_a_row_waiting_on_a_row_still_in_the_plan_is_not_free(tmp_path: Path) -> None:
    p = rows_project(
        tmp_path,
        {
            3: row(3, "Wire the bell", "soon"),
            5: row(
                5,
                "Ring it",
                "soon",
                waits="3 (needs the bell), 7–9; check: owner listens",
            ),
            6: row(6, "Tune it", "soon", waits="check: the owner listens"),
        },
    )
    _, out = p.json("--list")
    assert "plan row 5" not in free(out)
    assert "plan row 6" in free(out)  # a check alone waits on no row
    # Row 3 built and its file deleted: row 5 waits on nothing left.
    git(p.clone, "rm", "-q", "docs/plan/3.md")
    _, out = p.json("--list")
    assert "plan row 5" in free(out)


def test_a_row_the_rows_after_it_wait_on_moves_up_with_them(tmp_path: Path) -> None:
    p = rows_project(
        tmp_path,
        {
            2: row(2, "Lay the groundwork", "late"),
            3: row(3, "Build on it", "soon", waits="2"),
        },
    )
    _, out = p.json("--list")
    assert free(out)[:3] == [
        "The timer skips a second",
        "plan row 2",
        "Add a snooze button",
    ]


def test_a_row_with_a_skip_marker_needs_the_owner(tmp_path: Path) -> None:
    p = rows_project(
        tmp_path,
        {3: row(3, "Send it to the shop (owner)", "soon")},
    )
    _, out = p.json("--list")
    assert "plan row 3" not in free(out)


def test_a_skip_marker_in_a_rows_waits_on_check_does_not_hold_it(
    tmp_path: Path,
) -> None:
    p = rows_project(
        tmp_path, {3: row(3, "Ship it", "soon", waits="check: the (owner) approves")}
    )
    _, out = p.json("--list")
    assert "plan row 3" in free(out)


def test_a_row_of_a_phase_with_no_section_comes_last(tmp_path: Path) -> None:
    p = rows_project(tmp_path, {8: row(8, "Someday", "maybe")})
    _, out = p.json("--list")
    assert free(out)[-1] == "plan row 8"


def test_claiming_a_row_marks_its_line_on_the_work_branch(tmp_path: Path) -> None:
    p = rows_project(tmp_path, {3: row(3, "Wire the bell", "soon")})
    git(p.clone, "checkout", "-q", "-b", "work/plan-row-3", "origin/main")
    rc, out = p.json("claim", "plan row 3")
    assert rc == 0, out
    text = (p.clone / "docs/plan/3.md").read_text()
    assert text.splitlines()[2].endswith("| [claimed by agent-a on box1, 2026-11-22]")
    assert git(p.clone, "status", "--porcelain").strip() == ""
    # Released again on the same branch: the line is as it was.
    rc, out = p.json("release", "plan row 3")
    assert rc == 0, out
    assert (p.clone / "docs/plan/3.md").read_text() == row(3, "Wire the bell", "soon")


def open_row_claim(p, n: int, who: str, number: int, delete: bool = False) -> None:
    name = f"work/{who}-{number}"
    git(p.clone, "checkout", "-q", "-B", name, "origin/main")
    path = p.clone / f"docs/plan/{n}.md"
    lines = path.read_text().splitlines(keepends=True)
    lines[2] = lines[2].rstrip("\n") + f" [claimed by {who} on box1, 2026-11-21]\n"
    path.write_text("".join(lines))
    git(p.clone, *IDENTITY, "commit", "-q", "-am", "chore: claim it")
    if delete:
        git(p.clone, "rm", "-q", f"docs/plan/{n}.md")
        git(p.clone, *IDENTITY, "commit", "-q", "-m", "feat: build it")
    git(p.clone, "push", "-q", "origin", f"HEAD:refs/heads/{name}")
    tip = git(p.clone, "rev-parse", "HEAD").strip()
    git(p.clone, "checkout", "-q", "--detach", "origin/main")
    p.prs(
        "open",
        [
            {
                "number": number,
                "title": "feat: it",
                "headRefName": name,
                "headRefOid": tip,
                "isCrossRepository": False,
                "author": {"login": who},
                "url": f"https://github.com/example/widgets/pull/{number}",
                "updatedAt": "2026-11-21T12:00:00Z",
            }
        ],
    )


def test_an_open_pull_request_marking_a_row_claims_it(tmp_path: Path) -> None:
    p = rows_project(tmp_path, {3: row(3, "Wire the bell", "soon")})
    open_row_claim(p, 3, "agent-b", 7)
    _, out = p.json("--list")
    assert "plan row 3" not in free(out)
    (claim,) = out["in_progress"]["claims"]
    assert (claim["title"], claim["pr"]) == ("plan row 3", 7)


def test_a_row_its_pull_request_already_deleted_is_still_claimed(
    tmp_path: Path,
) -> None:
    p = rows_project(
        tmp_path,
        {3: row(3, "Wire the bell", "soon"), 4: row(4, "Polish", "late")},
    )
    open_row_claim(p, 3, "agent-b", 7, delete=True)
    _, out = p.json("--list")
    assert "plan row 3" not in free(out)
    assert [c["title"] for c in out["in_progress"]["claims"]] == ["plan row 3"]


def test_a_row_file_that_is_not_one_numbered_row_is_named(tmp_path: Path) -> None:
    p = rows_project(tmp_path, {3: row(4, "Wrong number", "soon")})
    rc, out = p.json()
    assert rc == 2
    assert "docs/plan/3.md" in json.dumps(out)


def test_a_section_pair_needs_an_equals_sign(tmp_path: Path) -> None:
    p = make_project(
        tmp_path,
        todo=TODO,
        extra={".claude/work.toml": SETTINGS.replace("soon=Next", "soon Next")},
    )
    rc, out = p.json()
    assert rc == 2
    assert "rows_sections" in json.dumps(out)


def test_rows_come_in_number_order_not_text_order(tmp_path: Path) -> None:
    p = rows_project(
        tmp_path,
        {n: row(n, f"Step {n}", "soon") for n in (1, 2, 10)},
    )
    _, out = p.json("--list")
    assert [t for t in free(out) if t.startswith("plan row")] == [
        "plan row 1",
        "plan row 2",
        "plan row 10",
    ]


def test_a_short_row_is_named_not_a_crash(tmp_path: Path) -> None:
    p = rows_project(tmp_path, {3: HEADER + "| 3 | Wire the bell |\n"})
    rc, out = p.json()
    assert rc == 2
    assert "docs/plan/3.md" in json.dumps(out)


def test_a_short_row_on_a_pull_request_branch_is_left_out(tmp_path: Path) -> None:
    p = rows_project(tmp_path, {3: row(3, "Wire the bell", "soon")})
    name = "work/agent-b-7"
    git(p.clone, "checkout", "-q", "-B", name, "origin/main")
    (p.clone / "docs/plan/3.md").write_text(HEADER + "| 3 |\n")
    git(p.clone, *IDENTITY, "commit", "-q", "-am", "chore: break it")
    git(p.clone, "push", "-q", "origin", f"HEAD:refs/heads/{name}")
    tip = git(p.clone, "rev-parse", "HEAD").strip()
    git(p.clone, "checkout", "-q", "--detach", "origin/main")
    p.prs(
        "open",
        [
            {
                "number": 7,
                "headRefName": name,
                "headRefOid": tip,
                "isCrossRepository": False,
                "author": {"login": "b"},
                "updatedAt": "2026-11-21T12:00:00Z",
            }
        ],
    )
    rc, out = p.json("--list")
    assert rc == 0, out
    assert "plan row 3" in free(out)


def test_claiming_a_row_that_waits_on_another_is_refused(tmp_path: Path) -> None:
    p = rows_project(
        tmp_path,
        {3: row(3, "Wire the bell", "soon"), 5: row(5, "Ring it", "soon", waits="3")},
    )
    git(p.clone, "checkout", "-q", "-b", "work/plan-row-5", "origin/main")
    rc, out = p.json("claim", "plan row 5")
    assert rc == 3
    assert "plan row 3" in out["message"]


def test_waits_on_reads_only_row_numbers_before_a_check() -> None:
    import next_item

    assert next_item._waits("3, check: run 12") == (3,)
    assert next_item._waits("7–9 (needs the bell), 12") == (7, 8, 9, 12)
    assert next_item._waits("—") == ()
