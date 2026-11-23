# SPDX-License-Identifier: MIT

"""Park and resume on GitHub: a usage limit saves the work to its pull request, a later session takes it."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

WORK = Path(__file__).resolve().parents[1] / "plugins" / "work"
SCRIPT = WORK / "scripts" / "park.py"

_spec = importlib.util.spec_from_file_location("park", SCRIPT)
assert _spec and _spec.loader
park = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(park)


class Run:
    """A stand-in for git and gh: answers by the longest matching command prefix, records every call."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[int, str]]):
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: str) -> tuple[int, str]:
        self.calls.append(args)
        best = max(
            (k for k in self.answers if tuple(args[: len(k)]) == k),
            key=len,
            default=None,
        )
        return self.answers[best] if best is not None else (0, "")

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


ON_BRANCH = {
    ("git", "rev-parse", "--show-toplevel"): (0, "/w/repo-fix\n"),
    ("git", "branch", "--show-current"): (0, "fix/thing\n"),
    ("git", "symbolic-ref"): (0, "origin/main\n"),
    ("git", "status", "--porcelain"): (0, " M a.py\n?? notes.txt\n"),
    ("git", "rev-parse", "-q", "--verify"): (1, ""),
    ("gh", "pr", "view"): (
        0,
        json.dumps({"number": 7, "url": "https://x/pull/7", "state": "OPEN"}),
    ),
}
HOOK = {
    "session_id": "abcd1234-5678",
    "cwd": "/w/repo-fix",
    "error_details": "You've hit your limit · resets 3pm\nmore",
}


def test_park_commits_pushes_labels_and_comments():
    run = Run(dict(ON_BRANCH))
    out = park.park(HOOK, run, "host1")
    assert run.ran("git", "add", "-u") and not run.ran("git", "add", "-A")
    commit = run.ran("git", "commit")[0]
    assert "--no-verify" in commit and "wip: parked at a usage limit" in commit
    assert run.ran("git", "push", "origin", "HEAD")
    assert ["gh", "pr", "edit", "7", "--add-label", "parked"] in run.calls
    body = run.ran("gh", "pr", "comment", "7")[0][-1]
    assert body.startswith("🤖 **Agent update**")
    for fact in ("host1", "abcd1234-5678", "resets 3pm", "fix/thing", "park resume 7"):
        assert fact in body
    assert "more" not in body  # one line of the reason
    assert "/w/repo-fix" not in body  # no local path in a comment others read
    assert "1 untracked file" in body
    assert "#7" in out


def test_park_with_nothing_uncommitted_makes_no_commit_but_still_pushes():
    answers = dict(ON_BRANCH)
    answers[("git", "status", "--porcelain")] = (0, "")
    run = Run(answers)
    park.park(HOOK, run, "host1")
    assert not run.ran("git", "commit")
    assert run.ran("git", "push", "origin", "HEAD")


def test_park_on_the_default_branch_touches_nothing():
    answers = dict(ON_BRANCH)
    answers[("git", "branch", "--show-current")] = (0, "main\n")
    run = Run(answers)
    out = park.park(HOOK, run, "host1")
    assert not run.ran("git", "commit") and not run.ran("git", "push")
    assert not run.ran("gh")
    assert "nothing to park" in out


def test_park_outside_a_repo_or_in_a_subagent_does_nothing():
    run = Run({("git", "rev-parse", "--show-toplevel"): (128, "")})
    assert park.park(HOOK, run, "h") == ""
    run = Run(dict(ON_BRANCH))
    assert park.park({**HOOK, "agent_id": "a1"}, run, "h") == ""
    assert run.calls == []


def test_park_without_a_pull_request_pushes_and_says_so():
    answers = dict(ON_BRANCH)
    answers[("gh", "pr", "view")] = (1, "")
    run = Run(answers)
    out = park.park(HOOK, run, "host1")
    assert run.ran("git", "push", "origin", "HEAD")
    assert not run.ran("gh", "pr", "edit") and not run.ran("gh", "pr", "comment")
    assert "no open pull request" in out


def test_park_makes_the_label_when_the_repo_has_none():
    answers = dict(ON_BRANCH)
    answers[("gh", "pr", "edit")] = (1, "")
    run = Run(answers)
    park.park(HOOK, run, "host1")
    assert run.ran("gh", "label", "create", "parked")
    assert len(run.ran("gh", "pr", "edit")) == 2


def test_park_still_labels_when_the_push_fails():
    answers = dict(ON_BRANCH)
    answers[("git", "push")] = (1, "")
    run = Run(answers)
    out = park.park(HOOK, run, "host1")
    assert run.ran("gh", "pr", "edit")
    body = run.ran("gh", "pr", "comment", "7")[0][-1]
    assert "not pushed" in body and "host1" in body
    assert "not pushed" in out


PARKED = [
    {"number": 7, "title": "Fix the thing", "headRefName": "fix/thing"},
    {"number": 9, "title": "Add a page", "headRefName": "feat/page"},
]


