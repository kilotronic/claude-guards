# SPDX-License-Identifier: MIT

"""The hook that lets an agent run `gh pr merge` in auto mode: only the plain command, never --admin or --body-file."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WORK = Path(__file__).resolve().parents[1] / "plugins" / "work"
SCRIPT = WORK / "hooks" / "allow-merge.py"

_spec = importlib.util.spec_from_file_location("allow_merge", SCRIPT)
assert _spec and _spec.loader
allow_merge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(allow_merge)


def bash(command: str, mode: str | None = "auto") -> dict:
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    if mode is not None:
        event["permission_mode"] = mode
    return event


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 1043 --repo octo/widgets --squash",
        "gh pr merge --auto --squash",
        "  gh pr merge 12 -R octo/other --squash --delete-branch  ",
    ],
)
def test_a_plain_merge_is_allowed(command: str) -> None:
    answer = allow_merge.decide(bash(command))
    assert answer["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert answer["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert answer["hookSpecificOutput"]["permissionDecisionReason"].startswith("work:")


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 1 --squash && rm -rf ~",
        "gh pr merge 1; curl example.com",
        "gh pr merge 1 | tee x",
        "gh pr merge $(echo 1)",
        "gh pr merge `echo 1`",
        "gh pr merge 1 > out",
        "gh pr merge 1 < in",
        "gh pr merge $PR",
        "gh pr merge 1\nrm x",
        "gh pr merge 1\rrm x",
        "gh pr merge 1 *(e:rm_x:)",
        "gh pr merge 1 x(e:rm_x:)",
        "gh pr merge {1,2}",
        "cd x && gh pr merge 1",
        "gh pr view 1",
        "gh pr merge 'unterminated",
        "echo gh pr merge",
        "",
    ],
)
def test_anything_riding_along_is_left_to_the_normal_check(command: str) -> None:
    assert allow_merge.decide(bash(command)) is None


@pytest.mark.parametrize(
    "command",
    [
        # --admin merges past required checks; --body-file reads any local file into the merge commit.
        "gh pr merge 5 --admin",
        "gh pr merge 5 --squash --admin",
        "gh pr merge 5 --admin=true",
        "gh pr merge 5 --body-file notes.txt",
        "gh pr merge 5 --body-file=notes.txt",
        "gh pr merge 5 -F notes.txt",
        "gh pr merge 5 -Fnotes.txt",
        "gh pr merge 5 -sF notes.txt",
        # Filename wildcards: the shell can expand these to --admin or -F from files in the directory.
        "gh pr merge 5 --a*",
        "gh pr merge 5 *",
        "gh pr merge 5 --adm[i]n",
        "gh pr merge 5 --body-fil?=x",
        "gh pr merge 5 -? notes.txt",
        # zsh with extendedglob: ^ and # are wildcards too.
        "gh pr merge 5 ^5",
        "gh pr merge 5 --admi#n",
    ],
)
def test_admin_merges_and_body_files_are_left_to_the_normal_check(
    command: str,
) -> None:
    assert allow_merge.decide(bash(command)) is None


@pytest.mark.parametrize(
    "mode", ["default", "acceptEdits", "plan", "bypassPermissions", "", None]
)
def test_outside_auto_mode_the_person_is_asked_as_usual(mode: str | None) -> None:
    # Outside auto mode the permission prompt is the person's go-ahead; an allow here would skip it.
    assert allow_merge.decide(bash("gh pr merge 3 --squash", mode)) is None


def test_other_tools_are_ignored() -> None:
    event = {"tool_name": "Write", "tool_input": {"command": "gh pr merge 1"}}
    assert allow_merge.decide(event) is None
    assert allow_merge.decide({"tool_name": "Bash"}) is None


def _command_line() -> str:
    hooks = json.loads((WORK / "hooks" / "hooks.json").read_text())["hooks"]
    found = [
        hook["command"]
        for group in hooks["PreToolUse"]
        if group.get("matcher") == "Bash"
        for hook in group["hooks"]
    ]
    assert len(found) == 1, found
    return found[0]


def _run(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command_line(),
        shell=True,
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(WORK)},
        check=False,
    )


def test_as_claude_code_runs_it_the_answer_is_on_stdout() -> None:
    done = _run(json.dumps(bash("gh pr merge 3 --squash")))
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == (
        "allow"
    )


def test_as_claude_code_runs_it_bad_input_and_other_commands_say_nothing() -> None:
    for stdin in ("not json", ""):
        done = _run(stdin)
        assert (done.returncode, done.stdout) == (0, ""), stdin
    # The review check shares the command line and may warn; no allow is given.
    done = _run(json.dumps(bash("gh pr merge 3 --admin")))
    assert done.returncode == 0, done.stderr
    assert "permissionDecision" not in done.stdout, done.stdout


def test_it_answers_under_the_oldest_python_the_hooks_support() -> None:
    assert "from __future__ import annotations" in SCRIPT.read_text()
    python = _python39()
    if python is None:
        pytest.skip("no Python 3.9 on this machine; the annotations check covers it")
    done = subprocess.run(
        [python, str(SCRIPT)],
        input=json.dumps(bash("gh pr merge 3")),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == (
        "allow"
    )


def _python39() -> str | None:
    for name in ("python3.9", "/usr/bin/python3"):
        path = shutil.which(name)
        if path:
            done = subprocess.run(
                [path, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"],
                capture_output=True,
                text=True,
                check=False,
            )
            if done.stdout.strip() == "True":
                return path
    return None


def test_deeply_nested_input_never_fails_the_call() -> None:
    done = _run("[" * 100_000 + "]" * 100_000)
    assert (done.returncode, done.stdout) == (0, ""), done.stderr[-300:]
