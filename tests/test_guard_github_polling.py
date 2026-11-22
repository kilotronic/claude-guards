# SPDX-License-Identifier: MIT

"""no-github-polling: one slow watch (`--interval 60` or more) instead of a fast watch or a gh loop."""

from __future__ import annotations

import pytest
from hookrun import bash

SCRIPT = "guard-github-polling.py"


@pytest.mark.parametrize(
    "command",
    [
        "gh run watch 123",
        "gh run watch 123 --exit-status",
        "gh run watch 1 --interval 30",
        "gh run watch 1 -i 59",
        "gh pr checks 5 --watch",
        "gh pr checks --watch -i 10",
        "gh pr checks 5 --watch --interval=20",
        "gh pr checks 5 --watch --interval soon",
        "while true; do gh run list; sleep 30; done",
        "until gh pr checks 5; do sleep 60; done",
        "while :; do\n  gh api repos/o/r/pulls/5 --jq .state\n  sleep 30\ndone",
        "for i in $(seq 10); do gh run list; sleep 30; done",
        "repeat 10 { gh pr view 5; sleep 20 }",
        "watch -n 30 gh pr checks 5",
        "watch 'gh run list'",
        "cd x && bash -c 'while true; do gh run list; done'",
        "for i in $(seq 1 200); do gh api repos/o/r/pulls/5; done",
        "for i in {1..20}; do gh pr checks 5; sleep 30; done",
        'until [ "$(gh pr view 5 --json state -q .state)" = MERGED ]; do sleep 5; done',
        "while [ `gh pr view 5 --json state -q .state` != MERGED ]; do sleep 5; done",
    ],
)
def test_polling_is_refused(command: str) -> None:
    rc, err = bash(SCRIPT, command)
    assert rc == 2, err
    assert "[guards:no-github-polling]" in err
    assert "--interval 60" in err


def test_a_monitor_that_polls_github_is_refused() -> None:
    rc, err = bash(
        SCRIPT,
        "while true; do gh api repos/o/r/issues/5/comments; sleep 30; done",
        tool="Monitor",
    )
    assert rc == 2, err


@pytest.mark.parametrize(
    "command",
    [
        "gh pr checks 5 --watch --interval 60",
        "gh pr checks 5 --watch -i 120",
        "gh run watch 123 --interval=60 --exit-status",
        "gh pr checks 5",
        "gh run list --limit 5",
        "for n in 1 2 3; do gh pr view $n --json state; done",
        "gh run list --json databaseId -q '.[].databaseId' | while read -r id; do gh run view $id; done",
        "while read -r line; do echo $line; done < notes.txt",
        "until [ -f done.txt ]; do sleep 1; done",
        "echo 'while true; do gh run list; done'",
        "git commit -m 'stop: gh run watch polls every 3s'",
        "watch -n 1 ls",
        "gh pr list -q '.[].number' --json number | while read n; do gh pr merge $n --auto; sleep 1; done",
        "for pr in 12 13 14; do gh pr merge $pr --auto --squash; sleep 2; done",
        "for r in a b; do gh repo view o/$r --json name; done",
        'for d in */; do (cd "$d" && gh pr list); done',
        "for r in app api; do cd ~/src/$r && gh pr list; done",
        "for f in $(gh pr list -q '.[].number' --json number); do echo $f; done",
        "gh pr checks 5 --json name,state",
    ],
)
def test_one_off_and_slow_calls_pass(command: str) -> None:
    assert bash(SCRIPT, command) == (0, "")


def test_a_monitor_tailing_a_log_passes() -> None:
    assert bash(
        SCRIPT, "tail -f app.log | grep --line-buffered ERROR", tool="Monitor"
    ) == (0, "")