def test_notice_lists_parked_pull_requests():
    run = Run({("gh", "pr", "list"): (0, json.dumps(PARKED))})
    text = park.notice("/w/repo", run)
    call = run.ran("gh", "pr", "list")[0]
    assert "--label" in call and "parked" in call
    assert "#7 Fix the thing (fix/thing)" in text and "#9" in text
    assert "park resume <n>" in text


def test_notice_is_silent_with_none_or_when_github_fails():
    assert park.notice("/w/repo", Run({("gh", "pr", "list"): (0, "[]")})) == ""
    assert park.notice("/w/repo", Run({("gh", "pr", "list"): (1, "")})) == ""
    assert park.notice("/w/repo", Run({("gh", "pr", "list"): (0, "not json")})) == ""


def test_resume_takes_the_label_off_and_says_where_the_work_is():
    run = Run(
        {
            ("gh", "pr", "view"): (
                0,
                json.dumps(
                    {
                        "headRefName": "fix/thing",
                        "labels": [{"name": "parked"}],
                        "state": "OPEN",
                        "isCrossRepository": False,
                    }
                ),
            )
        }
    )
    code, text = park.resume("7", "/w/repo", "s-new", "host2", run)
    assert code == 0
    assert ["gh", "pr", "edit", "7", "--remove-label", "parked"] in run.calls
    body = run.ran("gh", "pr", "comment", "7")[0][-1]
    assert body.startswith("🤖 **Agent update**") and "host2" in body
    assert "fix/thing" in text and "git worktree add" in text


def test_resume_refuses_a_pull_request_that_is_not_parked():
    run = Run(
        {
            ("gh", "pr", "view"): (
                0,
                json.dumps(
                    {
                        "headRefName": "fix/thing",
                        "labels": [],
                        "state": "OPEN",
                        "isCrossRepository": False,
                    }
                ),
            )
        }
    )
    code, text = park.resume("7", "/w/repo", "s", "h", run)
    assert code == 1 and "not parked" in text
    assert not run.ran("gh", "pr", "edit")


def test_the_hooks_are_wired():
    hooks = json.loads((WORK / "hooks" / "hooks.json").read_text())["hooks"]
    stop = hooks["StopFailure"][0]
    assert stop["matcher"] == "rate_limit"
    assert stop["hooks"][0]["command"].endswith('park.py" hook')
    starts = [h["command"] for g in hooks["SessionStart"] for h in g["hooks"]]
    assert any(c.endswith('park.py" notice') for c in starts)
    assert (WORK / "bin" / "park").is_file()


def test_the_hook_never_fails_on_bad_input(tmp_path):
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "hook"],
        input="not json",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0


@pytest.mark.parametrize("args", [[], ["resume"], ["nope"]])
def test_bad_usage_exits_2(args):
    done = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 2


def test_the_machine_name_can_be_set_for_a_public_repo(monkeypatch):
    monkeypatch.setenv("GOAL_MACHINE", "laptop")
    assert park.machine() == "laptop"
    monkeypatch.delenv("GOAL_MACHINE")
    assert park.machine()


def test_park_mid_merge_touches_nothing():
    answers = dict(ON_BRANCH)
    answers[("git", "rev-parse", "-q", "--verify")] = (0, "abc\n")
    run = Run(answers)
    out = park.park(HOOK, run, "host1")
    assert not run.ran("git", "commit") and not run.ran("git", "push")
    assert "in progress" in out


def test_park_leaves_a_closed_pull_request_alone():
    answers = dict(ON_BRANCH)
    answers[("gh", "pr", "view")] = (
        0,
        json.dumps({"number": 7, "url": "u", "state": "MERGED"}),
    )
    run = Run(answers)
    out = park.park(HOOK, run, "host1")
    assert not run.ran("gh", "pr", "edit") and not run.ran("gh", "pr", "comment")
    assert "no open pull request" in out


def resume_run(**pr):
    base = {
        "headRefName": "fix/thing",
        "labels": [{"name": "parked"}],
        "state": "OPEN",
        "isCrossRepository": False,
    }
    return Run({("gh", "pr", "view"): (0, json.dumps({**base, **pr}))})


def test_resume_refuses_a_fork_or_a_closed_pull_request():
    for pr in ({"isCrossRepository": True}, {"state": "CLOSED"}):
        run = resume_run(**pr)
        code, _ = park.resume("7", "/w", "s", "h", run)
        assert code == 1
        assert not run.ran("gh", "pr", "edit")


def test_resume_quotes_the_branch_in_the_command_it_prints():
    run = resume_run(headRefName="fix/a;rm")
    code, text = park.resume("7", "/w", "s", "h", run)
    assert code == 0
    assert "'fix/a;rm'" in text


def test_git_and_gh_never_wait_for_a_password():
    env = park.quiet_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert park.DEADLINE <= 50
