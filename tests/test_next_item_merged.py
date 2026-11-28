# SPDX-License-Identifier: MIT

"""`next-item merged <branch>`: the merged-branch test that finish-work and branch-prune share.

Merged means every commit is on main as the same patch (`git cherry`), or a merged pull request's head is the
branch's tip, which is how a squash merge shows. A rebase onto a moved main changes a hunk's context lines and so
its patch id, so two more proofs follow: every commit has a commit of the same subject on main with the same
changed lines, or merging the branch into main changes nothing (`git merge-tree`). Anything else is not proven
merged; a same-subject commit that matches on most changed lines is reported as a suspect, never as merged.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import next_item
import pytest
from workrun import IDENTITY, Project, git, make_project

LINES = "".join(f"line {n}\n" for n in range(1, 13))
SUBJECT = "feat: work/snooze"  # the subject `Project.branch` gives the commit of a branch named work/snooze


@pytest.fixture(autouse=True)
def no_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests run git in scratch repos: a GIT_DIR or GIT_INDEX_FILE leaked by a hook would aim it elsewhere."""
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)


def edited(**lines: str) -> str:
    """LINES with some lines replaced: `edited(n6="six")` puts `six` on line 6."""
    text = LINES
    for n, new in lines.items():
        text = text.replace(f"line {n[1:]}\n", f"{new}\n")
    return text


def app_project(tmp_path: Path) -> Project:
    return make_project(tmp_path, extra={"app.txt": LINES})


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
        next_item.merged(args)


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


def test_a_commit_rebased_onto_a_moved_main_is_merged(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    p.branch("work/snooze", "2026-11-21", {"app.txt": edited(n6="six")})
    # Main changes a line inside the hunk's context, so the landed commit has another patch id.
    p.push_files({"app.txt": edited(n4="four")}, "fix: four")
    git(p.clone, "checkout", "-q", "-B", "tmp", "origin/main")
    git(p.clone, *IDENTITY, "cherry-pick", "origin/work/snooze")
    git(p.clone, "push", "-q", "origin", "HEAD:main")
    cherry = git(p.clone, "cherry", "origin/main", "origin/work/snooze")
    assert cherry.startswith("+"), "the patch id must differ, or this tests nothing new"
    # A later change to the same line, so merging the branch conflicts: only the changed lines prove it.
    p.push_files({"app.txt": edited(n4="four", n6="six again")}, "fix: six again")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 0, out
    assert out["merged"] is True
    assert "same changed lines" in out["reason"]


def test_a_branch_that_adds_nothing_to_main_is_merged(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    p.branch("work/snooze", "2026-11-21", {"app.txt": edited(n6="six")})
    # The same change lands inside a larger commit under another subject: no patch and no subject matches.
    p.push_files({"app.txt": edited(n2="two", n6="six")}, "feat: two and six")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 0, out
    assert "git merge-tree" in out["reason"]


def test_a_commit_that_landed_with_an_edit_is_a_suspect_not_merged(
    tmp_path: Path,
) -> None:
    p = app_project(tmp_path)
    tip = p.branch(
        "work/snooze",
        "2026-11-21",
        {"app.txt": edited(n6="six", n7="seven", n8="eight")},
    )
    git(
        p.clone, "branch", "-D", "work/snooze"
    )  # origin's copy alone, so one line of evidence
    p.push_files({"app.txt": edited(n6="six", n7="seven", n8="EIGHT")}, SUBJECT)
    landed = git(p.clone, "rev-parse", "origin/main").strip()
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out
    assert out["merged"] is False
    assert out["suspect"] is True
    # Six changed lines each side; the two `eight` lines differ, so 5 are shared of the 7 distinct ones.
    assert out["evidence"] == [
        {
            "ref": "origin/work/snooze",
            "commit": tip,
            "subject": SUBJECT,
            "base_commit": landed,
            "matched": 5,
            "total": 7,
        }
    ]
    rc, text, _ = p.run("merged", "work/snooze")
    assert rc == 3
    assert (
        f"{tip[:9]} matches {landed[:9]} on origin/main in 5 of 7 changed lines" in text
    )
    assert "suspect" in text


def test_the_same_subject_with_other_changes_is_not_merged(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    p.branch("work/snooze", "2026-11-21", {"app.txt": edited(n6="six")})
    p.push_files({"app.txt": edited(n10="ten", n11="eleven")}, SUBJECT)
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out
    assert out["merged"] is False
    assert out["suspect"] is False
    # The local branch and origin's copy each say so: nothing shared among the six changed lines.
    assert [(e["matched"], e["total"]) for e in out["evidence"]] == [(0, 6), (0, 6)]


def test_one_landed_commit_does_not_merge_a_branch_with_another(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    p.branch("work/snooze", "2026-11-21", {"app.txt": edited(n6="six")})
    p.push_files({"app.txt": edited(n4="four")}, "fix: four")
    git(p.clone, "checkout", "-q", "-B", "tmp", "origin/main")
    git(p.clone, *IDENTITY, "cherry-pick", "origin/work/snooze")
    git(p.clone, "push", "-q", "origin", "HEAD:main")
    git(p.clone, "checkout", "-q", "-B", "work/snooze", "origin/work/snooze")
    (p.clone / "more.txt").write_text("more\n")
    git(p.clone, "add", "more.txt")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", "feat: more")
    git(p.clone, "push", "-q", "origin", "HEAD:refs/heads/work/snooze")
    git(p.clone, "checkout", "-q", "--detach", "origin/main")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out
    assert out["suspect"] is False


def test_a_same_subject_commit_with_another_binary_file_is_not_merged(
    tmp_path: Path,
) -> None:
    p = make_project(tmp_path)
    git(p.clone, "checkout", "-q", "-B", "work/snooze", "origin/main")
    (p.clone / "bell.bin").write_bytes(b"\x00\x01ring")
    git(p.clone, "add", "bell.bin")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", SUBJECT)
    git(p.clone, "push", "-q", "origin", "HEAD:refs/heads/work/snooze")
    git(p.clone, "checkout", "-q", "--detach", "origin/main")
    (p.clone / "bell.bin").write_bytes(b"\x00\x02buzz")
    git(p.clone, "add", "bell.bin")
    git(p.clone, *IDENTITY, "commit", "-q", "-m", SUBJECT)
    git(p.clone, "push", "-q", "origin", "HEAD:main")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out
    assert out["suspect"] is False


def test_without_github_the_changed_lines_still_prove_a_merge(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    p.branch("work/snooze", "2026-11-21", {"app.txt": edited(n6="six")})
    p.push_files({"app.txt": edited(n2="two", n6="six")}, "feat: two and six")
    p.gh_fails()
    rc, out = p.json("merged", "work/snooze")
    assert rc == 0, out


def test_a_subject_on_main_cannot_name_a_commit_as_its_match(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    tip = p.branch("work/snooze", "2026-11-21", {"app.txt": edited(n6="six")})
    # A carriage return (or another line break) in a subject on main must not read as a second line of the log.
    for n, brk in enumerate(("\r", "\x0c", "\x1e", "\u2028")):
        p.push_files({f"other{n}.txt": "x\n"}, f"chore: innocuous{brk}{tip} {SUBJECT}")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out
    assert out["evidence"] == []


def test_a_merge_driver_that_keeps_mains_file_proves_nothing(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    p.push_files({".gitattributes": "app.txt merge=ours\n"}, "chore: keep ours")
    git(p.clone, "config", "merge.ours.driver", "true")
    p.branch("work/snooze", "2026-11-21", {"app.txt": edited(n6="six")})
    p.push_files({"app.txt": edited(n2="two")}, "fix: two")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out


def test_one_commit_on_main_matches_one_commit_of_the_branch(tmp_path: Path) -> None:
    p = app_project(tmp_path)
    git(p.clone, "checkout", "-q", "-B", "work/snooze", "origin/main")
    for _ in range(2):
        with (p.clone / "app.txt").open("a") as f:
            f.write("again\n")
        git(p.clone, *IDENTITY, "commit", "-q", "-am", SUBJECT)
    git(p.clone, "push", "-q", "origin", "HEAD:refs/heads/work/snooze")
    git(p.clone, "checkout", "-q", "--detach", "origin/main")
    # Main moves first, so its commit is not the branch's first one under the same SHA.
    p.push_files({"app.txt": edited(n4="four")}, "fix: four")
    p.push_files({"app.txt": edited(n4="four") + "again\n"}, SUBJECT)
    # Main's commit is the first one's patch, so it is not there to answer for the second one's changed lines.
    cherry = git(p.clone, "cherry", "origin/main", "origin/work/snooze")
    assert [line[0] for line in cherry.splitlines()] == ["-", "+"]
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out
    assert out["evidence"] == []


@pytest.mark.parametrize("how", ["committed", "info", "config"])
def test_a_union_merge_that_keeps_mains_line_proves_nothing(
    tmp_path: Path, how: str
) -> None:
    p = app_project(tmp_path)
    if how == "committed":
        p.push_files({".gitattributes": "app.txt merge=union\n"}, "chore: union")
    elif how == "info":
        info = Path(git(p.clone, "rev-parse", "--git-path", "info/attributes").strip())
        (p.clone / info).parent.mkdir(exist_ok=True)
        (p.clone / info).write_text("app.txt merge=union\n")
    else:
        git(p.clone, "config", "merge.default", "union")
    # The branch deletes a line that main then rewrites: a union merge keeps main's line, so main's tree.
    p.branch("work/snooze", "2026-11-21", {"app.txt": LINES.replace("line 6\n", "")})
    p.push_files({"app.txt": edited(n6="six")}, "fix: six")
    rc, out = p.json("merged", "work/snooze")
    assert rc == 3, out
